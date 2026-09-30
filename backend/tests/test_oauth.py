"""MCP OAuth(spec docs/superpowers/specs/2026-09-30-mcp-oauth-design.md)。"""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
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
