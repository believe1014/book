"""S3：登入暴力破解限流整合測試。

門檻：同一 (client_ip, email) 5 分鐘視窗內失敗達 MAX_FAILURES 次後，後續嘗試回
429。成功登入清除計數；不同 email 各自獨立。conftest 的 autouse fixture 會在每個
測試前後清空限流狀態。
"""
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
