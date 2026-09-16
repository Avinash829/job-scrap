"""Base for career sites that must be searched rather than dumped.

Greenhouse or Lever return a company's whole board in one call. Google,
Amazon, Workday and Avature don't: they're search UIs over thousands of
roles, so each has to be queried per term and paged. Terms and locations
come from config.yaml `search:`, so widening coverage never needs code.

Results from different terms overlap heavily ("intern" and "software
engineer" both return "Software Engineer Intern"), so every connector
de-duplicates by its own job id before returning.
"""
from __future__ import annotations

import abc
import asyncio
import logging
from typing import Iterable

from app.connectors.base import Connector
from app.core.config import get_profile
from app.domain.entities import RawJob

log = logging.getLogger(__name__)


class SearchConnector(Connector):
    concurrency: int = 4
    # a search only returns what matches the terms, within page caps
    full_listing = False

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier
        cfg = get_profile().search
        self.terms: list[str] = list(cfg.get("terms") or ["intern"])
        self.locations: list[str] = list(cfg.get("locations") or ["India"])
        self.max_pages: int = int(cfg.get("max_pages_per_term") or 3)

    @abc.abstractmethod
    async def search(self, term: str, location: str) -> list[RawJob]:
        """All pages for one (term, location) query."""

    async def fetch(self) -> Iterable[RawJob]:
        sem = asyncio.Semaphore(self.concurrency)
        failed = False

        async def one(term: str, location: str) -> list[RawJob]:
            nonlocal failed
            async with sem:
                try:
                    return await self.search(term, location)
                except Exception as exc:  # noqa: BLE001 - one query must not sink the source
                    failed = True
                    log.warning("%s: query %r in %r failed: %s", self.name, term, location, exc)
                    return []

        batches = await asyncio.gather(
            *(one(t, l) for t in self.terms for l in self.locations)
        )
        # A failed query means jobs may be missing from this run for reasons
        # other than closing, so nothing from this source is judged closed.
        self.covered_scopes = set() if failed else None
        unique: dict[str, RawJob] = {}
        for batch in batches:
            for job in batch:
                unique.setdefault(job.source_job_id, job)
        log.info("%s: %d unique postings across %d queries", self.name, len(unique), len(batches))
        return list(unique.values())

    async def fetch_per_company(self, companies: list, run_query, scope_of) -> list[RawJob]:
        """Shared loop for per-employer search sites (Workday, Oracle, Avature...).

        run_query(company, term, location) -> list[RawJob]. A company counts as
        covered only if every one of its queries succeeded.
        """
        sem = asyncio.Semaphore(self.concurrency)
        failed_scopes: set[str] = set()

        async def one(company, term: str, location: str) -> list[RawJob]:
            scope = scope_of(company)
            async with sem:
                try:
                    return self.scoped(await run_query(company, term, location), scope)
                except Exception as exc:  # noqa: BLE001 - one query must not sink the source
                    failed_scopes.add(scope)
                    log.warning("%s %s %r failed: %s", self.name, company.slug, term, exc)
                    return []

        batches = await asyncio.gather(
            *(one(c, t, l) for c in companies for t in self.terms for l in self.locations)
        )
        self.covered_scopes = {scope_of(c) for c in companies} - failed_scopes
        unique: dict[str, RawJob] = {}
        for batch in batches:
            for job in batch:
                unique.setdefault(job.source_job_id, job)
        return list(unique.values())
