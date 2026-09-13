"""Data access. All SQL lives here - services and routes never build queries.

Bulk paths matter: a run touches ~1500 rows, so the upsert fetches existing
keys in one query instead of issuing a SELECT per job.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.db.models import ConnectorRunRow, ExtractionCacheRow, JobRow
from app.domain.entities import ConnectorRun, Extraction, Job
from app.domain.enums import (
    Confidence,
    EmploymentType,
    ExtractionSource,
    HiringRegion,
    RoleCategory,
)


@dataclass(slots=True)
class JobFilter:
    """Query parameters for the listing endpoint."""

    role_categories: list[str] | None = None
    max_min_yoe: int | None = None
    include_unknown_yoe: bool = True
    regions: list[str] | None = None
    reachable_only: bool = False
    remote_only: bool = False
    employment_types: list[str] | None = None
    sources: list[str] | None = None
    tech: list[str] | None = None
    query: str | None = None
    posted_within_days: int | None = None
    new_grad_only: bool = False
    active_only: bool = True
    exclude_dead_links: bool = True
    order_by: str = "first_seen_at"
    limit: int = 50
    offset: int = 0


def row_to_job(row: JobRow) -> Job:
    return Job(
        source=row.source,
        source_job_id=row.source_job_id,
        url=row.url,
        description_hash=row.description_hash,
        title=row.title,
        title_normalized=row.title_normalized,
        company=row.company,
        company_normalized=row.company_normalized,
        description=row.description,
        location_raw=row.location_raw,
        ats=row.ats,
        role_category=RoleCategory(row.role_category),
        employment_type=EmploymentType(row.employment_type),
        tech_stack=json.loads(row.tech_stack or "[]"),
        min_yoe=row.min_yoe,
        max_yoe=row.max_yoe,
        is_new_grad=row.is_new_grad,
        grad_year=row.grad_year,
        yoe_source=ExtractionSource(row.yoe_source),
        is_remote=row.is_remote,
        hiring_regions=[HiringRegion(r) for r in json.loads(row.hiring_regions or "[]")],
        work_auth_required=row.work_auth_required,
        visa_sponsorship=row.visa_sponsorship,
        region_source=ExtractionSource(row.region_source),
        region_confidence=Confidence(row.region_confidence),
        salary_min=row.salary_min,
        salary_max=row.salary_max,
        salary_currency=row.salary_currency,
        # SQLite hands back naive datetimes, so every consumer doing date
        # math would hit "can't subtract offset-naive and offset-aware".
        # Normalize at the boundary instead of at each call site.
        posted_at=_aware_opt(row.posted_at),
        first_seen_at=_aware(row.first_seen_at),
        last_seen_at=_aware(row.last_seen_at),
        is_active=row.is_active,
        match_score=row.match_score,
        needs_enrichment=row.needs_enrichment,
        link_status=row.link_status or "",
        link_checked_at=_aware_opt(row.link_checked_at),
        raw_payload=json.loads(row.raw_payload or "{}"),
    )


def job_to_values(job: Job) -> dict:
    return {
        "source": job.source,
        "source_job_id": job.source_job_id,
        "url": job.url,
        "description_hash": job.description_hash,
        "title": job.title,
        "title_normalized": job.title_normalized,
        "company": job.company,
        "company_normalized": job.company_normalized,
        "description": job.description,
        "location_raw": job.location_raw,
        "ats": job.ats,
        "role_category": job.role_category.value,
        "employment_type": job.employment_type.value,
        "tech_stack": json.dumps(job.tech_stack),
        "min_yoe": job.min_yoe,
        "max_yoe": job.max_yoe,
        "is_new_grad": job.is_new_grad,
        "grad_year": job.grad_year,
        "yoe_source": job.yoe_source.value,
        "is_remote": job.is_remote,
        "hiring_regions": json.dumps([r.value for r in job.hiring_regions]),
        "work_auth_required": job.work_auth_required,
        "visa_sponsorship": job.visa_sponsorship,
        "region_source": job.region_source.value,
        "region_confidence": job.region_confidence.value,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "posted_at": job.posted_at,
        "first_seen_at": job.first_seen_at,
        "last_seen_at": job.last_seen_at,
        "is_active": job.is_active,
        "match_score": job.match_score,
        "needs_enrichment": job.needs_enrichment,
        "link_status": job.link_status,
        "link_checked_at": job.link_checked_at,
        "raw_payload": json.dumps(job.raw_payload, default=str),
    }


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    # ------------------------------------------------------------------ writes

    def upsert_many(self, jobs: list[Job]) -> tuple[int, int]:
        """Insert new postings, refresh last_seen_at on known ones.

        first_seen_at is never overwritten - it's our honest "date posted",
        more trustworthy than what most sources report.
        """
        if not jobs:
            return 0, 0

        keys = {(j.source, j.source_job_id) for j in jobs}
        sources = {j.source for j in jobs}

        existing: dict[tuple[str, str], JobRow] = {
            (r.source, r.source_job_id): r
            for r in self.s.scalars(
                select(JobRow).where(JobRow.source.in_(sources))
            ).all()
            if (r.source, r.source_job_id) in keys
        }

        now = datetime.now(timezone.utc)
        new_rows: list[JobRow] = []
        updated = 0

        for job in jobs:
            row = existing.get((job.source, job.source_job_id))
            if row is None:
                new_rows.append(JobRow(**job_to_values(job)))
                continue
            row.last_seen_at = now
            row.is_active = True
            # a re-run may have enriched fields the first pass lacked
            if row.needs_enrichment and not job.needs_enrichment:
                for k, v in job_to_values(job).items():
                    if k not in ("first_seen_at", "id"):
                        setattr(row, k, v)
            updated += 1

        if new_rows:
            self.s.bulk_save_objects(new_rows)
        self.s.flush()
        return len(new_rows), updated

    def apply_extractions(self, extractions: dict[str, Extraction]) -> int:
        """Fold LLM results into rows sharing that description_hash."""
        if not extractions:
            return 0
        rows = self.s.scalars(
            select(JobRow).where(JobRow.description_hash.in_(list(extractions)))
        ).all()
        count = 0
        for row in rows:
            ex = extractions.get(row.description_hash)
            if ex is None:
                continue
            merged = row_to_job(row).apply(ex)
            for k, v in job_to_values(merged).items():
                if k != "first_seen_at":
                    setattr(row, k, v)
            count += 1
        self.s.flush()
        return count

    def pending_enrichment(self, limit: int = 500) -> list[Job]:
        """Rows the rules couldn't resolve, prioritised by whether enriching
        them could actually change the outcome.

        Spending LLM calls on an out-of-scope role is pure waste: a Senior
        Account Executive stays out of scope however well its region resolves.
        So skip `other` roles entirely, and put rows whose region is unresolved
        first - those are the ones where extraction decides in-or-out.
        """
        unresolved_first = case(
            (JobRow.hiring_regions.like('%"unknown"%'), 0), else_=1
        )
        rows = self.s.scalars(
            select(JobRow)
            .where(
                JobRow.needs_enrichment.is_(True),
                JobRow.is_active.is_(True),
                JobRow.role_category != RoleCategory.OTHER.value,
            )
            .order_by(unresolved_first, JobRow.first_seen_at.desc())
            .limit(limit)
        ).all()
        return [row_to_job(r) for r in rows]

    def mark_stale(self, source: str, seen_ids: set[str], grace_days: int = 2) -> int:
        """Deactivate postings that vanished from their board.

        Knowing a role is STILL OPEN is the real edge over LinkedIn, where a
        large share of listings are month-old ghosts.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
        rows = self.s.scalars(
            select(JobRow).where(JobRow.source == source, JobRow.is_active.is_(True))
        ).all()
        stale = [
            r.id
            for r in rows
            if r.source_job_id not in seen_ids and _aware(r.last_seen_at) < cutoff
        ]
        if stale:
            self.s.execute(
                update(JobRow).where(JobRow.id.in_(stale)).values(is_active=False)
            )
        return len(stale)

    def purge_older_than(self, days: int = 60) -> int:
        """Retention - free-tier storage is ~0.5GB, so inactive rows must go."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = self.s.scalars(
            select(JobRow.id).where(
                JobRow.is_active.is_(False), JobRow.last_seen_at < cutoff
            )
        ).all()
        for row_id in rows:
            self.s.delete(self.s.get(JobRow, row_id))
        return len(rows)

    def needs_link_check(self, limit: int = 200, recheck_after_hours: int = 24) -> list[Job]:
        """In-scope rows whose apply link is unverified or stale.

        Only in-scope rows are checked - verifying 1200 filtered-out postings
        would be a lot of requests for links nobody will ever click.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=recheck_after_hours)
        rows = self.s.scalars(
            select(JobRow)
            .where(
                JobRow.is_active.is_(True),
                (JobRow.link_checked_at.is_(None)) | (JobRow.link_checked_at < cutoff),
            )
            .order_by(JobRow.match_score.desc())
            .limit(limit)
        ).all()
        return [row_to_job(r) for r in rows]

    def record_link_results(self, results: dict[str, str]) -> tuple[int, int]:
        """Store link statuses; deactivate the dead ones.

        UNKNOWN is recorded but never deactivates - a timeout or a 403 is not
        proof the job is gone, and acting on it would delete good listings.
        """
        if not results:
            return 0, 0
        now = datetime.now(timezone.utc)
        rows = self.s.scalars(
            select(JobRow).where(JobRow.url.in_(list(results)))
        ).all()
        checked = dead = 0
        for row in rows:
            status = results.get(row.url)
            if status is None:
                continue
            row.link_status = status
            row.link_checked_at = now
            checked += 1
            if status == "dead":
                row.is_active = False
                dead += 1
        self.s.flush()
        return checked, dead

    def update_active(self, source: str, job_id: str, active: bool) -> None:
        self.s.execute(
            update(JobRow)
            .where(JobRow.source == source, JobRow.source_job_id == job_id)
            .values(is_active=active)
        )

    def set_active_bulk(self, keys: list[tuple[str, str]], active: bool) -> int:
        """Flip is_active for many rows in a handful of statements.

        The per-row version issued one UPDATE per posting, which on a 18k-row
        corpus meant ~18k round trips in the filtering stage alone.
        """
        if not keys:
            return 0
        by_source: dict[str, list[str]] = {}
        for source, job_id in keys:
            by_source.setdefault(source, []).append(job_id)

        touched = 0
        for source, ids in by_source.items():
            # chunked to stay well under SQLite's variable limit
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                self.s.execute(
                    update(JobRow)
                    .where(
                        JobRow.source == source,
                        JobRow.source_job_id.in_(chunk),
                    )
                    .values(is_active=active)
                )
                touched += len(chunk)
        return touched

    def update_scores(self, scores: dict[tuple[str, str], float]) -> None:
        """Bulk-update match scores.

        Grouped by score so identical values share one statement - scores are
        rounded to 2dp, so a few hundred distinct values cover the corpus
        instead of one UPDATE per row.
        """
        if not scores:
            return
        buckets: dict[tuple[str, float], list[str]] = {}
        for (source, job_id), score in scores.items():
            buckets.setdefault((source, round(score, 2)), []).append(job_id)

        for (source, score), ids in buckets.items():
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                self.s.execute(
                    update(JobRow)
                    .where(
                        JobRow.source == source,
                        JobRow.source_job_id.in_(chunk),
                    )
                    .values(match_score=score)
                )

    # ------------------------------------------------------------------- reads

    def search(self, f: JobFilter) -> tuple[list[Job], int]:
        stmt = select(JobRow)
        conds = []

        if f.active_only:
            conds.append(JobRow.is_active.is_(True))
        if f.exclude_dead_links:
            conds.append(JobRow.link_status != "dead")
        if f.role_categories:
            conds.append(JobRow.role_category.in_(f.role_categories))
        if f.employment_types:
            conds.append(JobRow.employment_type.in_(f.employment_types))
        if f.sources:
            conds.append(JobRow.source.in_(f.sources))
        if f.remote_only:
            conds.append(JobRow.is_remote.is_(True))
        if f.new_grad_only:
            conds.append(JobRow.is_new_grad.is_(True))

        if f.max_min_yoe is not None:
            yoe_cond = JobRow.min_yoe <= f.max_min_yoe
            if f.include_unknown_yoe:
                yoe_cond = yoe_cond | JobRow.min_yoe.is_(None)
            conds.append(yoe_cond)

        if f.posted_within_days is not None:
            # Real posting date, falling back to first sighting. Filtering on
            # first_seen_at alone marks everything "fresh" on a first run,
            # even a posting that is three months old.
            cutoff = datetime.now(timezone.utc) - timedelta(days=f.posted_within_days)
            conds.append(
                func.coalesce(JobRow.posted_at, JobRow.first_seen_at) >= cutoff
            )

        if f.query:
            like = f"%{f.query.lower()}%"
            conds.append(
                func.lower(JobRow.title).like(like)
                | func.lower(JobRow.company).like(like)
            )

        # hiring_regions and tech_stack are JSON arrays; a LIKE on the encoded
        # text is exact enough here because values are quoted ("us" never
        # matches "usa"), and it keeps the query portable across SQLite/Postgres
        if f.regions:
            region_cond = None
            for r in f.regions:
                c = JobRow.hiring_regions.like(f'%"{r}"%')
                region_cond = c if region_cond is None else region_cond | c
            conds.append(region_cond)

        if f.reachable_only:
            conds.append(
                JobRow.hiring_regions.like('%"worldwide"%')
                | JobRow.hiring_regions.like('%"india"%')
                | (JobRow.visa_sponsorship.is_(True))
            )

        if f.tech:
            for t in f.tech:
                conds.append(JobRow.tech_stack.like(f'%"{t.lower()}"%'))

        if conds:
            stmt = stmt.where(*conds)

        total = self.s.scalar(
            select(func.count()).select_from(JobRow).where(*conds) if conds
            else select(func.count()).select_from(JobRow)
        ) or 0

        order_col = {
            "first_seen_at": JobRow.first_seen_at,
            "posted_at": func.coalesce(JobRow.posted_at, JobRow.first_seen_at),
            "match_score": JobRow.match_score,
        }.get(f.order_by, JobRow.first_seen_at)

        rows = self.s.scalars(
            stmt.order_by(order_col.desc()).limit(f.limit).offset(f.offset)
        ).all()
        return [row_to_job(r) for r in rows], total

    def stats(self) -> dict:
        total = self.s.scalar(select(func.count()).select_from(JobRow)) or 0
        active = (
            self.s.scalar(
                select(func.count()).select_from(JobRow).where(JobRow.is_active.is_(True))
            )
            or 0
        )
        pending = (
            self.s.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(JobRow.needs_enrichment.is_(True), JobRow.is_active.is_(True))
            )
            or 0
        )
        by_source = dict(
            self.s.execute(
                select(JobRow.source, func.count())
                .where(JobRow.is_active.is_(True))
                .group_by(JobRow.source)
            ).all()
        )
        by_role = dict(
            self.s.execute(
                select(JobRow.role_category, func.count())
                .where(JobRow.is_active.is_(True))
                .group_by(JobRow.role_category)
            ).all()
        )
        reachable = (
            self.s.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(
                    JobRow.is_active.is_(True),
                    JobRow.hiring_regions.like('%"worldwide"%')
                    | JobRow.hiring_regions.like('%"india"%'),
                )
            )
            or 0
        )
        return {
            "total": total,
            "active": active,
            "pending_enrichment": pending,
            "reachable_from_india": reachable,
            "by_source": by_source,
            "by_role": by_role,
        }

    # ------------------------------------------------------------------- cache

    def cached_extractions(self, hashes: list[str]) -> dict[str, Extraction]:
        if not hashes:
            return {}
        rows = self.s.scalars(
            select(ExtractionCacheRow).where(
                ExtractionCacheRow.description_hash.in_(hashes)
            )
        ).all()
        from app.services.enrichment.extractor import _clamp_role

        out: dict[str, Extraction] = {}
        for r in rows:
            try:
                ex = Extraction(**json.loads(r.payload))
            except Exception:  # noqa: BLE001 - a poisoned cache row must not break the run
                continue
            # Cached rows predate the config's current role list, so re-clamp
            # on read: otherwise a cached `devops` would be re-applied to a
            # category the user has since removed.
            ex.role_category = _clamp_role(ex.role_category)
            out[r.description_hash] = ex
        return out

    def store_extractions(self, extractions: dict[str, Extraction], model: str) -> None:
        now = datetime.now(timezone.utc)
        for h, ex in extractions.items():
            existing = self.s.get(ExtractionCacheRow, h)
            payload = ex.model_dump_json()
            if existing:
                existing.payload = payload
                existing.model = model
            else:
                self.s.add(
                    ExtractionCacheRow(
                        description_hash=h, payload=payload, model=model, created_at=now
                    )
                )

    # -------------------------------------------------------------------- runs

    def record_run(self, run: ConnectorRun) -> None:
        self.s.add(
            ConnectorRunRow(
                connector=run.connector,
                started_at=run.started_at,
                finished_at=run.finished_at,
                jobs_found=run.jobs_found,
                jobs_kept=run.jobs_kept,
                jobs_new=run.jobs_new,
                ok=run.ok,
                error=run.error,
            )
        )

    def connector_health(self) -> list[dict]:
        """Latest run per connector - the silent-zero dashboard."""
        rows = self.s.execute(
            select(
                ConnectorRunRow.connector,
                func.max(ConnectorRunRow.started_at).label("last_run"),
            ).group_by(ConnectorRunRow.connector)
        ).all()

        out = []
        for connector, last_run in rows:
            row = self.s.scalars(
                select(ConnectorRunRow)
                .where(
                    ConnectorRunRow.connector == connector,
                    ConnectorRunRow.started_at == last_run,
                )
                .limit(1)
            ).first()
            if row:
                out.append(
                    {
                        "connector": row.connector,
                        "last_run": row.started_at,
                        "ok": row.ok,
                        "jobs_found": row.jobs_found,
                        "jobs_kept": row.jobs_kept,
                        "jobs_new": row.jobs_new,
                        "error": row.error,
                    }
                )
        return sorted(out, key=lambda d: d["connector"])


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _aware_opt(dt: datetime | None) -> datetime | None:
    return None if dt is None else _aware(dt)
