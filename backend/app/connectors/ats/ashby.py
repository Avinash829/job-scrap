"""Ashby Public Job Posting API
    https://developers.ashbyhq.com/docs/public-job-posting-api

    GET api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true

Verified response: {"apiVersion": ..., "jobs": [...]} with genuinely useful
structured fields:

    isRemote            boolean - a real remote flag, not prose
    employmentType      "FullTime" | "Intern" | "Contract" | ...
    location            primary location string
    secondaryLocations  additional locations (an Indian office often lives here)
    publishedAt         real posting date
    applyUrl / jobUrl   direct apply links
    compensation        structured when the employer opts in

Tightest rate limiter of the four platforms - concurrency 5.
"""
from __future__ import annotations

from datetime import datetime

from app.connectors.ats.base import ATSConnector
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

_EMPLOYMENT = {
    "fulltime": EmploymentType.FULL_TIME,
    "full_time": EmploymentType.FULL_TIME,
    "intern": EmploymentType.INTERNSHIP,
    "internship": EmploymentType.INTERNSHIP,
    "contract": EmploymentType.CONTRACT,
    "parttime": EmploymentType.PART_TIME,
    "temporary": EmploymentType.CONTRACT,
}

_REGION_KEYS: tuple[tuple[tuple[str, ...], HiringRegion], ...] = (
    (("india", "bengaluru", "bangalore", "hyderabad", "pune", "chennai",
      "mumbai", "gurgaon", "noida", "delhi"), HiringRegion.INDIA),
    (("worldwide", "anywhere", "global", "remote - global"), HiringRegion.WORLDWIDE),
    (("united states", "usa", "san francisco", "new york", "seattle",
      "austin", "boston"), HiringRegion.US_ONLY),
    (("united kingdom", "london", "germany", "berlin", "france", "paris",
      "netherlands", "amsterdam", "ireland", "dublin", "europe", "spain",
      "poland"), HiringRegion.EUROPE),
    (("singapore", "tokyo", "japan", "sydney", "australia", "apac"),
     HiringRegion.APAC),
    (("canada", "toronto", "vancouver"), HiringRegion.CANADA),
    (("brazil", "mexico", "latam", "argentina"), HiringRegion.LATAM),
)


def _regions(location: str, secondary: list[str], is_remote: bool) -> list[HiringRegion]:
    blob = " ".join([location, *secondary]).lower()
    hits: list[HiringRegion] = []
    for keys, region in _REGION_KEYS:
        if any(k in blob for k in keys) and region not in hits:
            hits.append(region)
    if not hits and is_remote:
        # remote with an unmappable location is genuinely unknown, not worldwide
        return [HiringRegion.UNKNOWN]
    return hits or [HiringRegion.UNKNOWN]


class Ashby(ATSConnector):
    name = "ashby"
    platform = "ashby"
    tier = 1
    concurrency = 5           # tightest limiter of the four
    rate_limit_delay = 0.3

    def board_url(self, slug: str) -> str:
        return API.format(slug=slug)

    def parse(self, company: Company, payload) -> list[RawJob]:
        jobs: list[RawJob] = []
        for item in payload.get("jobs", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if item.get("isListed") is False:
                continue

            location = item.get("location") or ""
            secondary = [
                s.get("location", "") if isinstance(s, dict) else str(s)
                for s in (item.get("secondaryLocations") or [])
            ]
            is_remote = bool(item.get("isRemote"))
            emp_raw = str(item.get("employmentType") or "").lower().replace(" ", "")

            posted = None
            if raw := item.get("publishedAt"):
                try:
                    posted = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except ValueError:
                    posted = None

            comp = item.get("compensation") or {}
            salary_raw = None
            if isinstance(comp, dict):
                summary = comp.get("compensationTierSummary") or comp.get("summary")
                if summary:
                    salary_raw = str(summary)

            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=f"{company.slug}:{item['id']}",
                    url=item.get("applyUrl") or item.get("jobUrl") or "",
                    title=item.get("title", ""),
                    company=company.get("name") or company.slug,
                    description=item.get("descriptionPlain")
                    or item.get("descriptionHtml"),
                    location_raw=", ".join(x for x in [location, *secondary] if x) or None,
                    tags=[
                        t
                        for t in (item.get("department"), item.get("team"),
                                  item.get("workplaceType"))
                        if t
                    ],
                    salary_raw=salary_raw,
                    posted_at=posted,
                    hiring_regions_hint=_regions(location, secondary, is_remote),
                    employment_type_hint=_EMPLOYMENT.get(
                        emp_raw, EmploymentType.UNKNOWN
                    ),
                    raw_payload={
                        "id": item["id"],
                        "ats": "ashby",
                        "ats_slug": company.slug,
                        "structured_remote": is_remote,
                        "secondary_locations": secondary,
                    },
                )
            )
        return jobs
