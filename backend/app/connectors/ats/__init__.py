"""Per-company ATS board readers.

These are the highest-quality sources in the project: structured locations,
real posting dates, and direct apply links straight from the employer's own
board - no aggregator in between.
"""
from app.connectors.ats.ashby import Ashby
from app.connectors.ats.base import ATSConnector, BoardResult, BoardStatus
from app.connectors.ats.greenhouse import Greenhouse
from app.connectors.ats.lever import Lever
from app.connectors.ats.freshteam import Freshteam
from app.connectors.ats.gem import GemBoards
from app.connectors.ats.keka import Keka
from app.connectors.ats.recruitee import Recruitee
from app.connectors.ats.rippling import RipplingBoards
from app.connectors.ats.smartrecruiters import SmartRecruiters
from app.connectors.ats.workable import WorkableBoards

ATS_CONNECTORS = [
    Greenhouse, Lever, Ashby, SmartRecruiters,
    WorkableBoards, Freshteam, Keka, Recruitee, GemBoards, RipplingBoards,
]

__all__ = [
    "ATSConnector", "BoardResult", "BoardStatus",
    "Greenhouse", "Lever", "Ashby", "SmartRecruiters", "WorkableBoards", "Freshteam",
    "Keka", "Recruitee", "GemBoards", "RipplingBoards", "ATS_CONNECTORS",
]
