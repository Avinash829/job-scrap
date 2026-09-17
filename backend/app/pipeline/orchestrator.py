"""Pipeline orchestration.

    fetch -> normalize -> enrich (in memory) -> filter -> dedupe -> score
          -> sync each source with the database -> verify apply links

The database holds only what the site shows: live postings that pass every
filter, without their descriptions. Descriptions are read here, in memory, to
extract skills, experience and region (rules first, the LLM for what rules
can't resolve), and are then discarded.

Enrichment runs BEFORE filtering on purpose: filtering first would discard
postings whose region is merely unresolved rather than genuinely
unreachable - which is how you lose the worldwide-remote roles the whole tool
exists to find.

Closed postings are deleted as soon as a run proves they're gone - see
JobRepository.sync_source for the rules that keep a flaky fetch from
deleting live jobs. There is no daily wipe.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.exc import OperationalError

from app.connectors import connectors_for_tier
from app.core.config import get_settings
from app.db.repository import JobRepository
from app.db.session import get_engine, session_scope
from app.domain.entities import ConnectorRun, Extraction, Job
from app.domain.enums import HiringRegion, RoleCategory
from app.services.dedupe.matcher import dedupe
from app.services.enrichment.extractor import LLMExtractor
from app.services.enrichment.provider import GeminiProvider, LLMUnavailable
from app.services.normalization.link_check import LinkChecker, LinkStatus
from app.services.normalization.rules import normalize, passes_filters
from app.services.scoring.scorer import MatchScorer

log = logging.getLogger(__name__)

# Transient database failures worth retrying: a dropped TCP connection
# ("server closed the connection unexpectedly") or a DNS blip
# ("getaddrinfo failed"). A 43-minute run once lost every source's writes to
# one 30-second network drop, because each store was tried exactly once.
_STORE_ATTEMPTS = 4
_STORE_BACKOFF = (5, 20, 60)


async def _sync_with_retry(record: ConnectorRun, jobs: list[Job]) -> tuple[int, int, int]:
    """Sync one source in its own transaction, retrying on disconnects.

    Safe to retry: the transaction either committed or rolled back, and the
    sync is computed from the database state at the time it runs.
    """
    for attempt in range(_STORE_ATTEMPTS):
        try:
            with session_scope() as s:
                repo = JobRepository(s)
                new, updated, deleted = repo.sync_source(
                    record.connector,
                    jobs,
                    set(record.covered_scopes),
                    record.full_listing,
                    set(record.known_scopes) if record.known_scopes else None,
                )
                record.jobs_kept = len(jobs)
                record.jobs_new = new
                repo.record_run(record)
            return new, updated, deleted
        except OperationalError as exc:
            if attempt == _STORE_ATTEMPTS - 1:
                raise
            wait = _STORE_BACKOFF[min(attempt, len(_STORE_BACKOFF) - 1)]
            log.warning("storing %s: database unreachable (%s), retrying in %ss",
                        record.connector, str(exc).splitlines()[0][:80], wait)
            get_engine().dispose()   # drop pooled connections that died with the network
            await asyncio.sleep(wait)
    return 0, 0, 0


@dataclass
class PipelineResult:
    runs: list[ConnectorRun] = field(default_factory=list)
    drop_reasons: dict[str, int] = field(default_factory=dict)
    total_new: int = 0
    total_updated: int = 0
    total_closed: int = 0
    enriched: int = 0
    links_checked: int = 0
    links_dead: int = 0
    llm_calls: int = 0
    llm_model: str | None = None
    llm_note: str | None = None
    scored: int = 0

    @property
    def total_found(self) -> int:
        return sum(r.jobs_found for r in self.runs)


class IngestPipeline:
    def __init__(
        self,
        enrich: bool = True,
        apply_filter: bool = True,
        check_links: bool = True,
    ) -> None:
        self.settings = get_settings()
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
        fetched = await asyncio.gather(*(guarded(c) for c in connectors))

        # --- normalize: out-of-scope titles (sales, HR, ops... ~70% of all
        # postings) are dropped before anything else touches them
        records: list[ConnectorRun] = []
        jobs: list[Job] = []
        for raw_jobs, record in fetched:
            records.append(record)
            for raw in raw_jobs:
                try:
                    job = normalize(raw)
                except Exception as exc:  # noqa: BLE001
                    self._drop(result, f"normalize error: {type(exc).__name__}")
                    log.debug("normalize failed for %s: %s", raw.url, exc)
                    continue
                if job.role_category is RoleCategory.OTHER:
                    self._drop(result, "role not in scope")
                    continue
                jobs.append(job)
        log.info("fetch + normalize finished in %.0fs", time.monotonic() - started)

        # --- enrich in memory, while descriptions are still here
        if self.enrich:
            jobs = await self._enrich(jobs, result)

        # --- filter: every stored job must satisfy the profile
        if self.apply_filter:
            kept: list[Job] = []
            for job in jobs:
                ok, reason = passes_filters(job)
                if ok:
                    kept.append(job)
                else:
                    self._drop(result, reason)
            jobs = kept

        # --- dedupe ACROSS sources: the same role arrives from several places
        # (a VC board links to the employer's Greenhouse posting we also fetch);
        # the copy from the employer's own system wins.
        jobs, dupes = dedupe(jobs)
        if dupes:
            self._drop(result, "duplicate", dupes)

        # --- score in memory, then store
        scored = self.scorer.score_all_explained(jobs)
        for job in jobs:
            job.match_score, job.match_reasons = scored[(job.source, job.source_job_id)]
            job.description = None   # never stored
        result.scored = len(jobs)

        by_source: dict[str, list[Job]] = {}
        for job in jobs:
            by_source.setdefault(job.source, []).append(job)

        for record in records:
            source_jobs = by_source.get(record.connector, [])
            try:
                new, updated, closed = await _sync_with_retry(record, source_jobs)
            except Exception as exc:  # noqa: BLE001 - one source's write must not sink the rest
                record.ok = False
                record.error = f"storage: {type(exc).__name__}: {str(exc)[:160]}"
                log.error("storing %s failed: %s", record.connector, exc)
                new = updated = closed = 0
            result.total_new += new
            result.total_updated += updated
            result.total_closed += closed
            result.runs.append(record)
            log.info("%s: %d new / %d changed / %d closed", record.connector, new, updated, closed)

        # --- apply-link verification: a dead link means the job is gone
        if self.check_links:
            with session_scope() as s:
                await self._verify_links(JobRepository(s), result)

        with session_scope() as s:
            JobRepository(s).prune_housekeeping()
        return result

    # ------------------------------------------------------------- enrichment

    async def _enrich(self, jobs: list[Job], result: PipelineResult) -> list[Job]:
        """Fill what the rules couldn't resolve, from the LLM cache or Gemini.

        Only in-scope jobs with a description reach this point. The cache is
        keyed by a hash of the description, so a posting seen every day for a
        month costs exactly one LLM call.
        """
        pending = [j for j in jobs if j.needs_enrichment and j.description]
        if not pending:
            return jobs

        with session_scope() as s:
            extractions: dict[str, Extraction] = JobRepository(s).cached_extractions(
                list({j.description_hash for j in pending})
            )

        todo: dict[str, Job] = {}
        # unresolved region first: that's where extraction decides in-or-out
        for job in sorted(pending, key=lambda j: j.hiring_regions not in ([], [HiringRegion.UNKNOWN])):
            if job.description_hash not in extractions:
                todo.setdefault(job.description_hash, job)

        if todo and not self.settings.llm_enabled:
            result.llm_note = f"{len(todo)} postings need LLM but no GEMINI_API_KEYS set"
        elif todo:
            provider = GeminiProvider()
            try:
                fresh = await LLMExtractor(provider).extract(list(todo.values()))
            except LLMUnavailable as exc:
                fresh = {}
                result.llm_note = f"LLM unavailable: {exc}"
            if fresh:
                model = provider.active_model or self.settings.gemini_model
                with session_scope() as s:
                    JobRepository(s).store_extractions(fresh, model)
                extractions.update(fresh)
                result.llm_calls = provider.call_count
                result.llm_model = model
        elif pending:
            result.llm_note = "resolved entirely from cache"

        out: list[Job] = []
        for job in jobs:
            ex = extractions.get(job.description_hash) if job.needs_enrichment else None
            if ex is not None:
                out.append(job.apply(ex))
                result.enriched += 1
            else:
                out.append(job)
        return out

    # ------------------------------------------------------------ link checks

    async def _verify_links(self, repo: JobRepository, result: PipelineResult) -> None:
        """Check stored apply links, best matches first, and delete dead ones."""
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

    # ---------------------------------------------------------------- scoring

    def _rescore(self, repo: JobRepository) -> int:
        """Recompute scores for stored jobs (after changing config.yaml)."""
        jobs = list(repo.active_jobs_lean())
        if not jobs:
            return 0
        repo.update_scored(self.scorer.score_all_explained(jobs))
        return len(jobs)

    @staticmethod
    def _drop(result: PipelineResult, reason: str, n: int = 1) -> None:
        result.drop_reasons[reason] = result.drop_reasons.get(reason, 0) + n
