"""Tests for We Work Remotely connector parsing and field mapping."""
from app.connectors.weworkremotely import WeWorkRemotely, _regions
from app.domain.enums import HiringRegion


def test_wwr_regions():
    assert _regions("Anywhere in the World") == [HiringRegion.WORLDWIDE]
    assert _regions("Worldwide") == [HiringRegion.WORLDWIDE]
    assert _regions("India") == [HiringRegion.INDIA]
    assert _regions("USA") == [HiringRegion.US_ONLY]
    assert _regions("") == [HiringRegion.WORLDWIDE]


def test_wwr_title_company_split():
    raw_title = "Canonical: Software Engineer (Global)"
    company, sep, title = raw_title.partition(":")
    assert company.strip() == "Canonical"
    assert title.strip() == "Software Engineer (Global)"

