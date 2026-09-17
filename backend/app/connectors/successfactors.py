"""SAP SuccessFactors Recruiting Marketing ("RMK") career sites.

    GET {base}/search/?q=intern&locationsearch=india&startrow=N     (25 per page)

The server-rendered results table used by jobs.sap.com and many other
enterprise career sites. Rows are `tr.data-row` with `a.jobTitle-link`,
`span.jobLocation` ("Bangalore, IN, 560066") and `span.jobDate`
("Sep 12, 2026"). Verified 2026-09-15 on SAP: 18 India rows for "intern".

Each site is a companies.yaml `successfactors:` entry with `base` and `name`.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable

from selectolax.parser import HTMLParser

from app.connectors.common import BROWSER_UA, INTERN_TITLE, regions_from_text
from app.connectors.search_base import SearchConnector
from app.core.companies import Company, companies_for
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)
PAGE = 25
_JOB_ID = re.compile(r"/(\d{5,})/?$")


def _date(text: str) -> datetime | None:
    for fmt in ("%b %d, %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class SuccessFactors(SearchConnector):
    name = "successfactors"
    tier = 1
    concurrency = 3
    rate_limit_delay = 0.3

    def known_scopes(self) -> set[str] | None:
        return {f"successfactors:{c.slug}" for c in companies_for("successfactors")} or None

    async def fetch(self) -> Iterable[RawJob]:
        companies = companies_for("successfactors", self._tier_filter)
        return await self.fetch_per_company(companies, self.search_site, lambda c: f"successfactors:{c.slug}")

    async def search(self, term: str, location: str) -> list[RawJob]:  # pragma: no cover
        raise NotImplementedError("SuccessFactors searches per employer; see fetch()")

    async def search_site(self, c: Company, term: str, location: str) -> list[RawJob]:
        base = str(c.get("base") or "").rstrip("/")
        if not base:
            log.warning("successfactors %s is missing `base`", c.slug)
            return []
        label = c.get("name") or c.slug
        jobs: list[RawJob] = []
        for page in range(self.max_pages):
            html = await self.get_text(
                f"{base}/search/",
                params={"q": term, "locationsearch": location, "startrow": page * PAGE},
                headers={"User-Agent": BROWSER_UA, "Accept": "text/html"},
            )
            rows = HTMLParser(html or "").css("tr.data-row")
            for row in rows:
                link = row.css_first("a.jobTitle-link")
                if link is None:
                    continue
                href = link.attributes.get("href") or ""
                title = link.text(strip=True)
                loc_el = row.css_first("td.colLocation span.jobLocation") or row.css_first("span.jobLocation")
                loc = " ".join((loc_el.text() if loc_el else "").split())
                date_el = row.css_first("span.jobDate")
                regions = regions_from_text(loc)
                if re.search(r",\s*IN\b", loc) and HiringRegion.INDIA not in regions:
                    regions = [HiringRegion.INDIA]
                m = _JOB_ID.search(href)
                jobs.append(RawJob(
                    source=self.name,
                    source_job_id=f"{c.slug}:{m.group(1) if m else href}",
                    url=base + href if href.startswith("/") else href,
                    title=title,
                    company=label,
                    description=None,
                    location_raw=loc or None,
                    tags=[],
                    posted_at=_date(date_el.text()) if date_el else None,
                    hiring_regions_hint=regions,
                    employment_type_hint=(
                        EmploymentType.INTERNSHIP if INTERN_TITLE.search(title) else EmploymentType.UNKNOWN
                    ),
                    raw_payload={"ats": "successfactors", "ats_slug": c.slug},
                ))
            if len(rows) < PAGE:
                break
        return jobs
