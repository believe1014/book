"""MCP server exposing the 協作撰書系統 as tools (remote streamable HTTP).

Mounted onto the FastAPI app at /mcp so it ships with the same deployment.
Every tool authenticates with the same JWT the web app issues: the MCP client
must send `Authorization: Bearer <token>` (obtain a token from
POST /api/auth/login). Permission checks reuse the app's role matrix, so an MCP
caller can only touch books they are a member of.
"""
import json
from typing import Optional

from sqlmodel import Session, select

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

try:  # clean error messages to the client when available
    from mcp.server.fastmcp.exceptions import ToolError
except Exception:  # pragma: no cover - fallback for older SDKs
    ToolError = ValueError

from .auth import decode_token
from .config import settings
from .database import engine
from .deps import EDIT_ROLES
from .models import (
    Book, BookMember, Chapter, ChapterContent, Comment, ContentVersion, User, utcnow,
)
from .routers.content import _get_or_create_content, _prune_versions
from .services.wordcount import count_words, extract_text

_allowed_hosts = [h.strip() for h in settings.mcp_allowed_hosts.split(",") if h.strip()]
if _allowed_hosts:
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        allowed_origins=_allowed_hosts,
    )
else:
    # No allowlist configured → don't reject by Host (works behind any domain).
    # Safe because every tool requires a Bearer JWT.
    _transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

mcp_server = FastMCP(
    "協作撰書系統",
    instructions=(
        "Tools to manage collaborative book-writing projects: list/create books, "
        "manage chapters (max two levels), and read/write chapter content. "
        "Authenticate with a Bearer JWT from POST /api/auth/login."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_transport_security,
)

CHAPTER_STATUSES = {"not_started", "writing", "reviewing", "done"}


# ---------- auth & resolution helpers ----------
def _current_user(ctx: Context, session: Session) -> User:
    req = getattr(ctx.request_context, "request", None)
    authz = req.headers.get("authorization", "") if req is not None else ""
    if not authz.lower().startswith("bearer "):
        raise ToolError("未授權：請在 Authorization 標頭帶入 Bearer <token>")
    user_id = decode_token(authz.split(" ", 1)[1].strip())
    if user_id is None:
        raise ToolError("未授權：token 無效或已過期")
    user = session.get(User, user_id)
    if user is None:
        raise ToolError("未授權：使用者不存在")
    return user


def _membership(session: Session, book_id: int, user_id: int) -> Optional[BookMember]:
    return session.exec(
        select(BookMember).where(
            BookMember.book_id == book_id, BookMember.user_id == user_id
        )
    ).first()


def _resolve_book(session: Session, book_id: int, user: User) -> tuple[Book, BookMember]:
    book = session.get(Book, book_id)
    membership = _membership(session, book_id, user.id) if book else None
    if book is None or book.deleted_at is not None or membership is None:
        raise ToolError("書籍不存在或您無權存取")
    return book, membership


def _resolve_chapter(session: Session, chapter_id: int, user: User):
    chapter = session.get(Chapter, chapter_id)
    if chapter is None or chapter.deleted_at is not None:
        raise ToolError("章節不存在")
    book, membership = _resolve_book(session, chapter.book_id, user)
    return chapter, book, membership


def _require_edit(membership: BookMember) -> None:
    if membership.role not in EDIT_ROLES:
        raise ToolError("您沒有編輯權限（需 owner 或 editor）")


# ---------- content <-> plain text ----------
def _text_to_doc(text: str) -> dict:
    """Build a minimal ProseMirror-style doc from plain text (one para/line)."""
    nodes = []
    for line in (text or "").split("\n"):
        node = {"type": "paragraph"}
        if line.strip():
            node["content"] = [{"type": "text", "text": line}]
        nodes.append(node)
    return {"type": "doc", "content": nodes or [{"type": "paragraph"}]}


def _chapter_tree(session: Session, book_id: int) -> list[dict]:
    rows = session.exec(
        select(Chapter)
        .where(Chapter.book_id == book_id, Chapter.deleted_at.is_(None))
        .order_by(Chapter.order_index)
    ).all()
    by_parent: dict[Optional[int], list[Chapter]] = {}
    for c in rows:
        by_parent.setdefault(c.parent_id, []).append(c)

    def node(c: Chapter) -> dict:
        return {
            "id": c.id, "title": c.title, "status": c.status,
            "children": [node(k) for k in by_parent.get(c.id, [])],
        }

    return [node(c) for c in by_parent.get(None, [])]


# ---------- tools: books ----------
@mcp_server.tool()
def list_books(ctx: Context) -> list[dict]:
    """List all books the authenticated user can access (not deleted)."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        members = session.exec(
            select(BookMember).where(BookMember.user_id == user.id)
        ).all()
        out = []
        for m in members:
            book = session.get(Book, m.book_id)
            if book is None or book.deleted_at is not None:
                continue
            out.append({
                "id": book.id, "title": book.title, "status": book.status,
                "role": m.role, "description": book.description,
            })
        return out


@mcp_server.tool()
def create_book(ctx: Context, title: str, description: Optional[str] = None) -> dict:
    """Create a new book; the caller becomes its owner."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        if not title.strip():
            raise ToolError("書名不可為空")
        book = Book(title=title.strip(), description=description, owner_id=user.id)
        session.add(book)
        session.commit()
        session.refresh(book)
        session.add(BookMember(book_id=book.id, user_id=user.id, role="owner"))
        session.commit()
        return {"id": book.id, "title": book.title, "status": book.status}


@mcp_server.tool()
def get_book(ctx: Context, book_id: int) -> dict:
    """Get a book's details and its chapter tree (two levels)."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        book, membership = _resolve_book(session, book_id, user)
        return {
            "id": book.id, "title": book.title, "description": book.description,
            "status": book.status, "my_role": membership.role,
            "chapters": _chapter_tree(session, book_id),
        }


# ---------- tools: chapters ----------
@mcp_server.tool()
def create_chapter(
    ctx: Context, book_id: int, title: str, parent_id: Optional[int] = None,
) -> dict:
    """Create a chapter (or sub-chapter). Max two levels: a sub-chapter's parent
    must be a top-level chapter."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        _, membership = _resolve_book(session, book_id, user)
        _require_edit(membership)
        if parent_id is not None:
            parent = session.get(Chapter, parent_id)
            if parent is None or parent.book_id != book_id or parent.deleted_at is not None:
                raise ToolError("父章節不存在")
            if parent.parent_id is not None:
                raise ToolError("最多支援兩層結構")
        siblings = session.exec(
            select(Chapter).where(
                Chapter.book_id == book_id,
                Chapter.parent_id == parent_id if parent_id is not None
                else Chapter.parent_id.is_(None),
                Chapter.deleted_at.is_(None),
            )
        ).all()
        order_index = max((c.order_index for c in siblings), default=-1) + 1
        chapter = Chapter(
            book_id=book_id, parent_id=parent_id, title=title.strip() or "未命名",
            order_index=order_index,
        )
        session.add(chapter)
        session.commit()
        session.refresh(chapter)
        return {"id": chapter.id, "title": chapter.title, "parent_id": chapter.parent_id}


@mcp_server.tool()
def rename_chapter(ctx: Context, chapter_id: int, title: str) -> dict:
    """Rename a chapter."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        chapter, _, membership = _resolve_chapter(session, chapter_id, user)
        _require_edit(membership)
        if not title.strip():
            raise ToolError("章節標題不可為空")
        chapter.title = title.strip()
        chapter.updated_at = utcnow()
        session.add(chapter)
        session.commit()
        return {"id": chapter.id, "title": chapter.title}


@mcp_server.tool()
def set_chapter_status(ctx: Context, chapter_id: int, status: str) -> dict:
    """Set a chapter's status: not_started / writing / reviewing / done."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        chapter, _, membership = _resolve_chapter(session, chapter_id, user)
        _require_edit(membership)
        if status not in CHAPTER_STATUSES:
            raise ToolError(f"狀態無效，須為 {sorted(CHAPTER_STATUSES)} 其一")
        chapter.status = status
        chapter.updated_at = utcnow()
        session.add(chapter)
        session.commit()
        return {"id": chapter.id, "status": chapter.status}


@mcp_server.tool()
def delete_chapter(ctx: Context, chapter_id: int) -> dict:
    """Soft-delete a chapter (and its sub-chapters)."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        chapter, _, membership = _resolve_chapter(session, chapter_id, user)
        _require_edit(membership)
        ts = utcnow()
        chapter.deleted_at = ts
        session.add(chapter)
        children = session.exec(
            select(Chapter).where(
                Chapter.parent_id == chapter_id, Chapter.deleted_at.is_(None)
            )
        ).all()
        for k in children:
            k.deleted_at = ts
            session.add(k)
        session.commit()
        return {"id": chapter_id, "deleted": True, "children_deleted": len(children)}


# ---------- tools: content ----------
@mcp_server.tool()
def get_chapter_content(ctx: Context, chapter_id: int) -> dict:
    """Read a chapter's content as plain text, with word count and version."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        _resolve_chapter(session, chapter_id, user)
        content = _get_or_create_content(session, chapter_id)
        return {
            "chapter_id": chapter_id,
            "text": extract_text(content.content_json).strip(),
            "word_count": content.word_count,
            "version": content.version,
            "updated_at": content.updated_at,
        }


@mcp_server.tool()
def update_chapter_content(ctx: Context, chapter_id: int, text: str) -> dict:
    """Replace a chapter's content with the given plain text (paragraphs split on
    newlines). Bumps the version and saves a version snapshot."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        _, _, membership = _resolve_chapter(session, chapter_id, user)
        _require_edit(membership)
        content = _get_or_create_content(session, chapter_id)
        content_str = json.dumps(_text_to_doc(text), ensure_ascii=False)
        wc = count_words(content_str)
        content.content_json = content_str
        content.word_count = wc
        content.version += 1
        content.updated_by = user.id
        content.updated_at = utcnow()
        session.add(content)
        session.add(ContentVersion(
            chapter_id=chapter_id, version=content.version,
            content_json=content_str, word_count=wc, editor_id=user.id,
        ))
        session.commit()
        session.refresh(content)
        _prune_versions(session, chapter_id)
        return {"chapter_id": chapter_id, "version": content.version, "word_count": wc}


# ---------- tools: review comments ----------
@mcp_server.tool()
def list_comments(ctx: Context, chapter_id: int) -> dict:
    """List review comments on a chapter as threads (top-level comments with their
    single-level replies), plus the count of unresolved threads. Read-only."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        _resolve_chapter(session, chapter_id, user)
        rows = session.exec(
            select(Comment).where(
                Comment.chapter_id == chapter_id, Comment.deleted_at.is_(None)
            )
        ).all()
        rows.sort(key=lambda c: c.created_at)
        names = {}
        for uid in {c.author_id for c in rows}:
            u = session.get(User, uid)
            names[uid] = u.name if u else "?"

        def fmt(c: Comment) -> dict:
            return {
                "id": c.id, "author": names.get(c.author_id, "?"),
                "body": c.body, "image_url": c.image_url,
                "resolved": c.resolved, "created_at": c.created_at,
            }

        replies: dict[int, list] = {}
        for c in rows:
            if c.parent_id is not None:
                replies.setdefault(c.parent_id, []).append(c)
        threads = []
        for t in [c for c in rows if c.parent_id is None]:
            node = fmt(t)
            node["replies"] = [fmt(r) for r in replies.get(t.id, [])]
            threads.append(node)
        unresolved = sum(1 for c in rows if c.parent_id is None and not c.resolved)
        return {"chapter_id": chapter_id, "unresolved": unresolved,
                "total": len(threads), "comments": threads}
