"""Freshteam (Freshworks' ATS) - popular with Indian startups.

    GET {slug}.freshteam.com/hire/widgets/jobs.json

Verified 2026-09-15 on Haptik: {"jobs": [...], "branches": [...], "job_roles": [...]}.
A job has no location of its own; it points at a branch by `branch_id`, and
the branch carries city + `country_code` ("IN"). `remote` is a boolean.
A slug that isn't on Freshteam answers 200 with an HTML 404 page, which the
JSON decode turns into a fetch error rather than an empty board.
"""
from __future__ import annotations

from app.connectors.ats.base import ATSConnector
from app.connectors.ats.greenhouse import parse_iso
from app.connectors.common import INTERN_TITLE, html_to_text, regions_from_text
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion


class Freshteam(ATSConnector):
    name = "freshteam"
    platform = "freshteam"
    tier = 1
    concurrency = 10

    def board_url(self, slug: str) -> str:
        return f"https://{slug}.freshteam.com/hire/widgets/jobs.json"

    def parse(self, company: Company, payload) -> list[RawJob]:
        branches = {b.get("id"): b for b in (payload.get("branches") or []) if isinstance(b, dict)}
        roles = {r.get("id"): r.get("name") for r in (payload.get("job_roles") or []) if isinstance(r, dict)}
        jobs: list[RawJob] = []
        for j in payload.get("jobs") or []:
            if not isinstance(j, dict) or j.get("deleted") or not j.get("id"):
                continue
            branch = branches.get(j.get("branch_id")) or {}
            location = str(branch.get("location") or branch.get("city") or "")
            remote = bool(j.get("remote"))
            regions = regions_from_text(location)
            if branch.get("country_code") == "IN" and HiringRegion.INDIA not in regions:
                regions = [HiringRegion.INDIA]
            title = str(j.get("title") or "").strip()
            jobs.append(RawJob(
                source=self.name,
                source_job_id=f"{company.slug}:{j['id']}",
                url=str(j.get("url") or f"https://{company.slug}.freshteam.com/jobs"),
                title=title,
                company=company.get("name") or company.slug,
                description=html_to_text(j.get("description")),
                location_raw=(location + (" · Remote" if remote else "")) or None,
                tags=[t for t in (roles.get(j.get("job_role_id")),) if t],
                posted_at=parse_iso(j.get("created_at")),
                hiring_regions_hint=regions,
                employment_type_hint=(
                    EmploymentType.INTERNSHIP if INTERN_TITLE.search(title) else EmploymentType.UNKNOWN
                ),
                raw_payload={"ats": "freshteam", "ats_slug": company.slug, "remote": remote},
            ))
        return jobs
