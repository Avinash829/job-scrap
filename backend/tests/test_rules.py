"""The role, experience and eligibility gates: where a quiet mistake hides good jobs."""
from __future__ import annotations

import pytest

from app.services.normalization.rules import (
    classify_role,
    extract_grad_years,
    extract_pay,
    extract_yoe,
    is_unpaid,
    needs_advanced_degree,
    normalize,
    passes_filters,
    requires_us_eligibility,
)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Software Engineer Intern", "swe"),
        ("SDE Intern", "swe"),
        ("Application Engineering Intern, Summer 2027", "swe"),
        ("Founding Engineer (Golang/ReactJS)", "swe"),
        ("Engineering Trainee (Work with founders)", "swe"),
        ("Full Stack Developer Internship", "fullstack"),
        ("Frontend Engineer - Internship", "frontend"),
        ("Backend Engineer Intern", "backend"),
        ("AI/ML Engineer Internship", "ml"),
        ("Vision Engineer Intern (AI & Computer Vision)", "ml"),
        ("Data Analyst Internship", "data"),
        # out of scope: neither software nor early-career software work
        ("Market Development Internship", "other"),
        ("Medical Intern - Health & Technology", "other"),
        ("Sourcing Engineering Intern", "other"),
        ("IT Intern - Gadget & Tech Support Operations", "other"),
        ("Mechanical Engineer Trainee", "other"),
        ("DevOps Engineer", "other"),
        ("Sales Engineer", "other"),
        ("Recruiter", "other"),
    ],
)
def test_role_classification(title, expected):
    assert classify_role(title).value == expected


@pytest.mark.parametrize(
    "title,description,min_yoe",
    [
        ("Software Engineer Intern", None, 0),
        ("Data Scientist I", "mid_senior_level", 0),          # title beats source metadata
        ("Senior Software Engineer", "2+ years", 3),          # rank word beats the number
        ("Principal Software Engineer 1", None, 3),
        ("Vice President - Data Science", None, 3),
        ("Software Engineer", "5+ years of experience", 5),
        ("Software Engineer", "We need 2 years of relevant experience", 2),
        ("Software Engineer", None, None),                    # says nothing
    ],
)
def test_experience_extraction(title, description, min_yoe):
    assert extract_yoe(title, description)[0] == min_yoe


@pytest.mark.parametrize(
    "text,years",
    [
        ("Open to the 2027 batch only", [2027]),
        ("2026/2027 batch", [2026, 2027]),
        ("Class of 2027", [2027]),
        ("graduating in 2028", [2028]),
        ("passing out in 2026 or 2027", [2026, 2027]),
        ("Founded in 2012, we now serve millions", []),       # not an eligibility year
        ("no year here", []),
    ],
)
def test_grad_years(text, years):
    assert extract_grad_years(text) == years


@pytest.mark.parametrize(
    "title,description,advanced",
    [
        ("Software Engineering PhD Intern, Summer 2027", None, True),
        ("Research Intern", "Currently pursuing a PhD in Computer Science", True),
        ("ML Intern", "Master's degree required", True),
        # a bachelor's path exists -> applicable
        ("ML Intern", "BS/MS in Computer Science or related field", False),
        ("SDE Intern", "Bachelor's or Master's degree in Computer Science", False),
        ("SDE Intern", "Pursuing a B.Tech; M.Tech candidates also welcome", False),
        ("SDE Intern", None, False),
    ],
)
def test_advanced_degree_detection(title, description, advanced):
    assert needs_advanced_degree(title, description) is advanced


@pytest.mark.parametrize(
    "text,us_only",
    [
        ("Must be enrolled at a US university", True),
        ("US citizenship required", True),
        ("Candidates must be authorized to work in the United States", True),
        ("Open to students in India", False),
    ],
)
def test_us_eligibility_detection(text, us_only):
    assert requires_us_eligibility(text) is us_only


@pytest.mark.parametrize(
    "text,expected_min,label",
    [
        ("Stipend: ₹25,000/month", 25000, "₹25,000/month"),
        ("A monthly stipend of Rs 20000 per month", 20000, "₹20000/month"),
        ("Compensation: 12 LPA", 1200000, "₹12 LPA"),
        ("CTC 8-12 LPA", 800000, "₹8-12 LPA"),
        ("Salary $8,000 per month", 8000, "$8,000/month"),
        ("We serve 25,000 users every month", None, None),     # not compensation
        ("No numbers at all", None, None),
    ],
)
def test_pay_extraction(text, expected_min, label):
    lo, _hi, _cur, got_label = extract_pay(text)
    assert lo == expected_min
    assert got_label == label


def test_unpaid_detection():
    assert is_unpaid("This is an unpaid internship") is True
    assert is_unpaid("Stipend: none") is True
    assert is_unpaid("Stipend: ₹20,000/month") is False


# ------------------------------------------------------------------ the gate

def test_filters_keep_a_matching_internship(raw):
    job = normalize(raw(description="Python, React. Open to final-year students."))
    keep, reason = passes_filters(job)
    assert keep, reason


@pytest.mark.parametrize(
    "kwargs,reason_fragment",
    [
        ({"title": "Software Engineering PhD Intern"}, "PhD"),
        ({"description": "Must be enrolled at a US university", "location_raw": "Remote"}, "US work eligibility"),
        ({"description": "This is an unpaid internship"}, "unpaid"),
        ({"description": "Only 2025 and 2026 batch students may apply"}, "class of"),
        ({"title": "Senior Software Engineer"}, "yoe"),
        ({"title": "Account Executive"}, "role not in scope"),
    ],
)
def test_filters_drop_ineligible(raw, kwargs, reason_fragment):
    if "location_raw" in kwargs:
        kwargs.setdefault("hiring_regions_hint", [])
    keep, reason = passes_filters(normalize(raw(**kwargs)))
    assert not keep
    assert reason_fragment.lower() in reason.lower()


def test_pay_and_grad_year_reach_the_job(raw):
    job = normalize(raw(description="Stipend ₹40,000/month. Open to the 2027 batch."))
    assert job.salary_min == 40000
    assert job.salary_currency == "INR"
    assert job.raw_payload["pay"] == "₹40,000/month"
    assert job.grad_year == 2027


def test_aggregator_companies_are_dropped(raw):
    """Their listings are second-hand: middleman apply links, often filled.

    Enforced on the company name, not the board, because such a company also
    turns up on VC portfolio boards and can be re-added by a coverage import.
    """
    keep, reason = passes_filters(normalize(raw(company="Jobgether")))
    assert not keep and "aggregator" in reason
    keep, _ = passes_filters(normalize(raw(company="Randstad Digital")))
    assert not keep
    # a real employer with a similar-looking name is unaffected
    keep, reason = passes_filters(normalize(raw(company="Staffbase")))
    assert keep, reason
