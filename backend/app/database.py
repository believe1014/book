"""Database engine + session management.

Uses a managed PostgreSQL when ``database_url`` is configured (production /
Zeabur, so data survives container redeploys); otherwise falls back to a local
SQLite file with WAL mode for development (spec §8.1).
"""
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import settings


def _normalize_pg_url(url: str) -> str:
    """Use the psycopg (v3) driver for postgres URLs from the platform."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


if settings.database_url:
    engine = create_engine(
        _normalize_pg_url(settings.database_url),
        echo=False,
        pool_pre_ping=True,  # drop stale connections (managed DB may recycle them)
    )
else:
    # check_same_thread=False so the engine can be used across FastAPI threads.
    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode and foreign keys on every connection (SQLite only)."""
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    # Import models so SQLModel registers all tables before create_all.
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
