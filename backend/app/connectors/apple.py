"""Apple Jobs.

    GET jobs.apple.com/en-in/search?location=india-INDC&search=intern&page=N

The results page embeds its data as
`window.__staticRouterHydrationData = JSON.parse("...")`. The search API
(`/api/v1/search`) needs a CSRF token and still returned zero rows when
tried, so the embedded JSON is the reliable path. Verified 2026-09-14:
79 India roles for "intern". The search is loose (retail roles come back
too), so the role gate does the narrowing. Rows carry postingTitle,
postDateInGMT, positionId, team and structured locations.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from app.connectors.common import BROWSER_UA, INTERN_TITLE
from app.connectors.search_base import SearchConnector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

SEARCH = "https://jobs.apple.com/en-in/search"
# config.yaml location name -> Apple's location facet
LOCATION_IDS = {"india": "india-INDC"}
_HYDRATION = re.compile(
    r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\((\".*?\")\);", re.S
)


def _posted(value: str | None) -> datetime | None:
    if not value:
        return None
    # "2026-09-14T12:45:41.038245042Z": nanoseconds are too precise for fromisoformat
    v = re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


class AppleJobs(SearchConnector):
    name = "apple"
    tier = 1
    concurrency = 2
    rate_limit_delay = 0.5

    async def search(self, term: str, location: str) -> list[RawJob]:
        loc_id = LOCATION_IDS.get(location.lower())
        if not loc_id:
            return []
        jobs: list[RawJob] = []
        for page in range(1, self.max_pages + 1):
            html = await self.get_text(
                SEARCH,
                params={"location": loc_id, "search": term, "page": page},
                headers={"User-Agent": BROWSER_UA, "Accept": "text/html"},
            )
            m = _HYDRATION.search(html or "")
            if not m:
                break
            loader = json.loads(json.loads(m.group(1))).get("loaderData") or {}
            data = loader.get("search") or {}
            rows = data.get("searchResults") or []
            jobs.extend(self._to_raw(r) for r in rows if r.get("positionId"))
            if not rows or len(jobs) >= int(data.get("totalRecords") or 0):
                break
        return jobs

    def _to_raw(self, r: dict) -> RawJob:
        locs = r.get("locations") or []
        names = [
            ", ".join(x for x in (l.get("city"), l.get("stateProvince"), l.get("countryName")) if x)
            or str(l.get("name") or "")
            for l in locs
        ]
        india = any(str(l.get("countryID")) == "iso-country-IND" for l in locs)
        title = str(r.get("postingTitle") or "").strip()
        return RawJob(
            source=self.name,
            source_job_id=str(r["positionId"]),
            url=f"https://jobs.apple.com/en-in/details/{r['positionId']}/{r.get('transformedPostingTitle') or ''}",
            title=title,
            company="Apple",
            description=r.get("jobSummary"),
            location_raw="; ".join(n for n in names if n) or None,
            tags=[t for t in ((r.get("team") or {}).get("teamName"),) if t],
            posted_at=_posted(r.get("postDateInGMT")),
            hiring_regions_hint=[HiringRegion.INDIA] if india else [],
            employment_type_hint=(
                EmploymentType.INTERNSHIP if INTERN_TITLE.search(title) else EmploymentType.UNKNOWN
            ),
            raw_payload={"home_office": r.get("homeOffice")},
        )
