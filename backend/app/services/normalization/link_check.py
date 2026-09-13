"""Verify apply links are still live before a posting reaches the UI.

Two failure modes, and the second is the one that matters:

  hard 404  - server returns 404/410. Easy.
  soft 404  - server returns 200 with a "Page Not Found" body. Very common on
              SPA job boards: the CryptoJobsList link that surfaced in the UI
              returned a 200 whose body was their 404 page. A status-code-only
              check passes those straight through.

So: check the status, then sniff the body for not-found markers. Escalate to
curl_cffi when a host blocks a plain client at the TLS layer, and treat
network errors as UNKNOWN rather than dead - a timeout is not proof a job is
gone, and deactivating on one flaky request would delete good listings.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)


class LinkStatus(str, Enum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"   # network error / blocked - do not penalise


# Markers that appear in a soft-404 body. Deliberately narrow: "not found"
# alone appears in plenty of legitimate descriptions, so each pattern needs
# to look like page chrome rather than prose.
_SOFT_404 = re.compile(
    r"(page\s+not\s+found|404\s*[-–—:|]?\s*(page|not\s+found|error)|"
    r"job\s+(is\s+)?(no\s+longer|not)\s+(available|active|found|listed)|"
    r"this\s+(job|position|posting|role)\s+(has\s+been|is)\s+(closed|filled|removed|expired)|"
    r"position\s+(has\s+been\s+)?filled|"
    r"no\s+longer\s+accepting\s+applications|"
    r"posting\s+(has\s+)?expired|"
    r"check\s+if\s+the\s+url\s+is\s+correct)",
    re.I,
)
# Title-tag check is the strongest single signal for a soft 404.
_TITLE_404 = re.compile(r"<title[^>]*>([^<]{0,160})</title>", re.I)

DEAD_CODES = {404, 410}
BODY_SNIFF_BYTES = 60_000


@dataclass(slots=True)
class LinkResult:
    url: str
    status: LinkStatus
    http_code: int | None = None
    reason: str | None = None


def _looks_dead(body: str) -> str | None:
    """Return the matched marker if the body reads like a not-found page."""
    if m := _TITLE_404.search(body):
        title = m.group(1)
        if _SOFT_404.search(title) or re.fullmatch(r"\s*404\s*", title):
            return f"title: {title.strip()[:60]}"
    # only inspect the head of the document - page chrome lives there, and
    # this avoids matching a phrase buried in a long job description
    if m := _SOFT_404.search(body[:6000]):
        return f"body: {m.group(0)[:60]}"
    return None


class LinkChecker:
    def __init__(self, concurrency: int | None = None, timeout: float | None = None):
        s = get_settings()
        self.timeout = timeout or min(15.0, s.http_timeout)
        self.sem = asyncio.Semaphore(concurrency or 6)
        self.ua = s.user_agent

    async def check(self, url: str) -> LinkResult:
        async with self.sem:
            return await self._check_one(url)

    async def _check_one(self, url: str) -> LinkResult:
        headers = {"User-Agent": self.ua, "Accept": "text/html,*/*"}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                # GET, not HEAD: many boards don't implement HEAD, and a soft
                # 404 can only be detected from the body anyway.
                r = await client.get(url, headers=headers)

                if r.status_code in DEAD_CODES:
                    return LinkResult(url, LinkStatus.DEAD, r.status_code, "http status")

                if r.status_code in (403, 401, 429):
                    return await self._check_impersonated(url, headers)

                if r.status_code >= 500:
                    return LinkResult(url, LinkStatus.UNKNOWN, r.status_code, "server error")

                ctype = r.headers.get("content-type", "")
                if "html" in ctype or "text" in ctype:
                    if reason := _looks_dead(r.text[:BODY_SNIFF_BYTES]):
                        return LinkResult(url, LinkStatus.DEAD, r.status_code, reason)

                return LinkResult(url, LinkStatus.ALIVE, r.status_code)

        except (httpx.HTTPError, UnicodeDecodeError) as exc:
            return LinkResult(url, LinkStatus.UNKNOWN, None, type(exc).__name__)

    async def _check_impersonated(self, url: str, headers: dict) -> LinkResult:
        """Retry behind a browser TLS fingerprint before judging a link."""
        def _do():
            from curl_cffi import requests as cffi

            return cffi.get(
                url, headers=headers, timeout=self.timeout, impersonate="chrome124"
            )

        try:
            r = await asyncio.to_thread(_do)
        except Exception as exc:  # noqa: BLE001 - blocked is not dead
            return LinkResult(url, LinkStatus.UNKNOWN, None, f"blocked: {type(exc).__name__}")

        if r.status_code in DEAD_CODES:
            return LinkResult(url, LinkStatus.DEAD, r.status_code, "http status")
        if r.status_code >= 400:
            return LinkResult(url, LinkStatus.UNKNOWN, r.status_code, "blocked")
        try:
            if reason := _looks_dead(r.text[:BODY_SNIFF_BYTES]):
                return LinkResult(url, LinkStatus.DEAD, r.status_code, reason)
        except Exception:  # noqa: BLE001
            pass
        return LinkResult(url, LinkStatus.ALIVE, r.status_code)

    async def check_many(self, urls: list[str]) -> dict[str, LinkResult]:
        results = await asyncio.gather(*(self.check(u) for u in urls))
        return {r.url: r for r in results}
