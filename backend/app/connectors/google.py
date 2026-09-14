"""Google Careers.

    GET www.google.com/about/careers/applications/jobs/results?q=intern&location=India&page=N

There is no public JSON API. The results page is server-rendered, and the
jobs ride along as JSON inside an `AF_initDataCallback({key: 'ds:1', ...})`
script block. Decoded layout of one job row (verified 2026-09-14 on
"Application Engineering Intern, Winter 2027"):

    [0]  job id                      "92840449600824006"
    [1]  title
    [3]  [_, responsibilities html]
    [4]  [_, qualifications html]
    [7]  company                     "Google"
    [9]  [[display, [addr], city, zip, state, country_code], ...]
    [10] [_, about-the-role html]
    [12] [epoch_seconds, nanos]      publish time
    [15] [_, eligibility note html]  e.g. "must reside in India ... apply by Sept 15"
    [19] [_, minimum qualifications html]

The block is parsed with a real JSON decoder, never a regex: it is ~170 KB of
nested arrays, and a non-greedy regex silently truncates it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app.connectors.common import BROWSER_UA, html_to_text, regions_from_text, slugify
from app.connectors.search_base import SearchConnector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

BASE = "https://www.google.com/about/careers/applications/jobs/results"
_DS1 = re.compile(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:", re.S)
_INTERN = re.compile(r"\b(intern|internship|apprentice)\b", re.I)
PAGE_SIZE = 20


def _html_field(row: list, idx: int) -> str:
    try:
        v = row[idx]
        return v[1] if isinstance(v, list) and len(v) > 1 and isinstance(v[1], str) else ""
    except (IndexError, TypeError):
        return ""


def parse_results(page_html: str) -> tuple[list[list], int]:
    """Return (job rows, total result count) from one results page."""
    m = _DS1.search(page_html)
    if not m:
        return [], 0
    try:
        data, _ = json.JSONDecoder().raw_decode(page_html[m.end():])
    except json.JSONDecodeError:
        return [], 0
    rows = data[0] if data and isinstance(data[0], list) else []
    rows = [r for r in rows if isinstance(r, list) and len(r) > 9 and isinstance(r[0], str)]
    total = next((x for x in data[1:] if isinstance(x, int)), len(rows))
    return rows, total


class GoogleCareers(SearchConnector):
    name = "google"
    tier = 1
    concurrency = 2          # google.com rate-limits aggressive scrapers
    rate_limit_delay = 0.6

    async def search(self, term: str, location: str) -> list[RawJob]:
        jobs: list[RawJob] = []
        for page in range(1, self.max_pages + 1):
            html = await self.get_text(
                BASE,
                params={"q": term, "location": location, "page": page},
                headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"},
            )
            rows, total = parse_results(html)
            if not rows:
                break
            jobs.extend(self._to_raw(r) for r in rows)
            if page * PAGE_SIZE >= total:
                break
        return jobs

    def _to_raw(self, row: list) -> RawJob:
        job_id, title = row[0], row[1]

        places = row[9] if isinstance(row[9], list) else []
        loc_names = [p[0] for p in places if isinstance(p, list) and p and isinstance(p[0], str)]
        countries = {p[5] for p in places if isinstance(p, list) and len(p) > 5 and p[5]}
        regions = regions_from_text("; ".join(loc_names))
        if "IN" in countries and HiringRegion.INDIA not in regions:
            regions = [HiringRegion.INDIA, *[r for r in regions if r is not HiringRegion.UNKNOWN]]

        posted = None
        try:
            ts = row[12][0]
            posted = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (IndexError, TypeError, ValueError, OSError):
            posted = None

        sections = [
            ("About the job", _html_field(row, 10)),
            ("Responsibilities", _html_field(row, 3)),
            ("Minimum qualifications", _html_field(row, 19) or _html_field(row, 4)),
            ("Eligibility", _html_field(row, 15)),
        ]
        description = "\n\n".join(
            f"{h}\n{html_to_text(body)}" for h, body in sections if body and html_to_text(body)
        ) or None

        return RawJob(
            source=self.name,
            source_job_id=job_id,
            url=f"{BASE}/{job_id}-{slugify(title)}",
            title=title,
            company=row[7] if len(row) > 7 and isinstance(row[7], str) else "Google",
            description=description,
            location_raw="; ".join(loc_names) or None,
            posted_at=posted,
            hiring_regions_hint=regions,
            employment_type_hint=(
                EmploymentType.INTERNSHIP if _INTERN.search(title) else EmploymentType.UNKNOWN
            ),
            raw_payload={"google_job_id": job_id, "countries": sorted(countries)},
        )
