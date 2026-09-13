"""Pipeline orchestration: fetch -> normalize -> dedupe -> store -> enrich -> score.

Enrichment runs AFTER storage and BEFORE filtering, on purpose. Filtering
first would discard postings whose region is merely unresolved rather than
genuinely unreachable - which is how you lose the worldwide-remote roles the
whole tool exists to find.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app.connectors import connectors_for_tier
from app.core.config import get_settings
from app.db.repository import JobRepository
from app.db.session import session_scope
from app.domain.entities import ConnectorRun, Extraction, Job
from app.services.dedupe.matcher import dedupe
from app.services.enrichment.extractor import LLMExtractor
from app.services.enrichment.provider import GeminiProvider, LLMUnavailable
from app.services.normalization.link_check import LinkChecker, LinkStatus
from app.services.normalization.rules import normalize, passes_filters
from app.services.scoring.scorer import MatchScorer

log = logging.getLogger(__name__)


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

        fetched = await asyncio.gather(*(guarded(c) for c in connectors))

        with session_scope() as s:
            repo = JobRepository(s)

            for raw_jobs, record in fetched:
                jobs: list[Job] = []
                for raw in raw_jobs:
                    try:
                        jobs.append(normalize(raw))
                    except Exception as exc:  # noqa: BLE001
                        self._drop(result, f"normalize error: {type(exc).__name__}")
                        log.debug("normalize failed for %s: %s", raw.url, exc)

                kept, dupes = dedupe(jobs)
                if dupes:
                    self._drop(result, "duplicate", dupes)

                new, updated = repo.upsert_many(kept)
                record.jobs_kept = len(kept)
                record.jobs_new = new
                result.total_new += new
                result.total_updated += updated

                if kept:
                    repo.mark_stale(record.connector, {j.source_job_id for j in kept})
                repo.record_run(record)
                result.runs.append(record)

            # --- enrichment ---
            if self.enrich:
                await self._enrich(repo, result)

            # --- filtering (post-enrichment, so decisions use real data) ---
            if self.apply_filter:
                self._deactivate_out_of_scope(repo, result)

            # --- scoring (before link checks, so we verify best-first) ---
            result.scored = self._rescore(repo)

            # --- apply-link verification ---
            if self.check_links:
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
        jobs, _ = repo.search(_all_active_filter())
        doomed: list[tuple[str, str]] = []
        for job in jobs:
            keep, reason = passes_filters(job)
            if not keep:
                self._drop(result, reason)
                doomed.append((job.source, job.source_job_id))
        repo.set_active_bulk(doomed, active=False)

    def _rescore(self, repo: JobRepository) -> int:
        jobs, _ = repo.search(_all_active_filter())
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
