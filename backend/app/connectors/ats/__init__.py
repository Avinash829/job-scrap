"""Per-company ATS board readers.

These are the highest-quality sources in the project: structured locations,
real posting dates, and direct apply links straight from the employer's own
board - no aggregator in between.
"""
from app.connectors.ats.ashby import Ashby
from app.connectors.ats.base import ATSConnector, BoardResult, BoardStatus
from app.connectors.ats.greenhouse import Greenhouse
from app.connectors.ats.lever import Lever
from app.connectors.ats.smartrecruiters import SmartRecruiters

ATS_CONNECTORS = [Greenhouse, Lever, Ashby, SmartRecruiters]

__all__ = [
    "ATSConnector", "BoardResult", "BoardStatus",
    "Greenhouse", "Lever", "Ashby", "SmartRecruiters", "ATS_CONNECTORS",
]
