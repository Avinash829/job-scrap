"""Y Combinator startups' own job posts (Work at a Startup), read from ycombinator.com.

    GET yc-oss.github.io/api/companies/hiring.json        -> ~1,500 YC companies hiring now
    GET www.ycombinator.com/companies/{slug}/jobs          -> Inertia `data-page` JSON

Many YC startups post ONLY here - no Greenhouse, Lever or Ashby board - which
is why guessing ATS slugs found so few of them. Each posting carries `type`
("Internship", "Full-time", "Contract"), `minExperience` ("Any (new grads
ok)", "1+ years"...), `location` ("Bengaluru, KA, IN", "Remote (US)"),
`visa`, `salaryRange`, `skills` and `role`. Verified 2026-09-16.

Most YC roles are US-only, so rows are pre-filtered to what someone in India
can take: an Indian location, or a remote role not restricted to one country
or region. The hiring list is fetched live each run, so companies that start
hiring show up without any config change.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from typing import Iterable

from app.connectors.base import Connector, FetchError
from app.connectors.common import BROWSER_UA, INTERN_TITLE, regions_from_text
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)

HIRING = "https://yc-oss.github.io/api/companies/hiring.json"
JOBS = "https://www.ycombinator.com/companies/{slug}/jobs"
_DATA_PAGE = re.compile(r'data-page="([^"]+)"')
_YEARS = re.compile(r"(\d+)\+?\s*year", re.I)
# "Remote (US)", "Remote (CA; US)", "Remote (GB; EU)" - a remote role fenced to
# a region that isn't India. Plain "Remote", "(Anywhere)" or "(IN)" are open.
_REMOTE_FENCED = re.compile(r"remote\s*\((?![^)]*\b(IN|India|Anywhere|Worldwide|Global|Asia|APAC)\b)[^)]*\)", re.I)
_INDIA = re.compile(r"(,\s*IN\b|\bIndia\b|Bengaluru|Bangalore|Hyderabad|Gurugram|Gurgaon|Mumbai|Pune|Chennai|Noida|Delhi)", re.I)


def reachable_location(location: str) -> bool:
    """Is this YC posting takeable from India?"""
    if _INDIA.search(location):
        return True
    parts = [p.strip() for p in re.split(r"\s*/\s*", location) if p.strip()]
    return any(p.lower().startswith("remote") and not _REMOTE_FENCED.search(p) for p in parts)


def takeable(posting: dict) -> bool:
    """Reachable location, and not a remote role reserved for US persons."""
    location = str(posting.get("location") or "")
    if not reachable_location(location):
        return False
    if _INDIA.search(location):
        return True
    # A plain "Remote" role marked "US citizen/visa only" hires US persons only.
    return "us citizen" not in str(posting.get("visa") or "").lower()


class YCJobs(Connector):
    name = "yc_jobs"
    tier = 1
    impersonate = "chrome124"
    concurrency = 8

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier

    async def fetch(self) -> Iterable[RawJob]:
        companies = await self.get_json(HIRING, headers={"User-Agent": BROWSER_UA})
        companies = [c for c in (companies or []) if c.get("slug") and c.get("status") in (None, "Active", "Public")]
        sem = asyncio.Semaphore(self.concurrency)
        self.covered_scopes = set()

        async def one(c: dict) -> list[RawJob]:
            scope = f"{self.name}:{c['slug']}"
            async with sem:
                try:
                    page = await self.get_text(JOBS.format(slug=c["slug"]), headers={"User-Agent": BROWSER_UA, "Accept": "text/html"})
                except FetchError as exc:
                    if exc.status_code == 404:
                        self.covered_scopes.add(scope)  # company page gone: its jobs are too
                    return []
                except Exception as exc:  # noqa: BLE001
                    log.debug("yc %s failed: %s", c["slug"], exc)
                    return []
            m = _DATA_PAGE.search(page or "")
            if not m:
                return []
            try:
                postings = json.loads(html.unescape(m.group(1)))["props"].get("jobPostings") or []
            except (json.JSONDecodeError, KeyError, TypeError):
                return []
            self.covered_scopes.add(scope)
            return self.scoped([self._to_raw(c, p) for p in postings if p.get("id") and takeable(p)], scope)

        batches = await asyncio.gather(*(one(c) for c in companies))
        jobs = [j for b in batches for j in b]
        log.info("yc_jobs: %d hiring companies, %d India/remote-open postings", len(companies), len(jobs))
        return jobs

    def _to_raw(self, company: dict, p: dict) -> RawJob:
        location = str(p.get("location") or "")
        title = re.sub(r"\s*\(YC [A-Z]\d{2}\)\s*$", "", str(p.get("title") or "")).strip()
        kind = str(p.get("type") or "")
        intern = kind.lower() == "internship" or bool(INTERN_TITLE.search(title))
        experience = str(p.get("minExperience") or "")

        regions = regions_from_text(location)
        if _INDIA.search(location) and HiringRegion.INDIA not in regions:
            regions = [HiringRegion.INDIA, *[r for r in regions if r is not HiringRegion.UNKNOWN]]
        elif not _INDIA.search(location) and HiringRegion.WORLDWIDE not in regions:
            regions = [HiringRegion.WORLDWIDE]  # passed reachable_location: an open remote role

        payload: dict = {
            "yc_batch": p.get("companyBatchName") or company.get("batch"),
            "yc_name": company.get("name"),
            "team_size": company.get("team_size"),
            "experience": experience,
            "visa": p.get("visa"),
            "remote": "remote" in location.lower(),
        }
        if intern or "new grad" in experience.lower() or experience.lower().startswith("any"):
            payload["min_yoe_hint"] = 0
        elif m := _YEARS.search(experience):
            payload["min_yoe_hint"] = int(m.group(1))

        skills = [str(s) for s in (p.get("skills") or []) if s]
        desc = " ".join(x for x in (
            company.get("one_liner"),
            f"Role: {p.get('prettyRole')} ({p.get('roleSpecificType')})." if p.get("roleSpecificType") else None,
            f"Salary: {p['salaryRange']}." if p.get("salaryRange") else None,
            f"Equity: {p['equityRange']}." if p.get("equityRange") else None,
            f"Experience: {experience}." if experience else None,
            f"Skills: {', '.join(skills)}." if skills else None,
        ) if x)
        return RawJob(
            source=self.name,
            source_job_id=str(p["id"]),
            url="https://www.ycombinator.com" + str(p.get("url") or f"/companies/{company['slug']}/jobs"),
            title=title,
            company=str(p.get("companyName") or company.get("name") or company["slug"]),
            description=desc or None,
            location_raw=location or None,
            tags=[t for t in (p.get("prettyRole"), p.get("roleSpecificType"), kind, *skills) if t],
            posted_at=None,
            hiring_regions_hint=regions,
            employment_type_hint=EmploymentType.INTERNSHIP if intern else (
                EmploymentType.FULL_TIME if kind.lower() == "full-time" else EmploymentType.UNKNOWN
            ),
            raw_payload=payload,
        )
