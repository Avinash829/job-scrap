"""Greenhouse Job Board API - https://docs.greenhouse.io/job-board.html

    GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

Verified response: {"jobs": [...], "meta": {...}}, one call returns the whole
board (no pagination). Fields that matter to us:

    title           clean role title
    location.name   "Bengaluru, Karnataka, India"  <- structured enough to
                    resolve region without any LLM inference
    first_published real posting date (updated_at changes on any edit, so
                    first_published is the honest one for a freshness filter)
    absolute_url    direct apply link on the employer's own board
    content         full HTML description (html-escaped)
    offices/departments  extra location + team signal
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Iterable

from selectolax.parser import HTMLParser

from app.connectors.ats.base import ATSConnector
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

# Word-boundary matching is essential here: a substring check tagged
# "Full Stack Engineer - Internal Audit" and "Software Engineer, Internal
# Systems" as internships, because both contain "intern" inside "Internal".
_INTERN_TITLE = re.compile(
    r"\b(intern|interns|internship|co-?op|apprentice|trainee|summer\s+analyst)\b",
    re.I,
)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def regions_from_location(text: str) -> list[HiringRegion]:
    """Greenhouse location strings are human but consistent enough to map."""
    t = (text or "").lower()
    hits: list[HiringRegion] = []

    def add(r: HiringRegion) -> None:
        if r not in hits:
            hits.append(r)

    if any(k in t for k in ("india", "bengaluru", "bangalore", "hyderabad",
                            "pune", "chennai", "mumbai", "gurgaon", "noida",
                            "delhi", "kolkata")):
        add(HiringRegion.INDIA)
    if any(k in t for k in ("worldwide", "anywhere", "global", "remote - global")):
        add(HiringRegion.WORLDWIDE)
    if any(k in t for k in ("united states", "usa", ", ca", ", ny", ", tx",
                            ", wa", ", ma", "san francisco", "new york")):
        add(HiringRegion.US_ONLY)
    if any(k in t for k in ("united kingdom", "uk", "london", "ireland",
                            "dublin", "germany", "berlin", "munich", "france",
                            "paris", "netherlands", "amsterdam", "spain",
                            "poland", "portugal", "europe")):
        add(HiringRegion.EUROPE)
    if any(k in t for k in ("singapore", "japan", "tokyo", "australia",
                            "sydney", "apac")):
        add(HiringRegion.APAC)
    if "canada" in t or "toronto" in t or "vancouver" in t:
        add(HiringRegion.CANADA)
    return hits or [HiringRegion.UNKNOWN]



_BOARD_SUFFIX = re.compile(r"\s*[-|:]?\s*(job\s*board|careers?|jobs)\s*$", re.I)


def _clean_board_name(name: str | None) -> str | None:
    """Greenhouse board titles are often "Rubrik Job Board" or "Acme Careers"."""
    if not name:
        return None
    return _BOARD_SUFFIX.sub("", name).strip() or name

class Greenhouse(ATSConnector):
    name = "greenhouse"
    platform = "greenhouse"
    tier = 1
    concurrency = 30          # safe ceiling used by large public aggregators

    def board_url(self, slug: str) -> str:
        return API.format(slug=slug)

    def parse(self, company: Company, payload) -> list[RawJob]:
        jobs: list[RawJob] = []
        for item in payload.get("jobs", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue

            location = (item.get("location") or {}).get("name") or ""
            offices = [
                o.get("name", "")
                for o in (item.get("offices") or [])
                if isinstance(o, dict)
            ]
            departments = [
                d.get("name", "")
                for d in (item.get("departments") or [])
                if isinstance(d, dict)
            ]
            loc_blob = " ".join([location, *offices])

            raw_html = html.unescape(item.get("content") or "")
            description = HTMLParser(raw_html).text(separator="\n", strip=True)

            title = item.get("title", "")
            emp = (
                EmploymentType.INTERNSHIP
                if _INTERN_TITLE.search(title)
                else EmploymentType.UNKNOWN
            )

            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=f"{company.slug}:{item['id']}",
                    url=item.get("absolute_url", ""),
                    title=title,
                    company=company.get("name") or _clean_board_name(item.get("company_name")) or company.slug,
                    description=description,
                    location_raw=location or ", ".join(offices) or None,
                    tags=departments,
                    # first_published, not updated_at: an edit bumps
                    # updated_at and would fake freshness
                    posted_at=parse_iso(item.get("first_published"))
                    or parse_iso(item.get("updated_at")),
                    hiring_regions_hint=regions_from_location(loc_blob),
                    employment_type_hint=emp,
                    raw_payload={
                        "id": item["id"],
                        "requisition_id": item.get("requisition_id"),
                        "offices": offices,
                        "departments": departments,
                        "ats": "greenhouse",
                        "ats_slug": company.slug,
                    },
                )
            )
        return jobs
