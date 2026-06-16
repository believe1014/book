"""Application configuration.

Centralised settings for the 協作撰書系統 backend. Values can be overridden
via environment variables (see pydantic-settings).
"""
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BOOK_", env_file=".env", extra="ignore")

    # Auth / JWT
    jwt_secret: str = "dev-secret-change-me-in-production-please"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24  # spec FR-02: 24h access token

    # Database
    # If set (e.g. a Zeabur-managed PostgreSQL), it takes precedence over the
    # local SQLite file so data survives container redeploys. Accepts either the
    # unprefixed DATABASE_URL (Zeabur default) or BOOK_DATABASE_URL.
    database_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "BOOK_DATABASE_URL"),
    )
    db_path: str = str(BASE_DIR / "app.db")

    # Storage (spec §6.5: local ./storage)
    storage_dir: str = str(BASE_DIR / "storage")

    # Built frontend SPA (single-container deploy); served if the dir exists
    frontend_dir: str = str(BASE_DIR.parent / "frontend" / "dist")

    # Media limits (spec FR-82)
    max_file_size: int = 50 * 1024 * 1024  # 50MB per file
    book_quota: int = 1024 * 1024 * 1024  # 1GB per book

    # Soft-delete window (spec FR-15)
    restore_window_days: int = 30

    # Lock idle release (spec FR-45)
    lock_idle_seconds: int = 60

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # MCP endpoint Host-header allowlist (comma-separated, e.g.
    # "kkbook.zeabur.app"). If empty, the MCP transport's DNS-rebinding
    # protection is disabled so it works behind any domain — safe here because
    # every MCP tool is authenticated with a Bearer JWT.
    mcp_allowed_hosts: str = ""


settings = Settings()
