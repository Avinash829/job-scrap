"""Amazon Jobs.

    GET www.amazon.jobs/en/search.json?base_query=intern&normalized_country_code[]=IND

A public JSON search API with unusually good structured fields: `is_intern`,
`university_job`, `country_code`, `posted_date`, qualifications.

Filter names matter. `country[]=IND` and `category[]=...` are silently
ignored - the echoed request shows an empty filterFacets list - while
`normalized_country_code[]` is honoured.

Known limit, verified 2026-09-14: some campus roles are published link-only.
"Software Development Engineer Intern - Jan 2027 (6 month)" in Bengaluru is
live at its URL but absent from every public search, because the API
excludes postings flagged isUnsearchable. No scraper can list those.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.connectors.common import BROWSER_UA, html_to_text, regions_from_text
from app.connectors.search_base import SearchConnector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

BASE = "https://www.amazon.jobs/en/search.json"
PAGE_SIZE = 100

# config.yaml speaks country names; the API wants ISO-3 codes
COUNTRY_CODES = {"india": "IND", "united states": "USA", "canada": "CAN", "singapore": "SGP"}


def _parse_posted(value: str | None) -> datetime | None:
    """"September  2, 2026" - note the double space before single digits."""
    if not value:
        return None
    try:
        return datetime.strptime(re.sub(r"\s+", " ", value.strip()), "%B %d, %Y").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


class AmazonJobs(SearchConnector):
    name = "amazon"
    tier = 1
    concurrency = 3

    async def search(self, term: str, location: str) -> list[RawJob]:
        code = COUNTRY_CODES.get(location.lower())
        jobs: list[RawJob] = []
        for page in range(self.max_pages):
            params = {
                "base_query": term,
                "result_limit": PAGE_SIZE,
                "offset": page * PAGE_SIZE,
                "sort": "recent",
            }
            if code:
                params["normalized_country_code[]"] = code
            else:
                params["loc_query"] = location

            d = await self.get_json(BASE, params=params, headers={"User-Agent": BROWSER_UA})
            rows = (d or {}).get("jobs") or []
            jobs.extend(self._to_raw(r) for r in rows if r.get("id_icims"))
            if len(rows) < PAGE_SIZE or (page + 1) * PAGE_SIZE >= int(d.get("hits") or 0):
                break
        return jobs

    def _to_raw(self, j: dict) -> RawJob:
        loc = j.get("normalized_location") or j.get("location") or ""
        regions = regions_from_text(loc)
        if j.get("country_code") == "IND" and HiringRegion.INDIA not in regions:
            regions = [HiringRegion.INDIA]

        parts = [
            html_to_text(j.get("description")),
            ("Basic qualifications\n" + html_to_text(j["basic_qualifications"]))
            if j.get("basic_qualifications") else None,
            ("Preferred qualifications\n" + html_to_text(j["preferred_qualifications"]))
            if j.get("preferred_qualifications") else None,
        ]
        is_intern = bool(j.get("is_intern")) or "intern" in (j.get("title") or "").lower()

        return RawJob(
            source=self.name,
            source_job_id=str(j["id_icims"]),
            url=f"https://www.amazon.jobs{j['job_path']}" if j.get("job_path") else "https://www.amazon.jobs",
            title=j.get("title") or "",
            # company_name is a legal entity ("ADCI - Karnataka - A66"); show the brand
            company="Amazon Web Services" if "web services" in str(j.get("company_name") or "").lower() else "Amazon",
            description="\n\n".join(p for p in parts if p) or None,
            location_raw=loc or None,
            tags=[t for t in (j.get("job_category"), j.get("business_category")) if t],
            posted_at=_parse_posted(j.get("posted_date")),
            hiring_regions_hint=regions,
            employment_type_hint=EmploymentType.INTERNSHIP if is_intern else EmploymentType.UNKNOWN,
            raw_payload={
                "id_icims": j.get("id_icims"),
                "university_job": j.get("university_job"),
                "is_intern": j.get("is_intern"),
                **({"min_yoe_hint": 0} if (is_intern or j.get("university_job")) else {}),
            },
        )
