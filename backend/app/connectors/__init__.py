"""Connector registry. Add a source here and the runner picks it up."""
from app.connectors.arbeitnow import Arbeitnow
from app.connectors.ats import Ashby, Greenhouse, Lever, SmartRecruiters
from app.connectors.base import Connector
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
        out.append(cls(tier=tier) if issubclass(cls, ATSConnector) else cls())
    return out
