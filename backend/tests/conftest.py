"""Shared fixtures. Tests never touch the real database or the network."""
from __future__ import annotations

import os
import tempfile

import pytest

# Point every test at a throwaway SQLite file before app modules read settings.
_TMP = tempfile.mkdtemp(prefix="jobscrap-tests-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/test.db")

from app.domain.entities import RawJob  # noqa: E402
from app.domain.enums import EmploymentType, HiringRegion  # noqa: E402


@pytest.fixture
def raw():
    """A minimal RawJob builder; pass any field to override."""

    def make(**kw) -> RawJob:
        base = dict(
            source="greenhouse",
            source_job_id="1",
            url="https://example.test/jobs/1",
            title="Software Engineer Intern",
            company="Acme",
            description=None,
            location_raw="Bengaluru, India",
            hiring_regions_hint=[HiringRegion.INDIA],
            employment_type_hint=EmploymentType.INTERNSHIP,
            raw_payload={},
        )
        base.update(kw)
        return RawJob(**base)

    return make


@pytest.fixture
def session():
    """A session against an empty schema, rolled back per test."""
    from app.db.session import get_engine, get_sessionmaker, init_db
    from app.db.models import Base

    init_db()
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
