"""believe 中央登入(SSO):POST /api/auth/sso 與身分 header 信任規則(account spec §9/§10)。"""
import pytest

from app.config import settings

SECRET = "sso-test-secret"


@pytest.fixture
def sso_on(monkeypatch):
    monkeypatch.setattr(settings, "believe_sso_secret", SECRET)


def _hdr(email, name="", verified="1", auth=SECRET):
    h = {"X-Believe-User-Email": email, "X-Believe-User-Name": name,
         "X-Believe-Email-Verified": verified}
    if auth is not None:
        h["X-Believe-Auth"] = auth
    return h


def test_sso_disabled_returns_404(client):
    r = client.post("/api/auth/sso", headers=_hdr("x@test.com"))
    assert r.status_code == 404
    assert client.get("/api/auth/config").json()["data"]["sso"] is False


def test_wrong_or_missing_secret_ignores_headers(client, sso_on):
    assert client.post("/api/auth/sso", headers=_hdr("x@test.com", auth="nope")).status_code == 401
    assert client.post("/api/auth/sso", headers=_hdr("x@test.com", auth=None)).status_code == 401


def test_no_email_401(client, sso_on):
    assert client.post("/api/auth/sso", headers=_hdr("")).status_code == 401


def test_creates_new_user_and_jwt_works(client, sso_on):
    r = client.post("/api/auth/sso", headers=_hdr("New@Test.com", name="%E7%8E%8B%E5%B0%8F%E6%98%8E"))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["user"]["email"] == "new@test.com"
    assert d["user"]["name"] == "王小明"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {d['token']}"})
    assert me.json()["data"]["user"]["email"] == "new@test.com"
    # 沒有本地密碼:任何密碼都登不進
    assert client.post("/api/auth/login", json={"email": "new@test.com", "password": ""}).status_code == 401
    # name 缺 → 用 email 本地部分
    r2 = client.post("/api/auth/sso", headers=_hdr("bob@test.com"))
    assert r2.json()["data"]["user"]["name"] == "bob"
    assert client.get("/api/auth/config").json()["data"] == {
        "sso": True, "account_url": settings.believe_account_url}


def test_case_insensitive_matches_existing_user(client, sso_on):
    # 舊資料可能存成大小寫混合:直接寫 DB 模擬
    from sqlmodel import Session
    from app.auth import hash_password
    from app.database import engine
    from app.models import User
    with Session(engine) as s:
        s.add(User(email="Mixed@Test.com", password_hash=hash_password("pw-123456"), name="m"))
        s.commit()
    r = client.post("/api/auth/sso", headers=_hdr("mixed@test.com"))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["user"]["email"] == "Mixed@Test.com"
    # 本地帳密登入也不分大小寫
    assert client.post("/api/auth/login", json={"email": "MIXED@test.com", "password": "pw-123456"}).status_code == 200


def test_register_stores_lowercase_and_rejects_case_dup(client):
    r = client.post("/api/auth/register", json={"email": "Up@Test.com", "password": "pw-123456", "name": "u"})
    assert r.json()["data"]["user"]["email"] == "up@test.com"
    r = client.post("/api/auth/register", json={"email": "UP@test.com", "password": "pw-123456", "name": "u"})
    assert r.status_code == 409


def _invite(client, auth, email):
    book = client.post("/api/books", headers=auth["headers"], json={"title": "B"}).json()["data"]
    book_id = (book.get("book") or book)["id"]
    r = client.post(f"/api/books/{book_id}/members", headers=auth["headers"],
                    json={"email": email, "role": "editor"})
    assert r.status_code == 200, r.text
    return book_id


def test_unverified_email_does_not_auto_accept_invites(client, auth, sso_on):
    book_id = _invite(client, auth, "Inv@Test.com")
    r = client.post("/api/auth/sso", headers=_hdr("inv@test.com", verified="0"))
    h = {"Authorization": f"Bearer {r.json()['data']['token']}"}
    assert client.get(f"/api/books/{book_id}", headers=h).status_code in (403, 404)
    # 驗證過的再進來 → 自動接受
    client.post("/api/auth/sso", headers=_hdr("inv@test.com", verified="1"))
    assert client.get(f"/api/books/{book_id}", headers=h).status_code == 200


def test_pat_still_works_with_sso_on(client, auth, sso_on):
    tok = client.post("/api/tokens", headers=auth["headers"], json={"name": "mcp"}).json()["data"]["token"]
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["data"]["user"]["email"] == "a@test.com"
