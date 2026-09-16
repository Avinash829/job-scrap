"""Arbeitnow - https://www.arbeitnow.com/api/job-board-api

Free, paginated, no auth. Europe-heavy, and notable for you because many
EU postings explicitly mention visa sponsorship and relocation - which the
work_auth detector picks up and turns into a reachable-role signal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from selectolax.parser import HTMLParser

from app.connectors.base import Connector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://www.arbeitnow.com/api/job-board-api"
MAX_PAGES = 5  # ~100/page; plenty for a personal tool, and stays polite


def _regions(location: str | None, remote: bool) -> list[HiringRegion]:
    loc = (location or "").lower()
    if "india" in loc:
        return [HiringRegion.INDIA]
    if remote and ("worldwide" in loc or "anywhere" in loc):
        return [HiringRegion.WORLDWIDE]
    if any(x in loc for x in ("germany", "berlin", "munich", "netherlands", "amsterdam",
                              "france", "paris", "spain", "poland", "portugal")):
        return [HiringRegion.EUROPE]
    return [HiringRegion.UNKNOWN]


class Arbeitnow(Connector):
    name = "arbeitnow"
    tier = 3
    # a recent-postings feed, not a complete list of open roles
    full_listing = False
    rate_limit_delay = 0.5

    async def fetch(self) -> Iterable[RawJob]:
        jobs: list[RawJob] = []

        for page in range(1, MAX_PAGES + 1):
            payload = await self.get_json(f"{API}?page={page}")
            items = (payload or {}).get("data", [])
            if not items:
                break

            for item in items:
                if not isinstance(item, dict) or not item.get("slug"):
                    continue

                posted = None
                if ts := item.get("created_at"):
                    try:
                        posted = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                    except (TypeError, ValueError, OSError):
                        posted = None

                description = HTMLParser(item.get("description") or "").text(
                    separator="\n", strip=True
                )
                is_remote = bool(item.get("remote"))
                types = [str(t).lower() for t in (item.get("job_types") or [])]

                jobs.append(
                    RawJob(
                        source=self.name,
                        source_job_id=str(item["slug"]),
                        url=item.get("url", ""),
                        title=item.get("title", ""),
                        company=item.get("company_name", ""),
                        description=description,
                        location_raw=item.get("location"),
                        tags=[str(t) for t in (item.get("tags") or []) + types],
                        posted_at=posted,
                        hiring_regions_hint=_regions(item.get("location"), is_remote),
                        employment_type_hint=(
                            EmploymentType.INTERNSHIP
                            if any("intern" in t for t in types)
                            else EmploymentType.UNKNOWN
                        ),
                        raw_payload={
                            k: v for k, v in item.items() if k != "description"
                        },
                    )
                )
        return jobs
