"""MCP OAuth(spec docs/superpowers/specs/2026-09-30-mcp-oauth-design.md)。"""
import asyncio
import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from jose import jwt
from pydantic import AnyUrl
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from mcp.server.auth.provider import AuthorizationParams, RegistrationError, TokenError
from mcp.shared.auth import OAuthClientInformationFull

from app.config import settings
from app.database import engine
from app.deps import hash_pat, user_from_token
from app.models import OAuthClientRow, OAuthCodeRow, OAuthTokenRow
from app.oauth import decode_req, issue_code, provider
from app.services.rate_limit import SlidingWindowLimiter

REDIRECT = "http://localhost:33418/callback"


def run(coro):
    return asyncio.run(coro)


def _client_info(redirect=REDIRECT, name="Claude Code"):
    return OAuthClientInformationFull(
        client_id=secrets.token_hex(8), redirect_uris=[AnyUrl(redirect)], client_name=name,
        token_endpoint_auth_method="none",
    )


def _params(challenge="c" * 43, redirect=REDIRECT, state="st-1"):
    return AuthorizationParams(
        state=state, scopes=[], code_challenge=challenge,
        redirect_uri=AnyUrl(redirect), redirect_uri_provided_explicitly=True,
    )


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


# ---------- T1:資料表 ----------
def test_oauth_tables_exist_and_hashes_unique(client, auth):
    assert {"oauth_clients", "oauth_codes", "oauth_tokens"} <= set(inspect(engine).get_table_names())
    with Session(engine) as s:
        s.add(OAuthClientRow(client_id="c1", client_info="{}"))
        s.commit()
        row = dict(client_id="c1", user_id=auth["user"]["id"], expires_at="x", refresh_expires_at="x")
        s.add(OAuthTokenRow(token_hash="h1", refresh_hash="r1", **row))
        s.commit()
        s.add(OAuthTokenRow(token_hash="h1", refresh_hash="r2", **row))
        with pytest.raises(IntegrityError):
            s.commit()


# ---------- T2:provider ----------
@pytest.mark.parametrize("uri", [
    "http://evil.com/cb",
    "http://localhost.evil.com/cb",
    "http://localhost:80@evil.com/cb",
    "ftp://localhost/cb",
])
def test_register_rejects_bad_redirect(client, uri):
    with pytest.raises(RegistrationError) as e:
        run(provider.register_client(_client_info(redirect=uri)))
    assert e.value.error == "invalid_redirect_uri"


@pytest.mark.parametrize("uri", ["https://claude.ai/cb", "http://localhost:3334/cb", "http://127.0.0.1:9/cb"])
def test_register_accepts_https_and_loopback(client, uri):
    info = _client_info(redirect=uri)
    run(provider.register_client(info))
    assert run(provider.get_client(info.client_id)).redirect_uris == [AnyUrl(uri)]


def test_register_rejects_long_client_name(client):
    with pytest.raises(RegistrationError):
        run(provider.register_client(_client_info(name="x" * 101)))


def test_authorize_returns_consent_url_with_signed_req(client):
    info = _client_info()
    run(provider.register_client(info))
    url = run(provider.authorize(info, _params()))
    assert url.startswith(settings.public_base_url.rstrip("/") + "/oauth/consent?req=")
    req = parse_qs(urlparse(url).query)["req"][0]
    p = decode_req(req)
    assert p["cid"] == info.client_id and p["ru"] == REDIRECT and p["st"] == "st-1"
    assert decode_req(req[:-2] + "xx") is None
    with Session(engine) as s:  # req 不能當登入 token 用
        assert user_from_token(s, req) is None


def _code_for(auth, challenge="c" * 43):
    info = _client_info()
    run(provider.register_client(info))
    p = decode_req(parse_qs(urlparse(run(provider.authorize(info, _params(challenge)))).query)["req"][0])
    with Session(engine) as s:
        code = issue_code(s, p, auth["user"]["id"])
    return info, code


def test_code_single_use_and_hash_only(client, auth):
    info, code = _code_for(auth)
    with Session(engine) as s:
        assert s.exec(select(OAuthCodeRow)).one().code_hash == hash_pat(code)
    ac = run(provider.load_authorization_code(info, code))
    assert ac.subject == str(auth["user"]["id"]) and str(ac.redirect_uri) == REDIRECT
    tokens = run(provider.exchange_authorization_code(info, ac))
    assert tokens.access_token.startswith("kko_") and tokens.refresh_token.startswith("kkr_")
    assert tokens.expires_in == 30 * 86400
    assert run(provider.load_authorization_code(info, code)) is None
    with pytest.raises(TokenError):  # 併發重放:load 過但被別人先換掉
        run(provider.exchange_authorization_code(info, ac))
    with Session(engine) as s:
        row = s.exec(select(OAuthTokenRow)).one()
        assert row.token_hash == hash_pat(tokens.access_token)
        assert tokens.access_token not in (row.token_hash, row.refresh_hash)


def test_code_of_other_client_not_loadable(client, auth):
    _, code = _code_for(auth)
    other = _client_info()
    run(provider.register_client(other))
    assert run(provider.load_authorization_code(other, code)) is None


def test_access_token_resolves_user_until_expired_or_revoked(client, auth):
    info, code = _code_for(auth)
    tokens = run(provider.exchange_authorization_code(info, run(provider.load_authorization_code(info, code))))
    with Session(engine) as s:
        assert user_from_token(s, tokens.access_token).id == auth["user"]["id"]
        assert s.exec(select(OAuthTokenRow)).one().last_used_at
        assert user_from_token(s, tokens.refresh_token) is None  # refresh 不能當 bearer
        row = s.exec(select(OAuthTokenRow)).one()
        row.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        s.add(row)
        s.commit()
        assert user_from_token(s, tokens.access_token) is None


def test_refresh_rotates_in_place(client, auth):
    info, code = _code_for(auth)
    t1 = run(provider.exchange_authorization_code(info, run(provider.load_authorization_code(info, code))))
    rt = run(provider.load_refresh_token(info, t1.refresh_token))
    t2 = run(provider.exchange_refresh_token(info, rt, []))
    assert t2.access_token != t1.access_token and t2.refresh_token != t1.refresh_token
    assert run(provider.load_refresh_token(info, t1.refresh_token)) is None
    with pytest.raises(TokenError):  # 同一 refresh 被併發用第二次
        run(provider.exchange_refresh_token(info, rt, []))
    with Session(engine) as s:
        assert len(s.exec(select(OAuthTokenRow)).all()) == 1
        assert user_from_token(s, t1.access_token) is None
        assert user_from_token(s, t2.access_token).id == auth["user"]["id"]


def test_revoke_kills_access_and_refresh(client, auth):
    info, code = _code_for(auth)
    t = run(provider.exchange_authorization_code(info, run(provider.load_authorization_code(info, code))))
    run(provider.revoke_token(run(provider.load_refresh_token(info, t.refresh_token))))
    with Session(engine) as s:
        assert user_from_token(s, t.access_token) is None
    assert run(provider.load_refresh_token(info, t.refresh_token)) is None
    assert run(provider.load_access_token(t.access_token)) is None


# ---------- T3:HTTP 授權流程 ----------
def _http_register(client, redirect=REDIRECT, **extra):
    return client.post("/register", json={
        "client_name": "Claude Code", "redirect_uris": [redirect],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], **extra,
    })


def _http_authorize(client, cid, challenge, redirect=REDIRECT, state="st-1"):
    return client.get("/authorize", params={
        "response_type": "code", "client_id": cid, "redirect_uri": redirect,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    }, follow_redirects=False)


def _flow_until_code(client, auth):
    cid = _http_register(client).json()["client_id"]
    verifier, challenge = _pkce()
    r = _http_authorize(client, cid, challenge)
    assert r.status_code == 302, r.text
    loc = urlparse(r.headers["location"])
    assert loc.path == "/oauth/consent"
    req = parse_qs(loc.query)["req"][0]
    info = client.get("/api/oauth/consent", params={"req": req}, headers=auth["headers"])
    assert info.status_code == 200, info.text
    assert info.json()["data"] == {"client_name": "Claude Code", "redirect_origin": "http://localhost:33418"}
    r = client.post("/api/oauth/consent", json={"req": req, "approve": True}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    back = urlparse(r.json()["data"]["redirect_url"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    q = parse_qs(back.query)
    assert q["state"] == ["st-1"]
    return cid, verifier, q["code"][0]


def _http_token(client, cid, code, verifier, redirect=REDIRECT):
    return client.post("/token", data={
        "grant_type": "authorization_code", "client_id": cid, "code": code,
        "code_verifier": verifier, "redirect_uri": redirect,
    })


def _http_tokens(client, auth):
    cid, verifier, code = _flow_until_code(client, auth)
    r = _http_token(client, cid, code, verifier)
    assert r.status_code == 200, r.text
    return cid, r.json()


def test_metadata_endpoints(client):
    base = settings.public_base_url.rstrip("/")
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    m = r.json()
    assert m["code_challenge_methods_supported"] == ["S256"]
    for k, path in [("authorization_endpoint", "/authorize"), ("token_endpoint", "/token"),
                    ("registration_endpoint", "/register"), ("revocation_endpoint", "/revoke")]:
        assert m[k] == base + path
    for path in ("/.well-known/oauth-protected-resource/mcp/", "/.well-known/oauth-protected-resource"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.json()["resource"] == base + "/mcp/"
        assert r.json()["authorization_servers"][0].rstrip("/") == base


def test_full_flow_and_refresh_over_http(client, auth):
    cid, tok = _http_tokens(client, auth)
    assert tok["token_type"] == "Bearer" and tok["access_token"].startswith("kko_")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert me.status_code == 200 and me.json()["data"]["user"]["id"] == auth["user"]["id"]
    r = client.post("/token", data={"grant_type": "refresh_token", "client_id": cid, "refresh_token": tok["refresh_token"]})
    assert r.status_code == 200, r.text
    assert r.json()["refresh_token"] != tok["refresh_token"]
    again = client.post("/token", data={"grant_type": "refresh_token", "client_id": cid, "refresh_token": tok["refresh_token"]})
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_deny_redirects_with_access_denied(client, auth):
    cid = _http_register(client).json()["client_id"]
    req = parse_qs(urlparse(_http_authorize(client, cid, _pkce()[1]).headers["location"]).query)["req"][0]
    r = client.post("/api/oauth/consent", json={"req": req, "approve": False}, headers=auth["headers"])
    q = parse_qs(urlparse(r.json()["data"]["redirect_url"]).query)
    assert q == {"error": ["access_denied"], "state": ["st-1"]}
    with Session(engine) as s:
        assert s.exec(select(OAuthCodeRow)).first() is None


def test_token_errors(client, auth):
    cid, verifier, code = _flow_until_code(client, auth)
    bad = _http_token(client, cid, code, "wrong-verifier-" + "x" * 40)
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    mismatch = _http_token(client, cid, code, verifier, redirect="http://localhost:33418/other")
    assert mismatch.status_code == 400
    assert _http_token(client, cid, code, verifier).status_code == 200
    reuse = _http_token(client, cid, code, verifier)
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"


def test_expired_code_rejected(client, auth):
    cid, verifier, code = _flow_until_code(client, auth)
    with Session(engine) as s:
        row = s.get(OAuthCodeRow, hash_pat(code))
        row.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        s.add(row)
        s.commit()
    r = _http_token(client, cid, code, verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_authorize_bad_redirect_never_redirects(client):
    cid = _http_register(client).json()["client_id"]
    r = _http_authorize(client, cid, _pkce()[1], redirect="https://evil.com/cb")
    assert r.status_code == 400 and "location" not in r.headers
    r = _http_authorize(client, "no-such-client", _pkce()[1])
    assert r.status_code == 400 and "location" not in r.headers


def test_authorize_requires_pkce(client):
    cid = _http_register(client).json()["client_id"]
    r = client.get("/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT},
                   follow_redirects=False)
    assert r.status_code == 302  # client/redirect 合法 → SDK 把錯誤帶回 redirect_uri
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["invalid_request"] and "code" not in q


def test_register_http_rejects_evil_redirect(client):
    r = _http_register(client, redirect="http://evil.com/cb")
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"


def test_consent_rejects_tampered_or_expired_req(client, auth):
    cid = _http_register(client).json()["client_id"]
    req = parse_qs(urlparse(_http_authorize(client, cid, _pkce()[1]).headers["location"]).query)["req"][0]
    r = client.post("/api/oauth/consent", json={"req": req[:-2] + "xx", "approve": True}, headers=auth["headers"])
    assert r.status_code == 400
    p = decode_req(req)  # 同密鑰簽一張已過期的 req
    p["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    old = jwt.encode(p, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert client.post("/api/oauth/consent", json={"req": old, "approve": True}, headers=auth["headers"]).status_code == 400
    login_jwt = auth["token"]  # 登入 JWT 不是 req
    assert client.post("/api/oauth/consent", json={"req": login_jwt, "approve": True}, headers=auth["headers"]).status_code == 400


def test_consent_requires_login(client):
    assert client.post("/api/oauth/consent", json={"req": "x", "approve": True}).status_code == 401


def test_oauth_token_cannot_mint_pat_or_approve(client, auth):
    cid, tok = _http_tokens(client, auth)
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    assert client.post("/api/tokens", headers=h, json={"name": "x"}).status_code == 403
    req = parse_qs(urlparse(_http_authorize(client, cid, _pkce()[1]).headers["location"]).query)["req"][0]
    assert client.post("/api/oauth/consent", headers=h, json={"req": req, "approve": True}).status_code == 403
    rh = {"Authorization": f"Bearer {tok['refresh_token']}"}
    assert client.post("/api/tokens", headers=rh, json={"name": "x"}).status_code == 403


def test_rate_limits(client):
    for _ in range(10):
        assert _http_register(client).status_code == 201
    assert _http_register(client).status_code == 429
    for _ in range(30):
        assert client.post("/token", data={"grant_type": "refresh_token", "client_id": "x", "refresh_token": "y"}).status_code != 429
    assert client.post("/token", data={}).status_code == 429


def test_rate_limiter_keys_are_independent():
    lim = SlidingWindowLimiter()
    assert all(lim.allow(("/register", "1.1.1.1"), 2, 60) for _ in range(2))
    assert not lim.allow(("/register", "1.1.1.1"), 2, 60)
    assert lim.allow(("/register", "2.2.2.2"), 2, 60)
    assert lim.allow(("/token", "1.1.1.1"), 2, 60)


@pytest.mark.skipif(not os.path.isdir(settings.frontend_dir), reason="frontend/dist 未建置,SPA catch-all 未註冊")
def test_spa_get_routes_still_served(client):
    for path in ("/register", "/login", "/oauth/consent"):
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"], path
