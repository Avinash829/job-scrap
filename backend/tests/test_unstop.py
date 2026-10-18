"""Tests for Unstop connector parsing and field mapping."""
from datetime import datetime, timezone

from app.connectors.unstop import Unstop, _format_pay, _parse_iso
from app.domain.enums import EmploymentType, HiringRegion


def test_parse_iso():
    dt = _parse_iso("2026-10-16T23:59:59+05:30")
    assert isinstance(dt, datetime)
    assert dt.tzinfo is timezone.utc
    assert _parse_iso(None) is None
    assert _parse_iso("invalid-date") is None


def test_format_pay():
    assert _format_pay({"min_salary": 8000, "max_salary": 12000, "pay_in": "monthly"}) == "₹8,000-12,000/month"
    assert _format_pay({"min_salary": 15000, "max_salary": 15000, "pay_in": "monthly"}) == "₹15,000/month"
    assert _format_pay({"min_salary": 10000, "max_salary": None, "pay_in": "lump_sum"}) == "₹10,000/lump_sum"
    assert _format_pay({}) is None


def test_unstop_to_raw():
    conn = Unstop()
    sample = {
        "id": 1761488,
        "title": "Software Development Internship",
        "seo_url": "https://unstop.com/internships/software-dev-1761488",
        "organisation": {"name": "Tech Corp"},
        "jobDetail": {
            "locations": ["Bangalore"],
            "type": "in_office",
            "min_salary": 15000,
            "max_salary": 20000,
            "pay_in": "monthly",
            "paid_unpaid": "paid",
        },
        "required_skills": [{"skill_name": "Python"}, {"skill_name": "React"}],
        "details": "<p>Build high scale applications.</p>",
        "end_date": "2026-11-01T23:59:59+05:30",
        "approved_date": "2026-10-01T12:00:00+05:30",
    }
    raw = conn._to_raw(sample)
    assert raw is not None
    assert raw.source == "unstop"
    assert raw.source_job_id == "1761488"
    assert raw.title == "Software Development Internship"
    assert raw.company == "Tech Corp"
    assert raw.employment_type_hint is EmploymentType.INTERNSHIP
    assert HiringRegion.INDIA in raw.hiring_regions_hint
    assert raw.tags == ["Python", "React"]
    assert raw.raw_payload["pay"] == "₹15,000-20,000/month"
    assert raw.raw_payload["unpaid"] is False
    assert raw.raw_payload["min_yoe_hint"] == 0
    assert "Required Skills: Python, React" in raw.description
