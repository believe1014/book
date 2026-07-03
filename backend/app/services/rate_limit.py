"""行程內登入失敗限流器（S3：暴力破解防護）。

Dependency-free 滑動視窗限流，keyed by (client_ip, email)。5 分鐘視窗內失敗次數
達上限即擋下後續嘗試（回 429）；登入成功清除該 key 計數。

限制：狀態存於單一行程記憶體（per-process）。多副本部署時每副本各自計數；若需
跨副本一致的限流，應改用 Redis 之類的共享後端（屬後續工作）。

執行緒安全（threading.Lock），與 services/locks.py 的 LockManager 一致風格。測試
之間可用 reset()／clear() 清空狀態，避免污染其他測試。
"""
import threading
import time

WINDOW_SECONDS = 5 * 60  # 5 分鐘滑動視窗
MAX_FAILURES = 10  # 視窗內失敗達此數即擋下後續嘗試


def _norm_key(ip: str, email: str) -> tuple:
    return (ip or "unknown", (email or "").strip().lower())


class LoginRateLimiter:
    def __init__(self):
        # key -> 失敗時間戳(monotonic)清單
        self._fails: dict[tuple, list[float]] = {}
        self._lock = threading.Lock()

    def _prune_locked(self, key: tuple, now: float) -> list:
        """移除視窗外的時間戳，回傳(並回存)仍在視窗內的清單。需持有 self._lock。"""
        window = self._fails.get(key)
        if not window:
            return []
        cutoff = now - WINDOW_SECONDS
        fresh = [t for t in window if t >= cutoff]
        if fresh:
            self._fails[key] = fresh
        else:
            self._fails.pop(key, None)
        return fresh

    def is_blocked(self, ip: str, email: str) -> bool:
        key = _norm_key(ip, email)
        with self._lock:
            fresh = self._prune_locked(key, time.monotonic())
            return len(fresh) >= MAX_FAILURES

    def record_failure(self, ip: str, email: str) -> int:
        """記錄一次失敗，回傳目前視窗內失敗次數。"""
        key = _norm_key(ip, email)
        with self._lock:
            now = time.monotonic()
            fresh = self._prune_locked(key, now)
            fresh.append(now)
            self._fails[key] = fresh
            return len(fresh)

    def reset(self, ip: str, email: str) -> None:
        """清除單一 (ip, email) 的失敗計數（登入成功時呼叫）。"""
        key = _norm_key(ip, email)
        with self._lock:
            self._fails.pop(key, None)

    def clear(self) -> None:
        """清空全部狀態（測試隔離用）。"""
        with self._lock:
            self._fails.clear()


# 行程級單例。
login_rate_limiter = LoginRateLimiter()
