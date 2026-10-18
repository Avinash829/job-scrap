"""Diagnose a connector that has gone quiet, and propose the fix as a PR.

Thirty-two connectors read undocumented endpoints. They don't fail loudly:
a site changes a field name and the connector returns an empty list, which
looks exactly like "this company isn't hiring". `BoardStatus` catches that per
board, and `ConnectorRun.ok` catches it per source - but somebody still has to
work out what changed.

Every connector is checked, not only the ones that already failed, because by
the time a source reports zero it has been quiet for a while. Each check is
three questions:

  1. does a small live fetch still return jobs?              (broken if not)
  2. did the last REAL run collapse against this connector's own median?
     (degraded - the silent rot a pass/fail test misses)
  3. for board-based sources, are its boards still answering? (degraded if a
     third of them fail)

The trend question deliberately uses real runs on both sides. Comparing the
live sample - three boards, one search term - against a full run made every
healthy connector look degraded: Oracle sampled 58 jobs against a 427-job
history and was "broken" only in the arithmetic.

Anything not healthy is then diagnosed: the connector's own code, the error,
and what the endpoint returns right now go to the model, which answers with a
cause and a minimal patch. It never merges anything - a model's guess about a
parser belongs in a diff you read.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.connectors import ALL_CONNECTORS
from app.core.config import get_settings
from app.db.repository import JobRepository
from app.db.session import session_scope
from app.services.enrichment.provider import GeminiProvider, LLMUnavailable

log = logging.getLogger(__name__)

REPORT_DIR = Path(__file__).resolve().parents[2] / "docs" / "doctor"
SAMPLE_CHARS = 3_000
CODE_CHARS = 9_000

DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "cause": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence": {"type": "string"},
        "patch": {"type": "string"},
        "verify": {"type": "string"},
    },
    "required": ["cause", "confidence", "evidence"],
}


# A source whose live sample collapsed against its own median is rotting, even
# though it still "works". 0.4 catches a real break without firing on the
# normal week-to-week swing of a job board.
DEGRADED_RATIO = 0.4
BOARD_FAILURE_RATIO = 0.34
SMOKE_BOARDS = 3
SMOKE_CONCURRENCY = 6


@dataclass
class Health:
    """One connector's live check, plus the trend of its real runs."""

    connector: str
    jobs: int = 0                    # from the live sample
    last_run: int | None = None      # jobs_found on the most recent real run
    median_run: int | None = None    # median of the runs before that
    elapsed: float = 0.0
    error: str | None = None
    boards_failed: int = 0
    boards_checked: int = 0

    @property
    def collapsed(self) -> bool:
        """The last real run found far less than this connector normally does."""
        if self.last_run is None or not self.median_run:
            return False
        return self.last_run < max(1, self.median_run * DEGRADED_RATIO)

    @property
    def severity(self) -> str:
        if self.error or self.jobs == 0:
            return "broken"
        if self.collapsed:
            return "degraded"
        if self.boards_checked and self.boards_failed / self.boards_checked >= BOARD_FAILURE_RATIO:
            return "degraded"
        return "ok"

    @property
    def summary(self) -> str:
        if self.error:
            return self.error[:160]
        bits = [f"sample {self.jobs} in {self.elapsed:.0f}s"]
        if self.last_run is not None and self.median_run:
            bits.append(f"last run {self.last_run} vs median {self.median_run}")
        if self.boards_checked:
            bits.append(f"{self.boards_checked - self.boards_failed}/{self.boards_checked} boards answered")
        return " · ".join(bits)


@dataclass
class Diagnosis:
    connector: str
    error: str | None = None
    sample_url: str | None = None
    sample_status: int | None = None
    sample_head: str = ""
    file: str = ""
    cause: str = ""
    confidence: str = ""
    evidence: str = ""
    patch: str = ""
    verify: str = ""
    notes: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        parts = [
            f"# Connector doctor: `{self.connector}`",
            f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}_",
            "",
            f"**Symptom:** {self.error or 'returned zero jobs'}",
            "",
            f"**Likely cause ({self.confidence or 'unknown'} confidence):** {self.cause or 'not determined'}",
            "",
            f"**Evidence:** {self.evidence or '-'}",
        ]
        if self.sample_url:
            parts += ["", "## What the endpoint returns now",
                      f"`{self.sample_url}` -> HTTP {self.sample_status}", "", "```", self.sample_head[:1500], "```"]
        if self.patch:
            parts += ["", "## Suggested patch", "", "```diff", self.patch.strip(), "```"]
        if self.verify:
            parts += ["", f"**How to verify:** {self.verify}"]
        parts += ["", "---", "", "Reviewed by a human before merging - this is a model's reading of a page, not a test result."]
        return "\n".join(parts)


async def smoke_one(cls, trend: tuple[int | None, int | None]) -> Health:
    """Run a small, real fetch through the connector's own parser."""
    from app.connectors.ats.base import BoardStatus

    health = Health(cls.name, last_run=trend[0], median_run=trend[1])
    started = time.monotonic()
    conn = cls()
    try:
        jobs = await asyncio.wait_for(conn.smoke(SMOKE_BOARDS), timeout=180)
        health.jobs = len(jobs)
        results = getattr(conn, "board_results", None) or []
        if results:
            health.boards_checked = len(results)
            health.boards_failed = sum(
                1 for r in results if r.status in (BoardStatus.NO_BOARD, BoardStatus.FETCH_FAILED)
            )
            # a sampled board being genuinely empty is not a failure, but every
            # sampled board being empty is worth a look
            if health.jobs == 0 and health.boards_failed == 0:
                health.error = "every sampled board answered with zero jobs"
    except asyncio.TimeoutError:
        health.error = "timed out after 180s"
    except Exception as exc:  # noqa: BLE001 - the failure IS the finding
        health.error = f"{type(exc).__name__}: {exc}"
    finally:
        await conn.aclose()
        health.elapsed = time.monotonic() - started
    return health


def _trends() -> dict[str, tuple[int | None, int | None]]:
    """(last real run, median of the runs before it) per connector."""
    with session_scope() as s:
        history = JobRepository(s).run_history()
    out: dict[str, tuple[int | None, int | None]] = {}
    for connector, counts in history.items():     # newest first
        if not counts:
            continue
        earlier = sorted(c for c in counts[1:] if c > 0)
        median = earlier[len(earlier) // 2] if len(earlier) >= 3 else None
        out[connector] = (counts[0], median)
    return out


async def check_all(only: str | None = None) -> list[Health]:
    """Smoke-test every connector (or one), concurrently but politely."""
    trends = _trends()
    classes = [c for c in ALL_CONNECTORS if only is None or c.name == only]
    sem = asyncio.Semaphore(SMOKE_CONCURRENCY)

    async def guarded(cls):
        async with sem:
            log.info("doctor: checking %s", cls.name)
            return await smoke_one(cls, trends.get(cls.name, (None, None)))

    results = await asyncio.gather(*(guarded(c) for c in classes))
    return sorted(results, key=lambda h: (h.severity != "broken", h.severity != "degraded", h.connector))


def unhealthy() -> list[tuple[str, str | None]]:
    """(connector, error) for sources whose last run failed or found nothing."""
    with session_scope() as s:
        health = JobRepository(s).connector_health()
    out = []
    for row in health:
        if not row.get("ok"):
            out.append((row["connector"], row.get("error")))
    return out


def _module_urls(cls) -> list[str]:
    """Endpoint constants declared in the connector's module, for a live sample."""
    module = inspect.getmodule(cls)
    urls = []
    for name, value in vars(module).items():
        if isinstance(value, str) and value.startswith("http") and name.isupper():
            urls.append(value)
    return urls


async def _sample(cls) -> tuple[str | None, int | None, str]:
    """Fetch one representative endpoint, exactly as the connector would."""
    from app.core.companies import companies_for

    conn = cls()
    url = None
    if hasattr(conn, "board_url"):
        companies = companies_for(getattr(conn, "platform", ""), None)
        if companies:
            url = conn.board_url(companies[0].slug)
    if url is None:
        for candidate in _module_urls(cls):
            if "{" not in candidate:
                url = candidate
                break
    if url is None:
        return None, None, ""

    try:
        text = await conn.get_text(url)
        return url, 200, text[:SAMPLE_CHARS]
    except Exception as exc:  # noqa: BLE001 - the failure IS the evidence
        status = getattr(exc, "status_code", None)
        return url, status, f"{type(exc).__name__}: {exc}"
    finally:
        await conn.aclose()


def _relative_path(cls) -> str:
    """The connector module's path relative to backend/, as a patch must name it."""
    module = inspect.getmodule(cls)
    path = Path(inspect.getfile(module)).resolve()
    root = Path(__file__).resolve().parents[2]
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def normalize_patch(patch: str, file_path: str) -> str:
    """Point the diff at the real file.

    The model names the file differently every time ("backend/greenhouse.py",
    "a/app/connectors/ats/greenhouse.py"), and `git apply` refuses what it
    cannot resolve. The hunks are the part worth keeping, so the headers are
    rewritten to the path we know.
    """
    out = []
    for line in patch.strip().splitlines():
        if line.startswith("--- "):
            out.append("--- a/" + file_path)
        elif line.startswith("+++ "):
            out.append("+++ b/" + file_path)
        else:
            out.append(line)
    if not any(line.startswith("--- ") for line in out):
        out = ["--- a/" + file_path, "+++ b/" + file_path, *out]
    return "\n".join(out) + "\n"


def _source_code(cls) -> str:
    try:
        return inspect.getsource(inspect.getmodule(cls))[:CODE_CHARS]
    except OSError:
        return ""


PROMPT = """You are debugging a job-board scraper. One connector stopped returning jobs.

Connector: {name}
Symptom: {symptom}

Its source file is `{file}` (path relative to the `backend/` directory).

Its source code:
```python
{code}
```

What its endpoint returns right now ({url} -> HTTP {status}):
```
{sample}
```

Work out why the connector returns nothing. Typical causes: a renamed JSON
field, a changed CSS selector, a new required parameter or header, pagination
that now caps differently, an endpoint that moved, or anti-bot blocking.

Answer as JSON:
- cause: one sentence, specific ("`jobPostings` is now nested under `data`").
- confidence: high only if the sample proves it.
- evidence: the exact key, selector or status you are reasoning from.
- patch: a minimal unified diff against the file shown, or "" if the sample
  does not show enough. Never restructure the connector. Use exactly these
  headers, naming no other path:
      --- a/{file}
      +++ b/{file}
- verify: the one command or check that would confirm the fix.
"""


async def diagnose(connector_name: str, error: str | None = None) -> Diagnosis:
    cls = next((c for c in ALL_CONNECTORS if c.name == connector_name), None)
    if cls is None:
        return Diagnosis(connector_name, error=f"unknown connector {connector_name}")

    result = Diagnosis(connector_name, error=error)
    result.sample_url, result.sample_status, result.sample_head = await _sample(cls)

    if not get_settings().llm_enabled:
        result.notes.append("no GEMINI_API_KEYS set - evidence collected, no diagnosis")
        return result

    result.file = _relative_path(cls)
    prompt = PROMPT.format(
        name=connector_name,
        file=result.file,
        symptom=error or "returned zero jobs",
        code=_source_code(cls),
        url=result.sample_url or "n/a",
        status=result.sample_status if result.sample_status is not None else "n/a",
        sample=result.sample_head or "(no sample)",
    )
    try:
        answer = await GeminiProvider().generate_json(prompt, DIAGNOSIS_SCHEMA) or {}
    except LLMUnavailable as exc:
        result.notes.append(f"LLM unavailable: {exc}")
        return result

    result.cause = str(answer.get("cause") or "")
    result.confidence = str(answer.get("confidence") or "")
    result.evidence = str(answer.get("evidence") or "")
    result.patch = str(answer.get("patch") or "")
    result.verify = str(answer.get("verify") or "")
    return result


def summary_markdown(checks: list[Health], diagnoses: list[Diagnosis]) -> str:
    counts = {level: sum(1 for h in checks if h.severity == level) for level in ("ok", "degraded", "broken")}
    lines = [
        "# Connector health",
        f"_Checked {len(checks)} connectors, {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"**{counts['ok']} ok · {counts['degraded']} degraded · {counts['broken']} broken**",
        "",
        "| connector | state | live check |",
        "|---|---|---|",
    ]
    mark = {"ok": "ok", "degraded": "degraded", "broken": "**broken**"}
    for h in checks:
        lines.append(f"| `{h.connector}` | {mark[h.severity]} | {h.summary} |")
    if diagnoses:
        lines += ["", "## Diagnoses", ""]
        for d in diagnoses:
            lines.append(f"- **{d.connector}**: {d.cause or d.error or 'no diagnosis'}"
                         f"{' (patch suggested)' if d.patch else ''}")
    return "\n".join(lines) + "\n"


async def run_doctor(connector: str | None = None, write: bool = True,
                     diagnose_all: bool = False) -> tuple[list[Health], list[Diagnosis]]:
    """Check every connector live, then diagnose the ones that aren't healthy."""
    checks = await check_all(connector)
    targets = [h for h in checks if diagnose_all or h.severity != "ok"]

    diagnoses = []
    for health in targets:
        log.info("doctor: diagnosing %s (%s)", health.connector, health.severity)
        diagnoses.append(await diagnose(health.connector, health.error or f"{health.severity}: {health.summary}"))

    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (REPORT_DIR / f"health-{stamp}.md").write_text(summary_markdown(checks, diagnoses), encoding="utf-8")
        for d in diagnoses:
            (REPORT_DIR / f"{d.connector}-{stamp}.md").write_text(d.to_markdown(), encoding="utf-8")
        log.info("doctor: reports in %s", REPORT_DIR)
    return checks, diagnoses


def apply_patch(diagnosis: Diagnosis) -> tuple[bool, str]:
    """Try the suggested diff, keeping it only if it applies AND tests pass.

    A patch that needs force, or that breaks a test, is reported instead -
    the point of the PR is a diff a human can read, not a green build.
    """
    import subprocess

    if not diagnosis.patch.strip():
        return False, "no patch suggested"
    root = Path(__file__).resolve().parents[2]
    patch_file = root / ".import_cache" / f"doctor-{diagnosis.connector}.diff"
    patch_file.parent.mkdir(exist_ok=True)
    patch_file.write_text(normalize_patch(diagnosis.patch, diagnosis.file), encoding="utf-8")

    # the diff is written against the connector path as the model saw it
    # (app/connectors/...), which is relative to backend/, not the repo root
    # --recount and -C1 tolerate line numbers and context a model gets slightly
    # wrong; neither changes WHICH lines are replaced.
    apply_cmd = ["git", "apply", "--recount", "-C1", str(patch_file)]
    check = subprocess.run([*apply_cmd, "--check"], cwd=root, capture_output=True, text=True)
    if check.returncode != 0:
        return False, f"patch does not apply: {check.stderr.strip()[:200]}"
    subprocess.run(apply_cmd, cwd=root, check=True)

    # sys.executable, not "python": the venv interpreter is the one with pytest
    tests = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=root,
                           capture_output=True, text=True)
    if tests.returncode != 0:
        # revert ONLY the patched file. `git checkout -- .` here once reverted
        # every uncommitted change in backend/ - an afternoon of work - because
        # it takes the whole tree, not the patch.
        subprocess.run(["git", "checkout", "--", diagnosis.file], cwd=root)
        tail = (tests.stdout or tests.stderr or "no output").strip().splitlines()
        return False, f"tests failed after patch: {tail[-1][:160] if tail else 'no output'}"
    return True, "patch applied and tests pass"
