"""Gem job boards (a Greenhouse-shaped API used by many US startups).

    GET api.gem.com/job_board/v0/{slug}/job_posts/

Verified 2026-09-15: a bare array; each post has title, `location.name`,
`location_type` (remote|hybrid|onsite), `employment_type` (full_time|intern…),
`first_published_at`, `content_plain` and an `absolute_url`.
"""
from __future__ import annotations

from app.connectors.ats.base import ATSConnector
from app.connectors.ats.greenhouse import parse_iso
from app.connectors.common import INTERN_TITLE, regions_from_text
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType


class GemBoards(ATSConnector):
    name = "gem"
    platform = "gem"
    tier = 1
    concurrency = 8

    def board_url(self, slug: str) -> str:
        return f"https://api.gem.com/job_board/v0/{slug}/job_posts/"

    def parse(self, company: Company, payload) -> list[RawJob]:
        jobs: list[RawJob] = []
        for p in payload if isinstance(payload, list) else []:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            location = str((p.get("location") or {}).get("name") or "")
            offices = [str((o.get("location") or {}).get("name") or o.get("name") or "") for o in (p.get("offices") or [])]
            loc_text = "; ".join(x for x in [location, *offices] if x)
            remote = p.get("location_type") == "remote"
            title = str(p.get("title") or "")
            intern = "intern" in str(p.get("employment_type") or "") or bool(INTERN_TITLE.search(title))
            jobs.append(RawJob(
                source=self.name,
                source_job_id=f"{company.slug}:{p['id']}",
                url=str(p.get("absolute_url") or ""),
                title=title,
                company=company.get("name") or company.slug,
                description=p.get("content_plain"),
                location_raw=(loc_text + (" · Remote" if remote else "")) or None,
                tags=[d.get("name") for d in (p.get("departments") or []) if isinstance(d, dict) and d.get("name")],
                posted_at=parse_iso(p.get("first_published_at") or p.get("created_at")),
                hiring_regions_hint=regions_from_text(loc_text),
                employment_type_hint=EmploymentType.INTERNSHIP if intern else EmploymentType.UNKNOWN,
                raw_payload={"ats": "gem", "ats_slug": company.slug, "remote": remote},
            ))
        return jobs
