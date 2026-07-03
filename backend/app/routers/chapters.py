"""Chapter routes (spec §5.5): list (tree), create, update, reorder, delete.
Also chapter-level stats (spec §5.8/FR-61).
"""
from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from .. import errors
from ..database import get_session
from ..deps import (
    EDIT_ROLES, get_book_for_member, get_current_user, resolve_chapter_book,
)
from ..models import Chapter, ChapterContent, utcnow
from ..models import User
from ..schemas import ChapterCreateIn, ChapterUpdateIn, ReorderItem
from ..services.wordcount import (
    count_paragraphs, extract_text, reading_minutes,
)

router = APIRouter(prefix="/api", tags=["chapters"])


def _chapter_dict(c: Chapter) -> dict:
    return {
        "id": c.id, "book_id": c.book_id, "parent_id": c.parent_id,
        "title": c.title, "order_index": c.order_index, "status": c.status,
        "created_at": c.created_at, "updated_at": c.updated_at,
    }


@router.get("/books/{book_id}/chapters", response_model=None)
def list_chapters(
    book_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if book.deleted_at is not None:
        raise errors.not_found("書籍不存在")
    chapters = session.exec(
        select(Chapter).where(
            Chapter.book_id == book_id, Chapter.deleted_at == None  # noqa: E711
        )
    ).all()
    # Build a two-level tree (spec FR-31).
    tops = sorted([c for c in chapters if c.parent_id is None],
                  key=lambda x: x.order_index)
    children: dict[int, list] = {}
    for c in chapters:
        if c.parent_id is not None:
            children.setdefault(c.parent_id, []).append(c)
    tree = []
    for top in tops:
        node = _chapter_dict(top)
        kids = sorted(children.get(top.id, []), key=lambda x: x.order_index)
        node["children"] = [_chapter_dict(k) for k in kids]
        tree.append(node)
    return {"data": {"chapters": tree}}


@router.post("/books/{book_id}/chapters", response_model=None)
def create_chapter(
    book_id: int,
    body: ChapterCreateIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if book.deleted_at is not None:
        raise errors.not_found("書籍不存在")
    if membership.role not in EDIT_ROLES:
        raise errors.forbidden("僅擁有者或編輯者可新增章節")

    # Q2: 空白標題驗證一致化（對齊 books.py::create_book）。
    if not body.title.strip():
        raise errors.bad_request("請輸入章節標題")

    # Enforce max two levels (spec FR-31): parent must be a top-level chapter.
    if body.parent_id is not None:
        parent = session.get(Chapter, body.parent_id)
        if parent is None or parent.book_id != book_id or parent.deleted_at is not None:
            raise errors.bad_request("父章節不存在")
        if parent.parent_id is not None:
            raise errors.bad_request("最多支援兩層結構")

    # order_index = end of same level (spec FR-30)
    siblings = session.exec(
        select(Chapter).where(
            Chapter.book_id == book_id,
            Chapter.parent_id == body.parent_id,
            Chapter.deleted_at == None,  # noqa: E711
        )
    ).all()
    next_order = max([c.order_index for c in siblings], default=-1) + 1

    chapter = Chapter(
        book_id=book_id, parent_id=body.parent_id, title=body.title.strip(),
        order_index=next_order,
    )
    session.add(chapter)
    session.commit()
    session.refresh(chapter)
    # Auto-create content doc (spec §4: one content per chapter)
    session.add(ChapterContent(chapter_id=chapter.id, content_json="{}"))
    session.commit()
    return {"data": {"chapter": _chapter_dict(chapter)}}


@router.patch("/chapters/{chapter_id}", response_model=None)
def update_chapter(
    chapter_id: int,
    body: ChapterUpdateIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    chapter, book, membership = resolve_chapter_book(session, chapter_id, user)
    if membership.role not in EDIT_ROLES:
        raise errors.forbidden("僅擁有者或編輯者可修改章節")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(chapter, k, v)
    chapter.updated_at = utcnow()
    session.add(chapter)
    session.commit()
    session.refresh(chapter)
    return {"data": {"chapter": _chapter_dict(chapter)}}


@router.patch("/books/{book_id}/chapters/reorder", response_model=None)
def reorder_chapters(
    book_id: int,
    items: list[ReorderItem],
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if book.deleted_at is not None:
        raise errors.not_found("書籍不存在")
    if membership.role not in EDIT_ROLES:
        raise errors.forbidden("僅擁有者或編輯者可排序章節")

    # Validate all in single transaction (spec §6.3).
    chapters = {c.id: c for c in session.exec(
        select(Chapter).where(
            Chapter.book_id == book_id, Chapter.deleted_at == None  # noqa: E711
        )
    ).all()}
    # First pass: validate two-level constraint.
    for item in items:
        if item.id not in chapters:
            raise errors.bad_request("章節不存在或不屬於此書")
        if item.parent_id is not None:
            parent = chapters.get(item.parent_id)
            if parent is None:
                raise errors.bad_request("父章節不存在")
            # Parent must itself be top-level (no third level, FR-31/§6.3).
            # Account for the parent's NEW position in this same payload.
            new_parent_pid = next(
                (it.parent_id for it in items if it.id == item.parent_id),
                parent.parent_id,
            )
            if new_parent_pid is not None:
                raise errors.bad_request("最多支援兩層結構")
    # Second pass: apply.
    for item in items:
        c = chapters[item.id]
        c.parent_id = item.parent_id
        c.order_index = item.order_index
        c.updated_at = utcnow()
        session.add(c)
    session.commit()
    return {"data": {"success": True}}


@router.delete("/chapters/{chapter_id}", response_model=None)
def delete_chapter(
    chapter_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    chapter, book, membership = resolve_chapter_book(session, chapter_id, user)
    if membership.role not in EDIT_ROLES:
        raise errors.forbidden("僅擁有者或編輯者可刪除章節")
    now = utcnow()
    chapter.deleted_at = now
    session.add(chapter)
    # Cascade soft-delete children if this is a top-level chapter (spec FR-35).
    if chapter.parent_id is None:
        kids = session.exec(
            select(Chapter).where(
                Chapter.parent_id == chapter_id, Chapter.deleted_at == None  # noqa: E711
            )
        ).all()
        for k in kids:
            k.deleted_at = now
            session.add(k)
    session.commit()
    return {"data": {"success": True}}


@router.get("/chapters/{chapter_id}/stats", response_model=None)
def chapter_stats(
    chapter_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    chapter, book, membership = resolve_chapter_book(session, chapter_id, user)
    content = session.exec(
        select(ChapterContent).where(ChapterContent.chapter_id == chapter_id)
    ).first()
    if content is None:
        return {"data": {"word_count": 0, "paragraph_count": 0,
                         "reading_minutes": 0.0, "updated_at": chapter.updated_at}}
    text = extract_text(content.content_json)
    return {"data": {
        "word_count": content.word_count,
        "paragraph_count": count_paragraphs(content.content_json),
        "reading_minutes": reading_minutes(text),
        "updated_at": content.updated_at,
    }}
