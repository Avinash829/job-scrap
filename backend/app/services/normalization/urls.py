"""Apply-URL canonicalization and ATS fingerprinting.

Two jobs:

1. Prefer the employer's own apply link over an aggregator wrapper. Aggregator
   URLs carry referral params (`utm_source=arbeitnow.fr&ref=arbeitnow.fr`), so
   applying through one can attribute your application to a third party, and
   it costs an extra redirect. The direct link is usually sitting in the
   description text already.

2. Record which ATS the employer uses. A wrapper URL like
   `careers.datadoghq.com/detail/8114186/?gh_jid=8114186` proves Datadog is on
   Greenhouse - so that company can be fetched from the Greenhouse board API
   with structured location data instead of scraped second-hand.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, urlunparse

# Hosts that republish other people's postings. Their links work, but they are
# never the canonical apply URL.
AGGREGATOR_HOSTS = (
    "arbeitnow.com", "arbeitnow.fr", "arbeitnow.de",
    "remoteok.com", "remoteok.io",
    "remotive.com", "remotive.io",
    "himalayas.app",
    "weworkremotely.com",
    "jobs.workable.com",
    "linkedin.com", "indeed.com", "glassdoor.com",
    "ziprecruiter.com", "simplyhired.com", "jooble.org",
    "news.ycombinator.com",
)

# ATS fingerprints: (name, pattern). Order matters - first match wins.
ATS_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greenhouse", re.compile(r"(boards\.greenhouse\.io|job-boards\.greenhouse\.io|gh_jid=|greenhouse\.io/embed)", re.I)),
    ("lever", re.compile(r"(jobs\.lever\.co|lever\.co/[\w-]+/)", re.I)),
    ("ashby", re.compile(r"(jobs\.ashbyhq\.com|ashbyhq\.com/[\w-]+)", re.I)),
    ("workday", re.compile(r"(myworkdayjobs\.com|\.wd\d+\.myworkday)", re.I)),
    ("smartrecruiters", re.compile(r"(jobs\.smartrecruiters\.com|smartrecruiters\.com/[\w-]+)", re.I)),
    ("workable", re.compile(r"(apply\.workable\.com|\.workable\.com/j/)", re.I)),
    ("recruitee", re.compile(r"\.recruitee\.com", re.I)),
    ("eightfold", re.compile(r"(\.eightfold\.ai|jobs\.eightfold)", re.I)),
    ("successfactors", re.compile(r"(successfactors\.(com|eu)|sapsf\.(com|eu))", re.I)),
    ("icims", re.compile(r"\.icims\.com", re.I)),
    ("taleo", re.compile(r"\.taleo\.net", re.I)),
    ("jobvite", re.compile(r"\.jobvite\.com", re.I)),
    ("darwinbox", re.compile(r"\.darwinbox\.(in|com)", re.I)),
    ("teamtailor", re.compile(r"\.teamtailor\.com", re.I)),
)

_TRACKING_PARAM = re.compile(
    r"^(utm_\w+|ref|referrer|source|src|gh_src|lever-source|fbclid|gclid|"
    r"mc_cid|mc_eid|_hsenc|_hsmi|trk|trackingId)$",
    re.I,
)

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'\)\]]+")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def is_aggregator(url: str) -> bool:
    host = host_of(url)
    return any(host == h or host.endswith(f".{h}") for h in AGGREGATOR_HOSTS)


def detect_ats(*texts: str | None) -> str | None:
    """Identify the employer's ATS from any URL or text we have."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    for name, pattern in ATS_SIGNATURES:
        if pattern.search(blob):
            return name
    return None


def strip_tracking(url: str) -> str:
    """Drop referral/analytics params but keep functional ones (e.g. gh_jid)."""
    try:
        parts = urlparse(url)
    except ValueError:
        return url

    kept: list[str] = []
    for key, values in parse_qs(parts.query, keep_blank_values=False).items():
        if _TRACKING_PARAM.match(key):
            continue
        kept.extend(f"{key}={v}" for v in values)

    return urlunparse(
        (
            parts.scheme.lower(),
            (parts.netloc or "").lower(),
            parts.path.rstrip("/") or "/",
            "",
            "&".join(kept),
            "",
        )
    )


def canonical_apply_url(
    url: str, description: str | None = None, company: str | None = None
) -> tuple[str, str | None]:
    """Best apply URL plus the detected ATS.

    If `url` is an aggregator wrapper, look through the description for a
    direct employer link and prefer it - scoring ATS links highest, then any
    non-aggregator host. Falls back to the cleaned original when nothing
    better exists.
    """
    cleaned = strip_tracking(url)

    if not is_aggregator(cleaned):
        return cleaned, detect_ats(cleaned, description)

    candidates = _URL_IN_TEXT.findall(description or "")
    best: str | None = None
    best_rank = -1

    for candidate in candidates:
        candidate = candidate.rstrip(".,;")
        if is_aggregator(candidate):
            continue
        rank = 0
        if detect_ats(candidate):
            rank = 2
        elif re.search(r"(careers?|jobs?|apply|work-with-us)", candidate, re.I):
            rank = 1
        if rank > best_rank:
            best, best_rank = candidate, rank

    if best and best_rank > 0:
        chosen = strip_tracking(best)
        return chosen, detect_ats(chosen, description)

    return cleaned, detect_ats(cleaned, description)
