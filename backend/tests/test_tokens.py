"""Personal access token (PAT): 建立/列出/撤銷、REST 與 MCP 認證、撤銷/過期、PAT 不可管 PAT。"""
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.database import engine
from app.deps import hash_pat
from app.models import PersonalAccessToken

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _create(client, auth, **body):
    r = client.post("/api/tokens", headers=auth["headers"], json={"name": "mcp", **body})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def _mcp_list_books(client, tok):
    r = client.post("/mcp/", headers={**MCP_HEADERS, **_bearer(tok)}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "list_books", "arguments": {}},
    })
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_create_list_revoke_and_db_stores_hash_only(client, auth):
    d = _create(client, auth)
    tok = d["token"]
    assert tok.startswith("kkb_") and len(tok) > 40
    assert d["prefix"] == tok[:8] and d["expires_at"]

    r = client.get("/api/tokens", headers=auth["headers"])
    rows = r.json()["data"]
    assert [x["id"] for x in rows] == [d["id"]] and "token" not in rows[0]

    with Session(engine) as s:
        pat = s.exec(select(PersonalAccessToken)).one()
        assert pat.token_hash == hash_pat(tok) and tok not in (pat.token_hash, pat.prefix)

    # REST auth via PAT, updates last_used_at
    assert client.get("/api/auth/me", headers=_bearer(tok)).status_code == 200
    assert client.get("/api/tokens", headers=auth["headers"]).json()["data"][0]["last_used_at"]

    r = client.delete(f"/api/tokens/{d['id']}", headers=auth["headers"])
    assert r.status_code == 200 and r.json()["data"]["revoked_at"]
    assert client.get("/api/auth/me", headers=_bearer(tok)).status_code == 401
    assert _mcp_list_books(client, tok)["isError"] is True


def test_mcp_accepts_pat(client, auth):
    client.post("/api/books", headers=auth["headers"], json={"title": "書"})
    tok = _create(client, auth)["token"]
    res = _mcp_list_books(client, tok)
    assert res.get("isError") is not True, res
    assert "書" in res["content"][0]["text"]


def test_expired_pat_rejected(client, auth):
    d = _create(client, auth, expires_in_days=1)
    with Session(engine) as s:
        pat = s.get(PersonalAccessToken, d["id"])
        pat.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        s.add(pat)
        s.commit()
    assert client.get("/api/auth/me", headers=_bearer(d["token"])).status_code == 401
    assert _mcp_list_books(client, d["token"])["isError"] is True


def test_pat_cannot_manage_pats(client, auth):
    d = _create(client, auth)
    h = _bearer(d["token"])
    assert client.post("/api/tokens", headers=h, json={"name": "x"}).status_code == 403
    assert client.get("/api/tokens", headers=h).status_code == 403
    assert client.delete(f"/api/tokens/{d['id']}", headers=h).status_code == 403


def test_auth_required_and_own_tokens_only(client, auth, user_factory):
    assert client.get("/api/tokens").status_code == 401
    assert client.post("/api/tokens", headers=_bearer("kkb_fake"), json={"name": "x"}).status_code == 403
    assert client.get("/api/auth/me", headers=_bearer("kkb_fake")).status_code == 401
    d = _create(client, auth)
    other = user_factory(email="b@test.com")
    assert client.get("/api/tokens", headers=other["headers"]).json()["data"] == []
    assert client.delete(f"/api/tokens/{d['id']}", headers=other["headers"]).status_code == 404
