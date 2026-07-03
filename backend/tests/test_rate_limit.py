"""S3：登入暴力破解限流整合測試。

門檻：同一 (client_ip, email) 5 分鐘視窗內失敗達 MAX_FAILURES 次後，後續嘗試回
429。成功登入清除計數；不同 email 各自獨立。conftest 的 autouse fixture 會在每個
測試前後清空限流狀態。
"""
from app.services import rate_limit as rl
from app.services.rate_limit import MAX_FAILURES, login_rate_limiter


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_repeated_failures_hit_429(client, user_factory):
    user_factory(email="brute@test.com", password="pw-123456")
    # 前 MAX_FAILURES 次錯誤密碼 → 401（每次記一筆失敗）。
    for _ in range(MAX_FAILURES):
        r = _login(client, "brute@test.com", "wrong-pw")
        assert r.status_code == 401, r.text
    # 下一次即被限流 → 429。
    r = _login(client, "brute@test.com", "wrong-pw")
    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "TOO_MANY_REQUESTS"


def test_successful_login_resets_counter(client, user_factory):
    user_factory(email="reset@test.com", password="pw-123456")
    # 幾次失敗（未達上限）。
    for _ in range(MAX_FAILURES - 1):
        assert _login(client, "reset@test.com", "wrong-pw").status_code == 401
    # 成功登入清除計數。
    assert _login(client, "reset@test.com", "pw-123456").status_code == 200
    # 重置後可再度失敗多次而不立即被擋。
    for _ in range(MAX_FAILURES):
        assert _login(client, "reset@test.com", "wrong-pw").status_code == 401


def test_different_emails_counted_independently(client, user_factory):
    user_factory(email="one@test.com", password="pw-123456")
    user_factory(email="two@test.com", password="pw-123456")
    # 把 one@ 打到被限流。
    for _ in range(MAX_FAILURES):
        assert _login(client, "one@test.com", "wrong-pw").status_code == 401
    assert _login(client, "one@test.com", "wrong-pw").status_code == 429
    # two@ 不受影響，仍可正常（錯誤密碼 → 401，而非 429）。
    assert _login(client, "two@test.com", "wrong-pw").status_code == 401
    # two@ 用正確密碼仍可登入成功。
    assert _login(client, "two@test.com", "pw-123456").status_code == 200


def test_limiter_reset_helper_clears_single_key():
    login_rate_limiter.clear()
    ip = "1.2.3.4"
    for _ in range(MAX_FAILURES):
        login_rate_limiter.record_failure(ip, "x@test.com")
    assert login_rate_limiter.is_blocked(ip, "x@test.com")
    login_rate_limiter.reset(ip, "x@test.com")
    assert not login_rate_limiter.is_blocked(ip, "x@test.com")
    login_rate_limiter.clear()


# ── 修復 2：per-IP 撞庫防護（單 IP 掃大量帳號）─────────────────────────────────
def test_per_ip_blocks_across_many_emails(monkeypatch):
    """單 IP 對多個不同 email 各失敗一次、皆未達 per-email 門檻，累計達 per-IP
    門檻即擋下該 IP 的後續嘗試（含全新 email）。"""
    login_rate_limiter.clear()
    monkeypatch.setattr(rl, "MAX_IP_FAILURES", 5)
    ip = "9.9.9.9"
    for i in range(5):
        # 每個 email 僅一次失敗，遠低於 per-email 門檻（MAX_FAILURES=10）。
        assert not login_rate_limiter.is_blocked(ip, f"u{i}@test.com")
        login_rate_limiter.record_failure(ip, f"u{i}@test.com")
    # per-IP 累計達 5 → 同 IP 的全新 email 也被擋。
    assert login_rate_limiter.is_blocked(ip, "brand-new@test.com")
    login_rate_limiter.clear()


def test_per_ip_counter_independent_across_ips(monkeypatch):
    """不同 IP 的 per-IP 計數各自獨立。"""
    login_rate_limiter.clear()
    monkeypatch.setattr(rl, "MAX_IP_FAILURES", 5)
    hot_ip = "9.9.9.9"
    for i in range(5):
        login_rate_limiter.record_failure(hot_ip, f"u{i}@test.com")
    assert login_rate_limiter.is_blocked(hot_ip, "x@test.com")
    # 另一個 IP 完全不受影響。
    assert not login_rate_limiter.is_blocked("8.8.8.8", "x@test.com")
    login_rate_limiter.clear()


def test_per_ip_integration_returns_429(client, monkeypatch):
    """整合層：TestClient 單一來源 IP 對多個不同 email 失敗累計 → 429。"""
    monkeypatch.setattr(rl, "MAX_IP_FAILURES", 4)
    for i in range(4):
        r = _login(client, f"scan{i}@test.com", "wrong-pw")
        assert r.status_code == 401, r.text
    # 同來源 IP 的下一次嘗試（即使是全新 email）被 per-IP 擋下。
    r = _login(client, "scan-fresh@test.com", "wrong-pw")
    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "TOO_MANY_REQUESTS"


# ── 修復 1：追蹤 key 數有界（DoS 防護）────────────────────────────────────────
def test_tracked_keys_are_bounded(monkeypatch):
    """塞入遠超上限的不同 key（一次性隨機 email／IP）後，兩個計數字典皆有界。"""
    login_rate_limiter.clear()
    monkeypatch.setattr(rl, "MAX_TRACKED_KEYS", 20)
    for i in range(500):
        login_rate_limiter.record_failure(f"10.0.{i // 256}.{i % 256}", f"rand{i}@test.com")
    assert len(login_rate_limiter._fails) <= 20
    assert len(login_rate_limiter._ip_fails) <= 20
    login_rate_limiter.clear()
