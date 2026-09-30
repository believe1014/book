"""MCP server exposing the 協作撰書系統 as tools (remote streamable HTTP).

Mounted onto the FastAPI app at /mcp so it ships with the same deployment.
Every tool authenticates with a Bearer token: either the web JWT
(POST /api/auth/login, short-lived) or a personal access token `kkb_...`
(POST /api/tokens, long-lived — preferred for MCP clients). Permission checks reuse the app's role matrix, so an MCP
caller can only touch books they are a member of.
"""
import json
import re
from typing import Annotated, Optional

from pydantic import Field
from sqlmodel import Session, select

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from starlette.concurrency import run_in_threadpool
from mcp.server.transport_security import TransportSecuritySettings

try:  # clean error messages to the client when available
    from mcp.server.fastmcp.exceptions import ToolError
except Exception:  # pragma: no cover - fallback for older SDKs
    ToolError = ValueError

from .config import settings
from .database import engine
from .deps import EDIT_ROLES, user_from_token
from .models import (
    Book, BookMember, Chapter, ChapterContent, Comment, ContentVersion, User, utcnow,
)
from .routers.content import _get_or_create_content, _prune_versions
from .services.wordcount import count_words

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

class KkbookTokenVerifier:
    """SDK TokenVerifier:JWT / PAT / OAuth access 都走 deps.user_from_token。

    無效回 None → SDK RequireAuthMiddleware 回 401 + WWW-Authenticate(觸發客戶端 OAuth)。
    """

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        def _user_id() -> Optional[int]:
            with Session(engine) as session:
                user = user_from_token(session, token.strip())
                return user.id if user else None

        uid = await run_in_threadpool(_user_id)
        return AccessToken(token=token, client_id=f"user:{uid}", scopes=[], subject=str(uid)) if uid else None


_base_url = settings.public_base_url.rstrip("/")

mcp_server = FastMCP(
    "協作撰書系統",
    instructions=(
        "Tools to manage collaborative book-writing projects: list/create books, "
        "manage chapters (max two levels), and read/write chapter content. "
        "Authenticate with a Bearer JWT from POST /api/auth/login or a personal access token (kkb_...) from POST /api/tokens."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_transport_security,
    auth=AuthSettings(issuer_url=_base_url, resource_server_url=f"{_base_url}/mcp/"),
    token_verifier=KkbookTokenVerifier(),
)

CHAPTER_STATUSES = {"not_started", "writing", "reviewing", "done"}


# ---------- auth & resolution helpers ----------
def _current_user(ctx: Context, session: Session) -> User:
    req = getattr(ctx.request_context, "request", None)
    authz = req.headers.get("authorization", "") if req is not None else ""
    if not authz.lower().startswith("bearer "):
        raise ToolError("未授權：請在 Authorization 標頭帶入 Bearer <token>")
    user = user_from_token(session, authz.split(" ", 1)[1].strip())
    if user is None:
        raise ToolError("未授權：token 無效、已過期或已撤銷")
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


# ---------- content <-> Markdown ----------
# The web editor (frontend/src/components/RichTextEditor.jsx) persists a
# ProseMirror-style doc limited to: paragraph, heading (level 1-3), blockquote,
# bulletList / orderedList > listItem (nestable), codeBlock(language), image;
# text marks bold / italic / underline. Links, inline code, hr, tables are NOT
# in that schema, so on write their Markdown stays as literal text.
#
# Line model (kept from the old plain-text format): every line is one block,
# an empty line is an empty paragraph. So a read -> write round-trip is exact.
_FENCE_OPEN_RE = re.compile(r"^```([A-Za-z0-9_+-]*)\s*$")
_FENCE_CLOSE = "```"
_HEADING_RE = re.compile(r"^(#{1,3}) (.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_LIST_RE = re.compile(r"^( *)([-*+]|\d+\.) (.*)$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)\)")
_ESC = set("\\*<!#>-+.`")
_LIST_TYPES = ("bulletList", "orderedList")


# ----- doc -> Markdown -----
def _esc_inline(s: str) -> str:
    out = []
    for i, ch in enumerate(s):
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if (
            ch == "*"
            or (ch == "\\" and nxt in _ESC)
            or (ch == "!" and nxt == "[")
            or (ch == "<" and (s.startswith("<u>", i) or s.startswith("</u>", i)))
        ):
            out.append("\\")
        out.append(ch)
    return "".join(out)


def _esc_line_start(line: str) -> str:
    """Escape a paragraph line that would otherwise parse as another block."""
    m = _LIST_RE.match(line)
    if m:
        pos = len(m.group(1)) + len(m.group(2)) - 1  # bullet char or the '.'
        return line[:pos] + "\\" + line[pos:]
    if (_HEADING_RE.match(line) or line.startswith(">")
            or _FENCE_OPEN_RE.match(line.strip())):
        k = len(line) - len(line.lstrip())
        return line[:k] + "\\" + line[k:]
    return line


def _inline_md(nodes, skip_lists: bool = False) -> str:
    out = []
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        t = n.get("type")
        if t == "text":
            s = _esc_inline(n.get("text") or "")
            if not s:
                continue
            marks = {m.get("type") for m in n.get("marks") or [] if isinstance(m, dict)}
            if "bold" in marks and "italic" in marks:
                s = f"***{s}***"
            elif "bold" in marks:
                s = f"**{s}**"
            elif "italic" in marks:
                s = f"*{s}*"
            if "underline" in marks:
                s = f"<u>{s}</u>"
            out.append(s)
        elif t == "image":
            a = n.get("attrs") or {}
            # ponytail: alt with ']' / src with ')' or spaces not escaped; editor URLs never have them
            out.append(f"![{a.get('alt') or ''}]({a.get('src') or ''})")
        elif t in _LIST_TYPES and skip_lists:
            continue  # the caller emits nested lists as their own lines
        else:  # unknown inline/wrapper node (e.g. a TipTap paragraph in a li): keep its text
            out.append(_inline_md(n.get("content")))
    return "".join(out)


def _list_md(node: dict, depth: int, lines: list) -> None:
    ordered = node.get("type") == "orderedList"
    k = 0
    for li in node.get("content") or []:
        if not isinstance(li, dict):
            continue
        kids = li.get("content") if li.get("type") == "listItem" else [li]
        text = _inline_md(kids, skip_lists=True)
        if li.get("type") == "text" and not text.strip():
            continue  # whitespace text between <li>s
        k += 1
        lines.append("  " * depth + (f"{k}. " if ordered else "- ") + text)
        for c in kids or []:
            if isinstance(c, dict) and c.get("type") in _LIST_TYPES:
                _list_md(c, depth + 1, lines)


def _doc_to_md(content_json: str) -> str:
    try:
        doc = json.loads(content_json) if content_json else {}
    except (json.JSONDecodeError, TypeError):
        return ""
    lines: list[str] = []
    for n in (doc.get("content") if isinstance(doc, dict) else None) or []:
        if not isinstance(n, dict):
            continue
        t = n.get("type")
        if t == "codeBlock":
            a = n.get("attrs") or {}
            body = "".join(c.get("text", "") for c in n.get("content") or []
                           if isinstance(c, dict) and c.get("type") == "text")
            # ponytail: a body line that is exactly ``` would end the block early on write (same as before)
            lines.append(f"```{a.get('language') or a.get('lang') or ''}\n{body}\n```")
        elif t == "heading":
            level = (n.get("attrs") or {}).get("level") or 2
            lines.append("#" * int(level) + " " + _inline_md(n.get("content")))
        elif t == "blockquote":
            lines.append("> " + _inline_md(n.get("content"), skip_lists=True))
            for c in n.get("content") or []:  # nested list in a quote: keep its text
                if isinstance(c, dict) and c.get("type") in _LIST_TYPES:
                    sub: list[str] = []
                    _list_md(c, 0, sub)
                    lines.extend("> " + s for s in sub)
        elif t in _LIST_TYPES:
            _list_md(n, 0, lines)
        elif t == "listItem":
            _list_md({"type": "bulletList", "content": [n]}, 0, lines)
        else:  # paragraph, image, and any unknown block: its inline text as a line
            lines.append(_esc_line_start(_inline_md(n.get("content") if t != "image" else [n])))
    return "\n".join(lines)


# ----- Markdown -> doc -----
def _find(s: str, tok: str, i: int) -> int:
    """Index of the next unescaped ``tok`` in s at/after i, or -1."""
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in _ESC:
            i += 2
            continue
        if s.startswith(tok, i):
            return i
        i += 1
    return -1


def _parse_inline(s: str, marks: tuple = ()) -> list:
    out: list = []
    buf: list[str] = []

    def flush():
        if buf:
            node = {"type": "text", "text": "".join(buf)}
            if marks:
                order = ("bold", "italic", "underline")
                node["marks"] = [{"type": m} for m in order if m in marks]
            if out and out[-1].get("type") == "text" and out[-1].get("marks") == node.get("marks"):
                out[-1]["text"] += node["text"]
            else:
                out.append(node)
            buf.clear()

    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] in _ESC:
            buf.append(s[i + 1])
            i += 2
            continue
        img = _IMAGE_RE.match(s, i) if ch == "!" else None
        if img:
            flush()
            out.append({"type": "image", "attrs": {"src": img.group(2), "alt": img.group(1)}})
            i = img.end()
            continue
        for open_, close, add in (("<u>", "</u>", ("underline",)), ("***", "***", ("bold", "italic")),
                                  ("**", "**", ("bold",)), ("*", "*", ("italic",))):
            if s.startswith(open_, i):
                j = _find(s, close, i + len(open_))
                if j > i + len(open_):
                    flush()
                    for n in _parse_inline(s[i + len(open_):j], marks + add):
                        if (n.get("type") == "text" and out and out[-1].get("type") == "text"
                                and out[-1].get("marks") == n.get("marks")):
                            out[-1]["text"] += n["text"]
                        else:
                            out.append(n)
                    i = j + len(close)
                    break
        else:  # no delimiter matched: literal char (unmatched * or <u> stays as text)
            buf.append(ch)
            i += 1
    flush()
    return out


def _block(ntype: str, text: str, **attrs) -> dict:
    node: dict = {"type": ntype}
    if attrs:
        node["attrs"] = attrs
    content = _parse_inline(text)
    if content:
        node["content"] = content
    return node


def _parse_list(lines: list, i: int, indent: int) -> tuple:
    ordered = _LIST_RE.match(lines[i]).group(2)[0].isdigit()
    items: list = []
    while i < len(lines):
        m = _LIST_RE.match(lines[i])
        if not m or len(m.group(1)) < indent:
            break
        if len(m.group(1)) > indent:  # nested list belongs to the previous item
            sub, i = _parse_list(lines, i, len(m.group(1)))
            if not items:
                items.append({"type": "listItem"})
            items[-1].setdefault("content", []).append(sub)
            continue
        if m.group(2)[0].isdigit() != ordered:
            break  # switching - / 1. starts a new list
        items.append(_block("listItem", m.group(3)))
        i += 1
    return {"type": "orderedList" if ordered else "bulletList", "content": items}, i


def _md_to_doc(text: str) -> dict:
    """Parse the Markdown subset above into the editor's doc JSON. Anything
    outside the subset becomes a plain paragraph with its text intact."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    nodes: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _FENCE_OPEN_RE.match(line.strip())
        if m:
            j = i + 1
            while j < len(lines) and lines[j].strip() != _FENCE_CLOSE:
                j += 1
            if j < len(lines):  # matched fence pair -> one code block
                body = "\n".join(lines[i + 1:j])
                node = {"type": "codeBlock", "attrs": {"language": m.group(1)}}
                if body:
                    node["content"] = [{"type": "text", "text": body}]
                nodes.append(node)
                i = j + 1
                continue
            # no closing fence: ordinary text below
        if _LIST_RE.match(line):
            node, i = _parse_list(lines, i, len(_LIST_RE.match(line).group(1)))
            nodes.append(node)
            continue
        h = _HEADING_RE.match(line)
        q = _QUOTE_RE.match(line)
        if h:
            nodes.append(_block("heading", h.group(2), level=len(h.group(1))))
        elif q:
            nodes.append(_block("blockquote", q.group(1)))
        else:
            nodes.append(_block("paragraph", line))
        i += 1
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
def get_chapter_content(
    ctx: Context,
    chapter_id: Annotated[int, Field(description="Chapter id (from get_book).")],
) -> dict:
    """Read a chapter's content as Markdown, with word count and version.

    Any book member can read. `text` uses the editor's Markdown subset, one
    block per line (an empty line is an empty paragraph):
    `# ` / `## ` / `### ` headings, `> ` quote, `- ` bullet and `1. ` numbered
    lists (nest with 2-space indent), ```lang fenced code blocks (e.g.
    Mermaid), `**bold**`, `*italic*`, `<u>underline</u>`, `![alt](src)` images,
    and `\\` escapes for literal markup characters. Pass the text back to
    update_chapter_content unchanged apart from your edits and the formatting
    is preserved exactly."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        _resolve_chapter(session, chapter_id, user)
        content = _get_or_create_content(session, chapter_id)
        return {
            "chapter_id": chapter_id,
            "text": _doc_to_md(content.content_json).strip("\n"),
            "word_count": content.word_count,
            "version": content.version,
            "updated_at": content.updated_at,
        }


@mcp_server.tool()
def update_chapter_content(
    ctx: Context,
    chapter_id: Annotated[int, Field(description="Chapter id (from get_book).")],
    text: Annotated[str, Field(description=(
        "The chapter's full new content as Markdown (same subset that "
        "get_chapter_content returns). Plain text also works: each line "
        "becomes a paragraph."
    ))],
) -> dict:
    """Replace a chapter's entire content with the given Markdown. Requires the
    owner or editor role on the book. Bumps the version and saves a version
    snapshot (restorable from the web app's version history).

    Supported, one block per line (an empty line becomes an empty paragraph,
    so do not add blank lines between paragraphs unless you want the gap):
    `#`/`##`/`###` headings, `> ` quote (one line per quote), `- ` / `* `
    bullet and `1. ` numbered lists (nest with 2-space indent), ```lang fenced
    code blocks (```mermaid renders a diagram), `**bold**`, `*italic*`,
    `***both***`, `<u>underline</u>`, `![alt](src)` images; `\\` escapes a
    markup character. The editor has no links, inline code, horizontal rules,
    tables or h4+: such syntax is kept as literal text, never dropped.
    To edit safely, get_chapter_content first and change only what you need."""
    with Session(engine) as session:
        user = _current_user(ctx, session)
        _, _, membership = _resolve_chapter(session, chapter_id, user)
        _require_edit(membership)
        content = _get_or_create_content(session, chapter_id)
        content_str = json.dumps(_md_to_doc(text), ensure_ascii=False)
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
