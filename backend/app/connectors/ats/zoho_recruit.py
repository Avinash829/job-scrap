"""Zoho Recruit career sites - common with Indian companies.

    GET https://{slug}/jobs/Careers        slug = full host, e.g. eplane.zohorecruit.in

The page embeds every open job as HTML-escaped JSON in
`<input type="hidden" value="[...]" id="jobs">`. Verified 2026-09-16 on
The ePlane Company: 82 jobs with Posting_Title, City/State/Country,
Remote_Job, Work_Experience ("2+ years"), Job_Type ("Full time",
"Internship"), Date_Opened and the full description.
Job page: https://{slug}/jobs/Careers/{id}
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

from app.connectors.ats.base import ATSConnector, BoardResult, BoardStatus
from app.connectors.base import FetchError
from app.connectors.common import BROWSER_UA, INTERN_TITLE, html_to_text, regions_from_text
from app.core import yc_index
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

_JOBS_INPUT = re.compile(r'<input type="hidden" value="(\[[^"]*?)"\s*id="jobs"')
_YEARS = re.compile(r"(\d+)")


def _day(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc) if value else None
    except ValueError:
        return None


class ZohoRecruit(ATSConnector):
    name = "zoho_recruit"
    platform = "zoho_recruit"
    tier = 1
    concurrency = 6
    impersonate = "chrome124"

    def board_url(self, slug: str) -> str:
        return f"https://{slug}/jobs/Careers"

    async def fetch_board(self, company: Company) -> BoardResult:
        try:
            page = await self.get_text(self.board_url(company.slug), headers={"User-Agent": BROWSER_UA, "Accept": "text/html"})
        except FetchError as exc:
            code = getattr(exc, "status_code", None)
            status = BoardStatus.NO_BOARD if code in (404, 410) else BoardStatus.FETCH_FAILED
            return BoardResult(company, status, http_code=code, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return BoardResult(company, BoardStatus.FETCH_FAILED, error=f"{type(exc).__name__}: {exc}")

        m = _JOBS_INPUT.search(page or "")
        if not m:
            return BoardResult(company, BoardStatus.NO_BOARD, http_code=200, error="no Zoho Recruit job list on page")
        try:
            payload = json.loads(html.unescape(m.group(1)))
        except json.JSONDecodeError:
            return BoardResult(company, BoardStatus.FETCH_FAILED, http_code=200, error="unparseable job list")
        jobs = self.parse(company, payload)
        if not jobs:
            return BoardResult(company, BoardStatus.EMPTY_BOARD, http_code=200)
        if meta := yc_index.lookup(company.slug.split(".")[0]):
            for job in jobs:
                job.raw_payload.update(meta)
        return BoardResult(company, BoardStatus.OK, jobs, 200)

    def parse(self, company: Company, payload) -> list[RawJob]:
        label = company.get("name") or company.slug.split(".")[0]
        jobs: list[RawJob] = []
        for j in payload if isinstance(payload, list) else []:
            if not isinstance(j, dict) or not j.get("id") or j.get("Publish") is False:
                continue
            loc = ", ".join(str(x) for x in (j.get("City"), j.get("State"), j.get("Country")) if x)
            remote = bool(j.get("Remote_Job"))
            regions = regions_from_text(loc)
            if str(j.get("Country") or "").lower() == "india" and HiringRegion.INDIA not in regions:
                regions = [HiringRegion.INDIA]
            title = str(j.get("Posting_Title") or j.get("Job_Opening_Name") or "").strip()
            kind = str(j.get("Job_Type") or "")
            intern = "intern" in kind.lower() or bool(INTERN_TITLE.search(title))
            exp = str(j.get("Work_Experience") or "")
            payload_extra: dict = {"ats": "zoho_recruit", "ats_slug": company.slug, "remote": remote, "experience": exp}
            if intern or "fresher" in exp.lower():
                payload_extra["min_yoe_hint"] = 0
            elif m := _YEARS.search(exp):
                payload_extra["min_yoe_hint"] = int(m.group(1))
            jobs.append(RawJob(
                source=self.name,
                source_job_id=f"{company.slug}:{j['id']}",
                url=f"https://{company.slug}/jobs/Careers/{j['id']}",
                title=title,
                company=label,
                description=html_to_text(j.get("Job_Description")),
                location_raw=(loc + (" · Remote" if remote else "")) or None,
                tags=[t for t in (j.get("Industry"), kind) if t],
                posted_at=_day(j.get("Date_Opened")),
                hiring_regions_hint=regions,
                employment_type_hint=EmploymentType.INTERNSHIP if intern else EmploymentType.UNKNOWN,
                raw_payload=payload_extra,
            ))
        return jobs
