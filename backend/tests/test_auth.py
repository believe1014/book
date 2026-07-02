"""Auth 路由整合測試（register / login / me）。"""


def test_register_and_me(client, auth):
    r = client.get("/api/auth/me", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["user"]["email"] == "a@test.com"


def test_register_duplicate_email_conflicts(client, user_factory):
    user_factory(email="dup@test.com")
    r = client.post(
        "/api/auth/register",
        json={"email": "dup@test.com", "password": "pw-123456", "name": "dup"},
    )
    assert r.status_code == 409, r.text


def test_login_success(client, user_factory):
    user_factory(email="log@test.com", password="pw-123456")
    r = client.post(
        "/api/auth/login", json={"email": "log@test.com", "password": "pw-123456"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["token"]


def test_login_wrong_password_unauthorized(client, user_factory):
    user_factory(email="log2@test.com", password="pw-123456")
    r = client.post(
        "/api/auth/login", json={"email": "log2@test.com", "password": "wrong-pw"}
    )
    assert r.status_code == 401, r.text


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_bad_token(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer garbage.token"})
    assert r.status_code == 401
