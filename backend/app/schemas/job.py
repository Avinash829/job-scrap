"""API request/response DTOs.

Separate from domain entities on purpose: the wire format can stay stable
while the internal model changes. Job descriptions are not stored, so there is
no summary or description field.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities import Job
from app.domain.enums import (
    Confidence,
    EmploymentType,
    ExtractionSource,
    HiringRegion,
    RoleCategory,
)

class JobSummary(BaseModel):
    """List-view payload - deliberately light."""

    model_config = ConfigDict(from_attributes=True)

    source: str
    source_job_id: str
    url: str
    title: str
    company: str
    location_raw: str | None
    ats: str | None
    role_category: RoleCategory
    employment_type: EmploymentType
    tech_stack: list[str]
    min_yoe: int | None
    max_yoe: int | None
    is_new_grad: bool
    grad_year: int | None
    is_remote: bool
    hiring_regions: list[HiringRegion]
    work_auth_required: bool | None
    visa_sponsorship: bool | None
    region_confidence: Confidence
    region_source: ExtractionSource
    reachable_from_india: bool
    match_score: float
    match_reasons: list[str] = Field(default_factory=list)
    yc_batch: str | None = None
    team_size: int | None = None
    # stated stipend or salary, as written ("₹25,000/month", "₹12-15 LPA")
    pay: str | None = None
    posted_at: datetime | None
    first_seen_at: datetime
    age_days: int | None = None
    link_status: str = ""

    @classmethod
    def from_domain(cls, job: Job) -> JobSummary:
        return cls(
            source=job.source,
            source_job_id=job.source_job_id,
            url=job.url,
            title=job.title,
            company=job.company,
            location_raw=job.location_raw,
            ats=job.ats,
            role_category=job.role_category,
            employment_type=job.employment_type,
            tech_stack=job.tech_stack,
            min_yoe=job.min_yoe,
            max_yoe=job.max_yoe,
            is_new_grad=job.is_new_grad,
            grad_year=job.grad_year,
            is_remote=job.is_remote,
            hiring_regions=job.hiring_regions,
            work_auth_required=job.work_auth_required,
            visa_sponsorship=job.visa_sponsorship,
            region_confidence=job.region_confidence,
            region_source=job.region_source,
            reachable_from_india=job.reachable_from_india,
            match_score=job.match_score,
            match_reasons=job.match_reasons,
            yc_batch=(job.raw_payload or {}).get("yc_batch") or None,
            team_size=(job.raw_payload or {}).get("team_size"),
            pay=(job.raw_payload or {}).get("pay") or None,
            posted_at=job.posted_at,
            first_seen_at=job.first_seen_at,
            age_days=job.age_days,
            link_status=job.link_status,
        )


class JobDetail(JobSummary):
    """Detail view - same fields as the listing (descriptions aren't stored)."""


class JobPage(BaseModel):
    items: list[JobSummary]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class ConnectorHealth(BaseModel):
    connector: str
    last_run: datetime
    ok: bool
    jobs_found: int
    jobs_kept: int
    jobs_new: int
    error: str | None = None


class StatsResponse(BaseModel):
    total: int
    active: int
    pending_enrichment: int
    reachable_from_india: int
    by_source: dict[str, int]
    by_role: dict[str, int]
    connectors: list[ConnectorHealth] = Field(default_factory=list)


class FacetsResponse(BaseModel):
    """Values the UI needs to build its filter controls."""

    role_categories: list[str]
    employment_types: list[str]
    regions: list[str]
    sources: list[str]
