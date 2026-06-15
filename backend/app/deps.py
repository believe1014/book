"""Auth & permission dependencies (spec §6.7 permission matrix enforcement).

Permission matrix (PRD §3.3):
- owner:    full control (incl. delete book, manage members, transfer)
- editor:   chapter CRUD, edit content, upload media
- reviewer: view only (comments deferred to P2)
- viewer:   view only

Resolution rule (spec §6.7): look up book_members for the user's role, then
check against the matrix. If the book is missing/soft-deleted, return 404 to
non-members (don't leak existence).
"""
from typing import Optional

from fastapi import Depends, Header
from sqlmodel import Session, select

from . import errors
from .auth import decode_token
from .database import get_session
from .models import Book, BookMember, Chapter, User

# Roles that may edit chapters / content / media.
EDIT_ROLES = {"owner", "editor"}
# Roles that may view.
VIEW_ROLES = {"owner", "editor", "reviewer", "viewer"}


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    """Resolve the current user from the Bearer JWT (spec FR-03)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise errors.unauthorized()
    token = authorization.split(" ", 1)[1].strip()
    user_id = decode_token(token)
    if user_id is None:
        raise errors.unauthorized()
    user = session.get(User, user_id)
    if user is None:
        raise errors.unauthorized()
    return user


def get_membership(session: Session, book_id: int, user_id: int) -> Optional[BookMember]:
    return session.exec(
        select(BookMember).where(
            BookMember.book_id == book_id, BookMember.user_id == user_id
        )
    ).first()


def get_book_for_member(session: Session, book_id: int, user: User) -> tuple[Book, BookMember]:
    """Return (book, membership) or raise 404 if not an accessible member.

    Soft-deleted books still resolve here (needed for restore); callers that
    must exclude deleted books should check `book.deleted_at`.
    """
    book = session.get(Book, book_id)
    membership = get_membership(session, book_id, user.id) if book else None
    # Don't leak existence to non-members (spec §6.7).
    if book is None or membership is None:
        raise errors.not_found("書籍不存在")
    return book, membership


def require_book_member(book_id: int):
    """Dependency factory: any role can access (view)."""
    def _dep(
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ):
        book, membership = get_book_for_member(session, book_id, user)
        if book.deleted_at is not None:
            raise errors.not_found("書籍不存在")
        return book, membership, user

    return _dep


def resolve_chapter_book(session: Session, chapter_id: int, user: User) -> tuple[Chapter, Book, BookMember]:
    """Resolve a chapter + its book + the user's membership (with 404 guard)."""
    chapter = session.get(Chapter, chapter_id)
    if chapter is None or chapter.deleted_at is not None:
        raise errors.not_found("章節不存在")
    book = session.get(Book, chapter.book_id)
    membership = get_membership(session, chapter.book_id, user.id) if book else None
    if book is None or book.deleted_at is not None or membership is None:
        raise errors.not_found("章節不存在")
    return chapter, book, membership
