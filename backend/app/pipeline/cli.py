"""Operator CLI.

    python -m app.pipeline.cli ingest --tier 1
    python -m app.pipeline.cli ingest --tier 1 --no-enrich
    python -m app.pipeline.cli show --limit 20
    python -m app.pipeline.cli enrich          # LLM pass over pending rows only
    python -m app.pipeline.cli health
    python -m app.pipeline.cli purge --days 60

The same entry point runs locally and in GitHub Actions; only the flags differ.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from rich.console import Console
from rich.table import Table

from app.db.repository import JobFilter, JobRepository
from app.db.session import init_db, session_scope
from app.domain.enums import HiringRegion
from app.pipeline.orchestrator import IngestPipeline, PipelineResult

console = Console()


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


# --------------------------------------------------------------------- ingest


def _print_result(result: PipelineResult) -> None:
    health = Table(title="connector health", header_style="bold")
    for col in ("source", "found", "kept", "new", "secs", "status"):
        health.add_column(col)

    for r in sorted(result.runs, key=lambda x: x.connector):
        status = "[green]ok[/]" if r.ok else f"[red]{r.error or 'failed'}[/]"
        health.add_row(
            r.connector,
            str(r.jobs_found),
            str(r.jobs_kept),
            str(r.jobs_new),
            f"{r.duration_s:.1f}" if r.duration_s else "-",
            status,
        )
    console.print(health)

    if result.drop_reasons:
        console.print("\n[dim]filtered out:[/]")
        for reason, n in sorted(
            result.drop_reasons.items(), key=lambda kv: -kv[1]
        )[:10]:
            console.print(f"  [dim]{n:>5}  {reason}[/]")

    console.print(
        f"\nfetched [bold]{result.total_found}[/] | "
        f"new [bold green]{result.total_new}[/] | "
        f"refreshed [bold]{result.total_updated}[/] | "
        f"scored [bold]{result.scored}[/]"
    )
    if result.enriched:
        model = result.llm_model or "cache"
        console.print(
            f"enriched [bold cyan]{result.enriched}[/] rows "
            f"({result.llm_calls} LLM calls, model={model})"
        )
    if result.links_checked:
        console.print(
            f"links verified [bold]{result.links_checked}[/] | "
            f"dead removed [bold red]{result.links_dead}[/]"
        )
    if result.llm_note:
        console.print(f"[yellow]{result.llm_note}[/]")


async def cmd_ingest(args: argparse.Namespace) -> int:
    pipeline = IngestPipeline(
        enrich=not args.no_enrich,
        apply_filter=not args.no_filter,
        check_links=not args.no_link_check,
        fresh=args.fresh,
    )
    console.print(f"[bold cyan]scraping[/] tier={args.tier or 'all'}")
    result = await pipeline.run(args.tier)
    if not result.runs:
        console.print(f"[yellow]no connectors registered for tier {args.tier}[/]")
        return 1
    _print_result(result)
    return 0


async def cmd_enrich(args: argparse.Namespace) -> int:
    """LLM pass only - useful after adding keys, with no re-fetching."""
    from app.core.config import get_settings
    from app.services.enrichment.extractor import LLMExtractor
    from app.services.enrichment.provider import GeminiProvider, LLMUnavailable

    settings = get_settings()
    if not settings.llm_enabled:
        console.print("[red]no GEMINI_API_KEYS in .env[/]")
        return 1

    with session_scope() as s:
        repo = JobRepository(s)
        pending = repo.pending_enrichment(limit=args.limit)
        if not pending:
            console.print("[green]nothing pending enrichment[/]")
            return 0

        cached = repo.cached_extractions([j.description_hash for j in pending])
        applied = repo.apply_extractions(cached) if cached else 0
        pending = [j for j in pending if j.description_hash not in cached]
        console.print(f"{len(cached)} from cache, {len(pending)} need the LLM")

        if pending:
            provider = GeminiProvider()
            extractor = LLMExtractor(provider)
            by_hash = {j.description_hash: j for j in pending}
            try:
                fresh = await extractor.extract(list(by_hash.values()))
            except LLMUnavailable as exc:
                console.print(f"[red]LLM unavailable: {exc}[/]")
                return 1
            if fresh:
                model = provider.active_model or settings.gemini_model
                repo.store_extractions(fresh, model)
                applied += repo.apply_extractions(fresh)
                console.print(
                    f"model={model} calls={provider.call_count} "
                    f"extracted={len(fresh)}"
                )
            for st in provider.pool.stats():
                console.print(f"  [dim]{st['key']}  ok={st['ok']} fail={st['fail']}[/]")

        console.print(f"\n[bold green]{applied}[/] rows enriched")
    return 0


# ----------------------------------------------------------------------- show


def cmd_show(args: argparse.Namespace) -> int:
    with session_scope() as s:
        repo = JobRepository(s)
        jobs, total = repo.search(
            JobFilter(
                limit=args.limit,
                order_by="match_score" if not args.newest else "first_seen_at",
                reachable_only=args.reachable,
                max_min_yoe=0,
                include_unknown_yoe=True,
            )
        )

    if not jobs:
        console.print("[yellow]nothing stored - run: ingest --tier 1[/]")
        return 0

    t = Table(
        title=f"top {len(jobs)} of {total} in-scope postings",
        header_style="bold",
        expand=True,
    )
    t.add_column("score", width=5)
    t.add_column("role", style="cyan", max_width=30, overflow="ellipsis")
    t.add_column("company", max_width=16, overflow="ellipsis")
    t.add_column("cat", width=9)
    t.add_column("yoe", width=4)
    t.add_column("region", width=11)
    t.add_column("stack", max_width=22, style="dim", overflow="ellipsis")
    t.add_column("src", width=9, style="dim")

    for j in jobs:
        regions = j.hiring_regions or [HiringRegion.UNKNOWN]
        label = ",".join(r.value for r in regions[:2])
        if HiringRegion.WORLDWIDE in regions:
            region = f"[bold green]{label}[/]"
        elif HiringRegion.INDIA in regions:
            region = f"[cyan]{label}[/]"
        elif regions == [HiringRegion.UNKNOWN]:
            region = f"[dim]{label}[/]"
        else:
            region = f"[yellow]{label}[/]"

        yoe = "-" if j.min_yoe is None else str(j.min_yoe)
        if j.is_new_grad:
            yoe = f"[green]{yoe}*[/]"

        t.add_row(
            f"{j.match_score:.0f}",
            j.title,
            j.company,
            j.role_category.value,
            yoe,
            region,
            ", ".join(j.tech_stack[:3]),
            j.source,
        )

    console.print(t)
    console.print(
        "[dim]* new-grad/intern signal   "
        "green region = reachable from India   dim = unresolved[/]"
    )
    return 0


def cmd_health(_: argparse.Namespace) -> int:
    with session_scope() as s:
        repo = JobRepository(s)
        stats = repo.stats()
        health = repo.connector_health()

    console.print(
        f"[bold]{stats['active']}[/] active / {stats['total']} total | "
        f"[green]{stats['reachable_from_india']}[/] reachable from India | "
        f"[yellow]{stats['pending_enrichment']}[/] pending enrichment"
    )

    if health:
        t = Table(title="last run per connector", header_style="bold")
        for col in ("connector", "last run", "found", "kept", "new", "status"):
            t.add_column(col)
        for h in health:
            status = "[green]ok[/]" if h["ok"] else f"[red]{h['error'] or 'failed'}[/]"
            t.add_row(
                h["connector"],
                h["last_run"].strftime("%m-%d %H:%M"),
                str(h["jobs_found"]),
                str(h["jobs_kept"]),
                str(h["jobs_new"]),
                status,
            )
        console.print(t)

    if stats["by_role"]:
        console.print("\n[dim]by role: " + "  ".join(
            f"{k}={v}" for k, v in sorted(stats["by_role"].items())
        ) + "[/]")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    with session_scope() as s:
        removed = JobRepository(s).purge_older_than(args.days)
    console.print(f"purged [bold]{removed}[/] inactive rows older than {args.days}d")
    return 0


# ----------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    # -v is accepted both before and after the subcommand. argparse only
    # binds a top-level flag BEFORE the subcommand, so `cli ingest -v` failed
    # with "unrecognized arguments: -v" and broke every scheduled Actions run.
    # The subparser copy uses SUPPRESS so it never overwrites a -v that was
    # given in the top-level position with its own False default.
    verbose = argparse.ArgumentParser(add_help=False)
    verbose.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="info-level logging",
    )

    ap = argparse.ArgumentParser(prog="jobscrap", parents=[verbose])
    sub = ap.add_subparsers(dest="command", required=True)

    _add_parser = sub.add_parser

    def add_parser(name, **kw):
        return _add_parser(name, parents=[verbose], **kw)

    sub.add_parser = add_parser  # every subcommand inherits -v

    # "scrape" is the name you'll see everywhere; "ingest" still works as an
    # alias so older commands and workflow runs don't break.
    p = sub.add_parser(
        "scrape",
        aliases=["ingest"],
        help="scrape every source, filter to your profile, score, and save",
    )
    p.add_argument("--tier", type=int, choices=[1, 2, 3])
    p.add_argument("--no-enrich", action="store_true", help="skip the LLM pass")
    p.add_argument("--fresh", action="store_true",
                   help="after saving, delete jobs this run didn't see (the daily fresh start)")
    p.add_argument("--no-filter", action="store_true", help="keep out-of-scope rows active")
    p.add_argument("--no-link-check", action="store_true", help="skip apply-link verification")

    p = sub.add_parser("enrich", help="LLM pass over pending rows, no fetching")
    p.add_argument("--limit", type=int, default=300)

    p = sub.add_parser("show", help="print top matches")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--reachable", action="store_true", help="worldwide/India only")
    p.add_argument("--newest", action="store_true", help="order by first seen")

    sub.add_parser("health", help="connector + corpus health")

    p = sub.add_parser(
        "reset",
        help="empty the job table for a fresh daily start (keeps the LLM cache)",
    )
    p.add_argument("--yes", action="store_true", help="required - this deletes every job row")

    sub.add_parser(
        "rescore",
        help="recompute match scores and reasons for active jobs, no fetching",
    )

    p = sub.add_parser("validate", help="test candidate ATS slugs, update companies.yaml")
    p.add_argument("--apply", action="store_true", help="write companies.yaml")
    p.add_argument("--all", action="store_true", help="re-check verified slugs too")

    p = sub.add_parser("discover", help="find new ATS slugs via Common Crawl")
    p.add_argument("--platform", default="greenhouse")
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--apply", action="store_true")

    p = sub.add_parser(
        "import-companies",
        help="probe a public company dataset and add employers with India openings",
    )
    p.add_argument("--platform", required=True, choices=["workday", "greenhouse", "lever", "ashby",
                            "freshteam", "keka", "gem", "recruitee", "workable", "rippling"])
    p.add_argument("--apply", action="store_true", help="write companies.yaml")
    p.add_argument("--limit", type=int, help="probe only the first N (for a quick test)")
    p.add_argument("--retry-failed", action="store_true",
                   help="re-probe boards that failed for a transient reason (timeout, 429, 5xx)")

    p = sub.add_parser("yc", help="seed YC-backed companies that are hiring")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--recent", action="store_true", help="2024-26 batches only")

    p = sub.add_parser("purge", help="delete old inactive rows")
    p.add_argument("--days", type=int, default=60)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(getattr(args, "verbose", False))
    init_db()

    if args.command in ("scrape", "ingest"):
        return asyncio.run(cmd_ingest(args))
    if args.command == "enrich":
        return asyncio.run(cmd_enrich(args))
    if args.command == "show":
        return cmd_show(args)
    if args.command == "health":
        return cmd_health(args)
    if args.command == "reset":
        if not args.yes:
            console.print("[red]refusing to reset without --yes[/] (this deletes every job row)")
            return 2
        with session_scope() as s:
            out = JobRepository(s).reset_daily()
        console.print(
            f"reset: deleted [bold]{out['jobs_deleted']}[/] jobs · "
            f"pruned {out['cache_pruned']} stale cache entries · {out['runs_pruned']} old run logs"
        )
        return 0
    if args.command == "rescore":
        from app.pipeline.orchestrator import IngestPipeline

        with session_scope() as s:
            n = IngestPipeline()._rescore(JobRepository(s))
        console.print(f"rescored [bold]{n}[/] active jobs")
        return 0
    if args.command == "validate":
        from app.pipeline.validate import validate

        asyncio.run(validate(apply=args.apply, include_verified=args.all))
        return 0
    if args.command == "discover":
        from app.pipeline.discover import discover

        asyncio.run(discover(args.platform, pages=args.pages, apply=args.apply))
        return 0
    if args.command == "import-companies":
        from app.pipeline.import_companies import import_companies

        asyncio.run(import_companies(
            args.platform, apply=args.apply, limit=args.limit, retry_failed=args.retry_failed
        ))
        return 0
    if args.command == "yc":
        from app.pipeline.yc import discover_yc

        asyncio.run(discover_yc(apply=args.apply, recent_only=args.recent))
        return 0
    if args.command == "purge":
        return cmd_purge(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
