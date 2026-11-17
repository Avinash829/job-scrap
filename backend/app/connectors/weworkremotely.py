"""We Work Remotely - https://weworkremotely.com/categories/remote-programming-jobs.rss

World's largest remote work community, providing daily remote programming listings.
Attribution: Displayed postings are sourced from We Work Remotely.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Iterable

from selectolax.parser import HTMLParser

from app.connectors.base import Connector
from app.connectors.common import html_to_text
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)

FEED_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"

_REGION_MAP = {
    "anywhere": HiringRegion.WORLDWIDE,
    "worldwide": HiringRegion.WORLDWIDE,
    "global": HiringRegion.WORLDWIDE,
    "india": HiringRegion.INDIA,
    "asia": HiringRegion.APAC,
    "apac": HiringRegion.APAC,
    "usa": HiringRegion.US_ONLY,
    "united states": HiringRegion.US_ONLY,
    "north america": HiringRegion.US_ONLY,
    "canada": HiringRegion.CANADA,
    "europe": HiringRegion.EUROPE,
    "uk": HiringRegion.EUROPE,
    "emea": HiringRegion.EMEA,
    "latam": HiringRegion.LATAM,
}


def _regions(text: str | None) -> list[HiringRegion]:
    t = (text or "").lower()
    if not t or "anywhere in the world" in t or "worldwide" in t:
        return [HiringRegion.WORLDWIDE]
    hits: list[HiringRegion] = []
    for needle, region in _REGION_MAP.items():
        if needle in t and region not in hits:
            hits.append(region)
    return hits or [HiringRegion.UNKNOWN]


class WeWorkRemotely(Connector):
    name = "weworkremotely"
    tier = 1
    full_listing = False

    async def fetch(self) -> Iterable[RawJob]:
        try:
            xml_text = await self.get_text(FEED_URL)
        except Exception as exc:
            log.warning("WeWorkRemotely fetch failed: %s", exc)
            return []

        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            log.warning("WeWorkRemotely XML parse error: %s", exc)
            return []

        jobs: list[RawJob] = []
        for item in root.findall(".//item"):
            title_node = item.find("title")
            link_node = item.find("link")
            desc_node = item.find("description")
            pub_node = item.find("pubDate")
            guid_node = item.find("guid")
            region_node = item.find("region")

            url = link_node.text.strip() if link_node is not None and link_node.text else ""
            if not url:
                continue

            title_raw = title_node.text.strip() if title_node is not None and title_node.text else ""
            company, sep, title = title_raw.partition(":")
            if not sep:
                title, company = company, ""
            title = title.strip()
            company = company.strip()

            desc_html = desc_node.text if desc_node is not None and desc_node.text else ""
            description = html_to_text(desc_html)

            posted = None
            if pub_node is not None and pub_node.text:
                try:
                    posted = parsedate_to_datetime(pub_node.text.strip())
                except Exception:
                    posted = None

            region_str = region_node.text.strip() if region_node is not None and region_node.text else ""
            guid = guid_node.text.strip() if guid_node is not None and guid_node.text else url

            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=guid,
                    url=url,
                    title=title or title_raw,
                    company=company or "Remote Employer",
                    description=description,
                    location_raw=f"Remote ({region_str})" if region_str else "Remote",
                    tags=["remote", "programming"],
                    posted_at=posted,
                    hiring_regions_hint=_regions(region_str),
                    employment_type_hint=EmploymentType.FULL_TIME,
                    raw_payload={
                        "wwr_region": region_str,
                        "attribution": "We Work Remotely",
                    },
                )
            )
        return jobs

