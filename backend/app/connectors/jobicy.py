"""Jobicy - https://jobicy.com/api/v2/remote-jobs

Free REST API returning worldwide and international remote vacancies
across engineering, tech, and software development.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from selectolax.parser import HTMLParser

from app.connectors.base import Connector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)

API = "https://jobicy.com/api/v2/remote-jobs"

_REGION_MAP = {
    "anywhere": HiringRegion.WORLDWIDE,
    "worldwide": HiringRegion.WORLDWIDE,
    "global": HiringRegion.WORLDWIDE,
    "india": HiringRegion.INDIA,
    "asia": HiringRegion.APAC,
    "apac": HiringRegion.APAC,
    "usa": HiringRegion.US_ONLY,
    "united states": HiringRegion.US_ONLY,
    "canada": HiringRegion.CANADA,
    "europe": HiringRegion.EUROPE,
    "uk": HiringRegion.EUROPE,
    "united kingdom": HiringRegion.EUROPE,
    "germany": HiringRegion.EUROPE,
    "emea": HiringRegion.EMEA,
    "latam": HiringRegion.LATAM,
    "latin america": HiringRegion.LATAM,
}

_TYPE_MAP = {
    "full-time": EmploymentType.FULL_TIME,
    "full time": EmploymentType.FULL_TIME,
    "internship": EmploymentType.INTERNSHIP,
    "intern": EmploymentType.INTERNSHIP,
    "contract": EmploymentType.CONTRACT,
    "part-time": EmploymentType.PART_TIME,
}


def _regions(geo: str | None) -> list[HiringRegion]:
    text = (geo or "").strip().lower()
    if not text or text in {"anywhere", "worldwide", "everywhere", "global"}:
        return [HiringRegion.WORLDWIDE]
    hits: list[HiringRegion] = []
    for needle, region in _REGION_MAP.items():
        if needle in text and region not in hits:
            hits.append(region)
    return hits or [HiringRegion.UNKNOWN]


class Jobicy(Connector):
    name = "jobicy"
    tier = 1
    full_listing = False
    rate_limit_delay = 0.3

    async def fetch(self) -> Iterable[RawJob]:
        # Query engineering remote jobs
        url = f"{API}?count=100&industry=engineering"
        payload = await self.get_json(url)
        if not isinstance(payload, dict):
            return []

        items = payload.get("jobs", [])
        if not isinstance(items, list):
            return []

        jobs: list[RawJob] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("id") or not item.get("jobTitle"):
                continue

            posted = None
            if raw_date := item.get("pubDate"):
                try:
                    posted = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                except ValueError:
                    posted = None

            desc_raw = item.get("jobDescription") or item.get("jobExcerpt") or ""
            description = HTMLParser(desc_raw).text(separator="\n", strip=True) if desc_raw else None

            emp_type = EmploymentType.UNKNOWN
            if t := item.get("jobType"):
                emp_type = _TYPE_MAP.get(str(t).lower().strip(), EmploymentType.UNKNOWN)

            min_yoe_hint = None
            level = str(item.get("jobLevel") or "").lower()
            if any(j in level for j in ("entry", "junior", "trainee", "intern", "student")):
                min_yoe_hint = 0

            geo = item.get("jobGeo") or "Anywhere"

            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=str(item["id"]),
                    url=item.get("url") or f"https://jobicy.com/jobs/{item['id']}",
                    title=item["jobTitle"],
                    company=item.get("companyName") or "",
                    description=description,
                    location_raw=f"Remote ({geo})",
                    tags=[str(t) for t in (item.get("jobIndustry") or [])]
                    + ([str(item["jobLevel"])] if item.get("jobLevel") else []),
                    posted_at=posted,
                    hiring_regions_hint=_regions(geo),
                    employment_type_hint=emp_type,
                    raw_payload={
                        "jobicy_id": item.get("id"),
                        "geo": geo,
                        "salary_min": item.get("annualSalaryMin"),
                        "salary_max": item.get("annualSalaryMax"),
                        "currency": item.get("salaryCurrency"),
                        "min_yoe_hint": min_yoe_hint,
                    },
                )
            )
        return jobs

