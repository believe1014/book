"""Chapter content & version routes (spec §5.6, §5.7).

Content PATCH bumps version, writes a version snapshot, recomputes word count
(spec FR-42/43, §4.3) and enforces soft-lock (FR-44, 423) + version conflict
(409, §6.2).
"""
import asyncio
import json

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from .. import errors
from ..database import get_session
from ..deps import (
    EDIT_ROLES, get_current_user, resolve_chapter_book,
)
from ..models import ChapterContent, ContentVersion, User, utcnow
from ..schemas import ContentPatchIn
from ..services.locks import lock_info_public, lock_manager
from ..services.wordcount import count_words
from ..services.ws_manager import room_manager

router = APIRouter(prefix="/api", tags=["content"])

MAX_VERSIONS = 100  # spec FR-73


def _get_or_create_content(session: Session, chapter_id: int) -> ChapterContent:
    content = session.exec(
        select(ChapterContent).where(ChapterContent.chapter_id == chapter_id)
    ).first()
    if content is None:
        content = ChapterContent(chapter_id=chapter_id, content_json="{}")
        session.add(content)
        session.commit()
        session.refresh(content)
    return content


@router.get("/chapters/{chapter_id}/content", response_model=None)
def get_content(
    chapter_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    chapter, book, membership = resolve_chapter_book(session, chapter_id, user)
    content = _get_or_create_content(session, chapter_id)
    lock = lock_manager.get(chapter_id)
    return {"data": {
        "content_json": json.loads(content.content_json) if content.content_json else {},
        "version": content.version,
        "word_count": content.word_count,
        "updated_at": content.updated_at,
        "lock": lock_info_public(lock),
        "can_edit": membership.role in EDIT_ROLES,
    }}


@router.patch("/chapters/{chapter_id}/content", response_model=None)
def patch_content(
    chapter_id: int,
    body: ContentPatchIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    chapter, book, membership = resolve_chapter_book(session, chapter_id, user)
    if membership.role not in EDIT_ROLES:  # spec: Viewer/Reviewer can't edit (403)
        raise errors.forbidden("您沒有編輯權限")

    # Soft-lock check (spec FR-44, 423). Auto-acquire if free.
    holder = lock_manager.holder(chapter_id)
    if holder is not None and holder != user.id:
        raise errors.locked("章節正由他人編輯")
    lock_manager.acquire(chapter_id, user.id, user.name)

    content = _get_or_create_content(session, chapter_id)

    # Version conflict (spec §6.2, 409)
    if body.base_version != content.version:
        raise errors.conflict("內容已被更新，請重新整理後再試")

    content_str = json.dumps(body.content_json, ensure_ascii=False)
    wc = count_words(content_str)
    content.content_json = content_str
    content.word_count = wc
    content.version += 1
    content.updated_by = user.id
    content.updated_at = utcnow()
    session.add(content)

    # Version snapshot (spec FR-42/70)
    session.add(ContentVersion(
        chapter_id=chapter_id, version=content.version,
        content_json=content_str, word_count=wc, editor_id=user.id,
    ))
    session.commit()
    session.refresh(content)

    _prune_versions(session, chapter_id)

    # Broadcast content_updated (spec FR-51/52)
    _broadcast_safe(chapter_id, {"type": "content_updated", "version": content.version})

    return {"data": {
        "version": content.version,
        "word_count": content.word_count,
        "updated_at": content.updated_at,
    }}


def _prune_versions(session: Session, chapter_id: int) -> None:
    """spec FR-73: keep latest 100 + first-of-each-day."""
    versions = session.exec(
        select(ContentVersion).where(ContentVersion.chapter_id == chapter_id)
        .order_by(ContentVersion.version.desc())
    ).all()
    if len(versions) <= MAX_VERSIONS:
        return
    keep_ids = set(v.id for v in versions[:MAX_VERSIONS])
    # Keep first-of-day for older ones.
    first_of_day: dict[str, int] = {}
    for v in sorted(versions, key=lambda x: x.version):
        day = v.created_at[:10]
        if day not in first_of_day:
            first_of_day[day] = v.id
    keep_ids.update(first_of_day.values())
    for v in versions:
        if v.id not in keep_ids:
            session.delete(v)
    session.commit()


def _broadcast_safe(chapter_id: int, message: dict) -> None:
    """Fire-and-forget broadcast from sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(room_manager.broadcast(chapter_id, message))
    except RuntimeError:
        pass


# ---------- Locks (spec §5.6) ----------
@router.post("/chapters/{chapter_id}/lock", response_model=None)
def acquire_lock(
    chapter_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    chapter, book, membership = resolve_chapter_book(session, chapter_id, user)
    if membership.role not in EDIT_ROLES:
        raise errors.forbidden("您沒有編輯權限")
    ok, entry = lock_manager.acquire(chapter_id, user.id, user.name)
    if not ok:
        raise errors.locked("章節正由他人編輯")
    _broadcast_safe(chapter_id, {"type": "lock_changed", "lock_owner": user.id})
    return {"data": lock_info_public(entry)}


@router.delete("/chapters/{chapter_id}/lock", response_model=None)
def release_lock(
    chapter_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    chapter, book, membership = resolve_chapter_book(session, chapter_id, user)
    lock_manager.release(chapter_id, user.id)
    _broadcast_safe(chapter_id, {"type": "lock_changed", "lock_owner": None})
    return {"data": {"success": True}}


# ---------- Versions (spec §5.7) ----------
@router.get("/chapters/{chapter_id}/versions", response_model=None)
def list_versions(
    chapter_id: int,
    page: int = 1,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    chapter, book, membership = resolve_chapter_book(session, chapter_id, user)
    page_size = 20
    all_versions = session.exec(
        select(ContentVersion).where(ContentVersion.chapter_id == chapter_id)
        .order_by(ContentVersion.version.desc())
    ).all()
    total = len(all_versions)
    start = (page - 1) * page_size
    items = []
    for v in all_versions[start:start + page_size]:
        editor = session.get(User, v.editor_id) if v.editor_id else None
        items.append({
            "version": v.version, "word_count": v.word_count,
            "editor_id": v.editor_id,
            "editor_name": editor.name if editor else "未知",
            "created_at": v.created_at,
        })
    return {"data": {"items": items, "total": total}}


@router.get("/chapters/{chapter_id}/versions/{ver}", response_model=None)
def get_version(
    chapter_id: int,
    ver: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    chapter, book, membership = resolve_chapter_book(session, chapter_id, user)
    v = session.exec(
        select(ContentVersion).where(
            ContentVersion.chapter_id == chapter_id, ContentVersion.version == ver
        )
    ).first()
    if v is None:
        raise errors.not_found("版本不存在")
    editor = session.get(User, v.editor_id) if v.editor_id else None
    return {"data": {
        "content_json": json.loads(v.content_json) if v.content_json else {},
        "version": v.version,
        "word_count": v.word_count,
        "editor": {"id": v.editor_id, "name": editor.name if editor else "未知"},
        "created_at": v.created_at,
    }}


@router.post("/chapters/{chapter_id}/versions/{ver}/restore", response_model=None)
def restore_version(
    chapter_id: int,
    ver: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    chapter, book, membership = resolve_chapter_book(session, chapter_id, user)
    if membership.role not in EDIT_ROLES:
        raise errors.forbidden("您沒有編輯權限")
    old = session.exec(
        select(ContentVersion).where(
            ContentVersion.chapter_id == chapter_id, ContentVersion.version == ver
        )
    ).first()
    if old is None:
        raise errors.not_found("版本不存在")
    # Restore = create a NEW version (spec FR-72), don't overwrite history.
    content = _get_or_create_content(session, chapter_id)
    content.content_json = old.content_json
    content.word_count = old.word_count
    content.version += 1
    content.updated_by = user.id
    content.updated_at = utcnow()
    session.add(content)
    session.add(ContentVersion(
        chapter_id=chapter_id, version=content.version,
        content_json=old.content_json, word_count=old.word_count, editor_id=user.id,
    ))
    session.commit()
    session.refresh(content)
    _broadcast_safe(chapter_id, {"type": "content_updated", "version": content.version})
    return {"data": {"version": content.version}}
