"""Hacker News "Ask HN: Who is hiring?" via the free Algolia API.

Best startup remote source that exists, and posts state region bluntly
("REMOTE (Worldwide)", "Onsite SF only"). No auth, no rate limit worth
worrying about.

Each top-level comment is one posting, in free text. Convention is roughly:
    Company | Role | Location | REMOTE | Salary | url
...which most posters follow loosely and some ignore entirely, so parsing is
best-effort: we extract what we can and leave the rest to the LLM pass.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable

from selectolax.parser import HTMLParser

from app.connectors.base import Connector
from app.domain.entities import RawJob
from app.domain.enums import EmploymentType, HiringRegion

log = logging.getLogger(__name__)

# search_by_date, NOT search. Algolia's /search endpoint is relevance-ordered,
# and its top hits for this query are threads from 2015-2020 - an earlier
# version of this connector silently ingested the March 2020 thread and filled
# the board with six-year-old COVID-era listings.
SEARCH = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?tags=story,author_whoishiring&restrictSearchableAttributes=title"
    "&query=who%20is%20hiring&hitsPerPage=10"
)
# A thread older than this is a stale month; refuse it rather than ingest it.
MAX_THREAD_AGE_DAYS = 75
COMMENTS = "https://hn.algolia.com/api/v1/search?tags=comment,story_{sid}&hitsPerPage=1000"

_SEP = re.compile(r"\s*(?:\||–|—| - |•)\s*")
_URL = re.compile(r"https?://[^\s<>\"')]+")


def _text(html: str) -> str:
    return HTMLParser(html or "").text(separator=" ", strip=True)


def _regions(text: str) -> list[HiringRegion]:
    t = text.lower()
    hits: list[HiringRegion] = []

    def add(r: HiringRegion) -> None:
        if r not in hits:
            hits.append(r)

    if re.search(r"remote\s*\(?\s*(worldwide|global|anywhere)", t) or "remote worldwide" in t:
        add(HiringRegion.WORLDWIDE)
    if re.search(r"remote\s*\(?\s*(us|usa|united states)\b", t) or "us only" in t:
        add(HiringRegion.US_ONLY)
    for needle, region in (
        ("emea", HiringRegion.EMEA),
        ("europe", HiringRegion.EUROPE),
        ("apac", HiringRegion.APAC),
        ("india", HiringRegion.INDIA),
        ("canada", HiringRegion.CANADA),
        ("latam", HiringRegion.LATAM),
    ):
        if needle in t:
            add(region)

    # bare "REMOTE" with no qualifier: unknown, not worldwide. Assuming
    # worldwide here is how you end up with a board full of unreachable roles.
    if not hits and "remote" in t:
        add(HiringRegion.UNKNOWN)
    return hits or [HiringRegion.UNKNOWN]


_ROLE_HINT = re.compile(
    r"\b(engineer|developer|dev|swe|sde|scientist|architect|designer|"
    r"programmer|analyst|intern|manager|lead|sre|devops|founding)\b",
    re.I,
)
# "San Francisco, CA", "London, UK", "Berlin", "NYC", "Remote (US)"
_LOC_HINT = re.compile(
    r"\b(remote|onsite|on-site|hybrid|worldwide|anywhere|"
    r"[A-Z][a-z]+,\s*[A-Z]{2}\b|SF|NYC|bay\s+area|"
    r"san\s+francisco|new\s+york|london|berlin|bangalore|toronto|austin|seattle)\b",
    re.I,
)


def _split_header(text: str) -> tuple[str, str, str]:
    """Parse an HN hiring header into (company, role, location).

    Convention is `Company | Role | Location | REMOTE | salary | url`, but
    posters reorder and omit fields freely. So rather than trusting position,
    score each segment: company is first (that part is reliable), then pick
    the best role-looking and location-looking segments from the rest.
    """
    head = text.split("\n", 1)[0][:400]
    parts = [p.strip() for p in _SEP.split(head) if p.strip()]
    if not parts:
        return "", "", ""

    company, rest = parts[0], parts[1:]
    role_seg = ""
    loc_segs: list[str] = []

    for seg in rest:
        if _URL.search(seg) or len(seg) > 120:
            continue
        is_role = bool(_ROLE_HINT.search(seg))
        is_loc = bool(_LOC_HINT.search(seg))
        # a segment can look like both ("Remote Backend Engineer"); role wins
        if is_role and not role_seg:
            role_seg = seg
        elif is_loc:
            loc_segs.append(seg)

    # Deliberately no body fallback: slicing a role keyword out of prose
    # produces titles like "to block it. We're hiring for many", which is
    # worse than dropping the comment. An unparseable header means the
    # poster didn't follow the convention - skip it.
    return company, role_seg, " | ".join(loc_segs)


class HNWhoIsHiring(Connector):
    name = "hn_hiring"
    tier = 3
    rate_limit_delay = 0.3

    async def _latest_story_id(self) -> tuple[str, int] | None:
        """Newest 'Who is hiring' thread, rejected if stale.

        Returning None is correct behaviour when the newest thread is old -
        the connector then reports zero jobs, which trips the silent-zero
        health check instead of quietly serving expired listings.
        """
        payload = await self.get_json(SEARCH)
        best: tuple[str, int] | None = None

        for hit in (payload or {}).get("hits", []):
            title = (hit.get("title") or "").lower()
            if "who is hiring" not in title or "hiring right now" in title:
                continue
            created = hit.get("created_at_i") or 0
            if best is None or created > best[1]:
                best = (str(hit.get("objectID")), created)

        if best is None:
            return None

        age_days = (datetime.now(timezone.utc).timestamp() - best[1]) / 86400
        if age_days > MAX_THREAD_AGE_DAYS:
            log.warning(
                "newest HN hiring thread is %.0f days old (id=%s); refusing it",
                age_days,
                best[0],
            )
            return None
        return best

    async def fetch(self) -> Iterable[RawJob]:
        story = await self._latest_story_id()
        if not story:
            return []
        sid, _ = story

        payload = await self.get_json(COMMENTS.format(sid=sid))
        jobs: list[RawJob] = []

        for hit in (payload or {}).get("hits", []):
            # top-level comments only - replies are discussion, not postings
            if str(hit.get("parent_id")) != sid:
                continue
            body = _text(hit.get("comment_text") or "")
            if len(body) < 40:
                continue

            company, role, location = _split_header(body)
            if not company or not role:
                # no identifiable role - almost always a meta/discussion comment
                continue

            url_match = _URL.search(hit.get("comment_text") or "")
            posted = None
            if ts := hit.get("created_at_i"):
                posted = datetime.fromtimestamp(int(ts), tz=timezone.utc)

            emp = (
                EmploymentType.INTERNSHIP
                if re.search(r"\bintern(ship)?\b", body, re.I)
                else EmploymentType.UNKNOWN
            )

            jobs.append(
                RawJob(
                    source=self.name,
                    source_job_id=str(hit.get("objectID")),
                    url=url_match.group(0) if url_match
                        else f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    title=role,
                    company=company,
                    description=body,
                    location_raw=location or None,
                    posted_at=posted,
                    hiring_regions_hint=_regions(body),
                    employment_type_hint=emp,
                    raw_payload={"hn_story_id": sid, "objectID": hit.get("objectID")},
                )
            )
        return jobs
