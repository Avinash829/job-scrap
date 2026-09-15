"""Discover ATS company slugs from the Common Crawl index.

Common Crawl publishes a free, key-less CDX index of every URL it has
crawled. Querying it for `boards.greenhouse.io/*` yields the board tokens of
essentially every company that has ever had a public Greenhouse board -
which is how large public aggregators build 90k+ company lists without
hand-curating anything.

Discovered slugs land in companies.yaml as *candidates*, never as verified:
a crawled URL can belong to a portfolio company, an aggregator, or a board
that no longer exists. `validate` is what promotes them.
"""
from __future__ import annotations

import json
import re
from collections import Counter

import httpx
from rich.console import Console

from app.core.companies import get_registry

console = Console()

CDX = "https://index.commoncrawl.org/CC-MAIN-2025-08-index"

# URL pattern -> slug-extracting regex, per platform
PATTERNS: dict[str, tuple[str, re.Pattern[str]]] = {
    "greenhouse": (
        "boards.greenhouse.io/*",
        re.compile(r"boards\.greenhouse\.io/([A-Za-z0-9_-]{2,40})"),
    ),
    "lever": (
        "jobs.lever.co/*",
        re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]{2,40})"),
    ),
    "ashby": (
        "jobs.ashbyhq.com/*",
        re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]{2,40})"),
    ),
    "workable": (
        "apply.workable.com/*",
        re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]{2,40})"),
    ),
    "freshteam": (
        "*.freshteam.com/jobs*",
        re.compile(r"([A-Za-z0-9-]{2,40})\.freshteam\.com"),
    ),
    "recruitee": (
        "*.recruitee.com/*",
        re.compile(r"([A-Za-z0-9-]{2,40})\.recruitee\.com"),
    ),
    "keka": (
        "*.keka.com/careers*",
        re.compile(r"([A-Za-z0-9-]{2,40})\.keka\.com"),
    ),
    "gem": (
        "jobs.gem.com/*",
        re.compile(r"jobs\.gem\.com/([A-Za-z0-9_-]{2,40})"),
    ),
    "rippling": (
        "ats.rippling.com/*",
        re.compile(r"ats\.rippling\.com/([A-Za-z0-9_-]{2,40})"),
    ),
    "smartrecruiters": (
        "jobs.smartrecruiters.com/*",
        re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]{2,40})"),
    ),
}

# Path segments that look like slugs but aren't companies.
NOT_A_SLUG = {
    "embed", "jobs", "job", "api", "v1", "board", "boards", "search",
    "assets", "static", "css", "js", "img", "images", "favicon.ico",
    "robots.txt", "sitemap.xml", "login", "signup", "apply", "oauth",
    "en-us", "en", "www", "careers", "company", "companies",
}


async def fetch_page(client: httpx.AsyncClient, url_pattern: str, page: int) -> list[str]:
    r = await client.get(
        CDX,
        params={
            "url": url_pattern,
            "output": "json",
            "limit": "1000",
            "page": str(page),
        },
    )
    if r.status_code != 200:
        return []
    return r.text.strip().splitlines()


async def discover(platform: str, pages: int = 3, apply: bool = False) -> list[str]:
    if platform not in PATTERNS:
        console.print(
            f"[red]unknown platform {platform}[/] (have: {', '.join(PATTERNS)})"
        )
        return []

    url_pattern, slug_re = PATTERNS[platform]
    counts: Counter[str] = Counter()

    console.print(f"[dim]querying Common Crawl for {url_pattern}…[/]")
    async with httpx.AsyncClient(
        timeout=90, headers={"User-Agent": "jobscrap/0.1 (personal job search)"}
    ) as client:
        for page in range(pages):
            try:
                lines = await fetch_page(client, url_pattern, page)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[yellow]page {page} failed: {type(exc).__name__}[/]")
                break
            if not lines:
                break
            for line in lines:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if m := slug_re.search(rec.get("url", "")):
                    slug = m.group(1).lower()
                    if slug not in NOT_A_SLUG and not slug.isdigit():
                        counts[slug] += 1
            console.print(f"[dim]  page {page}: {len(counts)} unique slugs so far[/]")

    slugs = [s for s, _ in counts.most_common()]
    console.print(f"\nfound [bold]{len(slugs)}[/] candidate {platform} slugs")
    if slugs:
        console.print(f"[dim]sample: {', '.join(slugs[:15])}[/]")

    if apply and slugs:
        registry = get_registry()
        added = registry.add_discovered(platform, slugs)
        registry.save()
        console.print(
            f"companies.yaml: +{added} new candidates "
            f"(run `validate --apply` to confirm which are live)"
        )
    elif slugs:
        console.print("[dim]re-run with --apply to add them as candidates[/]")

    return slugs
