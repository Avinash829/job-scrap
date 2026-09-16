"""Himalayas - https://himalayas.app/jobs/api

Strong remote-native source with an explicit `locationRestrictions` array,
which maps cleanly onto hiring_regions - exactly the signal that decides
whether a role is reachable from India.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from selectolax.parser import HTMLParser

from app.connectors.base import Connector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

# The API hard-caps at 20 per response and ignores `limit`, so page with
# `offset` instead. 10 pages = ~200 freshest postings, which is plenty.
API = "https://himalayas.app/jobs/api"
PAGE_SIZE = 20
MAX_PAGES = 10

_REGION_MAP = {
    "worldwide": HiringRegion.WORLDWIDE,
    "anywhere": HiringRegion.WORLDWIDE,
    "united states": HiringRegion.US_ONLY,
    "usa": HiringRegion.US_ONLY,
    "canada": HiringRegion.CANADA,
    "emea": HiringRegion.EMEA,
    "europe": HiringRegion.EUROPE,
    "united kingdom": HiringRegion.EUROPE,
    "germany": HiringRegion.EUROPE,
    "asia": HiringRegion.APAC,
    "apac": HiringRegion.APAC,
    "india": HiringRegion.INDIA,
    "latin america": HiringRegion.LATAM,
    "latam": HiringRegion.LATAM,
}

_TYPE_MAP = {
    "full-time": EmploymentType.FULL_TIME,
    "full time": EmploymentType.FULL_TIME,
    "internship": EmploymentType.INTERNSHIP,
    "contract": EmploymentType.CONTRACT,
    "part-time": EmploymentType.PART_TIME,
}


def _regions(restrictions: list | None) -> list[HiringRegion]:
    """An EMPTY restriction list on Himalayas means no restriction = worldwide."""
    if not restrictions:
        return [HiringRegion.WORLDWIDE]
    hits: list[HiringRegion] = []
    for entry in restrictions:
        text = str(entry).lower()
        for needle, region in _REGION_MAP.items():
            if needle in text and region not in hits:
                hits.append(region)
    return hits or [HiringRegion.UNKNOWN]


class Himalayas(Connector):
    name = "himalayas"
    tier = 1
    # a recent-postings feed, not a complete list of open roles
    full_listing = False
    rate_limit_delay = 0.4

    async def fetch(self) -> Iterable[RawJob]:
        items: list[dict] = []
        seen_guids: set[str] = set()

        for page in range(MAX_PAGES):
            payload = await self.get_json(f"{API}?offset={page * PAGE_SIZE}")
            batch = (payload or {}).get("jobs", [])
            if not batch:
                break
            # guard against the API ignoring offset and looping the same page
            fresh = [j for j in batch if str(j.get("guid")) not in seen_guids]
            if not fresh:
                break
            seen_guids.update(str(j.get("guid")) for j in fresh)
            items.extend(fresh)

        jobs: list[RawJob] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("guid"):
                continue

            posted = None
            if ts := item.get("pubDate"):
                try:
                    posted = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                except (TypeError, ValueError, OSError):
                    posted = None

            desc_html = item.get("description") or item.get("excerpt") or ""
            description = HTMLParser(desc_html).text(separator="\n", strip=True)

            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=str(item["guid"]),
                    url=item.get("applicationLink") or item.get("link", ""),
                    title=item.get("title", ""),
                    company=item.get("companyName", ""),
                    description=description,
                    location_raw=", ".join(
                        str(x) for x in (item.get("locationRestrictions") or [])
                    )
                    or "Worldwide",
                    tags=[
                        str(t)
                        for t in (item.get("categories") or []) + (item.get("skills") or [])
                    ],
                    posted_at=posted,
                    hiring_regions_hint=_regions(item.get("locationRestrictions")),
                    employment_type_hint=_TYPE_MAP.get(
                        str(item.get("employmentType", "")).lower(),
                        EmploymentType.UNKNOWN,
                    ),
                    raw_payload={
                        k: v for k, v in item.items() if k != "description"
                    },
                )
            )
        return jobs
