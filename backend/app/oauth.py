"""MCP OAuth 2.1 授權伺服器:SDK provider 的 DB 實作 + 同意頁用的 req 簽章/授權碼。

spec: docs/superpowers/specs/2026-09-30-mcp-oauth-design.md(§4-§10)。
SDK(mcp==1.27.2)負責協定驗證(PKCE S256、redirect_uri 精確比對、code 效期);
這裡只負責存取 DB 與 kkbook 自己的規則(DCR redirect 限制、單次 code、原地輪替)。
ponytail: provider 方法是 async 但內部用同步 Session(SQLite/PG 單筆查詢,毫秒級);
流量變大再改 run_in_threadpool。
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from jose import JWTError, jwt
from pydantic import AnyHttpUrl, AnyUrl
from sqlalchemy import delete, update
from sqlmodel import Session, select
from starlette.routing import Route

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .config import settings
from .database import engine
from .deps import OAUTH_ACCESS_PREFIX, OAUTH_REFRESH_PREFIX, hash_pat
from .models import OAuthClientRow, OAuthCodeRow, OAuthTokenRow, utcnow

CODE_TTL = timedelta(minutes=10)
REQ_TTL = timedelta(minutes=10)
REFRESH_TTL = timedelta(days=90)
REQ_TYP = "oauth_req"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def redirect_uri_allowed(uri: str) -> bool:
    """https 任意主機,或 http 僅限 localhost / 127.0.0.1(用 hostname 判斷,擋 userinfo 繞過)。"""
    u = urlparse(uri)
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1"))


def client_by_id(session: Session, client_id: str) -> Optional[OAuthClientInformationFull]:
    row = session.get(OAuthClientRow, client_id)
    return OAuthClientInformationFull.model_validate_json(row.client_info) if row else None


def sign_req(client_id: str, params: AuthorizationParams) -> str:
    """把 /authorize 已驗過的參數簽成 10 分鐘的 req(不含 sub,不能當登入 JWT 用)。"""
    return jwt.encode(
        {
            "typ": REQ_TYP,
            "cid": client_id,
            "ru": str(params.redirect_uri),
            "rue": params.redirect_uri_provided_explicitly,
            "cc": params.code_challenge,
            "st": params.state,
            "sc": params.scopes or [],
            "exp": _now() + REQ_TTL,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_req(req: str) -> Optional[dict]:
    try:
        p = jwt.decode(req, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    return p if p.get("typ") == REQ_TYP else None


def issue_code(session: Session, req: dict, user_id: int) -> str:
    code = secrets.token_urlsafe(32)
    session.add(OAuthCodeRow(
        code_hash=hash_pat(code),
        client_id=req["cid"],
        user_id=user_id,
        redirect_uri=req["ru"],
        redirect_uri_provided_explicitly=req["rue"],
        code_challenge=req["cc"],
        scopes=" ".join(req["sc"]),
        expires_at=(_now() + CODE_TTL).isoformat(),
    ))
    session.commit()
    return code


def _new_tokens() -> tuple[dict, OAuthToken]:
    """產生一組 access/refresh;回傳 (要寫進 oauth_tokens 的欄位, 給客戶端的回應)。"""
    access = OAUTH_ACCESS_PREFIX + secrets.token_urlsafe(32)
    refresh = OAUTH_REFRESH_PREFIX + secrets.token_urlsafe(32)
    now, days = _now(), settings.oauth_access_ttl_days
    cols = {
        "token_hash": hash_pat(access),
        "refresh_hash": hash_pat(refresh),
        "expires_at": (now + timedelta(days=days)).isoformat(),
        "refresh_expires_at": (now + REFRESH_TTL).isoformat(),
    }
    return cols, OAuthToken(access_token=access, expires_in=days * 86400, refresh_token=refresh)


class KkbookOAuthProvider:
    """實作 mcp.server.auth.provider.OAuthAuthorizationServerProvider(Protocol)。"""

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        with Session(engine) as s:
            return client_by_id(s, client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in client_info.redirect_uris or []]
        if not uris or not all(redirect_uri_allowed(u) for u in uris):
            raise RegistrationError("invalid_redirect_uri", "redirect_uris 只接受 https 或 http://localhost|127.0.0.1")
        if client_info.client_name and len(client_info.client_name) > 100:
            raise RegistrationError("invalid_client_metadata", "client_name 最多 100 字")
        with Session(engine) as s:
            s.add(OAuthClientRow(client_id=client_info.client_id, client_info=client_info.model_dump_json()))
            s.commit()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        base = settings.public_base_url.rstrip("/")
        return f"{base}/oauth/consent?req={sign_req(client.client_id, params)}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        with Session(engine) as s:
            row = s.get(OAuthCodeRow, hash_pat(authorization_code))
        if row is None or row.client_id != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=row.scopes.split(),
            expires_at=_ts(row.expires_at),
            client_id=row.client_id,
            code_challenge=row.code_challenge,
            redirect_uri=AnyUrl(row.redirect_uri),
            redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
            subject=str(row.user_id),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        cols, tokens = _new_tokens()
        with Session(engine) as s:
            gone = s.execute(
                delete(OAuthCodeRow).where(OAuthCodeRow.code_hash == hash_pat(authorization_code.code))
            ).rowcount
            if gone != 1:  # 併發重放:另一個請求已先用掉這個 code
                raise TokenError("invalid_grant", "authorization code already used")
            s.add(OAuthTokenRow(client_id=client.client_id, user_id=int(authorization_code.subject), **cols))
            s.commit()
        return tokens

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        if not refresh_token.startswith(OAUTH_REFRESH_PREFIX):
            return None
        with Session(engine) as s:
            row = s.exec(select(OAuthTokenRow).where(OAuthTokenRow.refresh_hash == hash_pat(refresh_token))).first()
        if row is None or row.revoked_at is not None or row.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token, client_id=row.client_id, scopes=[],
            expires_at=int(_ts(row.refresh_expires_at)), subject=str(row.user_id),
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        cols, tokens = _new_tokens()
        with Session(engine) as s:
            n = s.execute(
                update(OAuthTokenRow)
                .where(OAuthTokenRow.refresh_hash == hash_pat(refresh_token.token), OAuthTokenRow.revoked_at.is_(None))
                .values(**cols)
            ).rowcount
            s.commit()
        if n != 1:  # 併發:同一 refresh 已被另一請求輪替掉
            raise TokenError("invalid_grant", "refresh token already used")
        return tokens

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        """只給 /revoke 用;/mcp 的驗證走 mcp_server.KkbookTokenVerifier → deps.user_from_token。"""
        if not token.startswith(OAUTH_ACCESS_PREFIX):
            return None
        with Session(engine) as s:
            row = s.exec(select(OAuthTokenRow).where(OAuthTokenRow.token_hash == hash_pat(token))).first()
        if row is None or row.revoked_at is not None:
            return None
        return AccessToken(
            token=token, client_id=row.client_id, scopes=[],
            expires_at=int(_ts(row.expires_at)), subject=str(row.user_id),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        col = OAuthTokenRow.token_hash if isinstance(token, AccessToken) else OAuthTokenRow.refresh_hash
        with Session(engine) as s:
            s.execute(
                update(OAuthTokenRow)
                .where(col == hash_pat(token.token), OAuthTokenRow.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            s.commit()


provider = KkbookOAuthProvider()


def auth_routes() -> list[Route]:
    """掛在主 app 根層(SPA catch-all 之前)的 AS 路由 + PRM(RFC 8414 / 9728 discovery)。"""
    base = settings.public_base_url.rstrip("/")
    prm = create_protected_resource_routes(AnyHttpUrl(f"{base}/mcp/"), [AnyHttpUrl(base)])
    return [
        *create_auth_routes(
            provider,
            issuer_url=AnyHttpUrl(base),
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        ),
        *prm,  # /.well-known/oauth-protected-resource/mcp/
        # 只查根層的客戶端:同一份 metadata 再掛一條裸路徑
        Route("/.well-known/oauth-protected-resource", endpoint=prm[0].endpoint, methods=["GET", "OPTIONS"]),
    ]
