"""SQLModel data models — maps directly to spec §4.2 table definitions.

Time columns are stored as ISO8601 UTC text (spec §4). Rich-text content is
stored as a JSON string in TEXT columns.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> str:
    """ISO8601 UTC timestamp string (spec §4 time format)."""
    return datetime.now(timezone.utc).isoformat()


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    name: str
    avatar_url: Optional[str] = None
    created_at: str = Field(default_factory=utcnow)


class Book(SQLModel, table=True):
    __tablename__ = "books"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    description: Optional[str] = None
    cover_url: Optional[str] = None
    status: str = Field(default="draft")  # draft/writing/completed/archived
    tags: Optional[str] = None  # JSON array string
    word_count_goal: Optional[int] = None
    owner_id: int = Field(foreign_key="users.id", index=True)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    deleted_at: Optional[str] = Field(default=None, index=True)  # soft delete


class BookMember(SQLModel, table=True):
    __tablename__ = "book_members"
    # UNIQUE(book_id, user_id) — one role per user per book (spec §4.2)

    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="books.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    role: str  # owner/editor/reviewer/viewer
    created_at: str = Field(default_factory=utcnow)


class Invitation(SQLModel, table=True):
    __tablename__ = "invitations"

    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="books.id", index=True)
    email: str = Field(index=True)
    role: str
    status: str = Field(default="pending")  # pending/accepted/revoked
    token: str = Field(unique=True, index=True)
    created_at: str = Field(default_factory=utcnow)


class Chapter(SQLModel, table=True):
    __tablename__ = "chapters"

    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="books.id", index=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="chapters.id", index=True)
    title: str
    order_index: int
    status: str = Field(default="not_started")  # not_started/writing/reviewing/done
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    deleted_at: Optional[str] = Field(default=None, index=True)


class ChapterContent(SQLModel, table=True):
    __tablename__ = "chapter_contents"

    id: Optional[int] = Field(default=None, primary_key=True)
    chapter_id: int = Field(foreign_key="chapters.id", unique=True, index=True)
    content_json: str = Field(default="{}")
    word_count: int = Field(default=0)
    version: int = Field(default=1)
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_at: str = Field(default_factory=utcnow)


class ContentVersion(SQLModel, table=True):
    __tablename__ = "content_versions"

    id: Optional[int] = Field(default=None, primary_key=True)
    chapter_id: int = Field(foreign_key="chapters.id", index=True)
    version: int
    content_json: str
    word_count: int
    editor_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: str = Field(default_factory=utcnow)


class MediaAsset(SQLModel, table=True):
    __tablename__ = "media_assets"

    id: Optional[int] = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="books.id", index=True)
    type: str  # image/video/audio/file/link
    url: str
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    ref_count: int = Field(default=0)
    uploaded_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: str = Field(default_factory=utcnow)
