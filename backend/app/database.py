"""Database engine + session management.

Uses a managed PostgreSQL when ``database_url`` is configured (production /
Zeabur, so data survives container redeploys); otherwise falls back to a local
SQLite file with WAL mode for development (spec §8.1).
"""
import logging

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

logger = logging.getLogger("uvicorn.error")


def _normalize_pg_url(url: str) -> str:
    """Use the psycopg (v3) driver for postgres URLs from the platform."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _valid_database_url(raw: str | None) -> str | None:
    """Return a usable DB URL, or None to fall back to SQLite.

    Guards against an unresolved platform variable reference (e.g. a literal
    "${POSTGRES_CONNECTION_STRING}" when the reference didn't resolve), which
    would otherwise crash the process on startup with an unparseable URL.
    """
    if not raw:
        return None
    url = raw.strip()
    if not url:
        return None
    if "${" in url or not url.startswith(("postgres://", "postgresql://", "sqlite")):
        logger.error(
            "DATABASE_URL is set but not a valid DB URL (%r); falling back to "
            "local SQLite. Data will NOT persist across redeploys until this is "
            "fixed — set DATABASE_URL to the actual connection string.", url,
        )
        return None
    return url


_db_url = _valid_database_url(settings.database_url)

if _db_url:
    engine = create_engine(
        _normalize_pg_url(_db_url),
        echo=False,
        pool_pre_ping=True,  # drop stale connections (managed DB may recycle them)
    )
    logger.info("Database: using configured URL (%s)", engine.dialect.name)
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
    _ensure_comment_quote_column()


def _ensure_comment_quote_column() -> None:
    """Add comments.quote on pre-existing DBs (create_all never alters tables).

    ponytail: single hand-rolled ALTER, no migration tool — add Alembic only if
    schema churn grows beyond a handful of columns. Works on SQLite + Postgres.
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("comments")}
    if "quote" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE comments ADD COLUMN quote TEXT"))


def get_session():
    with Session(engine) as session:
        yield session
