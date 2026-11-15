"""Auth routes (spec §5.2): register, login, me."""
import logging

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

log = logging.getLogger(__name__)

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
    uid, email, name = ident["uid"], ident["email"], ident["name"][:100]
    user = session.exec(select(User).where(User.believe_uid == uid)).first() if uid else None
    if user is not None:
        # spec §13.2:中央改 email → 跟隨(新 email 被別的本地帳號占用則保留舊的)
        if user.email.lower() != email:
            other = _find_user(session, email)
            if other is None:
                user.email = email
            else:
                log.warning("SSO email 跟隨失敗:%s 已被本地 user %s 占用(user %s 保留 %s)",
                            email, other.id, user.id, user.email)
    else:
        user = _find_user(session, email)
        if user is not None and user.believe_uid and uid and user.believe_uid != uid:
            # 此 email 的本地帳號已屬另一個中央帳號(對方改過 email)→ 不交給新的人
            log.warning("SSO 拒絕:%s 本地帳號已綁中央 %s,請求者 %s", email, user.believe_uid, uid)
            raise errors.conflict("此 email 的 book 帳號已屬於另一位會員,請聯絡管理員")
        if user is None:
            user = User(email=email, password_hash=unusable_password_hash(),
                        name=name or email.split("@")[0])
        if uid:
            user.believe_uid = uid
    # 名稱跟隨中央:book 沒有本地改名功能,中央是唯一可編輯處;中央名稱空則不清掉
    if name and user.name != name:
        user.name = name
    if user.id is None or session.is_modified(user):
        session.add(user)
        session.commit()
        session.refresh(user)
    # spec §9-3:未驗證 email 不自動接受邀請(防帳號預先劫持)。
    if ident["email_verified"]:
        _auto_accept_invites(session, user)
    token = create_access_token(user.id)
    return {"data": AuthOut(user=_user_out(user), token=token).model_dump()}
