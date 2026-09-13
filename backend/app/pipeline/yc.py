"""Y Combinator company discovery via the free yc-oss static API.

    https://yc-oss.github.io/api/meta.json  -> endpoint index
    .../companies/hiring.json               -> companies currently hiring

No key, no rate limit, refreshed daily. Each record carries a `slug` that is
usually also the company's ATS board token, which makes YC an unusually good
seed list: these are funded startups, they hire interns far more readily than
enterprises, and small teams mean a new grad does real work.

We only seed candidates here. `validate` decides which boards actually exist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from rich.console import Console

from app.core.companies import get_registry

console = Console()

META = "https://yc-oss.github.io/api/meta.json"

# Batches worth prioritising: recent enough to be growing and still small.
RECENT_BATCH = re.compile(r"(2024|2025|2026)")

INDIA_HINT = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|pune|chennai|mumbai|delhi|"
    r"gurgaon|gurugram|noida)\b",
    re.I,
)


@dataclass(slots=True)
class YCCompany:
    name: str
    slug: str
    batch: str
    website: str
    team_size: int | None
    locations: str
    one_liner: str
    tags: list[str]

    @property
    def is_recent(self) -> bool:
        return bool(RECENT_BATCH.search(self.batch or ""))

    @property
    def in_india(self) -> bool:
        return bool(INDIA_HINT.search(self.locations or ""))

    @property
    def slug_variants(self) -> list[str]:
        """Plausible ATS board tokens for this company.

        Board tokens are usually the slug, but teams also use the bare name
        with punctuation stripped, or the apex domain.
        """
        out = [self.slug]
        squashed = re.sub(r"[^a-z0-9]", "", self.name.lower())
        if squashed and squashed != self.slug:
            out.append(squashed)
        if self.website:
            if m := re.search(r"https?://(?:www\.)?([a-z0-9-]+)\.", self.website.lower()):
                host = m.group(1)
                if host not in out:
                    out.append(host)
        return [s for s in out if 2 <= len(s) <= 40]


async def fetch_hiring(session: httpx.AsyncClient | None = None) -> list[YCCompany]:
    close = session is None
    client = session or httpx.AsyncClient(
        timeout=120, headers={"User-Agent": "jobscrap/0.1 (personal job search)"}
    )
    try:
        meta = (await client.get(META)).json()
        endpoints = meta.get("companies", {})
        # prefer the pre-filtered hiring list; fall back to all + isHiring
        url = (endpoints.get("hiring") or endpoints.get("all", {})).get("api")
        if not url:
            return []
        rows = (await client.get(url)).json()
    finally:
        if close:
            await client.aclose()

    out: list[YCCompany] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("slug"):
            continue
        if r.get("status") not in (None, "Active"):
            continue
        if "hiring" not in url and not r.get("isHiring"):
            continue
        out.append(
            YCCompany(
                name=r.get("name", ""),
                slug=str(r["slug"]).lower(),
                batch=str(r.get("batch") or ""),
                website=str(r.get("website") or ""),
                team_size=r.get("team_size"),
                locations=str(r.get("all_locations") or ""),
                one_liner=str(r.get("one_liner") or ""),
                tags=[str(t) for t in (r.get("tags") or [])],
            )
        )
    return out


async def discover_yc(apply: bool = False, recent_only: bool = False) -> list[YCCompany]:
    companies = await fetch_hiring()
    if not companies:
        console.print("[red]YC API returned nothing[/]")
        return []

    recent = [c for c in companies if c.is_recent]
    india = [c for c in companies if c.in_india]
    console.print(
        f"YC companies hiring: [bold]{len(companies)}[/] | "
        f"recent batch (2024-26): [bold]{len(recent)}[/] | "
        f"India-located: [bold green]{len(india)}[/]"
    )
    if india:
        console.print(
            "[dim]India YC: " + ", ".join(c.name for c in india[:14]) + "[/]"
        )

    pool = recent if recent_only else companies
    # small teams first: a 6-person YC startup is far more likely to give an
    # intern real ownership than a 400-person one, and to reply at all
    pool = sorted(pool, key=lambda c: (c.team_size or 9999))

    slugs: list[str] = []
    for c in pool:
        slugs.extend(c.slug_variants)
    slugs = list(dict.fromkeys(slugs))
    console.print(f"seeding [bold]{len(slugs)}[/] candidate slugs from {len(pool)} companies")

    # Write the slug -> metadata index first: connectors read it at ingest
    # time to attach yc_batch / team_size, which scoring uses to favour
    # small funded teams that actually hire interns.
    from app.core import yc_index

    index: dict[str, dict] = {}
    for c in pool:
        meta = {
            "yc_name": c.name,
            "yc_batch": c.batch,
            "team_size": c.team_size,
            "yc_locations": c.locations,
            "yc_one_liner": c.one_liner[:200],
        }
        for variant in c.slug_variants:
            index.setdefault(variant, meta)
    yc_index.save(index)
    console.print(f"yc_index.json: {len(index)} slug -> company entries")

    if apply:
        registry = get_registry()
        total = 0
        for platform in ("greenhouse", "lever", "ashby", "smartrecruiters"):
            added = registry.add_discovered(platform, slugs)
            total += added
            console.print(f"  {platform:<16} +{added}")
        registry.save()
        console.print(
            f"companies.yaml: +{total} candidates — run `validate --apply` next"
        )
    else:
        console.print("[dim]re-run with --apply to add them as candidates[/]")

    return pool
