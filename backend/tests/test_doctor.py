"""The connector doctor's judgement: when is a source broken, degraded, or fine.

These thresholds decide whether a human gets woken up, so they are worth
pinning. The first version compared a three-board live sample against a
full-run history and called 18 of 36 healthy connectors "degraded".
"""
from __future__ import annotations

from app.pipeline.doctor import Health, normalize_patch


def health(**kw) -> Health:
    base = dict(connector="greenhouse", jobs=50, last_run=400, median_run=380, elapsed=3.0)
    base.update(kw)
    return Health(**base)


def test_a_working_connector_is_ok():
    assert health().severity == "ok"


def test_no_jobs_from_the_live_sample_is_broken():
    assert health(jobs=0).severity == "broken"


def test_an_exception_is_broken():
    assert health(error="FetchError: HTTP 403").severity == "broken"


def test_a_collapsed_run_is_degraded():
    # it still "works", which is exactly why this check exists
    assert health(last_run=40, median_run=400).severity == "degraded"


def test_a_normal_swing_is_not_degraded():
    assert health(last_run=300, median_run=400).severity == "ok"
    assert health(last_run=900, median_run=400).severity == "ok"


def test_a_small_live_sample_never_counts_as_collapse():
    """The sample reads three boards; the trend compares real runs only."""
    assert health(jobs=9, last_run=760, median_run=762).severity == "ok"


def test_mostly_failing_boards_is_degraded():
    assert health(boards_checked=3, boards_failed=2).severity == "degraded"
    assert health(boards_checked=3, boards_failed=1).severity == "ok"


def test_no_history_means_no_trend_judgement():
    assert health(last_run=5, median_run=None).severity == "ok"


def test_patch_headers_are_rewritten_to_the_real_file():
    patch = "--- a/backend/greenhouse.py\n+++ b/backend/greenhouse.py\n@@ -1 +1 @@\n-a\n+b"
    fixed = normalize_patch(patch, "app/connectors/ats/greenhouse.py")
    assert fixed.startswith("--- a/app/connectors/ats/greenhouse.py\n+++ b/app/connectors/ats/greenhouse.py")
    assert "-a\n+b" in fixed


def test_patch_without_headers_gets_them():
    fixed = normalize_patch("@@ -1 +1 @@\n-a\n+b", "app/connectors/x.py")
    assert fixed.splitlines()[0] == "--- a/app/connectors/x.py"
