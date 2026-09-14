"""Workday career sites - the platform behind most large enterprises.

    POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": "intern India"}

Not a documented API, but it's exactly what every Workday career site calls
from the browser. Verified 2026-09-14 against Nvidia, Salesforce, Adobe and
Intel - Adobe returned 13 India rows out of 20 for "intern India".

Traps, all of which fail silently:
  * `limit` is hard-capped at 20. Ask for 100 and you get HTTP 200 with an
    EMPTY jobPostings list - it looks exactly like "no jobs".
  * `postedOn` is human text ("Posted 14 Days Ago", "Posted 30+ Days Ago").
  * The tenant, `wd` shard and `site` name must all be exact; a wrong one
    returns 422 or 404 (Walmart's guessed site name 422'd).

Each tenant is one entry in companies.yaml under `workday:` with `wd` and
`site` keys. Locations are appended to the search text, because relevance
ranking otherwise buries India roles below thousands of US postings.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Iterable

from app.connectors.common import BROWSER_UA, parse_relative_posted, regions_from_text
from app.connectors.search_base import SearchConnector
from app.core.companies import Company, companies_for
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType

log = logging.getLogger(__name__)

PAGE_SIZE = 20          # hard cap - larger values return an empty list
_INTERN = re.compile(r"\b(intern|internship|co-?op|apprentice|trainee)\b", re.I)


class Workday(SearchConnector):
    name = "workday"
    tier = 1
    concurrency = 15

    def __init__(self, tier: int | None = None) -> None:
        super().__init__(tier)
        from app.core.config import get_profile

        cfg = get_profile().search
        self.terms = list(cfg.get("workday_terms") or self.terms)
        self.max_pages = int(cfg.get("workday_max_pages") or self.max_pages)

    async def fetch(self) -> Iterable[RawJob]:
        companies = companies_for("workday", self._tier_filter)
        if not companies:
            return []
        sem = asyncio.Semaphore(self.concurrency)

        async def one(company: Company, term: str, location: str) -> list[RawJob]:
            async with sem:
                try:
                    return await self.search_company(company, term, location)
                except Exception as exc:  # noqa: BLE001
                    log.warning("workday %s %r failed: %s", company.slug, term, exc)
                    return []

        batches = await asyncio.gather(
            *(one(c, t, l) for c in companies for t in self.terms for l in self.locations)
        )
        unique: dict[str, RawJob] = {}
        for batch in batches:
            for job in batch:
                unique.setdefault(job.source_job_id, job)
        return list(unique.values())

    async def search(self, term: str, location: str) -> list[RawJob]:  # pragma: no cover
        raise NotImplementedError("Workday searches per company; see fetch()")

    async def search_company(self, company: Company, term: str, location: str) -> list[RawJob]:
        tenant, wd, site = company.slug, company.get("wd"), company.get("site")
        if not (wd and site):
            log.warning("workday %s is missing `wd` or `site` in companies.yaml", tenant)
            return []
        host = f"https://{tenant}.{wd}.myworkdayjobs.com"
        endpoint = f"{host}/wday/cxs/{tenant}/{site}/jobs"
        # never fall back to `note` - for imported tenants it holds probe
        # stats ("India 13, intern/grad 1"), not a company name
        label = company.get("name") or tenant.replace("-", " ").title()

        jobs: list[RawJob] = []
        for page in range(self.max_pages):
            d = await self.post_json(
                endpoint,
                json={
                    "appliedFacets": {},
                    "limit": PAGE_SIZE,
                    "offset": page * PAGE_SIZE,
                    "searchText": f"{term} {location}".strip(),
                },
                headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            )
            rows = (d or {}).get("jobPostings") or []
            for p in rows:
                path = p.get("externalPath")
                title = p.get("title")
                if not (path and title):
                    continue
                loc = p.get("locationsText") or ""
                jobs.append(
                    RawJob(
                        source=self.name,
                        source_job_id=f"{tenant}:{site}:{path}",
                        url=f"{host}/{site}{path}",
                        title=title,
                        company=label,
                        description=None,
                        location_raw=loc or None,
                        tags=[str(b) for b in (p.get("bulletFields") or [])],
                        posted_at=parse_relative_posted(p.get("postedOn")),
                        hiring_regions_hint=regions_from_text(f"{loc} {title}"),
                        employment_type_hint=(
                            EmploymentType.INTERNSHIP if _INTERN.search(title) else EmploymentType.UNKNOWN
                        ),
                        raw_payload={"ats": "workday", "ats_slug": tenant, "posted_on": p.get("postedOn")},
                    )
                )
            total = int((d or {}).get("total") or 0)
            if len(rows) < PAGE_SIZE or (page + 1) * PAGE_SIZE >= total:
                break
        return jobs
