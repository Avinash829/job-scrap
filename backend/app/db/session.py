"""Engine and session management.

One engine per process. SQLite gets WAL + a longer busy timeout so the CLI
and the API can hold it open at the same time; Postgres gets pre-ping and a
modest pool, sized for a free-tier instance rather than a busy server.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models import Base


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = get_settings().database_url
    is_sqlite = url.startswith("sqlite")

    if is_sqlite:
        engine = create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")   # concurrent read + write
            cur.execute("PRAGMA synchronous=NORMAL")  # fast, still crash-safe
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine

    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,   # Neon autosuspends; stale connections must be caught
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope - commits on success, rolls back on error."""
    s = get_sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.close()
