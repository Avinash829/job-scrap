"""IBM careers, via IBM's site-search API.

    POST www-api.ibm.com/search/api/v2   appId=careers, scopes=[careers2]

Elasticsearch-style. `_source` fields: title, url, dcdate (posting date),
description (teaser), field_keyword_05 (country), field_keyword_18
(employment type, e.g. "Internship"), field_keyword_19 (city),
field_keyword_08 (job family). A server-side country filter returned zero,
so the connector searches "<term> India" and keeps rows whose country is India.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.connectors.common import BROWSER_UA, html_to_text
from app.connectors.search_base import SearchConnector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

API = "https://www-api.ibm.com/search/api/v2"
PAGE = 100
FIELDS = ["title", "url", "description", "dcdate", "field_keyword_05", "field_keyword_08",
          "field_keyword_18", "field_keyword_19"]


class IBMCareers(SearchConnector):
    name = "ibm"
    tier = 1
    concurrency = 2

    async def search(self, term: str, location: str) -> list[RawJob]:
        if location.lower() != "india":
            return []
        jobs: list[RawJob] = []
        for page in range(self.max_pages):
            body = {
                "appId": "careers", "scopes": ["careers2"], "size": PAGE, "from": page * PAGE,
                "query": {"bool": {"must": [{"simple_query_string": {"query": f"{term} India"}}]}},
                "_source": FIELDS,
            }
            d = await self.post_json(API, json=body, headers={"User-Agent": BROWSER_UA})
            hits = ((d or {}).get("hits") or {}).get("hits") or []
            for h in hits:
                s = h.get("_source") or {}
                if str(s.get("field_keyword_05") or "").lower() == "india" and s.get("url"):
                    jobs.append(self._to_raw(h.get("_id") or "", s))
            if len(hits) < PAGE:
                break
        return jobs

    def _to_raw(self, doc_id: str, s: dict) -> RawJob:
        posted = None
        if s.get("dcdate"):
            try:
                posted = datetime.strptime(s["dcdate"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                posted = None
        kind = str(s.get("field_keyword_18") or "")
        city = str(s.get("field_keyword_19") or "")
        url = str(s["url"])
        return RawJob(
            source=self.name,
            source_job_id=url.rsplit("jobId=", 1)[-1] if "jobId=" in url else doc_id,
            url=url,
            title=str(s.get("title") or ""),
            company="IBM",
            description=html_to_text(s.get("description")),
            location_raw=", ".join(x for x in (city, "India") if x),
            tags=[t for t in (s.get("field_keyword_08"), kind) if t],
            posted_at=posted,
            hiring_regions_hint=[HiringRegion.INDIA],
            employment_type_hint=(
                EmploymentType.INTERNSHIP if "intern" in kind.lower() else EmploymentType.UNKNOWN
            ),
            raw_payload={"employment": kind},
        )
