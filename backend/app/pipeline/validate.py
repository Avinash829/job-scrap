"""Validate candidate company slugs against each ATS, then update companies.yaml.

Promotion needs a live 200 with >=1 posting. Pruning requires a definitive
404 - an empty board or a timeout is left alone, because "zero openings today"
and "the endpoint was down" are not evidence that a slug is wrong. That
distinction is exactly what BoardStatus exists for.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from rich.console import Console
from rich.table import Table

from app.connectors.ats import ATS_CONNECTORS
from app.connectors.ats.base import BoardStatus
from app.core.companies import Company, get_registry

console = Console()


async def validate(apply: bool = False, include_verified: bool = False) -> dict:
    registry = get_registry()
    summary: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    promote: dict[tuple[str, str], str | None] = {}
    prune: list[tuple[str, str]] = []
    rows: list[tuple[str, str, str, int]] = []

    for cls in ATS_CONNECTORS:
        conn = cls()
        targets = [
            c
            for c in registry.companies(cls.platform, include_candidates=True)
            if include_verified or not c.verified
        ]
        if not targets:
            continue

        console.print(f"[dim]checking {len(targets)} {cls.platform} slugs…[/]")
        sem = asyncio.Semaphore(cls.concurrency)

        async def one(company: Company):
            async with sem:
                return await conn.fetch_board(company)

        results = await asyncio.gather(*(one(c) for c in targets))

        for r in results:
            summary[cls.platform][r.status.value] += 1
            if r.status is BoardStatus.OK:
                rows.append((cls.platform, r.company.slug, "ok", len(r.jobs)))
                if not r.company.verified:
                    promote[(cls.platform, r.company.slug)] = f"{len(r.jobs)} jobs"
            elif r.status is BoardStatus.NO_BOARD and not r.company.verified:
                prune.append((cls.platform, r.company.slug))

    t = Table(title="validation", header_style="bold")
    for col in ("platform", "ok", "empty", "no_board", "failed"):
        t.add_column(col)
    for platform, counts in sorted(summary.items()):
        t.add_row(
            platform,
            str(counts["ok"]),
            str(counts["empty"]),
            str(counts["no_board"]),
            str(counts["failed"]),
        )
    console.print(t)

    if rows:
        live = Table(title="boards with open postings", header_style="bold")
        for col in ("platform", "slug", "jobs"):
            live.add_column(col)
        for platform, slug, _, n in sorted(rows, key=lambda r: -r[3])[:40]:
            live.add_row(platform, slug, str(n))
        console.print(live)

    console.print(
        f"\n[green]{len(promote)}[/] new working boards | "
        f"[red]{len(prune)}[/] confirmed 404s"
    )

    if apply:
        added = registry.promote(promote)
        removed = registry.prune(prune)
        registry.save()
        console.print(
            f"companies.yaml updated: +{added} verified, -{removed} dead candidates"
        )
    elif promote or prune:
        console.print("[dim]re-run with --apply to write companies.yaml[/]")

    return {"promote": promote, "prune": prune, "summary": dict(summary)}
