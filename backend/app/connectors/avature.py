"""Avature career portals (EA and many other large employers).

    GET {base}/SearchJobs/{term}?jobOffset=N        e.g. base = https://jobs.ea.com/en_US/careers

Server-rendered HTML; each result is an `article.article--result` with
structured spans. Verified 2026-09-14 on EA - "intern" returned both
"AI Full Stack Intern" (215939) and "Data and AI Engineering Intern" (215934):

    .list-item-location     "Hyderabad, India"
    .list-item-id           "Role ID 215939"
    .list-item-workerType   "Intern - Temporary Employee"

The portal's RSS feed (/SearchJobs/feed/) looks tempting but ignores the
search term and always returns the newest 20 roles, so HTML it is. Listings
carry no posting date; freshness falls back to when we first saw the role.

Each portal is a companies.yaml `avature:` entry with a `base` URL.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable
from urllib.parse import quote

from selectolax.parser import HTMLParser

from app.connectors.common import BROWSER_UA, regions_from_text
from app.connectors.search_base import SearchConnector
from app.core.companies import Company, companies_for
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType

log = logging.getLogger(__name__)
PAGE_SIZE = 20
_ROLE_ID = re.compile(r"(\d{3,})")


class Avature(SearchConnector):
    name = "avature"
    tier = 1
    concurrency = 3
    rate_limit_delay = 0.4

    async def fetch(self) -> Iterable[RawJob]:
        companies = companies_for("avature", self._tier_filter)
        # Avature search is keyword-only; location narrowing happens in the
        # region filter, so one location slot is enough per term.
        self.locations = self.locations[:1]
        return await self.fetch_per_company(
            companies, lambda c, term, _loc: self.search_portal(c, term), lambda c: f"avature:{c.slug}"
        )

    async def search(self, term: str, location: str) -> list[RawJob]:  # pragma: no cover
        raise NotImplementedError("Avature searches per portal; see fetch()")

    async def search_portal(self, company: Company, term: str) -> list[RawJob]:
        base = (company.get("base") or "").rstrip("/")
        if not base:
            log.warning("avature %s is missing `base` in companies.yaml", company.slug)
            return []
        label = company.get("name") or company.note or company.slug

        jobs: list[RawJob] = []
        for page in range(self.max_pages):
            html = await self.get_text(
                f"{base}/SearchJobs/{quote(term)}",
                params={"jobOffset": page * PAGE_SIZE} if page else None,
                headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US"},
            )
            articles = HTMLParser(html).css("article.article--result")
            for a in articles:
                link = a.css_first("a.link_result")
                if not link:
                    continue
                href = link.attributes.get("href") or ""
                title = link.text(strip=True)

                def span(cls: str) -> str:
                    node = a.css_first(f".list-item-{cls}")
                    return node.text(strip=True) if node else ""

                location = span("location")
                worker = span("workerType")
                id_match = _ROLE_ID.search(span("id")) or _ROLE_ID.search(href)
                role_id = id_match.group(1) if id_match else href

                is_intern = "intern" in worker.lower() or "intern" in title.lower()
                jobs.append(
                    RawJob(
                        source=self.name,
                        source_job_id=f"{company.slug}:{role_id}",
                        url=href,
                        title=title,
                        company=label,
                        description=None,
                        location_raw=location or None,
                        tags=[t for t in (worker,) if t],
                        posted_at=None,
                        hiring_regions_hint=regions_from_text(location),
                        employment_type_hint=(
                            EmploymentType.INTERNSHIP if is_intern else EmploymentType.UNKNOWN
                        ),
                        raw_payload={
                            "ats": "avature",
                            "ats_slug": company.slug,
                            "worker_type": worker,
                            **({"min_yoe_hint": 0} if is_intern else {}),
                        },
                    )
                )
            if len(articles) < PAGE_SIZE:
                break
        return jobs
