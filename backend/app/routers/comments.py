"""Review comments on chapters (chapter-level threads, single-level replies).

Reviewers (and editors/owners) can post comments with an optional image, reply
to a thread, edit/delete their own, and mark a top-level thread resolved. The
author's chapter content stays read-only for reviewers — comments never touch it.
"""
from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from .. import errors
from ..database import get_session
from ..deps import (
    COMMENT_ROLES, get_current_user, resolve_chapter_book,
)
from ..models import Comment, User
from ..models import utcnow
from ..schemas import CommentCreateIn, CommentUpdateIn

router = APIRouter(prefix="/api", tags=["comments"])


def _comment_dict(c: Comment, author_name: str) -> dict:
    return {
        "id": c.id,
        "chapter_id": c.chapter_id,
        "author_id": c.author_id,
        "author_name": author_name,
        "parent_id": c.parent_id,
        "body": c.body,
        "image_url": c.image_url,
        "quote": c.quote,
        "resolved": c.resolved,
        "resolved_by": c.resolved_by,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def _names(session: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = session.exec(select(User).where(User.id.in_(ids))).all()
    return {u.id: u.name for u in rows}


def _get_comment(session: Session, comment_id: int) -> Comment:
    c = session.get(Comment, comment_id)
    if c is None or c.deleted_at is not None:
        raise errors.not_found("評論不存在")
    return c


@router.get("/chapters/{chapter_id}/comments", response_model=None)
def list_comments(
    chapter_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    # Any member of the book may read comments.
    resolve_chapter_book(session, chapter_id, user)
    rows = session.exec(
        select(Comment).where(
            Comment.chapter_id == chapter_id, Comment.deleted_at == None  # noqa: E711
        )
    ).all()
    names = _names(session, {c.author_id for c in rows})
    rows.sort(key=lambda c: c.created_at)
    tops = [c for c in rows if c.parent_id is None]
    replies: dict[int, list] = {}
    for c in rows:
        if c.parent_id is not None:
            replies.setdefault(c.parent_id, []).append(c)
    threads = []
    for t in tops:
        node = _comment_dict(t, names.get(t.author_id, "?"))
        node["replies"] = [_comment_dict(r, names.get(r.author_id, "?")) for r in replies.get(t.id, [])]
        threads.append(node)
    unresolved = sum(1 for t in tops if not t.resolved)
    return {"data": {"comments": threads, "unresolved": unresolved, "total": len(tops)}}


@router.post("/chapters/{chapter_id}/comments", response_model=None)
def create_comment(
    chapter_id: int,
    body: CommentCreateIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _, _, membership = resolve_chapter_book(session, chapter_id, user)
    if membership.role not in COMMENT_ROLES:
        raise errors.forbidden("您的角色無法發表評論")
    if not body.body and not body.image_url:
        raise errors.bad_request("評論需有文字或圖片")

    parent_id = body.parent_id
    if parent_id is not None:
        parent = _get_comment(session, parent_id)
        if parent.chapter_id != chapter_id:
            raise errors.bad_request("回覆的評論不屬於此章節")
        if parent.parent_id is not None:
            raise errors.bad_request("僅支援單層回覆")  # no reply-to-reply

    c = Comment(
        chapter_id=chapter_id, author_id=user.id, parent_id=parent_id,
        body=body.body, image_url=body.image_url,
        quote=body.quote if parent_id is None else None,  # replies carry no quote
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return {"data": {"comment": _comment_dict(c, user.name)}}


@router.patch("/comments/{comment_id}", response_model=None)
def update_comment(
    comment_id: int,
    body: CommentUpdateIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    c = _get_comment(session, comment_id)
    resolve_chapter_book(session, c.chapter_id, user)  # 404 guard / membership
    if c.author_id != user.id:
        raise errors.forbidden("只能編輯自己的評論")
    if body.body is not None:
        c.body = body.body.strip()
    if body.image_url is not None:
        c.image_url = body.image_url or None
    if not c.body and not c.image_url:
        raise errors.bad_request("評論需有文字或圖片")
    c.updated_at = utcnow()
    session.add(c)
    session.commit()
    session.refresh(c)
    return {"data": {"comment": _comment_dict(c, user.name)}}


@router.delete("/comments/{comment_id}", response_model=None)
def delete_comment(
    comment_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    c = _get_comment(session, comment_id)
    _, _, membership = resolve_chapter_book(session, c.chapter_id, user)
    # Author may delete own; book owner may delete any.
    if c.author_id != user.id and membership.role != "owner":
        raise errors.forbidden("只能刪除自己的評論")
    now = utcnow()
    c.deleted_at = now
    session.add(c)
    # Soft-delete replies along with a deleted top-level thread.
    if c.parent_id is None:
        for r in session.exec(
            select(Comment).where(
                Comment.parent_id == comment_id, Comment.deleted_at == None  # noqa: E711
            )
        ).all():
            r.deleted_at = now
            session.add(r)
    session.commit()
    return {"data": {"deleted": True}}


@router.post("/comments/{comment_id}/resolve", response_model=None)
def resolve_comment(
    comment_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return _set_resolved(session, user, comment_id, True)


@router.delete("/comments/{comment_id}/resolve", response_model=None)
def unresolve_comment(
    comment_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return _set_resolved(session, user, comment_id, False)


def _set_resolved(session: Session, user: User, comment_id: int, value: bool):
    c = _get_comment(session, comment_id)
    _, _, membership = resolve_chapter_book(session, c.chapter_id, user)
    if membership.role not in COMMENT_ROLES:
        raise errors.forbidden("您的角色無法變更評論狀態")
    if c.parent_id is not None:
        raise errors.bad_request("只能標記主評論為已解決")
    c.resolved = value
    c.resolved_by = user.id if value else None
    c.updated_at = utcnow()
    session.add(c)
    session.commit()
    session.refresh(c)
    return {"data": {"comment": _comment_dict(c, user.name)}}
