"""Juspay careers - an example of a custom company site.

    GET https://juspay.io/careers

An Astro static site (content managed in Sanity, but the jobs aren't in the
public Sanity dataset and the pages carry no schema.org JobPosting). The job
list is serialized into an `<astro-island props="...">` attribute, using
Astro's `[type, value]` encoding: `[0, v]` is a plain value, `[1, [...]]` an
array. Fields per job (verified 2026-09-14):

    job_id "DEV-BE02" · job_title · job_type "Intern" | "Full-Time"
    experience_year 0 · job_location "Bangalore" · opening_status true
    job_description_career (markdown) · category · is_global

Custom sites like this need one small adapter each. They're worth it for
high-signal Indian employers that aren't on any shared job platform.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from selectolax.parser import HTMLParser

from app.connectors.base import Connector
from app.connectors.common import BROWSER_UA, regions_from_text
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType

URL = "https://juspay.io/careers"


def decode_astro(node):
    """Decode Astro's serialized island props."""
    if isinstance(node, list) and len(node) == 2 and node[0] in (0, 1):
        kind, val = node
        if kind == 1:
            return [decode_astro(v) for v in val]
        if isinstance(val, dict):
            return {k: decode_astro(v) for k, v in val.items()}
        return val
    return node


def _plain(markdown: str | None) -> str | None:
    if not markdown:
        return None
    text = re.sub(r"[*_`#>]+", "", markdown)
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None


class Juspay(Connector):
    name = "juspay"
    tier = 1

    async def fetch(self) -> Iterable[RawJob]:
        html = await self.get_text(URL, headers={"User-Agent": BROWSER_UA})
        jobs: list[RawJob] = []
        seen: set[str] = set()

        for island in HTMLParser(html).css("astro-island"):
            props = island.attributes.get("props") or ""
            if "job_id" not in props:
                continue
            try:
                data = decode_astro(json.loads(props).get("jobData"))
            except (json.JSONDecodeError, AttributeError):
                continue

            for j in data or []:
                if not isinstance(j, dict) or not j.get("job_id") or j["job_id"] in seen:
                    continue
                if j.get("opening_status") is False:
                    continue
                seen.add(j["job_id"])

                job_type = str(j.get("job_type") or "")
                emp = {
                    "intern": EmploymentType.INTERNSHIP,
                    "full-time": EmploymentType.FULL_TIME,
                    "full time": EmploymentType.FULL_TIME,
                    "contract": EmploymentType.CONTRACT,
                }.get(job_type.lower(), EmploymentType.UNKNOWN)
                location = str(j.get("job_location") or "")
                exp = j.get("experience_year")

                jobs.append(
                    RawJob(
                        source=self.name,
                        source_job_id=j["job_id"],
                        url=f"{URL}/{j['job_id']}",
                        title=j.get("job_title") or "",
                        company="Juspay",
                        description=_plain(j.get("job_description_career")),
                        location_raw=location or None,
                        tags=[t for t in (j.get("category"), job_type) if t],
                        posted_at=None,
                        hiring_regions_hint=regions_from_text(location),
                        employment_type_hint=emp,
                        raw_payload={
                            "job_type": job_type,
                            "experience_year": exp,
                            **({"min_yoe_hint": int(exp)} if isinstance(exp, int) else {}),
                        },
                    )
                )
        return jobs
