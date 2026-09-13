"""SmartRecruiters Posting API

    GET api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=N

Best structured data of the four platforms, and the most useful one for an
India-based early-career search. Verified on Swiggy:

    location: {"city": "Bengaluru", "region": "KA", "country": "in",
               "remote": false, "hybrid": false,
               "fullLocation": "Bengaluru, KA, India"}
    experienceLevel: {"id": "entry_level", "label": "Entry Level"}
    typeOfEmployment: {"id": "permanent", "label": "Full-time"}
    releasedDate: "2026-09-11T16:26:50.932Z"

So country, remote-ness, employment type AND experience level all arrive as
structured enums. Nothing here needs LLM inference - which makes these rows
the highest-confidence data in the whole corpus.

Note the title field is `name`, not `title`. There is no cross-company search
endpoint (/v1/postings 404s), so a company list is mandatory.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.connectors.ats.base import ATSConnector
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
PAGE = 100
MAX_PAGES = 6

_COUNTRY_REGION = {
    "in": HiringRegion.INDIA,
    "us": HiringRegion.US_ONLY,
    "ca": HiringRegion.CANADA,
    "gb": HiringRegion.EUROPE, "uk": HiringRegion.EUROPE,
    "de": HiringRegion.EUROPE, "fr": HiringRegion.EUROPE,
    "nl": HiringRegion.EUROPE, "es": HiringRegion.EUROPE,
    "pl": HiringRegion.EUROPE, "pt": HiringRegion.EUROPE,
    "ie": HiringRegion.EUROPE, "it": HiringRegion.EUROPE,
    "sg": HiringRegion.APAC, "jp": HiringRegion.APAC,
    "au": HiringRegion.APAC, "id": HiringRegion.APAC,
    "br": HiringRegion.LATAM, "mx": HiringRegion.LATAM,
    "ae": HiringRegion.EMEA,
}

_EMPLOYMENT = {
    "permanent": EmploymentType.FULL_TIME,
    "full_time": EmploymentType.FULL_TIME,
    "intern": EmploymentType.INTERNSHIP,
    "internship": EmploymentType.INTERNSHIP,
    "temporary": EmploymentType.CONTRACT,
    "contractor": EmploymentType.CONTRACT,
    "part_time": EmploymentType.PART_TIME,
}

# Their enum, mapped to a minimum years-of-experience hint.
EXPERIENCE_MIN_YOE = {
    "internship": 0,
    "student": 0,
    "entry_level": 0,
    "associate": 1,
    "mid_senior_level": 3,
    "director": 8,
    "executive": 10,
    # "not_applicable" deliberately absent -> stays None (unstated)
}


class SmartRecruiters(ATSConnector):
    name = "smartrecruiters"
    platform = "smartrecruiters"
    tier = 1
    concurrency = 10
    rate_limit_delay = 0.2

    def board_url(self, slug: str) -> str:
        return f"{API.format(slug=slug)}?limit={PAGE}&offset=0"

    async def fetch_board(self, company: Company):
        """Paginate, then hand the merged payload to the shared status logic."""
        result = await super().fetch_board(company)
        if result.status.value != "ok":
            return result

        base = API.format(slug=company.slug)
        total = None
        collected = list(result.jobs)
        seen = {j.source_job_id for j in collected}

        for page in range(1, MAX_PAGES):
            offset = page * PAGE
            if total is not None and offset >= total:
                break
            try:
                payload = await self.get_json(f"{base}?limit={PAGE}&offset={offset}")
            except Exception:  # noqa: BLE001 - keep page 1 rather than lose the board
                break
            total = payload.get("totalFound", total)
            rows = self.parse(company, payload)
            fresh = [j for j in rows if j.source_job_id not in seen]
            if not fresh:
                break
            seen.update(j.source_job_id for j in fresh)
            collected.extend(fresh)

        result.jobs = collected
        return result

    def extract_items(self, payload) -> list:
        return payload.get("content", []) if isinstance(payload, dict) else []

    def parse(self, company: Company, payload) -> list[RawJob]:
        jobs: list[RawJob] = []
        for item in payload.get("content", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue

            loc = item.get("location") or {}
            country = str(loc.get("country") or "").lower()
            is_remote = bool(loc.get("remote"))

            regions: list[HiringRegion] = []
            if region := _COUNTRY_REGION.get(country):
                regions.append(region)
            if is_remote and not regions:
                regions.append(HiringRegion.UNKNOWN)

            exp_id = str((item.get("experienceLevel") or {}).get("id") or "")
            emp_id = str((item.get("typeOfEmployment") or {}).get("id") or "").lower()

            posted = None
            if raw := item.get("releasedDate"):
                try:
                    posted = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except ValueError:
                    posted = None

            company_name = (item.get("company") or {}).get("name") or company.slug
            full_loc = loc.get("fullLocation") or ", ".join(
                x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x
            )

            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=f"{company.slug}:{item['id']}",
                    # public apply page, not the API ref
                    url=f"https://jobs.smartrecruiters.com/{company.slug}/{item['id']}",
                    title=item.get("name", ""),          # `name`, not `title`
                    company=company_name,
                    description=None,                     # needs a per-job call
                    location_raw=(full_loc + (" · Remote" if is_remote else "")) or None,
                    tags=[
                        t
                        for t in (
                            (item.get("department") or {}).get("label"),
                            (item.get("function") or {}).get("label"),
                            (item.get("industry") or {}).get("label"),
                        )
                        if t
                    ],
                    posted_at=posted,
                    hiring_regions_hint=regions or [HiringRegion.UNKNOWN],
                    employment_type_hint=_EMPLOYMENT.get(
                        emp_id, EmploymentType.UNKNOWN
                    ),
                    raw_payload={
                        "id": item["id"],
                        "ats": "smartrecruiters",
                        "ats_slug": company.slug,
                        # consumed by the normalizer as a structured YoE hint
                        "experience_level": exp_id,
                        "min_yoe_hint": EXPERIENCE_MIN_YOE.get(exp_id),
                        "structured_remote": is_remote,
                        "structured_country": country,
                    },
                )
            )
        return jobs
