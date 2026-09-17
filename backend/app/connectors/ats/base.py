"""Shared base for per-company ATS board readers.

The important design decision here is BoardStatus.

Every one of these APIs collapses three very different outcomes into an empty
array: the board does not exist, the board exists with zero openings, and the
request failed. Treating them alike means a broken connector looks exactly
like a company that stopped hiring - so coverage silently rots and nothing
alerts. Each fetch therefore returns an explicit status, and only NO_BOARD
prunes a slug from the config.

Concurrency per platform follows what large public aggregators use without
getting blocked: Greenhouse/Lever 30, SmartRecruiters 10, Ashby 5.
"""
from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

import httpx

from app.connectors.base import Connector, FetchError
from app.core import yc_index
from app.core.companies import Company, companies_for
from app.domain.entities import RawJob

log = logging.getLogger(__name__)


class BoardStatus(str, Enum):
    OK = "ok"                   # board exists, returned >=1 posting
    EMPTY_BOARD = "empty"       # board exists, genuinely zero openings
    NO_BOARD = "no_board"       # 404 - wrong slug, or company not on this ATS
    FETCH_FAILED = "failed"     # 5xx / timeout / malformed - retry later


@dataclass
class BoardResult:
    company: Company
    status: BoardStatus
    jobs: list[RawJob] = field(default_factory=list)
    http_code: int | None = None
    error: str | None = None

    @property
    def prunable(self) -> bool:
        """Only a definitive 404 justifies dropping a slug from config."""
        return self.status is BoardStatus.NO_BOARD


class ATSConnector(Connector):
    """Iterates the companies configured for one ATS platform."""

    platform: str
    concurrency: int = 10

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier
        self.board_results: list[BoardResult] = []

    # ------------------------------------------------------------- subclass API

    def known_scopes(self) -> set[str] | None:
        return {f"{self.name}:{c.slug}" for c in companies_for(self.platform)} or None

    @abc.abstractmethod
    def board_url(self, slug: str) -> str:
        """Endpoint for one company's board."""

    @abc.abstractmethod
    def parse(self, company: Company, payload) -> list[RawJob]:
        """Convert this platform's payload into RawJobs."""

    def extract_items(self, payload) -> list:
        """Rows out of the envelope. Lever returns a bare array; the rest
        wrap their rows in a key, so subclasses override as needed."""
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("jobs", "content", "offers", "postings", "data"):
                if isinstance(payload.get(key), list):
                    return payload[key]
        return []

    # ------------------------------------------------------------------ fetching

    async def fetch_board(self, company: Company) -> BoardResult:
        url = self.board_url(company.slug)
        try:
            payload = await self.get_json(url)
        except FetchError as exc:
            code = getattr(exc, "status_code", None)
            status = BoardStatus.NO_BOARD if code == 404 else BoardStatus.FETCH_FAILED
            return BoardResult(company, status, http_code=code, error=str(exc))
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            status = BoardStatus.NO_BOARD if code in (404, 410) else BoardStatus.FETCH_FAILED
            return BoardResult(company, status, http_code=code, error=f"HTTP {code}")
        except Exception as exc:  # noqa: BLE001 - one bad board must not kill the run
            return BoardResult(
                company, BoardStatus.FETCH_FAILED, error=f"{type(exc).__name__}: {exc}"
            )

        items = self.extract_items(payload)
        if not items:
            return BoardResult(company, BoardStatus.EMPTY_BOARD, http_code=200)

        try:
            jobs = self.parse(company, payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: parse failed for %s: %s", self.platform, company.slug, exc)
            return BoardResult(
                company, BoardStatus.FETCH_FAILED, 200, f"parse: {type(exc).__name__}"
            )

        if not jobs:
            return BoardResult(company, BoardStatus.EMPTY_BOARD, 200)

        # Attach YC / team-size metadata when this slug is a known YC company.
        if meta := yc_index.lookup(company.slug):
            for job in jobs:
                job.raw_payload.update(meta)

        return BoardResult(company, BoardStatus.OK, jobs, 200)

    async def fetch(self) -> Iterable[RawJob]:
        companies = companies_for(self.platform, self._tier_filter)
        if not companies:
            return []

        sem = asyncio.Semaphore(self.concurrency)

        async def guarded(c: Company) -> BoardResult:
            async with sem:
                return await self.fetch_board(c)

        self.board_results = list(await asyncio.gather(*(guarded(c) for c in companies)))

        # A board that answered (with jobs or genuinely empty) was fully read;
        # its unseen jobs are closed. A failed or missing board proves nothing.
        self.covered_scopes = set()
        for r in self.board_results:
            scope = f"{self.name}:{r.company.slug}"
            self.scoped(r.jobs, scope)
            if r.status in (BoardStatus.OK, BoardStatus.EMPTY_BOARD):
                self.covered_scopes.add(scope)

        ok = sum(1 for r in self.board_results if r.status is BoardStatus.OK)
        empty = sum(1 for r in self.board_results if r.status is BoardStatus.EMPTY_BOARD)
        missing = sum(1 for r in self.board_results if r.status is BoardStatus.NO_BOARD)
        failed = sum(1 for r in self.board_results if r.status is BoardStatus.FETCH_FAILED)
        log.info(
            "%s: %d boards -> ok=%d empty=%d no_board=%d failed=%d",
            self.platform, len(companies), ok, empty, missing, failed,
        )

        jobs: list[RawJob] = []
        for r in self.board_results:
            jobs.extend(r.jobs)
        return jobs
