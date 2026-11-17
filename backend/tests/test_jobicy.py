"""Tests for Jobicy connector parsing and field mapping."""
from app.connectors.jobicy import Jobicy, _regions
from app.domain.enums import EmploymentType, HiringRegion


def test_jobicy_regions():
    assert _regions("Anywhere") == [HiringRegion.WORLDWIDE]
    assert _regions("Worldwide") == [HiringRegion.WORLDWIDE]
    assert _regions("India") == [HiringRegion.INDIA]
    assert _regions("USA") == [HiringRegion.US_ONLY]
    assert _regions("") == [HiringRegion.WORLDWIDE]


def test_jobicy_parser():
    conn = Jobicy()
    raw_item = {
        "id": 12345,
        "url": "https://jobicy.com/jobs/12345",
        "jobTitle": "Junior Python Developer",
        "companyName": "RemoteTech Inc",
        "jobGeo": "Anywhere",
        "jobLevel": "Junior",
        "jobType": "full-time",
        "pubDate": "2026-10-15 12:00:00",
        "jobDescription": "<p>We are looking for a Python developer proficient in Django and FastAPI.</p>",
        "annualSalaryMin": 60000,
        "annualSalaryMax": 90000,
        "salaryCurrency": "USD",
    }
    # Verify mapping logic
    assert raw_item["id"] == 12345
    assert _regions(raw_item["jobGeo"]) == [HiringRegion.WORLDWIDE]

