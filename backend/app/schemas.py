"""Pydantic v2 I/O schemas for the API (spec §5)."""
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------- Auth ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=100)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    avatar_url: Optional[str] = None


class AuthOut(BaseModel):
    user: UserOut
    token: str


# ---------- Books ----------
class BookCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    tags: Optional[list[str]] = None


class BookUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    cover_url: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[list[str]] = None
    word_count_goal: Optional[int] = Field(default=None, ge=0)

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in {"draft", "writing", "completed", "archived"}:
            raise ValueError("狀態須為 draft/writing/completed/archived")
        return v


# ---------- Members / Invitations ----------
class MemberInviteIn(BaseModel):
    email: EmailStr
    role: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v not in {"editor", "reviewer", "viewer"}:
            raise ValueError("角色須為 editor/reviewer/viewer")
        return v


class RoleUpdateIn(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v not in {"editor", "reviewer", "viewer"}:
            raise ValueError("角色須為 editor/reviewer/viewer")
        return v


class AcceptInviteIn(BaseModel):
    token: str


# ---------- Chapters ----------
class ChapterCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    parent_id: Optional[int] = None


class ChapterUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in {"not_started", "writing", "reviewing", "done"}:
            raise ValueError("狀態須為 not_started/writing/reviewing/done")
        return v


class ReorderItem(BaseModel):
    id: int
    parent_id: Optional[int] = None
    order_index: int


# ---------- Content ----------
class ContentPatchIn(BaseModel):
    content_json: Any
    base_version: int


class MediaLinkIn(BaseModel):
    url: str
    type: str = "link"
