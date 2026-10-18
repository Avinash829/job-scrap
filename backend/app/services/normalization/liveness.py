"""Ask a board directly whether a stored job still accepts applications.

The generic link check can only see what a browser sees, and the big
JS-rendered boards render the same empty shell for a live job and a removed
one. Where a platform exposes the truth in an API, ask it.

Workday first, because it is the largest source here (476 of 1,285 stored
jobs) and it is searched rather than listed: a closed requisition can keep
answering 200 long after it stopped accepting applications. Its job endpoint
returns `canApply`, `posted` and often `endDate` - a real application
deadline. Verified 2026-09-18: a live job answers
{"canApply": true, "posted": true, "endDate": "2026-09-21"}, and a removed one
answers 404.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.connectors.common import BROWSER_UA
from app.domain.entities import Job

log = logging.getLogger(__name__)

CONCURRENCY = 8
TIMEOUT = 20.0


@dataclass(slots=True)
class Liveness:
    """What the board says about one job. `open_` None = couldn't tell."""

    open_: bool | None = None
    deadline: datetime | None = None
    reason: str | None = None


def _parse_day(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc) if value else None
    except ValueError:
        return None


def workday_api_url(job: Job) -> str | None:
    """Rebuild the job's cxs endpoint from its stored id ("tenant:site:/job/...")."""
    parts = job.source_job_id.split(":", 2)
    if len(parts) != 3:
        return None
    tenant, site, path = parts
    host = job.url.split("/")[2] if "//" in job.url else f"{tenant}.wd1.myworkdayjobs.com"
    if not host.endswith("myworkdayjobs.com"):
        return None
    return f"https://{host}/wday/cxs/{tenant}/{site}{path}"


async def check_workday(jobs: list[Job]) -> dict[tuple[str, str], Liveness]:
    """Liveness for Workday jobs. Anything inconclusive is left alone."""
    import httpx

    out: dict[tuple[str, str], Liveness] = {}
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True,
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
    ) as client:

        async def one(job: Job) -> None:
            url = workday_api_url(job)
            if not url:
                return
            async with sem:
                try:
                    r = await client.get(url)
                except httpx.HTTPError as exc:
                    out[(job.source, job.source_job_id)] = Liveness(None, None, type(exc).__name__)
                    return
            if r.status_code in (404, 410):
                out[(job.source, job.source_job_id)] = Liveness(False, None, "requisition gone")
                return
            if r.status_code != 200:
                out[(job.source, job.source_job_id)] = Liveness(None, None, f"HTTP {r.status_code}")
                return
            try:
                info = (r.json() or {}).get("jobPostingInfo") or {}
            except ValueError:
                out[(job.source, job.source_job_id)] = Liveness(None, None, "unparseable")
                return
            deadline = _parse_day(info.get("endDate"))
            if info.get("canApply") is False or info.get("posted") is False:
                out[(job.source, job.source_job_id)] = Liveness(False, deadline, "not accepting applications")
            else:
                out[(job.source, job.source_job_id)] = Liveness(True, deadline)

        await asyncio.gather(*(one(j) for j in jobs))
    return out


async def check_jobs(jobs: list[Job]) -> dict[tuple[str, str], Liveness]:
    """Platform-aware liveness for the sources that expose it."""
    workday = [j for j in jobs if j.source == "workday"]
    if not workday:
        return {}
    results = await check_workday(workday)
    closed = sum(1 for v in results.values() if v.open_ is False)
    log.info("workday liveness: %d checked, %d closed", len(results), closed)
    return results
