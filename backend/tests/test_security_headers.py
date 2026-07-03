"""S5：安全回應標頭整合測試。

驗證回應含 X-Content-Type-Options / X-Frame-Options / Referrer-Policy 三個標頭。
"""
import pytest

EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
}


@pytest.mark.parametrize("path", ["/api/health"])
def test_security_headers_present(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    for header, value in EXPECTED.items():
        assert r.headers.get(header) == value, f"missing/incorrect {header}"


def test_security_headers_on_error_response(client):
    # 標頭應套用於所有回應，包含非 2xx。
    r = client.get("/api/auth/me")  # 未帶 token → 401
    assert r.status_code == 401
    for header, value in EXPECTED.items():
        assert r.headers.get(header) == value
