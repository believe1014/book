"""Personal access tokens (PAT) for MCP/API clients.

Only manageable with a web-login JWT (get_current_user_jwt), so a leaked PAT
can't mint or extend itself. The plaintext is returned once, on creation.
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import errors
from ..database import get_session
from ..deps import PAT_PREFIX, get_current_user_jwt, hash_pat
from ..models import PersonalAccessToken, User, utcnow

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int = Field(default=365, ge=1, le=3650)


def _out(t: PersonalAccessToken) -> dict:
    return {
        "id": t.id, "name": t.name, "prefix": t.prefix,
        "created_at": t.created_at, "last_used_at": t.last_used_at,
        "expires_at": t.expires_at, "revoked_at": t.revoked_at,
    }


@router.post("", response_model=None)
def create_token(
    body: TokenCreateIn,
    user: User = Depends(get_current_user_jwt),
    session: Session = Depends(get_session),
):
    plain = PAT_PREFIX + secrets.token_urlsafe(32)
    t = PersonalAccessToken(
        user_id=user.id,
        name=body.name,
        token_hash=hash_pat(plain),
        prefix=plain[:8],
        expires_at=(datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)).isoformat(),
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return {"data": {**_out(t), "token": plain}}


@router.get("", response_model=None)
def list_tokens(
    user: User = Depends(get_current_user_jwt),
    session: Session = Depends(get_session),
):
    rows = session.exec(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == user.id)
        .order_by(PersonalAccessToken.id)
    ).all()
    return {"data": [_out(t) for t in rows]}


@router.delete("/{token_id}", response_model=None)
def revoke_token(
    token_id: int,
    user: User = Depends(get_current_user_jwt),
    session: Session = Depends(get_session),
):
    t: Optional[PersonalAccessToken] = session.get(PersonalAccessToken, token_id)
    if t is None or t.user_id != user.id:
        raise errors.not_found("token 不存在")
    if t.revoked_at is None:
        t.revoked_at = utcnow()
        session.add(t)
        session.commit()
    return {"data": _out(t)}
