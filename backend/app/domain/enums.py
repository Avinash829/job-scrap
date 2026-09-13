"""Domain vocabulary. Imported by the ORM, the API schemas and the frontend
contract alike, so these string values are a public interface - renaming one
is a breaking change.
"""
from __future__ import annotations

from enum import Enum


class HiringRegion(str, Enum):
    """Where a company can actually hire, not where the office is.

    The distinction that makes this project worth building: most postings
    tagged "Remote" mean remote-within-one-country. Conflating WORLDWIDE and
    US_ONLY produces a board full of roles an India-based candidate cannot take.
    """

    WORLDWIDE = "worldwide"
    US_ONLY = "us"
    CANADA = "canada"
    EMEA = "emea"
    EUROPE = "europe"
    APAC = "apac"
    INDIA = "india"
    LATAM = "latam"
    UNKNOWN = "unknown"

    @property
    def reachable_from_india(self) -> bool:
        return self in (HiringRegion.WORLDWIDE, HiringRegion.INDIA, HiringRegion.APAC)


class RoleCategory(str, Enum):
    SWE = "swe"
    FRONTEND = "frontend"
    BACKEND = "backend"
    FULLSTACK = "fullstack"
    ML = "ml"
    DATA = "data"
    DEVOPS = "devops"
    SRE = "sre"
    OTHER = "other"


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    INTERNSHIP = "internship"
    CONTRACT = "contract"
    PART_TIME = "part_time"
    UNKNOWN = "unknown"


class ExtractionSource(str, Enum):
    """Provenance of a derived field - lets the UI show confidence and lets us
    re-run only the rows the rules guessed at."""

    TITLE = "title"
    REGEX = "regex"
    SOURCE_METADATA = "source"
    LLM = "llm"
    NONE = "none"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
