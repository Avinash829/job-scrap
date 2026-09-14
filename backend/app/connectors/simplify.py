"""SimplifyJobs internship and new-grad lists.

    raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json
    raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json

The most-starred internship list on GitHub (~47k stars), refreshed daily by
Simplify's own crawler plus community submissions. Measured 2026-09-14:
16,520 records, 3,715 active, 2,522 posted within 30 days.

It is US-heavy - only ~26 active listings are India-located - so it widens
coverage of global and remote internships rather than solving India on its
own. Its value is breadth across employers we have no connector for, and
that every row is already an internship or new-grad role.

`active` is maintained by Simplify, so a listing here is treated like an
employer board: open until marked inactive, whatever its posting date.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.connectors.base import Connector
from app.connectors.common import regions_from_text
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType

FEEDS = (
    (
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        EmploymentType.INTERNSHIP,
    ),
    (
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
        EmploymentType.FULL_TIME,
    ),
)

# Skip listings older than this even if still flagged active - a 9-month-old
# "active" internship is almost always a req nobody closed.
MAX_AGE_DAYS = 90


class SimplifyJobs(Connector):
    name = "simplify"
    tier = 1

    async def fetch(self) -> Iterable[RawJob]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
        jobs: list[RawJob] = []

        for url, emp in FEEDS:
            rows = await self.get_json(url)
            if not isinstance(rows, list):
                continue

            for x in rows:
                if not isinstance(x, dict):
                    continue
                if not x.get("active") or x.get("is_visible") is False:
                    continue
                if not x.get("url") or not x.get("title"):
                    continue

                posted = None
                if ts := x.get("date_posted"):
                    try:
                        posted = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                    except (TypeError, ValueError, OSError):
                        posted = None
                if posted and posted < cutoff:
                    continue

                locations = [str(l) for l in (x.get("locations") or [])]
                loc_text = "; ".join(locations)

                jobs.append(
                    RawJob(
                        source=self.name,
                        source_job_id=str(x.get("id") or x["url"]),
                        url=x["url"],
                        title=x["title"],
                        company=x.get("company_name") or "",
                        description=None,
                        location_raw=loc_text or None,
                        tags=[str(t) for t in (x.get("terms") or [])]
                        + ([str(x["category"])] if x.get("category") else []),
                        posted_at=posted,
                        hiring_regions_hint=regions_from_text(loc_text),
                        employment_type_hint=emp,
                        raw_payload={
                            "simplify_id": x.get("id"),
                            "terms": x.get("terms"),
                            "sponsorship": x.get("sponsorship"),
                            "degrees": x.get("degrees"),
                            # an internship/new-grad feed: treat as entry level
                            "min_yoe_hint": 0,
                        },
                    )
                )
        return jobs
