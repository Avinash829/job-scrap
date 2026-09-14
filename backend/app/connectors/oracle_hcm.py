"""Oracle Recruiting Cloud (Oracle HCM "Candidate Experience") career sites.

    GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true&expand=requisitionList.secondaryLocations
        &finder=findReqs;siteNumber={site},keyword=intern,location=India,
                limit=25,offset=N,sortBy=POSTING_DATES_DESC

The same public REST call every Oracle-hosted career page makes. Used by
JPMorgan Chase and many banks and enterprises with large Indian offices.
Verified 2026-09-14 on JPMorgan Chase (host jpmc.fa.oraclecloud.com, site
CX_1001): 165 India results, with PostedDate, PrimaryLocationCountry "IN".

Public job URL: https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{Id}

Each employer is a companies.yaml `oracle:` entry with `host` and `site`.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Iterable

from app.connectors.common import BROWSER_UA, regions_from_text
from app.connectors.search_base import SearchConnector
from app.core.companies import Company, companies_for
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)
PAGE = 25
_INTERN = re.compile(r"\b(intern|internship|trainee|apprentice|summer\s+analyst)\b", re.I)


class OracleHCM(SearchConnector):
    name = "oracle"
    tier = 1
    concurrency = 4

    async def fetch(self) -> Iterable[RawJob]:
        companies = companies_for("oracle", self._tier_filter)
        sem = asyncio.Semaphore(self.concurrency)

        async def one(c: Company, term: str, location: str) -> list[RawJob]:
            async with sem:
                try:
                    return await self.search_site(c, term, location)
                except Exception as exc:  # noqa: BLE001
                    log.warning("oracle %s %r failed: %s", c.slug, term, exc)
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
        raise NotImplementedError("Oracle HCM searches per employer; see fetch()")

    async def search_site(self, c: Company, term: str, location: str) -> list[RawJob]:
        host, site = c.get("host"), c.get("site")
        if not (host and site):
            log.warning("oracle %s is missing `host` or `site`", c.slug)
            return []
        label = c.get("name") or c.slug
        endpoint = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"

        jobs: list[RawJob] = []
        for page in range(self.max_pages):
            finder = (
                f"findReqs;siteNumber={site},keyword={term},location={location},"
                f"limit={PAGE},offset={page * PAGE},sortBy=POSTING_DATES_DESC"
            )
            d = await self.get_json(
                endpoint,
                params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder},
                headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            )
            items = (d or {}).get("items") or [{}]
            reqs = items[0].get("requisitionList") or []
            for q in reqs:
                jid = q.get("Id")
                title = q.get("Title") or ""
                if not jid or not title:
                    continue
                loc = q.get("PrimaryLocation") or ""
                regions = regions_from_text(loc)
                if q.get("PrimaryLocationCountry") == "IN" and HiringRegion.INDIA not in regions:
                    regions = [HiringRegion.INDIA]
                posted = None
                if q.get("PostedDate"):
                    try:
                        posted = datetime.strptime(q["PostedDate"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        posted = None
                jobs.append(
                    RawJob(
                        source=self.name,
                        source_job_id=f"{c.slug}:{jid}",
                        url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}",
                        title=title,
                        company=label,
                        description=q.get("ShortDescriptionStr"),
                        location_raw=loc or None,
                        tags=[t for t in (q.get("JobFamily"), q.get("WorkerType")) if t],
                        posted_at=posted,
                        hiring_regions_hint=regions,
                        employment_type_hint=(
                            EmploymentType.INTERNSHIP if _INTERN.search(title) else EmploymentType.UNKNOWN
                        ),
                        raw_payload={"ats": "oracle", "ats_slug": c.slug},
                    )
                )
            total = int(items[0].get("TotalJobsCount") or 0)
            if len(reqs) < PAGE or (page + 1) * PAGE >= total:
                break
        return jobs
