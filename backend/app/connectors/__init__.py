"""Connector registry. Add a source here and the runner picks it up."""
from app.connectors.amazon import AmazonJobs
from app.connectors.apple import AppleJobs
from app.connectors.atlassian import AtlassianCareers
from app.connectors.eightfold import Eightfold
from app.connectors.goldman import GoldmanSachs
from app.connectors.ibm import IBMCareers
from app.connectors.microsoft import MicrosoftCareers
from app.connectors.arbeitnow import Arbeitnow
from app.connectors.ats import (
    Ashby, Freshteam, GemBoards, Greenhouse, Keka, Lever, Recruitee, RipplingBoards, SmartRecruiters,
    WorkableBoards,
)
from app.connectors.avature import Avature
from app.connectors.base import Connector
from app.connectors.google import GoogleCareers
from app.connectors.juspay import Juspay
from app.connectors.oracle_hcm import OracleHCM
from app.connectors.search_base import SearchConnector
from app.connectors.simplify import SimplifyJobs
from app.connectors.successfactors import SuccessFactors
from app.connectors.workable_search import WorkableSearch
from app.connectors.workday import Workday
from app.connectors.himalayas import Himalayas
from app.connectors.hn_hiring import HNWhoIsHiring
from app.connectors.remoteok import RemoteOK
from app.connectors.remotive import Remotive

ALL_CONNECTORS: list[type[Connector]] = [
    # Tier 1 - employer boards. Structured locations, real posting dates,
    # direct apply links. This is where the good data comes from.
    Greenhouse,
    Lever,
    Ashby,
    SmartRecruiters,
    WorkableBoards,
    Freshteam,
    Keka,
    Recruitee,
    GemBoards,
    RipplingBoards,
    # Tier 1 - enterprise career sites that must be searched, not dumped.
    # These cover the MNCs (Google, Amazon, Microsoft, Apple, Nvidia, EA...) whose
    # India internships never appear on Greenhouse/Lever.
    Workday,
    GoogleCareers,
    AmazonJobs,
    Avature,
    OracleHCM,
    SuccessFactors,
    Eightfold,
    MicrosoftCareers,
    AppleJobs,
    AtlassianCareers,
    GoldmanSachs,
    IBMCareers,
    Juspay,
    # Tier 1 - cross-company searches: one query covers every employer on the
    # platform.
    WorkableSearch,
    # Tier 1 - curated internship / new-grad feed across hundreds of employers.
    SimplifyJobs,
    # Tier 1 - remote-native aggregators that state region honestly.
    Himalayas,
    RemoteOK,
    # Tier 3 - low signal-to-noise, kept for coverage only.
    # Arbeitnow is all-professions and Germany-heavy; HN's thread is monthly,
    # so its posts are already days old by the time we see them.
    HNWhoIsHiring,
    Arbeitnow,
    Remotive,
]


def connectors_for_tier(tier: int | None = None) -> list[Connector]:
    """Connectors at or below `tier`.

    Two different tier concepts meet here and must not be confused:
      * connector tier  - how good the SOURCE is (ATS boards 1, aggregators 3)
      * company tier    - how often a COMPANY is refreshed (companies.yaml)

    `--tier 2` therefore means "sources ranked 1-2, and for ATS connectors the
    companies tiered 1-2 as well", which is what the ATS classes take as a
    constructor argument. Using == here silently returned zero connectors.
    """
    from app.connectors.ats.base import ATSConnector

    out: list[Connector] = []
    for cls in ALL_CONNECTORS:
        if tier is not None and cls.tier > tier:
            continue
        takes_tier = issubclass(cls, (ATSConnector, SearchConnector))
        out.append(cls(tier=tier) if takes_tier else cls())
    return out
