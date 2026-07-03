"""Book routes (spec §5.3): list, create, get, update, soft-delete, restore.
Also member/invitation routes (spec §5.4) and book-level stats (spec §5.8).
"""
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from .. import errors
from ..config import settings
from ..database import get_session
from ..deps import get_current_user, get_book_for_member, get_membership
from ..models import (
    Book, BookMember, Chapter, ChapterContent, Invitation, User,
)
from ..models import utcnow
from ..schemas import (
    AcceptInviteIn, BookCreateIn, BookUpdateIn, MemberInviteIn, RoleUpdateIn,
)
from ..services.stats import book_stats

router = APIRouter(prefix="/api", tags=["books"])


def _book_dict(session: Session, book: Book) -> dict:
    """Serialize a book with computed word_count + progress (spec §5.3)."""
    chapters = session.exec(
        select(Chapter).where(
            Chapter.book_id == book.id, Chapter.deleted_at == None  # noqa: E711
        )
    ).all()
    chapter_ids = [c.id for c in chapters]
    total_words = 0
    if chapter_ids:
        contents = session.exec(
            select(ChapterContent).where(ChapterContent.chapter_id.in_(chapter_ids))
        ).all()
        total_words = sum(c.word_count for c in contents)
    completed = sum(1 for c in chapters if c.status == "done")
    progress = round(completed / len(chapters), 4) if chapters else 0.0
    return {
        "id": book.id,
        "title": book.title,
        "description": book.description,
        "cover_url": book.cover_url,
        "status": book.status,
        "tags": json.loads(book.tags) if book.tags else [],
        "word_count_goal": book.word_count_goal,
        "word_count": total_words,
        "progress": progress,
        "owner_id": book.owner_id,
        "created_at": book.created_at,
        "updated_at": book.updated_at,
        "deleted_at": book.deleted_at,
    }


# ---------- Books CRUD ----------
@router.get("/books", response_model=None)
def list_books(
    search: str | None = None,
    sort: str = Query("updated_at"),
    status: str | None = None,
    page: int = 1,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    # Books where the user is a member (spec FR-11), excluding soft-deleted.
    member_book_ids = session.exec(
        select(BookMember.book_id).where(BookMember.user_id == user.id)
    ).all()
    if not member_book_ids:
        return {"data": {"items": [], "total": 0}}

    stmt = select(Book).where(
        Book.id.in_(member_book_ids), Book.deleted_at == None  # noqa: E711
    )
    if search:
        stmt = stmt.where(Book.title.contains(search))
    if status:
        stmt = stmt.where(Book.status == status)
    books = session.exec(stmt).all()

    reverse = sort in {"updated_at", "created_at"}
    if sort == "title":
        books.sort(key=lambda b: b.title)
    elif sort == "created_at":
        books.sort(key=lambda b: b.created_at, reverse=True)
    else:  # updated_at default
        books.sort(key=lambda b: b.updated_at, reverse=True)

    items = [_book_dict(session, b) for b in books]
    return {"data": {"items": items, "total": len(items)}}


@router.post("/books", response_model=None)
def create_book(
    body: BookCreateIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book = Book(
        title=body.title.strip(),
        description=body.description,
        tags=json.dumps(body.tags) if body.tags else None,
        owner_id=user.id,
        status="draft",
    )
    if not book.title:
        raise errors.bad_request("請輸入 1–200 字書名")
    session.add(book)
    session.commit()
    session.refresh(book)
    # Creator becomes owner (spec FR-10, §6.1)
    session.add(BookMember(book_id=book.id, user_id=user.id, role="owner"))
    session.commit()
    return {"data": {"book": _book_dict(session, book)}}


@router.get("/books/trash", response_model=None)
def list_trash(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Recycle bin (design S5) — owner's soft-deleted books with remaining days.

    Declared BEFORE /books/{book_id} so the literal path wins route matching.
    """
    member_book_ids = session.exec(
        select(BookMember.book_id).where(
            BookMember.user_id == user.id, BookMember.role == "owner"
        )
    ).all()
    if not member_book_ids:
        return {"data": {"items": []}}
    books = session.exec(
        select(Book).where(
            Book.id.in_(member_book_ids), Book.deleted_at != None  # noqa: E711
        )
    ).all()
    items = []
    for b in books:
        deleted = datetime.fromisoformat(b.deleted_at)
        remaining = settings.restore_window_days - (datetime.now(timezone.utc) - deleted).days
        d = _book_dict(session, b)
        d["days_remaining"] = max(0, remaining)
        items.append(d)
    return {"data": {"items": items}}


@router.get("/books/{book_id}", response_model=None)
def get_book(
    book_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if book.deleted_at is not None:
        raise errors.not_found("書籍不存在")
    return {"data": {"book": _book_dict(session, book), "my_role": membership.role}}


@router.patch("/books/{book_id}", response_model=None)
def update_book(
    book_id: int,
    body: BookUpdateIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if book.deleted_at is not None:
        raise errors.not_found("書籍不存在")
    if membership.role != "owner":
        raise errors.forbidden("僅擁有者可修改書籍")
    data = body.model_dump(exclude_unset=True)
    if "tags" in data:
        book.tags = json.dumps(data.pop("tags"))
    for k, v in data.items():
        setattr(book, k, v)
    book.updated_at = utcnow()
    session.add(book)
    session.commit()
    session.refresh(book)
    return {"data": {"book": _book_dict(session, book)}}


@router.delete("/books/{book_id}", response_model=None)
def delete_book(
    book_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if book.deleted_at is not None:
        raise errors.not_found("書籍不存在")
    if membership.role != "owner":  # spec FR-14: only owner
        raise errors.forbidden("僅擁有者可刪除書籍")
    book.deleted_at = utcnow()
    session.add(book)
    session.commit()
    return {"data": {"success": True}}


@router.post("/books/{book_id}/restore", response_model=None)
def restore_book(
    book_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book = session.get(Book, book_id)
    membership = get_membership(session, book_id, user.id) if book else None
    if book is None or membership is None:
        raise errors.not_found("書籍不存在")
    if membership.role != "owner":
        raise errors.forbidden("僅擁有者可還原書籍")
    if book.deleted_at is None:
        raise errors.bad_request("書籍未被刪除")
    deleted = datetime.fromisoformat(book.deleted_at)
    if datetime.now(timezone.utc) - deleted > timedelta(days=settings.restore_window_days):
        raise errors.gone("已逾還原期限，無法復原")  # spec FR-15: 410
    book.deleted_at = None
    book.updated_at = utcnow()
    session.add(book)
    session.commit()
    session.refresh(book)
    return {"data": {"book": _book_dict(session, book)}}


# ---------- Members & Invitations (spec §5.4) ----------
@router.get("/books/{book_id}/members", response_model=None)
def list_members(
    book_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    members = session.exec(
        select(BookMember).where(BookMember.book_id == book_id)
    ).all()
    out = []
    for m in members:
        u = session.get(User, m.user_id)
        out.append({
            "user_id": m.user_id, "name": u.name if u else "未知",
            "email": u.email if u else "", "role": m.role,
            "avatar_url": u.avatar_url if u else None,
        })
    # pending invitations
    pending = session.exec(
        select(Invitation).where(
            Invitation.book_id == book_id, Invitation.status == "pending"
        )
    ).all()
    invites = [
        {"id": i.id, "email": i.email, "role": i.role, "token": i.token, "status": i.status}
        for i in pending
    ]
    return {"data": {"members": out, "invitations": invites}}


@router.post("/books/{book_id}/members", response_model=None)
def invite_member(
    book_id: int,
    body: MemberInviteIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if membership.role != "owner":
        raise errors.forbidden("僅擁有者可邀請成員")
    if body.email == user.email:
        raise errors.bad_request("不能邀請自己")  # §6.4

    target = session.exec(select(User).where(User.email == body.email)).first()
    if target:
        existing = get_membership(session, book_id, target.id)
        if existing:
            raise errors.conflict("該使用者已是成員")  # §6.4 409
        session.add(BookMember(book_id=book_id, user_id=target.id, role=body.role))
        session.commit()
        return {"data": {"invitation": {"email": body.email, "role": body.role,
                                        "status": "accepted", "registered": True}}}
    # Not registered → pending invitation (spec FR-21)
    token = secrets.token_urlsafe(24)
    inv = Invitation(book_id=book_id, email=body.email, role=body.role, token=token)
    session.add(inv)
    session.commit()
    session.refresh(inv)
    return {"data": {"invitation": {"id": inv.id, "email": inv.email, "role": inv.role,
                                    "status": "pending", "token": token, "registered": False}}}


@router.patch("/books/{book_id}/members/{user_id}", response_model=None)
def update_member_role(
    book_id: int,
    user_id: int,
    body: RoleUpdateIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if membership.role != "owner":
        raise errors.forbidden("僅擁有者可調整角色")
    target = get_membership(session, book_id, user_id)
    if target is None:
        raise errors.not_found("成員不存在")
    if target.role == "owner":  # spec FR-22: owner can't be demoted
        raise errors.forbidden("擁有者角色不可變更")
    target.role = body.role
    session.add(target)
    session.commit()
    return {"data": {"member": {"user_id": user_id, "role": body.role}}}


@router.delete("/books/{book_id}/members/{user_id}", response_model=None)
def remove_member(
    book_id: int,
    user_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if membership.role != "owner":
        raise errors.forbidden("僅擁有者可移除成員")
    target = get_membership(session, book_id, user_id)
    if target is None:
        raise errors.not_found("成員不存在")
    if target.role == "owner":  # spec FR-24: owner self excluded
        raise errors.forbidden("無法移除擁有者")
    session.delete(target)
    session.commit()
    return {"data": {"success": True}}


@router.post("/invitations/accept", response_model=None)
def accept_invitation(
    body: AcceptInviteIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    inv = session.exec(select(Invitation).where(Invitation.token == body.token)).first()
    if inv is None or inv.status != "pending":
        raise errors.not_found("邀請不存在或已失效")
    # 安全（S1）：邀請只能由受邀信箱本人接受，避免 token 外流被他人冒名兌換。
    if inv.email.lower() != user.email.lower():
        raise errors.forbidden("此邀請並非發給您的帳號")
    existing = get_membership(session, inv.book_id, user.id)
    if existing:
        inv.status = "accepted"
        session.add(inv)
        session.commit()
        raise errors.conflict("您已是此書成員")
    session.add(BookMember(book_id=inv.book_id, user_id=user.id, role=inv.role))
    inv.status = "accepted"
    session.add(inv)
    session.commit()
    return {"data": {"book_id": inv.book_id}}


# ---------- Book stats (spec §5.8) ----------
@router.get("/books/{book_id}/stats", response_model=None)
def get_book_stats(
    book_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if book.deleted_at is not None:
        raise errors.not_found("書籍不存在")
    return {"data": book_stats(session, book_id, user.id)}
