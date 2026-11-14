"""Auth routes (spec §5.2): register, login, me."""
from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, func, select

from .. import errors
from ..auth import (
    create_access_token,
    hash_password,
    sso_identity,
    unusable_password_hash,
    verify_password,
)
from ..config import settings
from ..database import get_session
from ..deps import get_current_user
from ..models import BookMember, Invitation, User
from ..schemas import AuthOut, LoginIn, RegisterIn, UserOut
from ..services.rate_limit import login_rate_limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name, avatar_url=u.avatar_url)


def _find_user(session: Session, email: str):
    """Email 不分大小寫找使用者(spec §9-6)。"""
    return session.exec(select(User).where(func.lower(User.email) == email.lower())).first()


def _auto_accept_invites(session: Session, user: User) -> None:
    """spec FR-21: pending invitations for this email auto-join on register."""
    invites = session.exec(
        select(Invitation).where(
            func.lower(Invitation.email) == user.email.lower(),
            Invitation.status == "pending",
        )
    ).all()
    for inv in invites:
        existing = session.exec(
            select(BookMember).where(
                BookMember.book_id == inv.book_id, BookMember.user_id == user.id
            )
        ).first()
        if existing is None:
            session.add(BookMember(book_id=inv.book_id, user_id=user.id, role=inv.role))
        inv.status = "accepted"
        session.add(inv)
    session.commit()


@router.post("/register", response_model=None)
def register(body: RegisterIn, session: Session = Depends(get_session)):
    if _find_user(session, body.email):
        raise errors.conflict("Email 已被註冊")
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        name=body.name,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    _auto_accept_invites(session, user)

    token = create_access_token(user.id)
    return {"data": AuthOut(user=_user_out(user), token=token).model_dump()}


@router.post("/login", response_model=None)
def login(body: LoginIn, request: Request, session: Session = Depends(get_session)):
    # S3：登入暴力破解限流（行程內，keyed by client_ip + email）。
    # 註：request.client.host 是真實來源 IP——uvicorn proxy headers 依 X-Forwarded-For 改寫，
    # 正式站以 FORWARDED_ALLOW_IPS 限定只信任 docker 網路閘道（host nginx）。
    client_ip = request.client.host if request.client else "unknown"
    if login_rate_limiter.is_blocked(client_ip, body.email):
        raise errors.too_many_requests("登入嘗試次數過多，請稍後再試")

    user = _find_user(session, body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        login_rate_limiter.record_failure(client_ip, body.email)
        raise errors.unauthorized("帳號或密碼錯誤")

    # 成功登入清除該 key 的失敗計數。
    login_rate_limiter.reset(client_ip, body.email)
    token = create_access_token(user.id)
    return {"data": AuthOut(user=_user_out(user), token=token).model_dump()}


@router.get("/me", response_model=None)
def me(user: User = Depends(get_current_user)):
    return {"data": {"user": _user_out(user).model_dump()}}


@router.get("/config", response_model=None)
def auth_config():
    """公開設定:前端據此決定顯示會員登入按鈕或帳密表單。"""
    sso = bool(settings.believe_sso_secret)
    return {"data": {"sso": sso, "account_url": settings.believe_account_url if sso else None}}


@router.post("/sso", response_model=None)
def sso(request: Request, session: Session = Depends(get_session)):
    """以 nginx 帶來的 believe 身分換 book 自己的 24h JWT(account spec §6.7)。"""
    if not settings.believe_sso_secret:
        raise errors.not_found()
    ident = sso_identity(request)
    if ident is None:
        raise errors.unauthorized("未登入 believe 會員")
    user = _find_user(session, ident["email"])
    if user is None:
        user = User(
            email=ident["email"],
            password_hash=unusable_password_hash(),
            name=(ident["name"] or ident["email"].split("@")[0])[:100],
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    # spec §9-3:未驗證 email 不自動接受邀請(防帳號預先劫持)。
    if ident["email_verified"]:
        _auto_accept_invites(session, user)
    token = create_access_token(user.id)
    return {"data": AuthOut(user=_user_out(user), token=token).model_dump()}
