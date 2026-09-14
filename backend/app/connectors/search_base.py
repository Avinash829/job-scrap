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

        async def one(term: str, location: str) -> list[RawJob]:
            async with sem:
                try:
                    return await self.search(term, location)
                except Exception as exc:  # noqa: BLE001 - one query must not sink the source
                    log.warning("%s: query %r in %r failed: %s", self.name, term, location, exc)
                    return []

        batches = await asyncio.gather(
            *(one(t, l) for t in self.terms for l in self.locations)
        )
        unique: dict[str, RawJob] = {}
        for batch in batches:
            for job in batch:
                unique.setdefault(job.source_job_id, job)
        log.info("%s: %d unique postings across %d queries", self.name, len(unique), len(batches))
        return list(unique.values())
