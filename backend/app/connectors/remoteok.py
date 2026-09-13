"""RemoteOK - https://remoteok.com/api

Quirk: the first element of the array is a legal/attribution notice, not a job.
Their terms ask for attribution when displaying their listings - the frontend
should credit RemoteOK on any card whose source is `remoteok`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.connectors.base import Connector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://remoteok.com/api"


def _regions(location: str | None) -> list[HiringRegion]:
    loc = (location or "").strip().lower()
    if not loc or loc in {"worldwide", "anywhere", "remote", "global"}:
        return [HiringRegion.WORLDWIDE]
    hits: list[HiringRegion] = []
    for needle, region in (
        ("worldwide", HiringRegion.WORLDWIDE),
        ("anywhere", HiringRegion.WORLDWIDE),
        ("united states", HiringRegion.US_ONLY),
        ("usa", HiringRegion.US_ONLY),
        ("us only", HiringRegion.US_ONLY),
        ("canada", HiringRegion.CANADA),
        ("emea", HiringRegion.EMEA),
        ("europe", HiringRegion.EUROPE),
        ("apac", HiringRegion.APAC),
        ("asia", HiringRegion.APAC),
        ("india", HiringRegion.INDIA),
        ("latam", HiringRegion.LATAM),
    ):
        if needle in loc and region not in hits:
            hits.append(region)
    return hits or [HiringRegion.UNKNOWN]


class RemoteOK(Connector):
    name = "remoteok"
    tier = 1

    async def fetch(self) -> Iterable[RawJob]:
        payload = await self.get_json(API)
        if not isinstance(payload, list):
            return []

        jobs: list[RawJob] = []
        for item in payload:
            # skip the attribution notice and anything malformed
            if not isinstance(item, dict) or "id" not in item or "position" not in item:
                continue

            posted = None
            if raw_date := item.get("date"):
                try:
                    posted = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                except ValueError:
                    posted = None

            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=str(item["id"]),
                    url=item.get("url") or f"https://remoteok.com/remote-jobs/{item['id']}",
                    title=item.get("position", ""),
                    company=item.get("company", ""),
                    description=item.get("description"),
                    location_raw=item.get("location"),
                    tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
                    salary_raw=item.get("salary"),
                    posted_at=posted,
                    hiring_regions_hint=_regions(item.get("location")),
                    employment_type_hint=EmploymentType.UNKNOWN,
                    raw_payload=item,
                )
            )
        return jobs
