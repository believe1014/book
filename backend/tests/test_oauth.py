"""MCP OAuth(spec docs/superpowers/specs/2026-09-30-mcp-oauth-design.md)。"""
import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.database import engine
from app.models import OAuthClientRow, OAuthTokenRow


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
