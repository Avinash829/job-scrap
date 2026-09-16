"""ORM models.

Typed columns for the fields we filter on; a JSON blob for everything else,
so adding a source never needs a migration. Composite indexes match the
actual query shape the API issues (see repository.search).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="uq_source_job"),
        # the default listing: active + in-scope, newest first
        Index("ix_jobs_active_seen", "is_active", "first_seen_at"),
        # the filtered listing the UI actually sends
        Index("ix_jobs_filter", "is_active", "role_category", "min_yoe"),
        Index("ix_jobs_hash", "description_hash"),
        # closed-job cleanup reads one source's rows per scope
        Index("ix_jobs_source_scope", "source", "scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source: Mapped[str] = mapped_column(String(64), index=True)
    source_job_id: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(Text)
    description_hash: Mapped[str] = mapped_column(String(64))

    title: Mapped[str] = mapped_column(Text)
    title_normalized: Mapped[str] = mapped_column(Text, index=True)
    company: Mapped[str] = mapped_column(Text)
    company_normalized: Mapped[str] = mapped_column(Text, index=True)

    location_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    role_category: Mapped[str] = mapped_column(String(32), index=True)
    employment_type: Mapped[str] = mapped_column(String(32), index=True)
    tech_stack: Mapped[str] = mapped_column(Text, default="[]")

    min_yoe: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    max_yoe: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_new_grad: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    grad_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yoe_source: Mapped[str] = mapped_column(String(16), default="none")

    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    hiring_regions: Mapped[str] = mapped_column(Text, default="[]")
    work_auth_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    visa_sponsorship: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    region_source: Mapped[str] = mapped_column(String(16), default="none")
    region_confidence: Mapped[str] = mapped_column(String(8), default="low")

    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    match_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    # why it scored what it did - a score you can't interrogate is useless
    match_reasons: Mapped[str] = mapped_column(Text, default="[]")
    needs_enrichment: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # apply-link health: 'alive' | 'dead' | 'unknown' | '' (unchecked)
    link_status: Mapped[str] = mapped_column(String(16), default="", index=True)
    link_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_payload: Mapped[str] = mapped_column(Text, default="{}")

    # --- lifecycle ---------------------------------------------------------
    # No description column: the text is read during a scrape to extract
    # skills, experience and region, then discarded. It was ~60% of the table.
    #
    # scope: the board/company this row was fetched under ("greenhouse:stripe").
    # A job is judged closed only when its scope was fully checked in a run.
    scope: Mapped[str] = mapped_column(String(200), default="")
    # consecutive runs a checked scope didn't list this job (search-style
    # sources are deleted at 2, full listings at 1)
    missed_runs: Mapped[int] = mapped_column(Integer, default=0)
    # hash of the stored values - unchanged jobs are not rewritten each run
    fingerprint: Mapped[str] = mapped_column(String(40), default="")


class ExtractionCacheRow(Base):
    """LLM results keyed by description_hash - we pay once per posting, ever.

    This is what keeps the free tier viable: a posting reappearing in daily
    runs for a month costs exactly one API call.
    """

    __tablename__ = "extraction_cache"

    description_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConnectorRunRow(Base):
    __tablename__ = "connector_runs"
    __table_args__ = (Index("ix_runs_connector_started", "connector", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connector: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    jobs_kept: Mapped[int] = mapped_column(Integer, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
