"""Unstop - curated Indian tech internships, campus drives and fresher opportunities.

    GET https://unstop.com/api/public/opportunity/search-result?opportunity=internships&searchTerm=...

The leading campus recruitment & internship platform in India. Verified 2026-10:
thousands of active tech internships across MNCs and startups with structured
stipends, deadlines, required skills, and direct apply URLs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Iterable

from app.connectors.base import Connector, FetchError
from app.connectors.common import BROWSER_UA, html_to_text, regions_from_text
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)

API = "https://unstop.com/api/public/opportunity/search-result"
PER_PAGE = 25
DEFAULT_TERMS = (
    "software",
    "developer",
    "web development",
    "full stack",
    "frontend",
    "backend",
    "machine learning",
    "data science",
    "python",
    "ai",
    "sde",
    "engineering",
)
MAX_PAGES_PER_TERM = 3


def _parse_iso(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        # e.g. "2026-10-16T23:59:59+05:30"
        return datetime.fromisoformat(val).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _format_pay(jd: dict) -> str | None:
    lo = jd.get("min_salary")
    hi = jd.get("max_salary")
    if not lo and not hi:
        return None
    period = jd.get("pay_in") or "month"
    period_label = "/month" if period == "monthly" else f"/{period}"
    if lo and hi and lo != hi:
        return f"₹{lo:,}-{hi:,}{period_label}"
    val = lo or hi
    return f"₹{val:,}{period_label}" if val else None


class Unstop(Connector):
    name = "unstop"
    tier = 1
    impersonate = "chrome124"
    rate_limit_delay = 0.5
    full_listing = False

    def __init__(self, tier: int | None = None) -> None:
        self._tier_filter = tier
        self.terms: list[str] = list(DEFAULT_TERMS)

    async def smoke(self, limit: int = 3) -> list[RawJob]:
        """One term, one page."""
        self.terms = self.terms[:1]
        self._smoke_limit = limit
        return list(await self.fetch())

    async def fetch(self) -> Iterable[RawJob]:
        unique: dict[str, RawJob] = {}
        pages_limit = 1 if hasattr(self, "_smoke_limit") else MAX_PAGES_PER_TERM

        for term in self.terms:
            for page in range(1, pages_limit + 1):
                params = {
                    "opportunity": "internships",
                    "searchTerm": term,
                    "per_page": PER_PAGE,
                    "page": page,
                }
                try:
                    d = await self.get_json(
                        API,
                        params=params,
                        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                    )
                except FetchError as exc:
                    log.warning("unstop query %r page %d failed: %s", term, page, exc)
                    break

                items = ((d or {}).get("data") or {}).get("data") or []
                if not items:
                    break

                for it in items:
                    job_id = str(it.get("id") or "")
                    if not job_id or job_id in unique:
                        continue
                    raw = self._to_raw(it)
                    if raw is not None:
                        unique[job_id] = raw

                if len(items) < PER_PAGE:
                    break
                await asyncio.sleep(0.2)

        return list(unique.values())

    def _to_raw(self, it: dict) -> RawJob | None:
        title = str(it.get("title") or "").strip()
        url = str(it.get("seo_url") or it.get("public_url") or "").strip()
        if not title or not url:
            return None

        org = it.get("organisation") or {}
        company = str(org.get("name") or "Unstop Employer").strip()
        jd = it.get("jobDetail") or {}

        # Locations
        is_remote = jd.get("type") == "work_from_home"
        locs = jd.get("locations") or []
        loc_str = ", ".join(str(l) for l in locs if l) if locs else "India"
        if is_remote and "remote" not in loc_str.lower():
            loc_str = f"Remote ({loc_str})"

        # Skills & Description
        skills = [str(s.get("skill_name")) for s in (it.get("required_skills") or []) if s.get("skill_name")]
        desc_text = html_to_text(it.get("details")) or ""
        if skills and "Skills:" not in desc_text:
            desc_text = f"Required Skills: {', '.join(skills)}\n\n{desc_text}".strip()

        # Dates & deadlines
        deadline = _parse_iso(it.get("end_date"))
        posted = _parse_iso(it.get("approved_date") or it.get("updated_at"))

        # Pay & Stipend
        pay_str = _format_pay(jd)
        unpaid = jd.get("paid_unpaid") == "unpaid"

        return RawJob(
            source=self.name,
            source_job_id=str(it["id"]),
            url=url,
            title=title,
            company=company,
            description=desc_text or None,
            location_raw=loc_str,
            tags=skills,
            posted_at=posted,
            hiring_regions_hint=[HiringRegion.INDIA],
            employment_type_hint=EmploymentType.INTERNSHIP,
            raw_payload={
                "pay": pay_str,
                "unpaid": unpaid,
                "remote": is_remote,
                "apply_deadline": deadline.isoformat() if deadline else None,
                "min_yoe_hint": 0,
            },
        )
