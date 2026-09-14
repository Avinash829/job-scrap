"""Goldman Sachs careers (higher.gs.com), via its public GraphQL gateway.

    POST api-higher.gs.com/gateway/api/v1/graphql   operationName=GetRoles

Filtered to India, EARLY_CAREER and PROFESSIONAL experiences. Verified
2026-09-14: 147 India roles. `corporateTitle` (Analyst / Associate / Vice
President) goes into the description so the seniority gate drops VP roles;
Analyst is Goldman's entry-level engineering title.

Public role URL: https://higher.gs.com/roles/{sourceId}
"""
from __future__ import annotations

from typing import Iterable

from app.connectors.base import Connector
from app.connectors.common import BROWSER_UA, INTERN_TITLE, regions_from_text
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://api-higher.gs.com/gateway/api/v1/graphql"
PAGE = 50
QUERY = (
    "query GetRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: "
    "$searchQueryInput) { totalCount items { roleId jobTitle corporateTitle jobFunction division "
    "externalSource { sourceId } locations { city state country } status } } }"
)


class GoldmanSachs(Connector):
    name = "goldman"
    tier = 1
    rate_limit_delay = 0.3

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier

    async def fetch(self) -> Iterable[RawJob]:
        jobs: dict[str, RawJob] = {}
        for page in range(10):
            body = {
                "operationName": "GetRoles",
                "query": QUERY,
                "variables": {"searchQueryInput": {
                    "page": {"pageSize": PAGE, "pageNumber": page},
                    "sort": {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"},
                    "filters": [{"filterCategoryType": "LOCATION",
                                 "filters": [{"filter": "India", "subFilters": []}]}],
                    "experiences": ["EARLY_CAREER", "PROFESSIONAL"],
                    "searchTerm": "",
                }},
            }
            d = await self.post_json(
                API, json=body, headers={"User-Agent": BROWSER_UA, "Content-Type": "application/json"}
            )
            rs = ((d or {}).get("data") or {}).get("roleSearch") or {}
            items = rs.get("items") or []
            for r in items:
                job = self._to_raw(r)
                if job is not None:
                    jobs[job.source_job_id] = job
            if len(items) < PAGE or (page + 1) * PAGE >= int(rs.get("totalCount") or 0):
                break
        return list(jobs.values())

    def _to_raw(self, r: dict) -> RawJob | None:
        src = (r.get("externalSource") or {}).get("sourceId")
        if not src or not r.get("roleId") or r.get("status") not in (None, "POSTED"):
            return None
        locs = r.get("locations") or []
        loc_text = "; ".join(
            ", ".join(x for x in (l.get("city"), l.get("state"), l.get("country")) if x) for l in locs
        )
        regions = regions_from_text(loc_text)
        if any(l.get("country") == "India" for l in locs) and HiringRegion.INDIA not in regions:
            regions = [HiringRegion.INDIA]
        title = str(r.get("jobTitle") or "")
        rank = str(r.get("corporateTitle") or "")
        early = "EARLY_CAREER" in str(r.get("roleId")) or rank == "Analyst"
        # Goldman's ladder is Analyst -> Associate -> VP -> MD. Associate hires
        # are lateral (typically 3+ years), but the generic title rules read
        # "Associate" as a junior signal ("Associate Software Engineer"), which
        # outranks source metadata. The corporate title is authoritative here,
        # so senior ranks are dropped at the source.
        if rank in {"Associate", "Vice President", "Managing Director"} and not early:
            return None
        yoe_hint = 0 if early else None
        return RawJob(
            source=self.name,
            source_job_id=str(r["roleId"]),
            url=f"https://higher.gs.com/roles/{src}",
            title=title,
            company="Goldman Sachs",
            description=(
                f"Corporate title: {rank}. Function: {r.get('jobFunction') or '-'}. "
                f"Division: {r.get('division') or '-'}."
            ),
            location_raw=loc_text or None,
            tags=[t for t in (r.get("jobFunction"), r.get("division"), rank) if t],
            posted_at=None,
            hiring_regions_hint=regions,
            employment_type_hint=(
                EmploymentType.INTERNSHIP if INTERN_TITLE.search(title) else EmploymentType.UNKNOWN
            ),
            raw_payload={"corporate_title": rank, **({"min_yoe_hint": yoe_hint} if yoe_hint is not None else {})},
        )
