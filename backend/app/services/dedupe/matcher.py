"""Stage 1 + 2 deduplication (no embeddings yet).

  1. canonical url          -> exact reposts
  2. (company, title, region) -> the same role surfacing on several boards

Stage 3 (local bge-small embeddings for reworded dupes) lands with the
embedding pass; the interface here won't change when it does.

Tie-break: prefer whichever source gives the truest apply link. ATS boards
beat aggregators, because an aggregator link is often a redirect wrapper.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from app.domain.entities import Job

# lower number wins when two records describe the same role
SOURCE_PRIORITY = {
    # the employer's own system: direct apply link, fullest data
    "greenhouse": 0, "lever": 0, "ashby": 0, "smartrecruiters": 0,
    "workday": 0, "google": 0, "amazon": 0, "avature": 0, "juspay": 0, "oracle": 0,
    "microsoft": 0, "apple": 0, "atlassian": 0, "goldman": 0, "ibm": 0, "eightfold": 0,
    "workable_boards": 0, "freshteam": 0, "recruitee": 0, "gem": 0, "rippling": 0, "successfactors": 0, "keka": 0, "zoho_recruit": 0,
    # cross-company searches: links go to the employer's posting on the platform
    "workable": 1, "vc_consider": 1, "vc_getro": 1, "instahyre": 1, "yc_jobs": 1,
    # curated feed that links back to the employer
    "simplify": 2,
    # aggregators
    "himalayas": 3, "hn_hiring": 3, "remoteok": 3, "remotive": 3, "arbeitnow": 3,
}
_DEFAULT_PRIORITY = 5

_TRACKING = re.compile(r"^(utm_|ref|source|gh_src|lever-|fbclid|gclid)", re.I)


def canonical_url(url: str) -> str:
    """Strip tracking params and trailing slashes so reposts collide."""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    kept = [
        kv
        for kv in p.query.split("&")
        if kv and not _TRACKING.match(kv.split("=", 1)[0])
    ]
    return urlunparse(
        (p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", "&".join(kept), "")
    )


def composite_key(job: Job) -> tuple[str, str, str]:
    region = job.hiring_regions[0].value if job.hiring_regions else "unknown"
    return (job.company_normalized, job.title_normalized, region)


def _priority(job: Job) -> int:
    return SOURCE_PRIORITY.get(job.source, _DEFAULT_PRIORITY)


def _borrow_detail(keep: Job, dupe: Job) -> None:
    """The employer copy keeps its apply link but may carry no text.

    EA's career site lists titles and locations only; the same role on another board
    has the full description, skills and a posting date. Fill the gaps
    instead of throwing that away.
    """
    if not keep.description and dupe.description:
        keep.description = dupe.description
    if not keep.tech_stack and dupe.tech_stack:
        keep.tech_stack = list(dupe.tech_stack)
    if keep.posted_at is None and dupe.posted_at is not None:
        keep.posted_at = dupe.posted_at


def dedupe(jobs: list[Job]) -> tuple[list[Job], int]:
    """Collapse duplicates within a batch. Returns (kept, dropped_count)."""
    by_url: dict[str, Job] = {}
    by_key: dict[tuple[str, str, str], Job] = {}
    dropped = 0

    # richer descriptions first, so the survivor carries the most text
    ordered = sorted(
        jobs, key=lambda j: (_priority(j), -len(j.description or "")), reverse=False
    )

    for job in ordered:
        curl = canonical_url(job.url)
        ckey = composite_key(job)

        incumbent = by_url.get(curl) or by_key.get(ckey)
        if incumbent is not None:
            _borrow_detail(incumbent, job)
            dropped += 1
            continue

        by_url[curl] = job
        by_key[ckey] = job

    return list(by_url.values()), dropped
