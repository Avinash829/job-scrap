"""Remotive - https://remotive.com/api/remote-jobs

`candidate_required_location` is unusually honest about region, which makes
this one of the better sources for worldwide-reachable roles.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.connectors.base import Connector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://remotive.com/api/remote-jobs?limit=500"

_REGION_MAP = {
    "worldwide": HiringRegion.WORLDWIDE,
    "anywhere": HiringRegion.WORLDWIDE,
    "usa": HiringRegion.US_ONLY,
    "united states": HiringRegion.US_ONLY,
    "canada": HiringRegion.CANADA,
    "emea": HiringRegion.EMEA,
    "europe": HiringRegion.EUROPE,
    "uk": HiringRegion.EUROPE,
    "apac": HiringRegion.APAC,
    "asia": HiringRegion.APAC,
    "india": HiringRegion.INDIA,
    "latam": HiringRegion.LATAM,
    "latin america": HiringRegion.LATAM,
}

_TYPE_MAP = {
    "full_time": EmploymentType.FULL_TIME,
    "internship": EmploymentType.INTERNSHIP,
    "contract": EmploymentType.CONTRACT,
    "part_time": EmploymentType.PART_TIME,
    "freelance": EmploymentType.CONTRACT,
}


def _regions(raw: str | None) -> list[HiringRegion]:
    text = (raw or "").lower()
    if not text:
        return [HiringRegion.UNKNOWN]
    hits: list[HiringRegion] = []
    for needle, region in _REGION_MAP.items():
        if needle in text and region not in hits:
            hits.append(region)
    return hits or [HiringRegion.UNKNOWN]


class Remotive(Connector):
    name = "remotive"
    tier = 3
    # a recent-postings feed, not a complete list of open roles
    full_listing = False

    async def fetch(self) -> Iterable[RawJob]:
        payload = await self.get_json(API)
        items = (payload or {}).get("jobs", [])

        jobs: list[RawJob] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue

            posted = None
            if raw_date := item.get("publication_date"):
                try:
                    posted = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                except ValueError:
                    posted = None

            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=str(item["id"]),
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    description=item.get("description"),
                    location_raw=item.get("candidate_required_location"),
                    tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
                    salary_raw=item.get("salary"),
                    posted_at=posted,
                    hiring_regions_hint=_regions(item.get("candidate_required_location")),
                    employment_type_hint=_TYPE_MAP.get(
                        str(item.get("job_type", "")).lower(), EmploymentType.UNKNOWN
                    ),
                    raw_payload=item,
                )
            )
        return jobs
