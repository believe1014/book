"""行程內登入失敗限流器（S3：暴力破解防護）。

Dependency-free 滑動視窗限流，同時追蹤兩個維度：

1. per-(client_ip, email)：針對「單一帳號」的密碼暴力破解（門檻 MAX_FAILURES）。
2. per-client_ip：針對「單一來源掃大量帳號」的撞庫攻擊（門檻 MAX_IP_FAILURES，
   較高）。單 IP 對大量不同 email 各失敗數次、每個 email 均未達 per-email 門檻時，
   per-(ip,email) 維度永遠不會觸發；per-IP 總計數可補上這個破口。

任一維度在 5 分鐘視窗內達其門檻，即擋下後續嘗試（回 429）。登入成功清除該
(ip, email) 計數；per-IP 計數不主動清除，交由滑動視窗自然衰減（成功登入不代表
該 IP 上其他帳號的掃描嘗試就此結束，故保守保留）。

記憶體有界（DoS 防護）：兩個計數字典皆設 MAX_TRACKED_KEYS 上限。新增 key 前若
達上限，先全域移除所有「視窗外過期」的 key；若仍達上限，移除最舊一批（最舊
10%）。一次性隨機 email／IP 因此不會使記憶體無界成長。

限制：狀態存於單一行程記憶體（per-process）。多副本部署時每副本各自計數；若需
跨副本一致的限流，應改用 Redis 之類的共享後端（屬後續工作）。

執行緒安全（threading.Lock），與 services/locks.py 的 LockManager 一致風格。測試
之間可用 reset()／clear() 清空狀態，避免污染其他測試。
"""
import threading
import time

WINDOW_SECONDS = 5 * 60  # 5 分鐘滑動視窗
MAX_FAILURES = 10  # 單一 (ip, email) 視窗內失敗達此數即擋下後續嘗試
MAX_IP_FAILURES = 50  # 單一 ip 視窗內「總」失敗達此數即擋下（撞庫防護，門檻較高）
MAX_TRACKED_KEYS = 10000  # 每個計數字典的 key 數上限（記憶體有界）
_EVICT_FRACTION = 0.10  # 達上限且無過期 key 可清時，移除最舊的這個比例


def _norm_key(ip: str, email: str) -> tuple:
    return (ip or "unknown", (email or "").strip().lower())


def _norm_ip(ip: str) -> str:
    return ip or "unknown"


def _prune_store(store: dict, key, now: float) -> list:
    """移除 store[key] 中視窗外的時間戳，回傳(並回存)仍在視窗內的清單。

    需由呼叫端持有鎖。key 不存在或清空後即從 store 移除。
    """
    window = store.get(key)
    if not window:
        return []
    cutoff = now - WINDOW_SECONDS
    fresh = [t for t in window if t >= cutoff]
    if fresh:
        store[key] = fresh
    else:
        store.pop(key, None)
    return fresh


def _evict_if_needed(store: dict, now: float) -> None:
    """確保 store 加入新 key 前有空間，維持有界。需由呼叫端持有鎖。

    先移除所有視窗外過期的 key；若仍達上限，移除最舊一批（依各 key 最近一次
    時間戳排序，最舊的 _EVICT_FRACTION 比例）。
    """
    if len(store) < MAX_TRACKED_KEYS:
        return
    cutoff = now - WINDOW_SECONDS
    # 1) 全域移除過期 key。
    expired = [k for k, ts in store.items() if not ts or max(ts) < cutoff]
    for k in expired:
        store.pop(k, None)
    if len(store) < MAX_TRACKED_KEYS:
        return
    # 2) 仍滿：移除最舊一批（依最近一次失敗時間排序）。
    n_evict = max(1, int(len(store) * _EVICT_FRACTION))
    oldest = sorted(store.items(), key=lambda kv: max(kv[1]))[:n_evict]
    for k, _ in oldest:
        store.pop(k, None)


class LoginRateLimiter:
    def __init__(self):
        # (ip, email) -> 失敗時間戳(monotonic)清單
        self._fails: dict[tuple, list[float]] = {}
        # ip -> 失敗時間戳(monotonic)清單（跨該 IP 所有 email 累計）
        self._ip_fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_blocked(self, ip: str, email: str) -> bool:
        key = _norm_key(ip, email)
        ip_key = _norm_ip(ip)
        with self._lock:
            now = time.monotonic()
            per_key = _prune_store(self._fails, key, now)
            per_ip = _prune_store(self._ip_fails, ip_key, now)
            return len(per_key) >= MAX_FAILURES or len(per_ip) >= MAX_IP_FAILURES

    def record_failure(self, ip: str, email: str) -> int:
        """記錄一次失敗（同時累計 per-(ip,email) 與 per-ip），回傳該 (ip,email)
        視窗內失敗次數。"""
        key = _norm_key(ip, email)
        ip_key = _norm_ip(ip)
        with self._lock:
            now = time.monotonic()
            per_key = _prune_store(self._fails, key, now)
            per_ip = _prune_store(self._ip_fails, ip_key, now)
            # 新增 key 前確保字典有界。
            if key not in self._fails:
                _evict_if_needed(self._fails, now)
            if ip_key not in self._ip_fails:
                _evict_if_needed(self._ip_fails, now)
            per_key.append(now)
            self._fails[key] = per_key
            per_ip.append(now)
            self._ip_fails[ip_key] = per_ip
            return len(per_key)

    def reset(self, ip: str, email: str) -> None:
        """清除單一 (ip, email) 的失敗計數（登入成功時呼叫）。

        per-ip 計數不在此清除：成功登入單一帳號，不代表該來源對其他帳號的掃描
        已停止，故保守保留、交由滑動視窗自然衰減。
        """
        key = _norm_key(ip, email)
        with self._lock:
            self._fails.pop(key, None)

    def clear(self) -> None:
        """清空全部狀態（測試隔離用）：per-(ip,email) 與 per-ip 皆清。"""
        with self._lock:
            self._fails.clear()
            self._ip_fails.clear()


# 行程級單例。
login_rate_limiter = LoginRateLimiter()
