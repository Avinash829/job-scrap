"""Microsoft Careers (Eightfold "PCSX" search).

    GET apply.careers.microsoft.com/api/pcsx/search
        ?domain=microsoft.com&query=intern&location=India&start=0&sort_by=timestamp

The JSON call behind careers.microsoft.com. Ten positions per page, with
structured `standardizedLocations` ("Hyderabad, TS, IN"), `postedTs` (epoch
seconds) and `workLocationOption` (onsite|hybrid|remote). Verified 2026-09-14:
8 India internships for "intern", 108 India results for "software engineer".

No description in search results; the title gate and seniority rules work on
titles, which is enough to decide scope.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.connectors.common import BROWSER_UA, INTERN_TITLE, regions_from_text
from app.connectors.search_base import SearchConnector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://apply.careers.microsoft.com/api/pcsx/search"
SITE = "https://apply.careers.microsoft.com"
PAGE = 10


class MicrosoftCareers(SearchConnector):
    name = "microsoft"
    tier = 1
    concurrency = 3
    rate_limit_delay = 0.3

    async def search(self, term: str, location: str) -> list[RawJob]:
        jobs: list[RawJob] = []
        for page in range(self.max_pages):
            d = await self.get_json(
                API,
                params={"domain": "microsoft.com", "query": term, "location": location,
                        "start": page * PAGE, "sort_by": "timestamp"},
                headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            )
            data = (d or {}).get("data") or {}
            rows = data.get("positions") or []
            jobs.extend(self._to_raw(p) for p in rows if p.get("id") and p.get("name"))
            if len(rows) < PAGE or (page + 1) * PAGE >= int(data.get("count") or 0):
                break
        return jobs

    def _to_raw(self, p: dict) -> RawJob:
        locs = [str(x) for x in (p.get("locations") or [])]
        std = [str(x) for x in (p.get("standardizedLocations") or [])]
        loc_text = "; ".join(locs)
        regions = regions_from_text(loc_text)
        if any(s.endswith(", IN") for s in std) and HiringRegion.INDIA not in regions:
            regions = [HiringRegion.INDIA, *[r for r in regions if r is not HiringRegion.UNKNOWN]]

        posted = None
        if ts := p.get("postedTs") or p.get("creationTs"):
            posted = datetime.fromtimestamp(int(ts), tz=timezone.utc)

        title = str(p.get("name") or "")
        workplace = str(p.get("workLocationOption") or "")
        return RawJob(
            source=self.name,
            source_job_id=str(p["id"]),
            url=SITE + (p.get("positionUrl") or f"/careers/job/{p['id']}"),
            title=title,
            company="Microsoft",
            description=None,
            location_raw=(loc_text + (" · Remote" if workplace == "remote" else "")) or None,
            tags=[t for t in (p.get("department"), workplace) if t],
            posted_at=posted,
            hiring_regions_hint=regions,
            employment_type_hint=(
                EmploymentType.INTERNSHIP if INTERN_TITLE.search(title) else EmploymentType.UNKNOWN
            ),
            raw_payload={"job_number": p.get("displayJobId"), "workplace": workplace},
        )
