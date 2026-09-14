"""Workable's public job search - every company on Workable, in one query.

    GET jobs.workable.com/api/v1/jobs?query=intern&location=India[&pageToken=...]

Workable hosts ~30k employers. Instead of discovering each board, this
searches all of them at once, with structured locations
({"city": "Bengaluru", "subregion": "Karnataka", "countryName": "India"}).
Verified 2026-09-14: 84 India results for "intern", including
"Forward Deployed Engineer - Intern" at Blue Machines AI, Bengaluru.

Paging is cursor-based via `nextPageToken`.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.connectors.common import BROWSER_UA, html_to_text, regions_from_text
from app.connectors.search_base import SearchConnector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://jobs.workable.com/api/v1/jobs"
_INTERN = re.compile(r"\b(intern|internship|trainee|apprentice)\b", re.I)


class WorkableSearch(SearchConnector):
    name = "workable"
    tier = 1
    concurrency = 3

    async def search(self, term: str, location: str) -> list[RawJob]:
        jobs: list[RawJob] = []
        token: str | None = None
        for _ in range(self.max_pages):
            params = {"query": term, "location": location}
            if token:
                params["pageToken"] = token
            d = await self.get_json(API, params=params, headers={"User-Agent": BROWSER_UA})
            rows = (d or {}).get("jobs") or []
            jobs.extend(self._to_raw(j) for j in rows if j.get("id"))
            token = (d or {}).get("nextPageToken")
            if not token or not rows:
                break
        return jobs

    def _to_raw(self, j: dict) -> RawJob:
        loc = j.get("location") or {}
        loc_text = ", ".join(x for x in (loc.get("city"), loc.get("subregion"), loc.get("countryName")) if x)
        loc_text = loc_text or "; ".join(str(l) for l in (j.get("locations") or []))
        workplace = str(j.get("workplace") or "")
        regions = regions_from_text(loc_text)
        if str(loc.get("countryName") or "").lower() == "india" and HiringRegion.INDIA not in regions:
            regions = [HiringRegion.INDIA]

        posted = None
        if raw := j.get("created"):
            try:
                posted = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                posted = None

        title = j.get("title") or ""
        emp_raw = str(j.get("employmentType") or "").lower()
        if _INTERN.search(title) or "intern" in emp_raw:
            emp = EmploymentType.INTERNSHIP
        elif "full" in emp_raw:
            emp = EmploymentType.FULL_TIME
        else:
            emp = EmploymentType.UNKNOWN

        return RawJob(
            source=self.name,
            source_job_id=str(j["id"]),
            url=j.get("url") or "",
            title=title,
            company=(j.get("company") or {}).get("title") or "",
            description=html_to_text(j.get("description")),
            location_raw=(loc_text + (" · Remote" if workplace == "remote" else "")) or None,
            tags=[t for t in (j.get("department"), workplace) if t],
            posted_at=posted,
            hiring_regions_hint=regions,
            employment_type_hint=emp,
            raw_payload={"workplace": workplace},
        )
