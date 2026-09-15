"""Rippling ATS boards.

    GET ats.rippling.com/api/v2/board/{slug}/jobs?page=N&pageSize=100

Verified 2026-09-15 on Rippling itself: {"items", "page", "pageSize",
"totalItems", "totalPages"}. Items carry name, url, department and structured
`locations[]` (countryCode, workplaceType ON_SITE|HYBRID|REMOTE) but no
posting date or description in the list call.
"""
from __future__ import annotations

from app.connectors.ats.base import ATSConnector, BoardResult, BoardStatus
from app.connectors.base import FetchError
from app.connectors.common import INTERN_TITLE, regions_from_text
from app.core import yc_index
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

PAGE = 100
MAX_PAGES = 10


class RipplingBoards(ATSConnector):
    name = "rippling"
    platform = "rippling"
    tier = 1
    concurrency = 6

    def board_url(self, slug: str) -> str:
        return f"https://ats.rippling.com/api/v2/board/{slug}/jobs?pageSize={PAGE}&page=0"

    async def fetch_board(self, company: Company) -> BoardResult:
        result = await super().fetch_board(company)
        if result.status is not BoardStatus.OK:
            return result
        # First page came through the shared path; read the remaining pages.
        try:
            first = await self.get_json(self.board_url(company.slug))
            for page in range(1, min(int(first.get("totalPages") or 1), MAX_PAGES)):
                more = await self.get_json(
                    f"https://ats.rippling.com/api/v2/board/{company.slug}/jobs?pageSize={PAGE}&page={page}"
                )
                result.jobs.extend(self.parse(company, more))
        except (FetchError, Exception):  # noqa: BLE001 - keep what page 0 gave us
            pass
        if meta := yc_index.lookup(company.slug):
            for job in result.jobs:
                job.raw_payload.update(meta)
        return result

    def extract_items(self, payload) -> list:
        return (payload or {}).get("items") or [] if isinstance(payload, dict) else []

    def parse(self, company: Company, payload) -> list[RawJob]:
        jobs: list[RawJob] = []
        for j in self.extract_items(payload):
            if not isinstance(j, dict) or not j.get("id"):
                continue
            locs = [l for l in (j.get("locations") or []) if isinstance(l, dict)]
            loc_text = "; ".join(str(l.get("name") or l.get("country") or "") for l in locs)
            remote = any(l.get("workplaceType") == "REMOTE" for l in locs)
            regions = regions_from_text(loc_text)
            if any(l.get("countryCode") == "IN" for l in locs) and HiringRegion.INDIA not in regions:
                regions = [HiringRegion.INDIA, *[r for r in regions if r is not HiringRegion.UNKNOWN]]
            title = str(j.get("name") or "")
            jobs.append(RawJob(
                source=self.name,
                source_job_id=f"{company.slug}:{j['id']}",
                url=str(j.get("url") or ""),
                title=title,
                company=company.get("name") or company.slug,
                description=None,
                location_raw=(loc_text + (" · Remote" if remote else "")) or None,
                tags=[t for t in ((j.get("department") or {}).get("name"),) if t],
                posted_at=None,
                hiring_regions_hint=regions,
                employment_type_hint=(
                    EmploymentType.INTERNSHIP if INTERN_TITLE.search(title) else EmploymentType.UNKNOWN
                ),
                raw_payload={"ats": "rippling", "ats_slug": company.slug, "remote": remote},
            ))
        return jobs
