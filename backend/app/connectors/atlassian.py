"""Atlassian careers.

    GET www.atlassian.com/endpoint/careers/listings

One JSON array of every open role (~240), with full overview /
responsibilities / qualifications HTML and iCIMS links. Locations are
strings like "Bengaluru - India - Bengaluru, 560071 India" or
"Remote - India - Remote". Kept: roles with an Indian location.
"""
from __future__ import annotations

from typing import Iterable

from app.connectors.base import Connector
from app.connectors.common import BROWSER_UA, INTERN_TITLE, html_to_text
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://www.atlassian.com/endpoint/careers/listings"


class AtlassianCareers(Connector):
    name = "atlassian"
    tier = 1

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier

    async def fetch(self) -> Iterable[RawJob]:
        rows = await self.get_json(API, headers={"User-Agent": BROWSER_UA, "Accept": "application/json"})
        jobs: list[RawJob] = []
        for j in rows or []:
            india = [str(l) for l in (j.get("locations") or []) if "india" in str(l).lower()]
            if not india or not j.get("id"):
                continue
            title = str(j.get("title") or "")
            remote = any(l.lower().startswith("remote") for l in india)
            desc = "\n\n".join(
                t for t in (html_to_text(j.get(k)) for k in ("overview", "responsibilities", "qualifications")) if t
            )
            portal = j.get("portalJobPost") if isinstance(j.get("portalJobPost"), dict) else {}
            url = portal.get("portalUrl") or str(j.get("applyUrl") or "").replace("?mode=apply", "")
            jobs.append(RawJob(
                source=self.name,
                source_job_id=str(j["id"]),
                url=url or "https://www.atlassian.com/company/careers/all-jobs",
                title=title,
                company="Atlassian",
                description=desc or None,
                location_raw="; ".join(india) + (" · Remote" if remote else ""),
                tags=[t for t in (j.get("category"),) if t],
                posted_at=None,
                hiring_regions_hint=[HiringRegion.INDIA],
                employment_type_hint=(
                    EmploymentType.INTERNSHIP
                    if INTERN_TITLE.search(title) or j.get("category") == "Interns"
                    else EmploymentType.UNKNOWN
                ),
                raw_payload={"category": j.get("category")},
            ))
        return jobs
