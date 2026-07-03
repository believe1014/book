"""Auth routes (spec §5.2): register, login, me."""
from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from .. import errors
from ..auth import create_access_token, hash_password, verify_password
from ..database import get_session
from ..deps import get_current_user
from ..models import BookMember, Invitation, User
from ..schemas import AuthOut, LoginIn, RegisterIn, UserOut
from ..services.rate_limit import login_rate_limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name, avatar_url=u.avatar_url)


def _auto_accept_invites(session: Session, user: User) -> None:
    """spec FR-21: pending invitations for this email auto-join on register."""
    invites = session.exec(
        select(Invitation).where(
            Invitation.email == user.email, Invitation.status == "pending"
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
    existing = session.exec(select(User).where(User.email == body.email)).first()
    if existing:
        raise errors.conflict("Email 已被註冊")
    user = User(
        email=body.email,
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
    # 註：反向代理後 request.client.host 會是代理 IP；若日後導入可信代理，
    # 應改用 X-Forwarded-For 的最右可信段。目前直接以連線來源 IP 計數。
    client_ip = request.client.host if request.client else "unknown"
    if login_rate_limiter.is_blocked(client_ip, body.email):
        raise errors.too_many_requests("登入嘗試次數過多，請稍後再試")

    user = session.exec(select(User).where(User.email == body.email)).first()
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
