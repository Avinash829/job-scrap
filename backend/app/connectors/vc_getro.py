"""Venture-capital portfolio job boards hosted on Getro.

    GET  https://{host}/jobs                  -> __NEXT_DATA__ props.pageProps.network.id
    POST https://api.getro.com/api/v2/collections/{network_id}/search/jobs
         {"hitsPerPage": 100, "page": N, "query": "", "filters": {"searchable_locations": ["India"]}}

Every company on these boards is funded by the VC (Accel, Blume, 3one4,
General Catalyst, Khosla...), which is exactly the "funded startup" filter
this project wants. Verified 2026-09-16: Accel lists 27,224 jobs, 3,640 in
India; the API needs `Accept: application/json` or it answers 406.

One search per board: India-located jobs. Job links often point at the employer's LinkedIn post rather than its
ATS; dedupe prefers the employer's own copy when we fetch it directly.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Iterable

from app.connectors.base import Connector
from app.connectors.common import BROWSER_UA, INTERN_TITLE, regions_from_text
from app.core.companies import Company, companies_for
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)

API = "https://api.getro.com/api/v2/collections/{nid}/search/jobs"
PAGE = 100
MAX_PAGES = 60
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class GetroBoards(Connector):
    name = "vc_getro"
    tier = 1
    impersonate = "chrome124"
    rate_limit_delay = 0.2

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier

    async def fetch(self) -> Iterable[RawJob]:
        unique: dict[str, RawJob] = {}
        self.covered_scopes = set()
        for board in companies_for("getro", self._tier_filter):
            scope = f"{self.name}:{board.slug}"
            try:
                for job in self.scoped(await self._board(board), scope):
                    unique.setdefault(job.source_job_id, job)
                self.covered_scopes.add(scope)
            except Exception as exc:  # noqa: BLE001 - one board must not sink the source
                log.warning("getro %s failed: %s", board.slug, exc)
        return list(unique.values())

    async def _board(self, board: Company) -> list[RawJob]:
        host = str(board.get("host") or "")
        page = await self.get_text(f"https://{host}/jobs", headers={"User-Agent": BROWSER_UA, "Accept": "text/html"})
        m = _NEXT_DATA.search(page or "")
        if not m:
            log.warning("getro %s: no board data on page", board.slug)
            return []
        network = json.loads(m.group(1))["props"]["pageProps"]["network"]
        nid, vc = network["id"], board.get("name") or network.get("name") or board.slug

        headers = {
            "User-Agent": BROWSER_UA, "Accept": "application/json", "Content-Type": "application/json",
            "Origin": f"https://{host}", "Referer": f"https://{host}/jobs",
        }
        # India-located jobs only. A second "remote internships anywhere"
        # search was tried and returned mostly US onsite roles (Getro's
        # work_mode filter is loose), so it was dropped.
        searches = [{"searchable_locations": ["India"]}]
        jobs: list[RawJob] = []
        for filters in searches:
            for n in range(MAX_PAGES):
                d = await self.post_json(
                    API.format(nid=nid),
                    json={"hitsPerPage": PAGE, "page": n, "query": "", "filters": filters},
                    headers=headers,
                )
                results = (d or {}).get("results") or {}
                rows = results.get("jobs") or []
                jobs.extend(self._to_raw(board, vc, j) for j in rows if j.get("id") and j.get("url"))
                # The API silently caps hitsPerPage (20 on some boards), so a
                # short page doesn't mean the last one; the total count does.
                if not rows or (n + 1) * len(rows) >= int(results.get("count") or 0):
                    break
        return jobs

    def _to_raw(self, board: Company, vc: str, j: dict) -> RawJob:
        org = j.get("organization") or {}
        locs = [str(x) for x in (j.get("locations") or [])]
        searchable = [str(x) for x in (j.get("searchable_locations") or [])]
        remote = j.get("work_mode") == "remote"
        regions = regions_from_text("; ".join(locs))
        if "India" in searchable and HiringRegion.INDIA not in regions:
            regions = [HiringRegion.INDIA, *[r for r in regions if r is not HiringRegion.UNKNOWN]]
        title = str(j.get("title") or "").strip()
        intern = j.get("seniority") == "internship" or bool(INTERN_TITLE.search(title))
        posted = datetime.fromtimestamp(int(j["created_at"]), tz=timezone.utc) if j.get("created_at") else None
        return RawJob(
            source=self.name,
            source_job_id=f"getro:{j['id']}",
            url=str(j["url"]),
            title=title,
            company=str(org.get("name") or ""),
            description=None,
            location_raw="; ".join(locs) + (" · Remote" if remote else "") or None,
            tags=[t for t in (org.get("stage"), *[str(s) for s in (j.get("skills") or [])]) if t],
            posted_at=posted,
            hiring_regions_hint=regions,
            employment_type_hint=EmploymentType.INTERNSHIP if intern else EmploymentType.UNKNOWN,
            raw_payload={
                "vc": vc, "funding_stage": org.get("stage"), "team_size": org.get("headCount") or None,
                "seniority": j.get("seniority"), "remote": remote,
                **({"min_yoe_hint": 0} if intern or j.get("seniority") == "entry_level" else {}),
            },
        )
