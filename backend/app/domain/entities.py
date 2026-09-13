"""Domain entities.

RawJob  - what a connector emits (source-shaped, permissive)
Job     - normalized and filterable (what the DB and API speak)
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import (
    Confidence,
    EmploymentType,
    ExtractionSource,
    HiringRegion,
    RoleCategory,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Some boards emit nonsense dates - a SmartRecruiters row came back dated
# 2998 days ago and a Mindtickle one 3892. Past this bound we treat the date
# as absent rather than reporting an age nobody would believe.
MAX_PLAUSIBLE_AGE_DAYS = 400


class RawJob(BaseModel):
    """Connector output. Deliberately lenient - normalization happens later."""

    model_config = ConfigDict(extra="allow")

    source: str
    source_job_id: str
    url: str
    title: str
    company: str

    description: str | None = None
    location_raw: str | None = None
    tags: list[str] = Field(default_factory=list)
    salary_raw: str | None = None
    posted_at: datetime | None = None

    # Sources that state region honestly (Himalayas, Remotive) should not have
    # that thrown away and re-derived from prose later.
    hiring_regions_hint: list[HiringRegion] = Field(default_factory=list)
    employment_type_hint: EmploymentType = EmploymentType.UNKNOWN

    raw_payload: dict = Field(default_factory=dict)

    @field_validator("posted_at")
    @classmethod
    def _ensure_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @property
    def description_hash(self) -> str:
        """Cache key for LLM extraction - we pay once per unique posting, ever."""
        basis = f"{self.title}\x00{self.company}\x00{self.description or ''}"
        return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()


class Extraction(BaseModel):
    """Structured output of the enrichment pass (rules or LLM)."""

    min_yoe: int | None = None
    max_yoe: int | None = None
    is_new_grad: bool = False
    grad_year: int | None = None

    hiring_regions: list[HiringRegion] = Field(default_factory=list)
    work_auth_required: bool | None = None
    visa_sponsorship: bool | None = None

    employment_type: EmploymentType = EmploymentType.UNKNOWN
    role_category: RoleCategory = RoleCategory.OTHER
    tech_stack: list[str] = Field(default_factory=list)

    source: ExtractionSource = ExtractionSource.NONE
    confidence: Confidence = Confidence.LOW
    notes: str | None = None


class Job(BaseModel):
    """Normalized posting - the shape the DB stores and the API returns."""

    source: str
    source_job_id: str
    url: str
    description_hash: str

    title: str
    title_normalized: str
    company: str
    company_normalized: str

    description: str | None = None
    location_raw: str | None = None

    # Which ATS the employer uses, when detectable. Drives the company->ATS
    # map: a posting found via an aggregator can be re-fetched from the
    # employer's own board next time, with structured fields.
    ats: str | None = None

    role_category: RoleCategory = RoleCategory.OTHER
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    tech_stack: list[str] = Field(default_factory=list)

    # None means "the posting says nothing", which is NOT the same as 0.
    min_yoe: int | None = None
    max_yoe: int | None = None
    is_new_grad: bool = False
    grad_year: int | None = None
    yoe_source: ExtractionSource = ExtractionSource.NONE

    is_remote: bool = False
    hiring_regions: list[HiringRegion] = Field(default_factory=list)
    work_auth_required: bool | None = None
    visa_sponsorship: bool | None = None
    region_source: ExtractionSource = ExtractionSource.NONE
    region_confidence: Confidence = Confidence.LOW

    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None

    posted_at: datetime | None = None
    first_seen_at: datetime = Field(default_factory=_utcnow)
    last_seen_at: datetime = Field(default_factory=_utcnow)
    is_active: bool = True

    match_score: float = 0.0
    needs_enrichment: bool = True
    link_status: str = ""
    link_checked_at: datetime | None = None
    raw_payload: dict = Field(default_factory=dict)

    @property
    def effective_posted_at(self) -> datetime:
        """Real posting date when the source gives one, else first sighting.

        posted_at is present on every source we currently use, but it stays a
        fallback because first_seen_at is the one date we control.
        """
        return self.posted_at or self.first_seen_at

    @property
    def age_days(self) -> int | None:
        """Days since posting, or None when the date is missing or absurd."""
        posted = self.effective_posted_at
        if posted is None:
            return None
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        age = max(0, (_utcnow() - posted).days)
        return None if age > MAX_PLAUSIBLE_AGE_DAYS else age

    @property
    def reachable_from_india(self) -> bool:
        if self.work_auth_required is True and self.visa_sponsorship is not True:
            return HiringRegion.INDIA in self.hiring_regions
        return any(r.reachable_from_india for r in self.hiring_regions)

    def apply(self, ex: Extraction) -> Job:
        """Fold an extraction into this job, never downgrading known values.

        Rules run first and are cheap but shallow; the LLM fills gaps. A
        confident earlier answer must not be overwritten by a vaguer later one.
        """
        data = self.model_dump()

        if self.min_yoe is None and ex.min_yoe is not None:
            data["min_yoe"] = ex.min_yoe
            data["max_yoe"] = ex.max_yoe
            data["yoe_source"] = ex.source
        if ex.is_new_grad:
            data["is_new_grad"] = True
        if self.grad_year is None and ex.grad_year is not None:
            data["grad_year"] = ex.grad_year

        unknown = (not self.hiring_regions) or self.hiring_regions == [
            HiringRegion.UNKNOWN
        ]
        if unknown and ex.hiring_regions:
            data["hiring_regions"] = ex.hiring_regions
            data["region_source"] = ex.source
            data["region_confidence"] = ex.confidence

        if self.work_auth_required is None and ex.work_auth_required is not None:
            data["work_auth_required"] = ex.work_auth_required
        if self.visa_sponsorship is None and ex.visa_sponsorship is not None:
            data["visa_sponsorship"] = ex.visa_sponsorship

        if self.employment_type is EmploymentType.UNKNOWN:
            data["employment_type"] = ex.employment_type

        # The LLM may NOT introduce a role category the config excludes. It
        # previously relabelled "Blockchain Security Engineer" from `other` to
        # `devops`, pulling an out-of-scope role back into scope and silently
        # overriding config.yaml. Rules own role classification; the LLM can
        # only refine a category that is already in scope.
        if self.role_category is not RoleCategory.OTHER and ex.role_category is not RoleCategory.OTHER:
            data["role_category"] = ex.role_category
        if ex.tech_stack:
            data["tech_stack"] = sorted(set(self.tech_stack) | set(ex.tech_stack))

        data["needs_enrichment"] = False
        return Job(**data)


class ConnectorRun(BaseModel):
    """Per-run health record - the silent-zero detector."""

    connector: str
    started_at: datetime
    finished_at: datetime | None = None
    jobs_found: int = 0
    jobs_kept: int = 0
    jobs_new: int = 0
    ok: bool = False
    error: str | None = None

    @property
    def duration_s(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()
