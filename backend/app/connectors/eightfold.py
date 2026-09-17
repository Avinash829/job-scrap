"""Eightfold-hosted career sites (Netflix and others).

    GET {host}/api/apply/v2/jobs?domain={domain}&location=India&start=N&num=100

The JSON call behind Eightfold career pages. Employers are companies.yaml
`eightfold:` entries with `host`, `domain` and `name`. Verified 2026-09-14 on
Netflix (explore.jobs.netflix.net / netflix.com). Some tenants (Qualcomm)
refuse it with 403 "Not authorized for PCSX" and are not listed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from app.connectors.base import Connector
from app.connectors.common import BROWSER_UA, INTERN_TITLE, html_to_text, regions_from_text
from app.core.companies import Company, companies_for
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)
PAGE = 100


class Eightfold(Connector):
    name = "eightfold"
    tier = 1

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier

    def known_scopes(self) -> set[str] | None:
        return {f"eightfold:{c.slug}" for c in companies_for("eightfold")} or None

    async def fetch(self) -> Iterable[RawJob]:
        out: list[RawJob] = []
        self.covered_scopes = set()
        for c in companies_for("eightfold", self._tier_filter):
            scope = f"{self.name}:{c.slug}"
            try:
                out.extend(self.scoped(await self._site(c), scope))
                self.covered_scopes.add(scope)
            except Exception as exc:  # noqa: BLE001 - one employer must not sink the source
                log.warning("eightfold %s failed: %s", c.slug, exc)
        return out

    async def _site(self, c: Company) -> list[RawJob]:
        host, domain, label = c.get("host"), c.get("domain"), c.get("name") or c.slug
        if not (host and domain):
            log.warning("eightfold %s is missing `host` or `domain`", c.slug)
            return []
        jobs: list[RawJob] = []
        for page in range(10):
            d = await self.get_json(
                f"https://{host}/api/apply/v2/jobs",
                params={"domain": domain, "location": "India", "start": page * PAGE, "num": PAGE},
                headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            )
            rows = (d or {}).get("positions") or []
            for p in rows:
                locs = [str(x) for x in (p.get("locations") or [p.get("location") or ""])]
                loc_text = "; ".join(locs)
                if "india" not in loc_text.lower() or not p.get("id"):
                    continue
                posted = (
                    datetime.fromtimestamp(int(p["t_create"]), tz=timezone.utc)
                    if p.get("t_create") else None
                )
                title = str(p.get("name") or "")
                workplace = str(p.get("work_location_option") or "")
                regions = regions_from_text(loc_text)
                if HiringRegion.INDIA not in regions:
                    regions = [HiringRegion.INDIA]
                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=f"{c.slug}:{p['id']}",
                    url=p.get("canonicalPositionUrl") or f"https://{host}/careers/job/{p['id']}",
                    title=title,
                    company=label,
                    description=html_to_text(p.get("job_description")),
                    location_raw=loc_text + (" · Remote" if workplace == "remote" else ""),
                    tags=[t for t in (p.get("department"), p.get("business_unit")) if t],
                    posted_at=posted,
                    hiring_regions_hint=regions,
                    employment_type_hint=(
                        EmploymentType.INTERNSHIP if INTERN_TITLE.search(title) else EmploymentType.UNKNOWN
                    ),
                    raw_payload={"ats": "eightfold", "ats_slug": c.slug},
                ))
            if len(rows) < PAGE or (page + 1) * PAGE >= int((d or {}).get("count") or 0):
                break
        return jobs
