"""Amazon campus postings that its own search API refuses to return.

Amazon publishes some university/campus requisitions as link-only: the page
is public and the role is open, but the posting is absent from
`amazon.jobs/search.json`. Verified 2026-09-18 with
"Software Development Engineer Intern - Jan 2027 (6 month)" (id 10499554):
the page answers 200, while a search for that exact id, that exact title, or
"intern" + India returns nothing. Filtering by country is correct and not the
problem - `normalized_country_code=IND` returns only India rows, whereas
`loc_query=India` is fuzzy and returns Brazil and Mexico.

Since Amazon won't list them, find them the way a candidate does: look for
indexed job pages on amazon.jobs, then read each page directly, which
robots.txt permits (only /internal is disallowed). The pages carry title,
location ("IND, KA, Bengaluru") and description.

Discovery uses three sources, because no single one is dependable:

  * ids already stored from previous runs - the sturdiest, and the reason a
    throttled search never loses coverage. Each is re-read, so a filled role
    drops out by itself when its page 404s.
  * Common Crawl's index - no key, no throttling, but weeks behind.
  * DuckDuckGo's HTML endpoint - the freshest, and the one that throttles:
    after a dozen queries it answers 202 with a search form and no results
    (which is what the connector doctor caught).

Whatever answers contributes; nothing is required.

The same six-id sweep that found the example also found five more 2027
internships (Applied Scientist, Data Engineer, Systems Development, Support
Engineer, MBA) that the API never returned.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
from typing import Iterable

from app.connectors.base import Connector, FetchError
from app.connectors.common import BROWSER_UA, INTERN_TITLE, regions_from_text
from app.core.config import get_profile
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)

SEARCH = "https://html.duckduckgo.com/html/"
CDX = "https://index.commoncrawl.org/CC-MAIN-2026-34-index"
JOB_PAGE = "https://www.amazon.jobs/en/jobs/{id}"
_JOB_ID = re.compile(r"amazon\.jobs(?:/[a-z-]{2,5})?/jobs/(\d{6,9})")
_OG_TITLE = re.compile(r'<meta property="og:title" content="([^"]+)"')
_OG_DESC = re.compile(r'<meta property="og:description" content="([^"]+)"')
_LOCATION = re.compile(
    r'class="association location-icon".*?<ul class="association-content"><li>([^<]+)</li>', re.S
)
_CATEGORY = re.compile(
    r'class="association job-category-icon.*?<ul class="association-content"><li>([^<]+)</li>', re.S
)
# "IND, KA, Bengaluru" - Amazon's own country-first location format
_INDIA_LOC = re.compile(r"^\s*IND\b", re.I)

DEFAULT_QUERIES = (
    "site:amazon.jobs intern India 2027",
    "site:amazon.jobs internship Bengaluru",
    "site:amazon.jobs intern Hyderabad 2027",
    "site:amazon.jobs university talent India intern",
)
MAX_PAGES_PER_RUN = 40


class AmazonCampus(Connector):
    name = "amazon_campus"
    tier = 1
    impersonate = "chrome124"
    rate_limit_delay = 2.0     # a handful of search queries per run, politely spaced
    # a web search, not a listing: a live job can be absent from the results
    full_listing = False

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier
        cfg = get_profile().search
        self.queries: list[str] = list(cfg.get("amazon_campus_queries") or DEFAULT_QUERIES)

    async def smoke(self, limit: int = 3) -> list[RawJob]:
        """One query, a couple of pages read."""
        self.queries = self.queries[:1]
        self._smoke_limit = limit
        return list(await self.fetch())

    async def fetch(self) -> Iterable[RawJob]:
        known = self._stored_ids()
        found = await self._discover_ids()
        ids = known | found
        if not ids:
            # nothing known and nothing discoverable: say nothing rather than
            # let an empty result delete jobs found on a previous run
            self.covered_scopes = set()
            return []
        log.info("amazon_campus: %d ids (%d known, %d newly discovered)",
                 len(ids), len(known), len(found - known))

        jobs: list[RawJob] = []
        for job_id in sorted(ids)[: getattr(self, "_smoke_limit", None) or MAX_PAGES_PER_RUN]:
            job = await self._read_page(job_id)
            if job is not None:
                jobs.append(job)
            await asyncio.sleep(0.4)
        return jobs

    def _stored_ids(self) -> set[str]:
        """Campus pages found on earlier runs; each is re-read and re-verified."""
        try:
            from sqlalchemy import select

            from app.db.models import JobRow
            from app.db.session import session_scope

            with session_scope() as s:
                rows = s.scalars(select(JobRow.source_job_id).where(JobRow.source == self.name)).all()
            return {str(r) for r in rows}
        except Exception as exc:  # noqa: BLE001 - no database (tests, first run) is fine
            log.debug("amazon_campus: no stored ids (%s)", exc)
            return set()

    async def _discover_ids(self) -> set[str]:
        found: set[str] = set()
        found |= await self._from_common_crawl()
        found |= await self._from_search()
        return found

    async def _from_common_crawl(self) -> set[str]:
        """Amazon job pages in the public crawl index: no key, no throttling."""
        ids: set[str] = set()
        for page in range(2):
            try:
                text = await self.get_text(
                    CDX, params={"url": "amazon.jobs/*/jobs/*", "output": "json", "page": page},
                    headers={"User-Agent": BROWSER_UA},
                )
            except Exception as exc:  # noqa: BLE001 - the index 502s regularly
                log.debug("amazon_campus: common crawl page %d unavailable (%s)", page, exc)
                break
            if not (text or "").strip():
                break
            ids.update(_JOB_ID.findall(text))
        return ids

    async def _from_search(self) -> set[str]:
        """The freshest source, and the one that throttles - best effort only."""
        found: set[str] = set()
        for query in self.queries:
            try:
                page = await self.post_text(
                    SEARCH, data={"q": query},
                    headers={"User-Agent": BROWSER_UA, "Accept": "text/html"},
                )
            except Exception as exc:  # noqa: BLE001 - blocked search is not our failure
                log.warning("amazon_campus: search %r failed: %s", query, exc)
                continue
            hits = set(_JOB_ID.findall(page or ""))
            if not hits and "form" in (page or "")[:2000].lower():
                log.info("amazon_campus: search is throttling us; relying on known ids")
                break
            found |= hits
        return found

    async def _read_page(self, job_id: str) -> RawJob | None:
        url = JOB_PAGE.format(id=job_id)
        try:
            page = await self.get_text(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html"})
        except FetchError as exc:
            if exc.status_code in (404, 410):
                return None      # posting gone
            log.debug("amazon_campus: %s -> %s", job_id, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            log.debug("amazon_campus: %s failed: %s", job_id, exc)
            return None

        title_m = _OG_TITLE.search(page or "")
        loc_m = _LOCATION.search(page or "")
        if not title_m or not loc_m:
            return None
        location = html_lib.unescape(loc_m.group(1)).strip()
        if not _INDIA_LOC.search(location):
            return None          # this connector exists for the India roles

        title = html_lib.unescape(title_m.group(1)).strip()
        desc = _OG_DESC.search(page or "")
        category = _CATEGORY.search(page or "")
        intern = bool(INTERN_TITLE.search(title))
        return RawJob(
            source=self.name,
            source_job_id=job_id,
            url=url,
            title=title,
            company="Amazon",
            description=html_lib.unescape(desc.group(1)) if desc else None,
            location_raw=location,
            tags=[html_lib.unescape(category.group(1)).strip()] if category else [],
            posted_at=None,      # the page carries no date; "found on" is used instead
            hiring_regions_hint=regions_from_text(location) or [HiringRegion.INDIA],
            employment_type_hint=EmploymentType.INTERNSHIP if intern else EmploymentType.UNKNOWN,
            raw_payload={"link_only": True, **({"min_yoe_hint": 0} if intern else {})},
        )
