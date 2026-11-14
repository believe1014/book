"""Authentication helpers: password hashing (bcrypt) and JWT (python-jose)."""
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    # bcrypt has a 72-byte limit; truncate defensively to avoid backend errors.
    return pwd_context.hash(password[:72])


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password[:72], password_hash)
    except ValueError:
        return False


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[int]:
    """Return the user id encoded in the token, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (JWTError, ValueError):
        return None


def sso_identity(request) -> Optional[dict]:
    """nginx 帶來的 believe 身分(account spec §10)。只有 X-Believe-Auth 等於
    BELIEVE_SSO_SECRET(constant-time)才信任;SSO 關閉、密鑰不符或未登入回 None。"""
    secret = settings.believe_sso_secret
    if not secret:
        return None
    given = request.headers.get("x-believe-auth", "")
    if not hmac.compare_digest(given.encode(), secret.encode()):
        return None
    email = request.headers.get("x-believe-user-email", "").strip().lower()
    if not email:
        return None
    return {
        "email": email,
        "name": unquote(request.headers.get("x-believe-user-name", "")).strip(),
        "email_verified": request.headers.get("x-believe-email-verified") == "1",
    }


def unusable_password_hash() -> str:
    """SSO 建立的本地帳號沒有本地密碼(spec §9-4):非 bcrypt 格式,verify 永遠 False。"""
    return "!sso!" + secrets.token_hex(16)
