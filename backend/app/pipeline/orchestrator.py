"""Pipeline orchestration: fetch -> normalize -> dedupe -> store -> enrich -> score.

Enrichment runs AFTER storage and BEFORE filtering, on purpose. Filtering
first would discard postings whose region is merely unresolved rather than
genuinely unreachable - which is how you lose the worldwide-remote roles the
whole tool exists to find.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.exc import OperationalError

from app.connectors import connectors_for_tier
from app.core.config import get_settings
from app.db.repository import JobRepository
from app.db.session import get_engine, session_scope
from app.domain.entities import ConnectorRun, Extraction, Job
from app.domain.enums import RoleCategory
from app.services.dedupe.matcher import dedupe
from app.services.enrichment.extractor import LLMExtractor
from app.services.enrichment.provider import GeminiProvider, LLMUnavailable
from app.services.normalization.link_check import LinkChecker, LinkStatus
from app.services.normalization.rules import normalize, passes_filters
from app.services.scoring.scorer import MatchScorer

log = logging.getLogger(__name__)

# Transient database failures worth retrying: a dropped TCP connection
# ("server closed the connection unexpectedly") or a DNS blip
# ("getaddrinfo failed"). A 43-minute run lost every source's writes to one
# 30-second network drop, because each store was tried exactly once.
_STORE_ATTEMPTS = 4
_STORE_BACKOFF = (5, 20, 60)


async def _store_with_retry(record: ConnectorRun, kept: list[Job]) -> tuple[int, int]:
    """Upsert one source's rows in its own transaction, retrying on disconnects.

    Safe to retry: the transaction either committed or rolled back, and
    upsert_many is keyed on (source, source_job_id), so a replay can't
    duplicate rows.
    """
    for attempt in range(_STORE_ATTEMPTS):
        try:
            with session_scope() as s:
                repo = JobRepository(s)
                new, updated = repo.upsert_many(kept)
                if kept:
                    repo.mark_stale(record.connector, {j.source_job_id for j in kept})
                record.jobs_kept = len(kept)
                record.jobs_new = new
                repo.record_run(record)
            return new, updated
        except OperationalError as exc:
            if attempt == _STORE_ATTEMPTS - 1:
                raise
            wait = _STORE_BACKOFF[min(attempt, len(_STORE_BACKOFF) - 1)]
            log.warning("storing %s: database unreachable (%s), retrying in %ss",
                        record.connector, str(exc).splitlines()[0][:80], wait)
            get_engine().dispose()   # drop pooled connections that died with the network
            await asyncio.sleep(wait)
    return 0, 0

@dataclass
class PipelineResult:
    runs: list[ConnectorRun] = field(default_factory=list)
    drop_reasons: dict[str, int] = field(default_factory=dict)
    total_new: int = 0
    total_updated: int = 0
    enriched: int = 0
    links_checked: int = 0
    links_dead: int = 0
    llm_calls: int = 0
    llm_model: str | None = None
    llm_note: str | None = None
    scored: int = 0
    removed_unseen: int = 0

    @property
    def total_found(self) -> int:
        return sum(r.jobs_found for r in self.runs)


class IngestPipeline:
    def __init__(
        self,
        enrich: bool = True,
        apply_filter: bool = True,
        check_links: bool = True,
        fresh: bool = False,
    ) -> None:
        self.settings = get_settings()
        self.fresh = fresh
        self.enrich = enrich
        self.apply_filter = apply_filter
        self.check_links = check_links
        self.scorer = MatchScorer()

    async def run(self, tier: int | None = None) -> PipelineResult:
        result = PipelineResult()
        connectors = connectors_for_tier(tier)
        if not connectors:
            return result

        # bounded concurrency - politeness, not throughput
        sem = asyncio.Semaphore(self.settings.max_concurrency)

        async def guarded(conn):
            async with sem:
                return await conn.run()

        started = time.monotonic()
        run_started_at = datetime.now(timezone.utc)
        fetched = await asyncio.gather(*(guarded(c) for c in connectors))

        # One transaction PER CONNECTOR, not one for the whole run.
        #
        # The original single transaction held ~28k inserts open for 30+
        # minutes against a remote database. A dropped connection at minute
        # 29 would have rolled back everything, and nothing was visible until
        # the very end. Committing per source makes a run resumable: whatever
        # finished stays finished, and a re-run only redoes the source that
        # failed.
        # Normalize everything first, then de-duplicate ACROSS sources. The
        # same role now arrives from several places - SimplifyJobs links to
        # the employer's Workday or Greenhouse posting that we also fetch
        # directly - and per-source dedupe stored it twice. dedupe() keeps the
        # copy from the highest-priority source (the employer's own system).
        normalized: list[tuple[ConnectorRun, list[Job]]] = []
        all_jobs: list[Job] = []
        for raw_jobs, record in fetched:
            jobs: list[Job] = []
            for raw in raw_jobs:
                try:
                    job = normalize(raw)
                except Exception as exc:  # noqa: BLE001
                    self._drop(result, f"normalize error: {type(exc).__name__}")
                    log.debug("normalize failed for %s: %s", raw.url, exc)
                    continue
                # Out-of-scope titles (sales, HR, ops... ~70% of everything
                # fetched) are not stored at all. They were kept as slim rows
                # for bookkeeping, but nothing reads them, and at ~135k
                # fetched postings a day they would push Neon's free 0.5 GB
                # and triple the write time. The title gate is deterministic,
                # so tomorrow's run reaches the same verdict without them.
                if job.role_category is RoleCategory.OTHER:
                    self._drop(result, "role not in scope")
                    continue
                jobs.append(job)
            normalized.append((record, jobs))
            all_jobs.extend(jobs)

        survivors, dupes = dedupe(all_jobs)
        if dupes:
            self._drop(result, "duplicate", dupes)
        surviving_keys = {(j.source, j.source_job_id) for j in survivors}

        log.info("fetch + normalize finished in %.0fs", time.monotonic() - started)
        for record, jobs in normalized:
            kept = [j for j in jobs if (j.source, j.source_job_id) in surviving_keys]
            try:
                new, updated = await _store_with_retry(record, kept)
            except Exception as exc:  # noqa: BLE001 - one source's write must not sink the rest
                record.ok = False
                record.error = f"storage: {type(exc).__name__}: {str(exc)[:160]}"
                log.error("storing %s failed: %s", record.connector, exc)
                new = updated = 0

            result.total_new += new
            result.total_updated += updated
            result.runs.append(record)
            log.info("%s: committed %d new / %d refreshed", record.connector, new, updated)

        # --- daily fresh start, without an empty site ---
        # Deleting everything BEFORE a 30-minute scrape left the site empty
        # for the whole scrape. Deleting what this run did NOT see, after it
        # has stored today's data, ends in the same state with no gap.
        # Sources whose fetch or storage failed are left alone, so one broken
        # connector can't wipe its employers' jobs.
        if self.fresh:
            healthy = [r.connector for r in result.runs if r.ok]
            with session_scope() as s:
                result.removed_unseen = JobRepository(s).drop_unseen(since=run_started_at, sources=healthy)
            log.info("fresh: removed %d jobs not seen in this run", result.removed_unseen)

        # --- enrichment ---
        if self.enrich:
            with session_scope() as s:
                await self._enrich(JobRepository(s), result)

        # --- filtering (post-enrichment, so decisions use real data) ---
        if self.apply_filter:
            with session_scope() as s:
                self._deactivate_out_of_scope(JobRepository(s), result)

        # --- scoring (before link checks, so we verify best-first) ---
        with session_scope() as s:
            result.scored = self._rescore(JobRepository(s))

        # --- apply-link verification ---
        if self.check_links:
            with session_scope() as s:
                repo = JobRepository(s)
                await self._verify_links(repo, result)
                result.scored = self._rescore(repo)

        return result

    # ------------------------------------------------------------- enrichment

    async def _enrich(self, repo: JobRepository, result: PipelineResult) -> None:
        pending = repo.pending_enrichment(limit=500)
        if not pending:
            return

        # cache first - a posting is paid for exactly once, ever
        cached = repo.cached_extractions([j.description_hash for j in pending])
        if cached:
            result.enriched += repo.apply_extractions(cached)
            pending = [j for j in pending if j.description_hash not in cached]

        if not pending:
            result.llm_note = "resolved entirely from cache"
            return

        if not self.settings.llm_enabled:
            result.llm_note = f"{len(pending)} rows need LLM but no GEMINI_API_KEYS set"
            return

        provider = GeminiProvider()
        extractor = LLMExtractor(provider)

        # de-duplicate by hash so identical postings from two boards cost one call
        by_hash: dict[str, Job] = {j.description_hash: j for j in pending}
        try:
            fresh: dict[str, Extraction] = await extractor.extract(list(by_hash.values()))
        except LLMUnavailable as exc:
            result.llm_note = f"LLM unavailable: {exc}"
            return

        if fresh:
            model = provider.active_model or self.settings.gemini_model
            repo.store_extractions(fresh, model)
            result.enriched += repo.apply_extractions(fresh)
            result.llm_calls = provider.call_count
            result.llm_model = model

    # ------------------------------------------------------------ link checks

    async def _verify_links(self, repo: JobRepository, result: PipelineResult) -> None:
        """Check in-scope apply links and retire the dead ones.

        Runs after scoring so the highest-scoring rows - the ones you'd
        actually click - are verified first if the limit is hit.
        """
        pending = repo.needs_link_check(limit=200)
        if not pending:
            return

        checker = LinkChecker()
        urls = list({j.url for j in pending if j.url.startswith("http")})
        if not urls:
            return

        outcomes = await checker.check_many(urls)
        statuses = {url: res.status.value for url, res in outcomes.items()}
        checked, dead = repo.record_link_results(statuses)

        result.links_checked = checked
        result.links_dead = dead
        if dead:
            self._drop(result, "dead apply link", dead)

        for url, res in outcomes.items():
            if res.status is LinkStatus.DEAD:
                log.info("dead link (%s): %s", res.reason, url)

    # -------------------------------------------------------------- filtering

    def _deactivate_out_of_scope(
        self, repo: JobRepository, result: PipelineResult
    ) -> None:
        """Mark rows that fail the config gate inactive rather than deleting.

        Keeping them means loosening a filter later re-surfaces them without
        a re-crawl, and the drop reasons stay auditable.
        """
        doomed: list[tuple[str, str]] = []
        for job in repo.active_jobs_lean():
            keep, reason = passes_filters(job)
            if not keep:
                self._drop(result, reason)
                doomed.append((job.source, job.source_job_id))
        repo.set_active_bulk(doomed, active=False)

    def _rescore(self, repo: JobRepository) -> int:
        jobs = list(repo.active_jobs_lean())
        if not jobs:
            return 0
        repo.update_scored(self.scorer.score_all_explained(jobs))
        return len(jobs)

    @staticmethod
    def _drop(result: PipelineResult, reason: str, n: int = 1) -> None:
        result.drop_reasons[reason] = result.drop_reasons.get(reason, 0) + n


def _all_active_filter():
    from app.db.repository import JobFilter

    return JobFilter(active_only=True, limit=100_000, offset=0)
