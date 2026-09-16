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
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate(engine)


def _migrate(engine: Engine) -> None:
    """Bring an existing jobs table to the current schema. Idempotent.

    create_all() only creates missing tables, never columns. This adds the
    lifecycle columns and, once, drops the description column together with
    the filtered-out rows that were kept as inactive bookkeeping - then
    compacts the table so Neon actually returns the space.
    """
    from sqlalchemy import inspect, text

    cols = {c["name"] for c in inspect(engine).get_columns("jobs")}
    compact = False
    with engine.begin() as conn:
        if "scope" not in cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN scope VARCHAR(200) NOT NULL DEFAULT ''"))
        if "missed_runs" not in cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN missed_runs INTEGER NOT NULL DEFAULT 0"))
        if "fingerprint" not in cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN fingerprint VARCHAR(40) NOT NULL DEFAULT ''"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_source_scope ON jobs (source, scope)"))
        if "description" in cols:
            # every stored job must satisfy the filters; inactive rows never did
            conn.execute(text("DELETE FROM jobs WHERE is_active = :f"), {"f": False})
            conn.execute(text("ALTER TABLE jobs DROP COLUMN description"))
            compact = True
    if compact and engine.dialect.name == "postgresql":
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("VACUUM FULL jobs"))
            conn.execute(text("VACUUM FULL extraction_cache"))


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
