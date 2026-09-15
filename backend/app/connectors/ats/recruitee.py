"""Recruitee boards.

    GET {slug}.recruitee.com/api/offers/

Verified 2026-09-15 on bunq: {"offers": [...]} with `country_code`, `remote`,
`hybrid`, `published_at` ("2026-09-15 08:11:57 UTC"), `employment_type_code`
(e.g. "internship"), `experience_code` ("entry_level"), description +
requirements HTML and a `careers_url`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.connectors.ats.base import ATSConnector
from app.connectors.common import INTERN_TITLE, html_to_text, regions_from_text
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion


def _ts(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc) if value else None
    except ValueError:
        return None


class Recruitee(ATSConnector):
    name = "recruitee"
    platform = "recruitee"
    tier = 1
    concurrency = 10

    def board_url(self, slug: str) -> str:
        return f"https://{slug}.recruitee.com/api/offers/"

    def parse(self, company: Company, payload) -> list[RawJob]:
        jobs: list[RawJob] = []
        for o in payload.get("offers") or []:
            if not isinstance(o, dict) or not o.get("id") or o.get("status") not in (None, "published"):
                continue
            location = str(o.get("location") or "")
            remote = bool(o.get("remote"))
            regions = regions_from_text(location)
            if o.get("country_code") == "IN" and HiringRegion.INDIA not in regions:
                regions = [HiringRegion.INDIA]
            title = str(o.get("title") or "")
            kind = str(o.get("employment_type_code") or "")
            intern = "intern" in kind or bool(INTERN_TITLE.search(title))
            desc = "\n\n".join(t for t in (html_to_text(o.get("description")), html_to_text(o.get("requirements"))) if t)
            jobs.append(RawJob(
                source=self.name,
                source_job_id=f"{company.slug}:{o['id']}",
                url=str(o.get("careers_url") or f"https://{company.slug}.recruitee.com/o/{o.get('slug')}"),
                title=title,
                company=company.get("name") or o.get("company_name") or company.slug,
                description=desc or None,
                location_raw=(location + (" · Remote" if remote else "")) or None,
                tags=[t for t in (o.get("department"), *(o.get("tags") or [])) if t],
                posted_at=_ts(o.get("published_at") or o.get("created_at")),
                hiring_regions_hint=regions,
                employment_type_hint=EmploymentType.INTERNSHIP if intern else EmploymentType.UNKNOWN,
                raw_payload={
                    "ats": "recruitee", "ats_slug": company.slug, "remote": remote,
                    **({"min_yoe_hint": 0} if intern or o.get("experience_code") == "entry_level" else {}),
                },
            ))
        return jobs
