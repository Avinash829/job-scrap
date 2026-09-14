"""Lever Postings API

    GET api.lever.co/v0/postings/{slug}?mode=json

Lever is the odd one out and the usual source of integration bugs:

  * the response is a BARE ARRAY, not {"jobs": [...]}
  * the title field is `text`, NOT `title`
  * `createdAt` is epoch MILLISECONDS, not an ISO string
  * location/team/commitment live inside `categories`
  * `country` is a real ISO-ish code - better than parsing the location string
  * `workplaceType` is "remote" | "hybrid" | "onsite" - a structured remote flag
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.connectors.ats.base import ATSConnector
from app.core.companies import Company
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://api.lever.co/v0/postings/{slug}?mode=json"

_COUNTRY_REGION = {
    "IN": HiringRegion.INDIA,
    "US": HiringRegion.US_ONLY,
    "CA": HiringRegion.CANADA,
    "GB": HiringRegion.EUROPE, "UK": HiringRegion.EUROPE,
    "DE": HiringRegion.EUROPE, "FR": HiringRegion.EUROPE,
    "NL": HiringRegion.EUROPE, "ES": HiringRegion.EUROPE,
    "PL": HiringRegion.EUROPE, "PT": HiringRegion.EUROPE,
    "IE": HiringRegion.EUROPE, "SE": HiringRegion.EUROPE,
    "SG": HiringRegion.APAC, "JP": HiringRegion.APAC,
    "AU": HiringRegion.APAC, "ID": HiringRegion.APAC,
    "BR": HiringRegion.LATAM, "MX": HiringRegion.LATAM,
    "AE": HiringRegion.EMEA,
}

_COMMITMENT = {
    "full-time": EmploymentType.FULL_TIME,
    "full time": EmploymentType.FULL_TIME,
    "intern": EmploymentType.INTERNSHIP,
    "internship": EmploymentType.INTERNSHIP,
    "contract": EmploymentType.CONTRACT,
    "part-time": EmploymentType.PART_TIME,
    "temporary": EmploymentType.CONTRACT,
}


class Lever(ATSConnector):
    name = "lever"
    platform = "lever"
    tier = 1
    concurrency = 30

    def board_url(self, slug: str) -> str:
        return API.format(slug=slug)

    def extract_items(self, payload) -> list:
        # bare array - the base class handles it, but be explicit since this
        # is the single most common Lever integration mistake
        return payload if isinstance(payload, list) else []

    def parse(self, company: Company, payload) -> list[RawJob]:
        jobs: list[RawJob] = []
        for item in payload:
            if not isinstance(item, dict) or not item.get("id"):
                continue

            cats = item.get("categories") or {}
            location = cats.get("location") or ""
            team = cats.get("team") or ""
            commitment = (cats.get("commitment") or "").lower()
            workplace = (item.get("workplaceType") or "").lower()
            country = (item.get("country") or "").upper()

            regions: list[HiringRegion] = []
            if region := _COUNTRY_REGION.get(country):
                regions.append(region)
            blob = f"{location} {cats.get('allLocations') or ''}".lower()
            if any(k in blob for k in ("worldwide", "anywhere", "global")):
                regions.insert(0, HiringRegion.WORLDWIDE)
            if "india" in blob and HiringRegion.INDIA not in regions:
                regions.append(HiringRegion.INDIA)

            description = (
                item.get("descriptionPlain")
                or item.get("description")
                or item.get("additionalPlain")
                or ""
            )

            posted = None
            if ts := item.get("createdAt"):
                try:
                    # epoch MILLISECONDS
                    posted = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
                except (TypeError, ValueError, OSError):
                    posted = None

            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=f"{company.slug}:{item['id']}",
                    url=item.get("hostedUrl") or item.get("applyUrl") or "",
                    title=item.get("text", ""),          # `text`, not `title`
                    company=company.get("name") or company.slug,
                    description=description,
                    location_raw=location or None,
                    tags=[t for t in (team, commitment, workplace) if t],
                    posted_at=posted,
                    hiring_regions_hint=regions or [HiringRegion.UNKNOWN],
                    employment_type_hint=_COMMITMENT.get(
                        commitment, EmploymentType.UNKNOWN
                    ),
                    raw_payload={
                        "id": item["id"],
                        "country": country,
                        "workplaceType": workplace,
                        "team": team,
                        "ats": "lever",
                        "ats_slug": company.slug,
                    },
                )
            )
        return jobs
