"""Scoring: the order of the list is the product, so its shape is worth pinning."""
from __future__ import annotations

from app.domain.enums import EmploymentType, HiringRegion
from app.services.normalization.rules import normalize
from app.services.scoring.scorer import MatchScorer, _brand_name


def score(raw_job):
    return MatchScorer().score(normalize(raw_job))


def test_internship_in_india_beats_full_time(raw):
    intern = score(raw(title="Software Engineer Intern"))
    full_time = score(raw(title="Software Engineer", employment_type_hint=EmploymentType.FULL_TIME,
                          raw_payload={"min_yoe_hint": 0}))
    assert intern.total > full_time.total
    assert "internship" in intern.reasons


def test_unreachable_role_scores_zero_on_reachability(raw):
    abroad = score(raw(location_raw="San Francisco, CA", hiring_regions_hint=[HiringRegion.US_ONLY]))
    assert abroad.parts["reachability"] == 0.0


def test_remote_worldwide_outranks_onsite_india(raw):
    worldwide = score(raw(title="Software Engineer Intern", location_raw="Remote",
                          hiring_regions_hint=[HiringRegion.WORLDWIDE]))
    india = score(raw(title="Software Engineer Intern"))
    assert worldwide.parts["reachability"] > india.parts["reachability"]


def test_known_company_and_vc_backing_count(raw):
    google = score(raw(company="Google"))
    unknown = score(raw(company="Some Tiny Agency"))
    vc_backed = score(raw(company="Some Tiny Agency", raw_payload={"vc": "Peak XV Partners"}))
    assert google.parts["company"] > vc_backed.parts["company"] > unknown.parts["company"]
    assert "well-known company" in google.reasons
    assert any("Peak XV" in r for r in vc_backed.reasons)


def test_matching_skills_raise_the_score(raw):
    match = score(raw(description="We use Python, React, FastAPI and MongoDB"))
    no_match = score(raw(description="We use COBOL on mainframes"))
    assert match.parts["stack"] > no_match.parts["stack"]


def test_brand_name_does_not_match_a_different_company():
    # "Confluent Solutions" (a small agency) must not pass as Confluent
    assert _brand_name("Confluent Solutions") == "confluent solutions"
    assert _brand_name("Electronic Arts Inc. (EA)") == "electronic arts"
    assert _brand_name("JPMorgan Chase & Co.") == "jpmorgan chase"
