"""Instahyre - curated Indian tech hiring (employers are screened; roles are paid).

    GET www.instahyre.com/api/v1/job_search?company_size=0&isLandingPage=true
        &years=0&limit=20&offset=N

The public search behind instahyre.com/search-jobs. Verified 2026-09-16:
13,609 active jobs, 570 open to 0 years of experience, 52 internships.
`years` is the candidate's experience, so years=0 and years=1 return roles a
fresher or near-graduate can take. Rows carry title, employer.company_name,
`locations` ("Bangalore, Work From Home"), `keywords` (skills) and a
public_url; there is no posting date, and every listed job is active.
"""
from __future__ import annotations

import asyncio
from typing import Iterable

from app.connectors.base import Connector, FetchError
from app.connectors.common import BROWSER_UA, INTERN_TITLE, regions_from_text
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://www.instahyre.com/api/v1/job_search"
PAGE = 20          # the API caps limit at 20
MAX_PAGES = 60


class Instahyre(Connector):
    name = "instahyre"
    tier = 1
    impersonate = "chrome124"
    rate_limit_delay = 1.5

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier

    # paged queries capped at MAX_PAGES and cut short by rate limits
    full_listing = False

    async def fetch(self) -> Iterable[RawJob]:
        unique: dict[int, RawJob] = {}
        self.covered_scopes = None
        for params in ({"years": 0}, {"years": 1}, {"job_type": 2}):
            for page in range(MAX_PAGES):
                try:
                    d = await self.get_json(
                        API,
                        params={"company_size": 0, "isLandingPage": "true", "limit": PAGE, "offset": page * PAGE, **params},
                        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                    )
                except FetchError as exc:
                    if exc.status_code == 429:
                        # rate-limited: keep what we have, pause, move to the next query.
                        # The run is now partial, so nothing is judged closed.
                        self.covered_scopes = set()
                        await asyncio.sleep(20)
                        break
                    raise
                rows = (d or {}).get("objects") or []
                for o in rows:
                    if o.get("id") and o.get("id") not in unique:
                        unique[o["id"]] = self._to_raw(o, intern_query="job_type" in params, years=params.get("years"))
                if len(rows) < PAGE or not ((d or {}).get("meta") or {}).get("next"):
                    break
        return list(unique.values())

    def _to_raw(self, o: dict, intern_query: bool, years: int | None) -> RawJob:
        employer = o.get("employer") or {}
        locs = str(o.get("locations") or "")
        remote = "work from home" in locs.lower()
        regions = regions_from_text(locs)
        if HiringRegion.INDIA not in regions:
            # Instahyre only lists roles in India (or remote for Indian employers)
            regions = [HiringRegion.INDIA]
        title = str(o.get("title") or o.get("candidate_title") or "").strip()
        intern = intern_query or bool(INTERN_TITLE.search(title))
        skills = [str(k) for k in (o.get("keywords") or []) if k]
        return RawJob(
            source=self.name,
            source_job_id=str(o["id"]),
            url=str(o.get("public_url") or f"https://www.instahyre.com/job-{o['id']}/"),
            title=title,
            company=str(employer.get("company_name") or ""),
            description=("Skills: " + ", ".join(skills)) if skills else None,
            location_raw=locs.replace("Work From Home", "Remote") or None,
            tags=skills,
            posted_at=None,
            hiring_regions_hint=regions,
            employment_type_hint=EmploymentType.INTERNSHIP if intern else EmploymentType.UNKNOWN,
            raw_payload={"remote": remote, "min_yoe_hint": 0 if (intern or years == 0) else 1},
        )
