"""Storage lifecycle: what gets written, what gets deleted, and what must not.

A bug here either deletes live jobs or keeps closed ones forever, and neither
is visible by looking at the site for a minute.
"""
from __future__ import annotations

from app.db.repository import JobRepository, fingerprint, job_to_values
from app.domain.enums import EmploymentType, HiringRegion
from app.services.normalization.rules import normalize

SCOPE = "greenhouse:acme"


def make(raw, i, title=None, scope=SCOPE):
    job = normalize(raw(source_job_id=f"j{i}", title=title or f"Software Engineer Intern {i}",
                        url=f"https://example.test/{i}", raw_payload={"scope": scope}))
    job.description = None
    return job


def sync(session, jobs, covered, full=True):
    return JobRepository(session).sync_source("greenhouse", jobs, set(covered), full)


def test_new_jobs_are_inserted_then_left_alone(session, raw):
    jobs = [make(raw, 1), make(raw, 2)]
    assert sync(session, jobs, {SCOPE}) == (2, 0, 0)
    # same jobs again: no writes at all
    assert sync(session, [make(raw, 1), make(raw, 2)], {SCOPE}) == (0, 0, 0)


def test_changed_job_is_updated(session, raw):
    sync(session, [make(raw, 1)], {SCOPE})
    assert sync(session, [make(raw, 1, title="Backend Engineer Intern")], {SCOPE}) == (0, 1, 0)


def test_closed_job_is_deleted_when_its_board_was_checked(session, raw):
    sync(session, [make(raw, 1), make(raw, 2)], {SCOPE})
    assert sync(session, [make(raw, 1)], {SCOPE}) == (0, 0, 1)


def test_job_survives_when_its_board_was_not_checked(session, raw):
    sync(session, [make(raw, 1), make(raw, 2)], {SCOPE})
    # another company was checked this run; ours wasn't
    assert sync(session, [make(raw, 1)], {"greenhouse:other"}) == (0, 0, 0)


def test_failed_run_deletes_nothing(session, raw):
    sync(session, [make(raw, 1), make(raw, 2)], {SCOPE})
    assert sync(session, [], set()) == (0, 0, 0)


def test_search_style_source_needs_two_consecutive_misses(session, raw):
    sync(session, [make(raw, 1), make(raw, 2)], {SCOPE}, full=False)
    assert sync(session, [make(raw, 1)], {SCOPE}, full=False) == (0, 0, 0)   # first miss
    assert sync(session, [make(raw, 1)], {SCOPE}, full=False) == (0, 0, 1)   # second miss


def test_reappearing_job_resets_the_miss_counter(session, raw):
    sync(session, [make(raw, 1), make(raw, 2)], {SCOPE}, full=False)
    sync(session, [make(raw, 1)], {SCOPE}, full=False)                       # miss 1
    sync(session, [make(raw, 1), make(raw, 2)], {SCOPE}, full=False)         # back
    assert sync(session, [make(raw, 1)], {SCOPE}, full=False) == (0, 0, 0)   # miss 1 again


def test_descriptions_are_never_stored(session, raw):
    job = normalize(raw(description="A long description " * 200, raw_payload={"scope": SCOPE}))
    values = job_to_values(job)
    assert "description" not in values
    assert len(values["raw_payload"]) < 200        # payload is whitelisted, not the whole blob


def test_fingerprint_ignores_last_seen_but_not_content():
    base = {"title": "A", "company": "B", "last_seen_at": "t1", "link_status": ""}
    same_job_later = {**base, "last_seen_at": "t2", "link_status": "alive"}
    changed_job = {**base, "title": "C"}
    assert fingerprint(base) == fingerprint(same_job_later)
    assert fingerprint(base) != fingerprint(changed_job)


def test_pay_label_survives_storage(session, raw):
    job = normalize(raw(description="Stipend ₹30,000/month", raw_payload={"scope": SCOPE}))
    job.description = None
    sync(session, [job], {SCOPE})
    stored = list(JobRepository(session).active_jobs_lean())
    assert stored[0].raw_payload["pay"] == "₹30,000/month"
    assert stored[0].salary_min == 30000


def test_dead_apply_link_deletes_the_job(session, raw):
    job = make(raw, 1)
    sync(session, [job], {SCOPE})
    checked, dead = JobRepository(session).record_link_results({job.url: "dead"})
    assert (checked, dead) == (1, 1)
    assert list(JobRepository(session).active_jobs_lean()) == []


def test_employment_type_and_region_round_trip(session, raw):
    job = normalize(raw(employment_type_hint=EmploymentType.INTERNSHIP,
                        hiring_regions_hint=[HiringRegion.INDIA], raw_payload={"scope": SCOPE}))
    job.description = None
    sync(session, [job], {SCOPE})
    stored = list(JobRepository(session).active_jobs_lean())[0]
    assert stored.employment_type is EmploymentType.INTERNSHIP
    assert HiringRegion.INDIA in stored.hiring_regions


def test_jobs_from_a_removed_board_are_deleted(session, raw):
    """A board taken out of companies.yaml is never checked again.

    Its jobs used to stay forever - a removed aggregator board kept showing
    jobs on the site days later.
    """
    sync(session, [make(raw, 1, scope="greenhouse:acme"), make(raw, 2, scope="greenhouse:gone")], {"greenhouse:acme", "greenhouse:gone"})
    repo = JobRepository(session)
    new, updated, deleted = repo.sync_source(
        "greenhouse",
        [make(raw, 1, scope="greenhouse:acme")],
        {"greenhouse:acme"},
        True,
        known_scopes={"greenhouse:acme"},      # "gone" is no longer configured
    )
    assert (new, updated, deleted) == (0, 0, 1)
    assert [j.source_job_id for j in repo.active_jobs_lean()] == ["j1"]


def test_known_scopes_absent_changes_nothing(session, raw):
    """Sources that aren't company-based (aggregators) pass no known scopes."""
    sync(session, [make(raw, 1), make(raw, 2)], {"greenhouse:acme"})
    repo = JobRepository(session)
    assert repo.sync_source("greenhouse", [make(raw, 1), make(raw, 2)], {"greenhouse:acme"}, True, None) == (0, 0, 0)
