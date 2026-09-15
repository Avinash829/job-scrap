"""Workable per-company boards.

    GET apply.workable.com/api/v1/widget/accounts/{slug}?details=true

The cross-company search (connectors/workable_search.py) only returns jobs
Workable chooses to surface for a query; this reads one employer's whole
board. Verified 2026-09-15 on Hugging Face: {"name", "description", "jobs"}
with `locations[].countryCode`, `telecommuting`, `employment_type`
("Full-time" / "Internship"), `published_on` and full HTML description.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.connectors.ats.base import ATSConnector
from app.connectors.common import INTERN_TITLE, html_to_text, regions_from_text
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion


def _day(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc) if value else None
    except ValueError:
        return None


class WorkableBoards(ATSConnector):
    name = "workable_boards"
    platform = "workable"
    tier = 1
    concurrency = 8

    def board_url(self, slug: str) -> str:
        return f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"

    def parse(self, company: Company, payload) -> list[RawJob]:
        label = company.get("name") or payload.get("name") or company.slug
        jobs: list[RawJob] = []
        for j in payload.get("jobs") or []:
            if not isinstance(j, dict) or not j.get("shortcode"):
                continue
            locs = [l for l in (j.get("locations") or []) if isinstance(l, dict)]
            text = "; ".join(
                ", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x) for l in locs
            ) or ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
            remote = bool(j.get("telecommuting"))
            regions = regions_from_text(text)
            if any(l.get("countryCode") == "IN" for l in locs) and HiringRegion.INDIA not in regions:
                regions = [HiringRegion.INDIA, *[r for r in regions if r is not HiringRegion.UNKNOWN]]
            title = str(j.get("title") or "")
            kind = str(j.get("employment_type") or "")
            intern = "intern" in kind.lower() or bool(INTERN_TITLE.search(title))
            jobs.append(RawJob(
                source=self.name,
                source_job_id=f"{company.slug}:{j['shortcode']}",
                url=str(j.get("url") or j.get("shortlink") or ""),
                title=title,
                company=label,
                description=html_to_text(j.get("description")),
                location_raw=(text + (" · Remote" if remote else "")) or None,
                tags=[t for t in (j.get("department"), j.get("function"), j.get("experience")) if t],
                posted_at=_day(j.get("published_on") or j.get("created_at")),
                hiring_regions_hint=regions,
                employment_type_hint=EmploymentType.INTERNSHIP if intern else EmploymentType.UNKNOWN,
                raw_payload={
                    "ats": "workable", "ats_slug": company.slug, "remote": remote,
                    **({"min_yoe_hint": 0} if intern or j.get("experience") in ("Entry level", "Internship") else {}),
                },
            ))
        return jobs
