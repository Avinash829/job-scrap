"""Find the hiring system behind a startup's own careers page.

Guessing ATS slugs from a company name ("acme" on Greenhouse?) misses most
startups: the board token is often "acmehq", "acme-labs" or "acmeinc", and
many use Keka, Freshteam, Rippling or Zoho rather than the big four. The
company's website already says where it hires, so read it:

    1. GET the homepage; collect ATS links already on it.
    2. Follow up to three same-site links that look like careers pages
       (careers, jobs, join-us, work-with-us, open-roles...).
    3. Pull ATS board URLs out of those pages (links, iframes, embed scripts).
    4. Probe each board with the real connector; keep boards with India or
       worldwide openings, same rule as import-companies.

Detected boards are written to companies.yaml under their platform, with the
company `website` alongside, so they are scraped on every run from then on.

Company lists come from the free yc-oss API (YC companies hiring now, plus
every YC company with an Indian location), so the run needs no key. The
result is checkpointed per website and the command can be re-run monthly.
"""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

from curl_cffi.requests import AsyncSession
from rich.console import Console

from app.connectors.common import BROWSER_UA
from app.core.companies import get_registry
from app.pipeline.import_companies import CHECKPOINT_DIR, PROBES

console = Console()

YC_HIRING = "https://yc-oss.github.io/api/companies/hiring.json"
YC_ALL = "https://yc-oss.github.io/api/companies/all.json"

# platform -> regex whose group(1) is the board slug as our connectors expect it
ATS_PATTERNS: dict[str, re.Pattern[str]] = {
    "greenhouse": re.compile(
        r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]{2,60})", re.I),
    "lever": re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]{2,60})", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]{2,60})", re.I),
    "workable": re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]{2,60})", re.I),
    "smartrecruiters": re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]{2,60})", re.I),
    "recruitee": re.compile(r"([A-Za-z0-9-]{2,60})\.recruitee\.com", re.I),
    "freshteam": re.compile(r"([A-Za-z0-9-]{2,60})\.freshteam\.com", re.I),
    "keka": re.compile(r"([A-Za-z0-9-]{2,60})\.keka\.com/careers", re.I),
    "zoho_recruit": re.compile(r"([A-Za-z0-9-]{2,60}\.zohorecruit\.(?:in|com|eu))", re.I),
    "rippling": re.compile(r"ats\.rippling\.com/([A-Za-z0-9_-]{2,60})", re.I),
    "gem": re.compile(r"jobs\.gem\.com/([A-Za-z0-9_-]{2,60})", re.I),
}
# Path segments that look like slugs but are the platform's own pages.
NOT_A_SLUG = {"embed", "jobs", "job", "api", "careers", "apply", "j", "www", "app", "static", "assets", "v1", "o"}

CAREERS_LINK = re.compile(
    r'href="([^"#]*(?:career|jobs|job-openings|join-us|joinus|join|work-with-us|workwithus|hiring|open-roles|openings|team#jobs)[^"#]*)"',
    re.I,
)
MAX_CAREERS_PAGES = 3
CONCURRENCY = 16


def find_boards(text: str) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for platform, pattern in ATS_PATTERNS.items():
        for m in pattern.finditer(text):
            slug = m.group(1).strip(".").rstrip("/")
            if platform != "zoho_recruit":
                slug = slug.lower()
            if slug.lower() not in NOT_A_SLUG:
                found.add((platform, slug))
    return found


async def _get(session: AsyncSession, url: str) -> tuple[str, str]:
    try:
        r = await session.get(url, timeout=15, allow_redirects=True, headers={"User-Agent": BROWSER_UA})
        return (r.text if r.status_code < 400 else ""), str(r.url)
    except Exception:  # noqa: BLE001 - dead sites, TLS errors, timeouts: nothing to detect
        return "", url


async def detect_site(session: AsyncSession, website: str) -> set[tuple[str, str]]:
    home, final_url = await _get(session, website)
    if not home:
        return set()
    boards = find_boards(home)
    host = urlparse(final_url).netloc.lower().removeprefix("www.")
    links: list[str] = []
    for href in CAREERS_LINK.findall(home):
        url = urljoin(final_url, href)
        netloc = urlparse(url).netloc.lower().removeprefix("www.")
        # same site (or a careers subdomain of it); external ATS links were already read above
        if (netloc == host or netloc.endswith("." + host)) and url not in links:
            links.append(url)
    for url in links[:MAX_CAREERS_PAGES]:
        page, _ = await _get(session, url)
        boards |= find_boards(page)
    return boards


async def _vc_companies() -> list[dict]:
    """Portfolio companies from the VC job boards (Peak XV, Lightspeed, Accel...).

    Their job links usually ARE the startup's ATS posting, so the board is read
    straight off the URL; Consider also gives the company's domain, whose
    careers page is scanned for anything the links didn't reveal.
    """
    from app.connectors.vc_consider import ConsiderBoards
    from app.connectors.vc_getro import GetroBoards

    by_company: dict[str, dict] = {}
    for connector in (ConsiderBoards(), GetroBoards()):
        try:
            raws = list(await connector.fetch())
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]{connector.name} failed: {exc}[/]")
            continue
        for r in raws:
            key = r.company.strip().lower()
            if not key:
                continue
            row = by_company.setdefault(key, {"slug": key, "name": r.company.strip(), "website": None, "urls": set()})
            row["urls"].add(r.url)
            domain = (r.raw_payload or {}).get("company_domain")
            if domain and not row["website"]:
                row["website"] = f"https://{domain}"
    companies = []
    for row in by_company.values():
        row["website"] = row["website"] or f"vc-board:{row['slug']}"
        row["prefound"] = sorted(find_boards(" ".join(row.pop("urls"))))
        companies.append(row)
    return companies


async def _load_companies(session: AsyncSession, source: str) -> list[dict]:
    if source == "vc":
        return await _vc_companies()
    hiring = (await session.get(YC_HIRING, timeout=120)).json()
    companies = {c["slug"]: c for c in hiring if c.get("website")}
    if source == "yc":
        # Indian YC companies often don't set the "hiring" flag on YC's site
        # but do run their own careers page.
        for c in (await session.get(YC_ALL, timeout=180)).json():
            locs = json.dumps([c.get("all_locations"), c.get("regions")])
            if c.get("website") and c.get("status") == "Active" and re.search(r"india", locs, re.I):
                companies.setdefault(c["slug"], c)
    return list(companies.values())


async def detect_careers(source: str = "yc", apply: bool = False, limit: int | None = None) -> dict:
    registry = get_registry()
    known = {(p, c.slug.lower()) for p in ATS_PATTERNS for c in registry.companies(p, include_candidates=True)}

    CHECKPOINT_DIR.mkdir(exist_ok=True)
    checkpoint = CHECKPOINT_DIR / f"careers_{source}.jsonl"
    done: dict[str, dict] = {}
    if checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                done[row["website"]] = row
            except (json.JSONDecodeError, KeyError):
                continue

    async with AsyncSession(impersonate="chrome124") as session:
        companies = await _load_companies(session, source)
        if limit:
            companies = companies[:limit]
        todo = [c for c in companies if c["website"] not in done]
        console.print(f"{source}: {len(companies)} companies with websites · {len(todo)} to scan")

        sem = asyncio.Semaphore(CONCURRENCY)

        async def scan(c: dict) -> dict:
            boards = {tuple(b) for b in c.get("prefound") or []}
            if c["website"].startswith("http"):
                async with sem:
                    boards |= await detect_site(session, c["website"])
            return {"website": c["website"], "slug": c["slug"], "name": c.get("name"),
                    "team_size": c.get("team_size"), "boards": sorted(boards)}

        for start in range(0, len(todo), 200):
            rows = await asyncio.gather(*(scan(c) for c in todo[start:start + 200]))
            with checkpoint.open("a", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
                    done[row["website"]] = row
            print(f"  {min(start + 200, len(todo))}/{len(todo)} sites scanned", flush=True)

    rows = [done[c["website"]] for c in companies if c["website"] in done]
    candidates = {(p, s): r for r in rows for p, s in (tuple(b) for b in r["boards"]) if (p, s.lower()) not in known}
    console.print(f"boards found on careers pages: {sum(len(r['boards']) for r in rows)} · new: {len(candidates)}")

    # Probe new boards with the real connectors; keep India / worldwide openings.
    import httpx

    kept: list[tuple[str, str, dict, object]] = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": BROWSER_UA}) as client:
        sem = asyncio.Semaphore(12)

        async def probe(platform: str, slug: str):
            async with sem:
                try:
                    return await PROBES[platform](client, slug)
                except Exception:  # noqa: BLE001
                    return None

        keys = list(candidates)
        results = await asyncio.gather(*(probe(p, s) for p, s in keys))
        for (platform, slug), result in zip(keys, results):
            if result is not None and result.ok and (result.india or result.worldwide):
                kept.append((platform, slug, candidates[(platform, slug)], result))

    kept.sort(key=lambda k: (-k[3].intern_india, -k[3].india))
    console.print(f"[bold green]{len(kept)}[/] new boards with India/worldwide openings "
                  f"([bold cyan]{sum(1 for k in kept if k[3].intern_india)}[/] with India internships)")
    for platform, slug, row, result in kept[:40]:
        console.print(f"  {platform:<15} {slug:<32} {str(row.get('name'))[:24]:<24} India={result.india} intern={result.intern_india}")

    if apply and kept:
        for platform, slug, row, result in kept:
            # A site linking to several boards is a recruiting product showing
            # its customers (YC's Cargo and Weekday list dozens). The boards are
            # real employers, but not this company - so don't borrow its name.
            if len(row["boards"]) > 2:
                row = {**row, "name": re.sub(r"(inc|hq)$", "", slug.split(".")[0]).replace("-", " ").title(),
                       "website": None}
            block = registry._data.setdefault(platform, {})
            verified = block.get("verified") or []
            block["verified"] = verified
            block.setdefault("candidates", [])
            if any(isinstance(e, dict) and e.get("slug") == slug for e in verified):
                continue
            entry = {"slug": slug, "tier": 1 if result.intern_india else 2, "name": row.get("name") or slug,
                     "note": f"{source} careers page · India {result.india}, intern {result.intern_india}"}
            if row.get("website"):
                entry["website"] = row["website"]
            verified.append(entry)
        registry.save()
        console.print(f"companies.yaml: +{len(kept)} boards")
    return {"scanned": len(rows), "new_boards": len(kept)}
