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


def _user(email):
    from sqlmodel import Session, select
    from app.database import engine
    from app.models import User
    with Session(engine) as s:
        return s.exec(select(User).where(User.email == email)).first()


def _seed(email, name="m"):
    from sqlmodel import Session
    from app.auth import hash_password
    from app.database import engine
    from app.models import User
    with Session(engine) as s:
        u = User(email=email, password_hash=hash_password("pw-123456"), name=name)
        s.add(u)
        s.commit()
        return u.id


def _sso(client, email, uid, name=""):
    h = _hdr(email, name=name)
    h["X-Believe-User-Id"] = uid
    return client.post("/api/auth/sso", headers=h)


def test_uid_bound_by_email_then_email_follows(client, sso_on):
    uid = _seed("a@test.com")
    r = _sso(client, "a@test.com", "U1")
    assert r.json()["data"]["user"]["id"] == uid and _user("a@test.com").believe_uid == "U1"
    r = _sso(client, "b@test.com", "U1")  # 中央改 email
    assert r.status_code == 200 and r.json()["data"]["user"] == {**r.json()["data"]["user"], "id": uid, "email": "b@test.com"}
    assert _user("a@test.com") is None


def test_email_follow_conflict_keeps_old(client, sso_on):
    uid = _seed("a@test.com")
    _sso(client, "a@test.com", "U1")
    _seed("taken@test.com")
    r = _sso(client, "taken@test.com", "U1")
    assert r.status_code == 200 and r.json()["data"]["user"]["id"] == uid
    assert r.json()["data"]["user"]["email"] == "a@test.com"


def test_email_bound_to_other_uid_refused(client, sso_on):
    _sso(client, "a@test.com", "U1")
    assert _sso(client, "a@test.com", "U2").status_code == 409


def test_new_user_gets_uid_and_name_follows(client, sso_on):
    _sso(client, "n@test.com", "U9")
    assert _user("n@test.com").believe_uid == "U9" and _user("n@test.com").name == "n"
    assert _sso(client, "n@test.com", "U9", name="%E7%8E%8B").json()["data"]["user"]["name"] == "王"
    # 中央名稱空 → 不清掉本地名稱
    assert _sso(client, "n@test.com", "U9").json()["data"]["user"]["name"] == "王"


def test_init_db_adds_believe_uid_to_old_db(tmp_path):
    import sqlalchemy as sa
    from app import database
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR UNIQUE, "
                          "password_hash VARCHAR, name VARCHAR, avatar_url VARCHAR, created_at VARCHAR)"))
        c.execute(sa.text("CREATE TABLE comments (id INTEGER PRIMARY KEY)"))
    orig = database.engine
    database.engine = eng
    try:
        database.init_db()
        database.init_db()  # 冪等
    finally:
        database.engine = orig
    with eng.begin() as c:
        cols = {r[1] for r in c.execute(sa.text("PRAGMA table_info(users)"))}
        assert {"believe_uid"} <= cols and "quote" in {r[1] for r in c.execute(sa.text("PRAGMA table_info(comments)"))}
        c.execute(sa.text("INSERT INTO users(email,password_hash,name,created_at,believe_uid) VALUES('a','x','a','x','U')"))
        with pytest.raises(sa.exc.IntegrityError):
            c.execute(sa.text("INSERT INTO users(email,password_hash,name,created_at,believe_uid) VALUES('b','x','b','x','U')"))
