"""MCP OAuth 同意頁 API(spec §5/§7):前端 /oauth/consent 用。

只收網頁 JWT(get_current_user_jwt):PAT / OAuth token 不能替自己核准新授權。
"""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import AnyUrl, BaseModel
from sqlmodel import Session

from mcp.server.auth.provider import construct_redirect_uri

from .. import errors
from ..database import get_session
from ..deps import get_current_user_jwt
from ..models import User
from ..oauth import client_by_id, decode_req, issue_code

router = APIRouter(prefix="/api/oauth", tags=["oauth"])


class ConsentIn(BaseModel):
    req: str
    approve: bool


def _load(session: Session, req: str):
    p = decode_req(req)
    if p is None:
        raise errors.bad_request("授權請求無效或已過期，請回 Claude Code 重新按授權")
    client = client_by_id(session, p["cid"])
    # 再比一次 redirect_uri(client 可能已被刪或換掉)
    if client is None or AnyUrl(p["ru"]) not in (client.redirect_uris or []):
        raise errors.bad_request("授權請求的應用程式不存在，請回 Claude Code 重新按授權")
    return p, client


@router.get("/consent", response_model=None)
def consent_info(
    req: str,
    user: User = Depends(get_current_user_jwt),
    session: Session = Depends(get_session),
):
    p, client = _load(session, req)
    u = urlparse(p["ru"])
    return {"data": {
        "client_name": client.client_name or "未命名的應用程式",
        "redirect_origin": f"{u.scheme}://{u.netloc}",
    }}


@router.post("/consent", response_model=None)
def consent_decide(
    body: ConsentIn,
    user: User = Depends(get_current_user_jwt),
    session: Session = Depends(get_session),
):
    p, _ = _load(session, body.req)
    if body.approve:
        url = construct_redirect_uri(p["ru"], code=issue_code(session, p, user.id), state=p["st"])
    else:
        url = construct_redirect_uri(p["ru"], error="access_denied", state=p["st"])
    return {"data": {"redirect_url": url}}
