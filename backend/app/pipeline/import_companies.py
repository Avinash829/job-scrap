"""Import company lists from a public dataset, keeping only India-relevant ones.

Source: github.com/Feashliaa/job-board-aggregator (MIT). It publishes company
identifiers harvested from Common Crawl:

    data/workday_companies.json     "tenant|wd|site"   (~12,900)
    data/greenhouse_companies.json  "slug"             (~8,300)
    data/lever_companies.json       "slug"             (~4,400)
    data/ashby_companies.json       "slug"             (~3,200)

Scraping all ~29k boards on every run would blow the free tier - on Neon
storage and on Actions minutes - and almost all of them are irrelevant to
someone looking for work in India. So each company gets ONE cheap probe:

    workday     POST searchText "India", 20 rows        -> rows whose location is India
                POST searchText "intern India", 20 rows -> India rows with intern titles
    greenhouse  GET board without content=true          -> count India / remote rows

Only companies with India (or worldwide-remote) openings are added. The ones
with India INTERNSHIPS go to tier 1 (refreshed every 6h), the rest to tier 2
(daily). The probe is a one-off; re-run it monthly to pick up new employers.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from rich.console import Console

from app.connectors.common import BROWSER_UA
from app.core.companies import get_registry

console = Console()

DATASET = "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/{platform}_companies.json"

_INDIA = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|pune|chennai|mumbai|delhi|gurgaon|"
    r"gurugram|noida|kolkata|ahmedabad|coimbatore|kochi)\b",
    re.I,
)
_WORLDWIDE = re.compile(r"\b(worldwide|anywhere|global)\b", re.I)
_INTERN = re.compile(r"\b(intern|internship|graduate|new\s*grad|trainee|apprentice)\b", re.I)


@dataclass(slots=True)
class Probe:
    key: str
    india: int = 0
    intern_india: int = 0
    worldwide: int = 0
    ok: bool = False
    meta: dict | None = None
    # last HTTP status; None = network error. 400/404/422 mean the board is
    # gone, anything else (429, 5xx, timeout) is worth probing again.
    status: int | None = None


async def _load_dataset(client: httpx.AsyncClient, platform: str) -> list[str]:
    r = await client.get(DATASET.format(platform=platform))
    r.raise_for_status()
    data = r.json()
    return [str(x) for x in data] if isinstance(data, list) else []


async def _probe_workday(client: httpx.AsyncClient, entry: str) -> Probe:
    parts = entry.split("|")
    if len(parts) != 3:
        return Probe(entry)
    tenant, wd, site = parts
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    probe = Probe(entry, meta={"slug": tenant, "wd": wd, "site": site})

    # Workday's `total` can't be trusted as a signal: search is fuzzy, so
    # "India" also matches Indiana (a US hospital chain came back with 178
    # "India" roles), and "intern India" behaves like an OR. So look at the
    # actual rows returned and count locations that really are in India -
    # _INDIA uses word boundaries, which keeps "Indiana" out.
    async def rows(text: str) -> list[dict] | None:
        # Retry transient failures: in the first full run 8,081 of 12,883
        # tenants "failed", and a sample re-probed later showed ~1 in 6 of
        # those answering fine - they had been rate-limited or timed out.
        for attempt in range(3):
            try:
                r = await client.post(
                    url, json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": text}
                )
            except httpx.HTTPError:
                probe.status = None
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            probe.status = r.status_code
            if r.status_code in DEAD_STATUS:
                return None
            if r.status_code != 200:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            try:
                return r.json().get("jobPostings") or []
            except (ValueError, json.JSONDecodeError):
                return None
        return None

    india_rows = await rows("India")
    if india_rows is None:
        return probe
    probe.ok = True
    probe.india = sum(1 for p in india_rows if _INDIA.search(str(p.get("locationsText") or "")))
    if probe.india:
        intern_rows = await rows("intern India") or []
        probe.intern_india = sum(
            1
            for p in intern_rows
            if _INDIA.search(str(p.get("locationsText") or ""))
            and _INTERN.search(str(p.get("title") or ""))
        )
    return probe


async def _probe_greenhouse(client: httpx.AsyncClient, slug: str) -> Probe:
    probe = Probe(slug, meta={"slug": slug})
    try:
        r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    except httpx.HTTPError:
        return probe
    if r.status_code != 200:
        return probe
    try:
        jobs = r.json().get("jobs") or []
    except (ValueError, json.JSONDecodeError):
        return probe
    probe.ok = True
    for j in jobs:
        loc = str((j.get("location") or {}).get("name") or "")
        title = str(j.get("title") or "")
        if _INDIA.search(loc):
            probe.india += 1
            if _INTERN.search(title):
                probe.intern_india += 1
        elif _WORLDWIDE.search(loc):
            probe.worldwide += 1
    return probe


PROBES = {"workday": _probe_workday, "greenhouse": _probe_greenhouse}
CONCURRENCY = {"workday": 12, "greenhouse": 40}
DEAD_STATUS = frozenset({400, 401, 404, 410, 422})


CHECKPOINT_DIR = Path(__file__).resolve().parents[2] / ".import_cache"
CHUNK = 500


def _load_checkpoint(path: Path) -> dict[str, Probe]:
    done: dict[str, Probe] = {}
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        done[d["key"]] = Probe(**d)
    return done


async def import_companies(
    platform: str, apply: bool = False, limit: int | None = None, retry_failed: bool = False
) -> dict:
    """Probe in chunks of 500, checkpointing each chunk to disk.

    The first version gathered all ~12,900 probes at once over HTTP/2: an
    hour in it was burning CPU on three connections with no visible progress
    and nothing saved, so it had to be killed and the work was lost. HTTP/2
    buys nothing when every request goes to a different host, chunks bound
    memory and show progress, and the checkpoint makes a killed run resumable.
    """
    if platform not in PROBES:
        console.print(f"[red]unsupported platform {platform}[/] (have: {', '.join(PROBES)})")
        return {}

    registry = get_registry()
    # A Workday tenant can run several career sites (Allegion has `careers`,
    # `earlytalent_careers` and `manufacturing_careers`), so its identity is
    # tenant+site. Keying on the tenant alone kept only the first site - and
    # would have dropped exactly the early-talent site that lists internships.
    def identity(c) -> str:
        return f"{c.slug}|{c.get('site')}" if platform == "workday" else c.slug

    known = {identity(c) for c in registry.companies(platform, include_candidates=True)}

    CHECKPOINT_DIR.mkdir(exist_ok=True)
    checkpoint = CHECKPOINT_DIR / f"{platform}_probe.jsonl"
    finished = _load_checkpoint(checkpoint)

    limits = httpx.Limits(max_connections=CONCURRENCY[platform], max_keepalive_connections=10)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0), limits=limits,
        follow_redirects=True, http2=False,
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
    ) as client:
        entries = await _load_dataset(client, platform)
        if platform == "workday":
            entries = [e for e in entries if "|".join(e.split("|")[::2]) not in known]
        else:
            entries = [e for e in entries if e not in known]
        if limit:
            entries = entries[:limit]
        def settled(e: str) -> bool:
            p = finished.get(e)
            if p is None:
                return False
            # --retry-failed re-probes boards that failed for a transient
            # reason; a 404/422 board is gone and stays skipped.
            return p.ok or not retry_failed or p.status in DEAD_STATUS
        todo = [e for e in entries if not settled(e)]
        console.print(
            f"{platform}: {len(entries)} candidates · {len(entries) - len(todo)} already "
            f"probed (checkpoint) · [bold]{len(todo)}[/] to probe",
            highlight=False,
        )

        sem = asyncio.Semaphore(CONCURRENCY[platform])

        async def run(entry: str) -> Probe:
            async with sem:
                try:
                    return await PROBES[platform](client, entry)
                except Exception:  # noqa: BLE001 - one bad host must not sink the chunk
                    return Probe(entry)

        for start in range(0, len(todo), CHUNK):
            chunk = todo[start : start + CHUNK]
            results = await asyncio.gather(*(run(e) for e in chunk))
            with checkpoint.open("a", encoding="utf-8") as fh:
                for p in results:
                    fh.write(json.dumps({
                        "key": p.key, "india": p.india, "intern_india": p.intern_india,
                        "worldwide": p.worldwide, "ok": p.ok, "meta": p.meta, "status": p.status,
                    }) + "\n")
                    finished[p.key] = p
            hits = sum(1 for p in results if p.india or p.worldwide)
            print(
                f"  {min(start + CHUNK, len(todo))}/{len(todo)} probed · "
                f"+{hits} with India openings in this chunk",
                flush=True,
            )

    probes = [finished[e] for e in entries if e in finished]

    reachable = [p for p in probes if p.ok]
    relevant = [p for p in reachable if p.india or p.worldwide]
    interns = [p for p in relevant if p.intern_india]
    console.print(
        f"\n{platform}: {len(reachable)} boards answered · "
        f"[bold green]{len(relevant)}[/] with India/worldwide openings · "
        f"[bold cyan]{len(interns)}[/] with India internship/graduate roles"
    )
    for p in sorted(interns, key=lambda x: -x.intern_india)[:25]:
        console.print(f"  {p.key:<50} India={p.india:<5} intern/grad={p.intern_india}")

    if apply and relevant:
        block = registry._data.setdefault(platform, {})
        verified = block.setdefault("verified", [])
        def ident(e) -> str:
            if not isinstance(e, dict):
                return str(e)
            return f"{e['slug']}|{e.get('site')}" if platform == "workday" else e["slug"]

        existing = {ident(e) for e in verified}
        added = 0
        for p in sorted(relevant, key=lambda x: (-x.intern_india, -x.india)):
            meta = dict(p.meta or {})
            if ident(meta) in existing:
                continue
            meta["tier"] = 1 if p.intern_india else 2
            meta["note"] = f"India {p.india}, intern/grad {p.intern_india}"
            verified.append(meta)
            existing.add(ident(meta))
            added += 1
        registry.save()
        console.print(f"companies.yaml: +{added} {platform} companies")

    return {
        "probed": len(entries),
        "reachable": len(reachable),
        "relevant": len(relevant),
        "with_interns": len(interns),
    }
