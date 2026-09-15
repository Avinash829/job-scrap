"""Keka Hire career portals - used by a large share of Indian startups.

    GET {slug}.keka.com/careers/                      -> portal id in
        fetch('/ats/documents/{portal_id}/careerportal/...')
    GET {slug}.keka.com/careers/api/embedjobs/default/active/{portal_id}

Two requests per company. Verified 2026-09-15 on Jupiter Money: a bare array
with title, HTML description, `jobLocations[]` (city, countryCode "IN"),
`experience` ("1-3 Years"), `skillNames`, `publishedOn` and `jobType`.
Job page: {slug}.keka.com/careers/jobdetails/{id}
"""
from __future__ import annotations

import re

from app.connectors.ats.base import ATSConnector, BoardResult, BoardStatus
from app.connectors.ats.greenhouse import parse_iso
from app.connectors.base import FetchError
from app.connectors.common import BROWSER_UA, INTERN_TITLE, html_to_text, regions_from_text
from app.core import yc_index
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

_PORTAL_ID = re.compile(r"/ats/documents/([0-9a-f-]{36})/careerportal", re.I)
_EXPERIENCE = re.compile(r"(\d+)\s*(?:-|to|–)?\s*(\d+)?\s*(?:\+\s*)?years?", re.I)


class Keka(ATSConnector):
    name = "keka"
    platform = "keka"
    tier = 1
    concurrency = 6

    def board_url(self, slug: str) -> str:
        return f"https://{slug}.keka.com/careers/"

    async def fetch_board(self, company: Company) -> BoardResult:
        slug = company.slug
        try:
            page = await self.get_text(self.board_url(slug), headers={"User-Agent": BROWSER_UA, "Accept": "text/html"})
        except FetchError as exc:
            code = getattr(exc, "status_code", None)
            status = BoardStatus.NO_BOARD if code in (404, 410) else BoardStatus.FETCH_FAILED
            return BoardResult(company, status, http_code=code, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return BoardResult(company, BoardStatus.FETCH_FAILED, error=f"{type(exc).__name__}: {exc}")

        m = _PORTAL_ID.search(page or "")
        if not m:
            return BoardResult(company, BoardStatus.NO_BOARD, http_code=200, error="no Keka career portal on page")
        try:
            payload = await self.get_json(
                f"https://{slug}.keka.com/careers/api/embedjobs/default/active/{m.group(1)}",
                headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            )
        except Exception as exc:  # noqa: BLE001
            return BoardResult(company, BoardStatus.FETCH_FAILED, error=f"{type(exc).__name__}: {exc}")

        jobs = self.parse(company, payload)
        if not jobs:
            return BoardResult(company, BoardStatus.EMPTY_BOARD, http_code=200)
        if meta := yc_index.lookup(slug):
            for job in jobs:
                job.raw_payload.update(meta)
        return BoardResult(company, BoardStatus.OK, jobs, 200)

    def parse(self, company: Company, payload) -> list[RawJob]:
        jobs: list[RawJob] = []
        for j in payload if isinstance(payload, list) else []:
            if not isinstance(j, dict) or not j.get("id"):
                continue
            locs = [l for l in (j.get("jobLocations") or []) if isinstance(l, dict)]
            loc_text = "; ".join(
                ", ".join(x for x in (l.get("city") or l.get("name"), l.get("countryName")) if x) for l in locs
            )
            regions = regions_from_text(loc_text)
            if any(l.get("countryCode") == "IN" for l in locs) and HiringRegion.INDIA not in regions:
                regions = [HiringRegion.INDIA]
            title = str(j.get("title") or "").strip()
            intern = bool(INTERN_TITLE.search(title))

            payload_extra: dict = {"ats": "keka", "ats_slug": company.slug, "experience": j.get("experience")}
            if exp := _EXPERIENCE.search(str(j.get("experience") or "")):
                payload_extra["min_yoe_hint"] = int(exp.group(1))
            elif intern or "fresher" in str(j.get("experience") or "").lower():
                payload_extra["min_yoe_hint"] = 0

            skills = [str(s) for s in (j.get("skillNames") or []) if s]
            desc = html_to_text(j.get("description"))
            if skills:
                desc = f"{desc or ''}\n\nSkills: {', '.join(skills)}".strip()
            jobs.append(RawJob(
                source=self.name,
                source_job_id=f"{company.slug}:{j['id']}",
                url=f"https://{company.slug}.keka.com/careers/jobdetails/{j['id']}",
                title=title,
                company=company.get("name") or company.slug,
                description=desc or None,
                location_raw=loc_text or None,
                tags=[t for t in (j.get("departmentName"), *skills) if t],
                posted_at=parse_iso(j.get("publishedOn")),
                hiring_regions_hint=regions,
                employment_type_hint=EmploymentType.INTERNSHIP if intern else EmploymentType.UNKNOWN,
                raw_payload=payload_extra,
            ))
        return jobs
