"""Closed-posting detection: the difference between a live job and a dead page."""
from __future__ import annotations

from app.domain.entities import Job
from app.services.normalization.link_check import redirected_away
from app.services.normalization.liveness import workday_api_url


def test_redirect_to_a_listing_page_means_closed():
    # verified 2026-09-18: a removed Greenhouse job lands on the careers search
    assert redirected_away("https://job-boards.greenhouse.io/stripe/jobs/1111111",
                           "https://stripe.com/careers/search") is True
    assert redirected_away("https://www.instahyre.com/job-425496-sde-at-acme/",
                           "https://www.instahyre.com/search-jobs/") is True


def test_routine_redirects_are_not_closed():
    # host change, https upgrade and trailing slashes keep the job's identity
    assert redirected_away("https://boards.greenhouse.io/acme/jobs/123456",
                           "https://job-boards.greenhouse.io/acme/jobs/123456") is False
    assert redirected_away("http://x.test/careers/job/software-engineer-intern",
                           "https://x.test/careers/job/software-engineer-intern") is False
    assert redirected_away("https://jobs.lever.co/acme/abc-123",
                           "https://jobs.lever.co/acme/abc-123/") is False


def _job(**kw) -> Job:
    base = dict(source="workday", source_job_id="marvell:marvellcareers2:/job/Hyderabad/Intern_R1",
                url="https://marvell.wd1.myworkdayjobs.com/marvellcareers2/job/Hyderabad/Intern_R1",
                description_hash="h", title="Intern", title_normalized="intern",
                company="Marvell", company_normalized="marvell")
    base.update(kw)
    return Job(**base)


def test_workday_api_url_is_rebuilt_from_the_stored_id():
    assert workday_api_url(_job()) == (
        "https://marvell.wd1.myworkdayjobs.com/wday/cxs/marvell/marvellcareers2/job/Hyderabad/Intern_R1"
    )


def test_workday_api_url_declines_other_sources():
    assert workday_api_url(_job(source_job_id="no-colons", url="https://example.test/x")) is None
