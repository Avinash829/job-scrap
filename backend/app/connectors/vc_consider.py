"""Venture-capital portfolio job boards hosted on Consider.

    GET  https://{host}/jobs          -> csrfToken + board id ("board":{"id":"sequoia-capital-india"})
    POST https://{host}/api-boards/search-jobs      header x-csrf-token
         {"meta": {"size": 100, "sequence": <cursor>},
          "board": {"id": <board>, "isParent": true},
          "query": {"locations": ["India"]}}

Used by Peak XV, Sequoia, Lightspeed, Bessemer, GV, Kleiner Perkins, First
Round and others. Verified 2026-09-16: Peak XV lists 1,319 India jobs whose
`applyUrl` is the employer's own Greenhouse/Lever/SmartRecruiters posting.
Rows carry `minYearsExp`, `jobSeniorityIds` ("intern", "junior", "mid"...),
`remote`, `timeStamp`, funding stage and company head count. Seniority
filters are ignored server-side, so they are applied by the normal gates.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterable

from app.connectors.base import Connector
from app.connectors.common import BROWSER_UA, INTERN_TITLE, regions_from_text
from app.core.companies import Company, companies_for
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)

PAGE = 100
MAX_PAGES = 40
_CSRF = re.compile(r'csrfToken["\']?\s*[:=]\s*["\']([^"\']+)')
_BOARD = re.compile(r'"board"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)"')


def _ts(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


class ConsiderBoards(Connector):
    name = "vc_consider"
    tier = 1
    impersonate = "chrome124"
    keep_cookies = True
    rate_limit_delay = 0.2

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier

    async def fetch(self) -> Iterable[RawJob]:
        unique: dict[str, RawJob] = {}
        for board in companies_for("consider", self._tier_filter):
            try:
                for job in await self._board(board):
                    unique.setdefault(job.source_job_id, job)
            except Exception as exc:  # noqa: BLE001 - one board must not sink the source
                log.warning("consider %s failed: %s", board.slug, exc)
        return list(unique.values())

    async def _board(self, board: Company) -> list[RawJob]:
        host = str(board.get("host") or "")
        base = f"https://{host}"
        # The curl_cffi session keeps the page's session cookie, which the
        # CSRF token is bound to.
        page = await self.get_text(f"{base}/jobs", headers={"User-Agent": BROWSER_UA, "Accept": "text/html"})
        token, board_id = _CSRF.search(page or ""), _BOARD.search(page or "")
        if not (token and board_id):
            log.warning("consider %s: no csrf token / board id on page", board.slug)
            return []
        vc = board.get("name") or board.slug
        headers = {
            "User-Agent": BROWSER_UA, "x-csrf-token": token.group(1), "Accept": "application/json",
            "Content-Type": "application/json", "Origin": base, "Referer": f"{base}/jobs",
        }
        jobs: list[RawJob] = []
        for query in ({"locations": ["India"]}, {"remoteOnly": True}):
            sequence = None
            for _ in range(MAX_PAGES):
                meta = {"size": PAGE, **({"sequence": sequence} if sequence else {})}
                d = await self.post_json(
                    f"{base}/api-boards/search-jobs",
                    json={"meta": meta, "board": {"id": board_id.group(1), "isParent": True}, "query": query},
                    headers=headers,
                )
                rows = (d or {}).get("jobs") or []
                jobs.extend(self._to_raw(vc, j) for j in rows if (j.get("applyUrl") or j.get("url")) and j.get("title"))
                sequence = ((d or {}).get("meta") or {}).get("sequence")
                if len(rows) < PAGE or not sequence:
                    break
        return jobs

    def _to_raw(self, vc: str, j: dict) -> RawJob:
        locs = [str(x) for x in (j.get("locations") or [])]
        norm = [str((x or {}).get("label") or "") for x in (j.get("normalizedLocations") or [])]
        loc_text = "; ".join(locs or norm)
        remote = bool(j.get("remote"))
        regions = regions_from_text("; ".join([*locs, *norm]))
        if any("india" in n.lower() or re.search(r",\s*IN\b", n) for n in [*locs, *norm]) and HiringRegion.INDIA not in regions:
            regions = [HiringRegion.INDIA, *[r for r in regions if r is not HiringRegion.UNKNOWN]]
        title = str(j.get("title") or "").strip()
        seniority = [str(s) for s in (j.get("jobSeniorityIds") or [])]
        intern = "intern" in seniority or bool(INTERN_TITLE.search(title))
        payload: dict = {
            "vc": vc, "remote": remote, "seniority": seniority,
            "funding_stage": ((j.get("fundingLV") or {}).get("label")),
            "team_size": j.get("companyStaffCount") or None,
        }
        if isinstance(j.get("minYearsExp"), int):
            payload["min_yoe_hint"] = j["minYearsExp"]
        elif intern or seniority == ["junior"]:
            payload["min_yoe_hint"] = 0
        url = str(j.get("applyUrl") or j.get("url"))
        return RawJob(
            source=self.name,
            source_job_id=f"{j.get('companySlug') or j.get('companyId')}:{j.get('jobId') or url}",
            url=url,
            title=title,
            company=str(j.get("companyName") or ""),
            description=None,
            location_raw=(loc_text + (" · Remote" if remote else "")) or None,
            tags=[str((f or {}).get("label")) for f in (j.get("jobFunctions") or []) if (f or {}).get("label")],
            posted_at=_ts(j.get("timeStamp")),
            hiring_regions_hint=regions,
            employment_type_hint=EmploymentType.INTERNSHIP if intern else EmploymentType.UNKNOWN,
            raw_payload=payload,
        )
