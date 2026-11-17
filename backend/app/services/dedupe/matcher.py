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
from urllib.parse import parse_qs, urlparse, urlunparse

from app.domain.entities import Job

# lower number wins when two records describe the same role
SOURCE_PRIORITY = {
    # the employer's own system: direct apply link, fullest data
    "greenhouse": 0, "lever": 0, "ashby": 0, "smartrecruiters": 0,
    "workday": 0, "google": 0, "amazon": 0, "avature": 0, "juspay": 0, "oracle": 0,
    # link-only Amazon campus pages: same employer, the searchable copy wins
    "amazon_campus": 1,
    "microsoft": 0, "apple": 0, "atlassian": 0, "goldman": 0, "ibm": 0, "eightfold": 0,
    "workable_boards": 0, "freshteam": 0, "recruitee": 0, "gem": 0, "rippling": 0, "successfactors": 0, "keka": 0, "zoho_recruit": 0,
    # cross-company searches: links go to the employer's posting on the platform
    "workable": 1, "vc_consider": 1, "vc_getro": 1, "instahyre": 1, "yc_jobs": 1,
    # curated feed that links back to the employer
    "simplify": 2,
    # aggregators
    "himalayas": 3, "jobicy": 3, "weworkremotely": 3, "hn_hiring": 3, "remoteok": 3, "remotive": 3, "arbeitnow": 3,
}
_DEFAULT_PRIORITY = 5

_TRACKING_PARAM = re.compile(
    r"^(utm_\w+|ref|referrer|source|src|gh_src|lever-source|lever-origin|fbclid|gclid|"
    r"mc_cid|mc_eid|_hsenc|_hsmi|trk|trackingId|mode|session)$",
    re.I,
)


def canonical_url(url: str) -> str:
    """Strip tracking params, trailing slashes, and redundant www so reposts collide."""
    if not url:
        return ""
    try:
        p = urlparse(url)
    except ValueError:
        return url

    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = f"{host}:{p.port}" if p.port else host

    kept: list[str] = []
    for key, values in parse_qs(p.query, keep_blank_values=False).items():
        if _TRACKING_PARAM.match(key):
            continue
        kept.extend(f"{key}={v}" for v in values)

    path = p.path.rstrip("/")
    query = "&".join(sorted(kept))
    return urlunparse((p.scheme.lower(), netloc, path, "", query, ""))


def composite_key(job: Job) -> tuple[str, str]:
    """Identify a role at a company regardless of slight region phrasing differences."""
    return (job.company_normalized, job.title_normalized)


def _priority(job: Job) -> int:
    return SOURCE_PRIORITY.get(job.source, _DEFAULT_PRIORITY)


def _borrow_detail(keep: Job, dupe: Job) -> None:
    """The employer copy keeps its apply link but may carry less text.

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
    if keep.apply_deadline is None and dupe.apply_deadline is not None:
        keep.apply_deadline = dupe.apply_deadline
    if not keep.grad_year and dupe.grad_year:
        keep.grad_year = dupe.grad_year
    if not keep.is_new_grad and dupe.is_new_grad:
        keep.is_new_grad = True
    if keep.raw_payload is not None and dupe.raw_payload:
        for k in ("pay", "team_size", "yc_batch", "vc", "funding_stage"):
            if k not in keep.raw_payload and k in dupe.raw_payload:
                keep.raw_payload[k] = dupe.raw_payload[k]


def dedupe(jobs: list[Job]) -> tuple[list[Job], int]:
    """Collapse duplicates within a batch or full corpus. Returns (kept, dropped_count)."""
    by_url: dict[str, Job] = {}
    by_key: dict[tuple[str, str], Job] = {}
    dropped = 0

    # Best priority, highest match score, richest description first
    ordered = sorted(
        jobs,
        key=lambda j: (_priority(j), -j.match_score, -len(j.description or "")),
        reverse=False,
    )

    for job in ordered:
        curl = canonical_url(job.url)
        ckey = composite_key(job)

        incumbent = by_url.get(curl) or (by_key.get(ckey) if ckey[0] and ckey[1] else None)
        if incumbent is not None:
            _borrow_detail(incumbent, job)
            dropped += 1
            continue

        if curl:
            by_url[curl] = job
        if ckey[0] and ckey[1]:
            by_key[ckey] = job

    # Return in descending order of match_score
    result = list(by_url.values())
    result.sort(key=lambda j: -j.match_score)
    return result, dropped
