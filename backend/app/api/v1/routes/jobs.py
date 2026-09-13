"""Job listing endpoints. Read-only - ingestion runs out-of-process.

The API never scrapes: a request here is a sub-100ms indexed query against
rows a scheduled run already collected.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.repository import JobFilter, JobRepository
from app.db.session import get_session
from app.domain.enums import EmploymentType, HiringRegion, RoleCategory
from app.schemas.job import (
    ConnectorHealth,
    FacetsResponse,
    JobDetail,
    JobPage,
    JobSummary,
    StatsResponse,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def get_repo(session: Session = Depends(get_session)) -> JobRepository:
    return JobRepository(session)


@router.get("", response_model=JobPage)
def list_jobs(
    repo: JobRepository = Depends(get_repo),
    role: list[str] | None = Query(default=None, description="role_category values"),
    employment_type: list[str] | None = Query(default=None),
    source: list[str] | None = Query(default=None),
    region: list[str] | None = Query(default=None),
    tech: list[str] | None = Query(default=None),
    q: str | None = Query(default=None, description="title/company substring"),
    max_min_yoe: int | None = Query(default=0, ge=0, le=20),
    include_unknown_yoe: bool = True,
    reachable_only: bool = Query(
        default=False, description="worldwide/India or sponsors visas"
    ),
    remote_only: bool = False,
    new_grad_only: bool = False,
    posted_within_days: int | None = Query(default=None, ge=1, le=365),
    order_by: str = Query(default="match_score", pattern="^(match_score|first_seen_at|posted_at)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JobPage:
    jobs, total = repo.search(
        JobFilter(
            role_categories=role,
            employment_types=employment_type,
            sources=source,
            regions=region,
            tech=tech,
            query=q,
            max_min_yoe=max_min_yoe,
            include_unknown_yoe=include_unknown_yoe,
            reachable_only=reachable_only,
            remote_only=remote_only,
            new_grad_only=new_grad_only,
            posted_within_days=posted_within_days,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )
    )
    return JobPage(
        items=[JobSummary.from_domain(j) for j in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/facets", response_model=FacetsResponse)
def facets(repo: JobRepository = Depends(get_repo)) -> FacetsResponse:
    stats = repo.stats()
    return FacetsResponse(
        role_categories=[r.value for r in RoleCategory if r is not RoleCategory.OTHER],
        employment_types=[e.value for e in EmploymentType],
        regions=[r.value for r in HiringRegion],
        sources=sorted(stats["by_source"]),
    )


@router.get("/stats", response_model=StatsResponse)
def stats(repo: JobRepository = Depends(get_repo)) -> StatsResponse:
    data = repo.stats()
    return StatsResponse(
        **data,
        connectors=[ConnectorHealth(**h) for h in repo.connector_health()],
    )


@router.get("/{source}/{source_job_id:path}", response_model=JobDetail)
def job_detail(
    source: str, source_job_id: str, repo: JobRepository = Depends(get_repo)
) -> JobDetail:
    jobs, _ = repo.search(
        JobFilter(sources=[source], active_only=False, limit=200_000, max_min_yoe=None)
    )
    for job in jobs:
        if job.source_job_id == source_job_id:
            return JobDetail.from_domain(job)
    raise HTTPException(status_code=404, detail="job not found")
