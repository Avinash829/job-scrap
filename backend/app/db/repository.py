"""Data access. All SQL lives here - services and routes never build queries.

The jobs table holds only live postings that pass the filters. Writes are
kept to what a free-tier database needs: new jobs are inserted, changed jobs
updated, unchanged jobs not touched at all, and closed jobs deleted
(see sync_source).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import bindparam, delete, func, insert, select, update
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
        match_reasons=json.loads(row.match_reasons or "[]"),
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
        "match_reasons": json.dumps(job.match_reasons),
        "needs_enrichment": job.needs_enrichment,
        "link_status": job.link_status,
        "link_checked_at": job.link_checked_at,
        "raw_payload": json.dumps(_slim_payload(job.raw_payload), default=str, sort_keys=True),
        "scope": str((job.raw_payload or {}).get("scope") or job.source),
        "missed_runs": 0,
    }


# The only raw_payload keys read after storage: the scorer's company signals
# and the card's YC badge. Everything else a connector attached is dropped.
_PAYLOAD_KEEP = frozenset({"yc_batch", "team_size", "vc", "funding_stage"})

# Values that change without the job changing - excluded from the fingerprint
# so an unchanged job is never rewritten.
_VOLATILE = frozenset({"first_seen_at", "last_seen_at", "link_status", "link_checked_at", "missed_runs"})


def _slim_payload(payload: dict | None) -> dict:
    return {k: v for k, v in (payload or {}).items() if k in _PAYLOAD_KEEP and v not in (None, "", [])}


def fingerprint(values: dict) -> str:
    stable = {k: v for k, v in values.items() if k not in _VOLATILE}
    return hashlib.sha1(json.dumps(stable, default=str, sort_keys=True).encode()).hexdigest()


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    # ------------------------------------------------------------------ writes

    def sync_source(
        self, source: str, jobs: list[Job], covered_scopes: set[str], full_listing: bool
    ) -> tuple[int, int, int]:
        """Make one source's stored jobs match this run. Returns (new, updated, deleted).

        * new job                    -> INSERT
        * known job, values changed  -> UPDATE; unchanged jobs are skipped, so
                                        no write, no dead tuple, no compute
        * stored job not in this run -> closed, but ONLY if its scope (board or
                                        company) was fully checked this run.
                                        Full listings delete on the first miss;
                                        search-style sources on the second,
                                        because a search can omit a live job once.
        Rows stored before scopes existed (empty scope) are judged whenever the
        source checked anything.
        """
        now = datetime.now(timezone.utc)
        wanted: dict[str, dict] = {}
        for job in jobs:
            values = job_to_values(job)
            values["fingerprint"] = fingerprint(values)
            wanted[job.source_job_id] = values

        existing = {
            r.source_job_id: r
            for r in self.s.execute(
                select(JobRow.id, JobRow.source_job_id, JobRow.fingerprint, JobRow.missed_runs, JobRow.scope)
                .where(JobRow.source == source)
            ).all()
        }

        inserts = [v for sid, v in wanted.items() if sid not in existing]
        updates: list[dict] = []
        for sid, values in wanted.items():
            row = existing.get(sid)
            if row is None:
                continue
            if row.fingerprint != values["fingerprint"] or row.missed_runs:
                upd = {k: v for k, v in values.items() if k not in ("source", "source_job_id", "first_seen_at")}
                upd["last_seen_at"] = now
                upd["_id"] = row.id
                updates.append(upd)

        delete_ids: list[int] = []
        miss_ids: list[int] = []
        if covered_scopes:
            threshold = 1 if full_listing else 2
            for sid, row in existing.items():
                if sid in wanted or (row.scope and row.scope not in covered_scopes):
                    continue
                if row.missed_runs + 1 >= threshold:
                    delete_ids.append(row.id)
                else:
                    miss_ids.append(row.id)

        if inserts:
            self.s.execute(insert(JobRow), inserts)
        if updates:
            # Core-level executemany: one statement, many parameter sets.
            # Bind names are prefixed because SQLAlchemy reserves bare column
            # names for its own SET clause parameters.
            table = JobRow.__table__
            cols = [k for k in updates[0] if k != "_id"]
            stmt = (
                update(table)
                .where(table.c.id == bindparam("b_id"))
                .values({c: bindparam(f"b_{c}") for c in cols})
            )
            conn = self.s.connection()
            for start in range(0, len(updates), 500):
                chunk = updates[start : start + 500]
                conn.execute(stmt, [{f"b_{k}" if k != "_id" else "b_id": v for k, v in u.items()} for u in chunk])
        for start in range(0, len(delete_ids), 1000):
            self.s.execute(delete(JobRow).where(JobRow.id.in_(delete_ids[start : start + 1000])))
        for start in range(0, len(miss_ids), 1000):
            self.s.execute(
                update(JobRow)
                .where(JobRow.id.in_(miss_ids[start : start + 1000]))
                .values(missed_runs=JobRow.missed_runs + 1)
            )
        self.s.flush()
        return len(inserts), len(updates), len(delete_ids)

    def prune_housekeeping(self, keep_cache_days: int = 60, keep_run_days: int = 14) -> None:
        """Bound the two side tables: the LLM cache and run-health history."""
        now = datetime.now(timezone.utc)
        self.s.execute(delete(ExtractionCacheRow).where(
            ExtractionCacheRow.created_at < now - timedelta(days=keep_cache_days)))
        self.s.execute(delete(ConnectorRunRow).where(
            ConnectorRunRow.started_at < now - timedelta(days=keep_run_days)))


    def reset_daily(self, keep_cache_days: int = 30, keep_run_days: int = 7) -> dict:
        """Empty the job table (manual `reset --yes` only; runs no longer need it).

        Deliberately NOT wiped:
          * extraction_cache - keyed by description_hash, so tomorrow's run
            re-fetching the same posting reuses the LLM result instead of
            spending Gemini quota again. Entries older than keep_cache_days
            are pruned so it can't grow without bound.
          * recent connector_runs - the silent-zero health history; only the
            last keep_run_days are kept.

        On Postgres, TRUNCATE (not DELETE) returns the space to Neon's 0.5 GB
        free-tier allowance immediately; DELETE only marks rows dead.
        """
        from sqlalchemy import text

        jobs = self.s.scalar(select(func.count()).select_from(JobRow)) or 0
        if self.s.bind.dialect.name == "postgresql":
            self.s.execute(text("TRUNCATE TABLE jobs RESTART IDENTITY"))
        else:
            self.s.execute(delete(JobRow))

        now = datetime.now(timezone.utc)
        cache = self.s.execute(
            delete(ExtractionCacheRow).where(
                ExtractionCacheRow.created_at < now - timedelta(days=keep_cache_days)
            )
        ).rowcount
        runs = self.s.execute(
            delete(ConnectorRunRow).where(
                ConnectorRunRow.started_at < now - timedelta(days=keep_run_days)
            )
        ).rowcount
        return {"jobs_deleted": jobs, "cache_pruned": cache or 0, "runs_pruned": runs or 0}

    def active_jobs_lean(self, batch_size: int = 2000):
        """Every stored job, streamed in batches (used by rescore)."""
        stmt = select(JobRow).order_by(JobRow.id).execution_options(yield_per=batch_size)
        for row in self.s.scalars(stmt):
            yield row_to_job(row)

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
        """Store link statuses; delete jobs whose apply link is dead.

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
                self.s.delete(row)
                dead += 1
        self.s.flush()
        return checked, dead

    def update_scored(self, scored: dict[tuple[str, str], tuple[float, list[str]]]) -> None:
        """Write score + reasons. Grouped by identical (score, reasons) so a
        corpus of thousands takes a few dozen statements, not one per row."""
        if not scored:
            return
        buckets: dict[tuple[str, float, str], list[str]] = {}
        for (source, job_id), (score, reasons) in scored.items():
            key = (source, round(score, 2), json.dumps(reasons))
            buckets.setdefault(key, []).append(job_id)

        for (source, score, reasons_json), ids in buckets.items():
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                self.s.execute(
                    update(JobRow)
                    .where(JobRow.source == source, JobRow.source_job_id.in_(chunk))
                    .values(match_score=score, match_reasons=reasons_json)
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
