"""Match scoring - what makes the list *yours* rather than a job dump.

Deliberately transparent: a weighted sum of explainable components, each
capped, returning both a score and the reasons. A black-box score you can't
interrogate is useless for deciding what to apply to.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.config import SearchProfile, get_profile
from app.domain.entities import Job
from app.domain.enums import Confidence, EmploymentType, HiringRegion

# component -> max contribution. Reachability dominates because an
# unreachable role is worth zero regardless of how good the match looks;
# `stage` is second because an internship you can actually start while still
# studying beats a full-time role you cannot take for six months.
WEIGHTS = {
    "reachability": 28.0,
    "stage": 25.0,        # internship / new-grad fit
    "stack": 18.0,        # overlap with the user's declared skills
    "company": 15.0,      # known MNC / funded startup / YC, and a direct employer link
    "freshness": 9.0,
    "role": 3.0,
    "confidence": 2.0,
}

# Where the posting was read. An employer's own system means a real, open
# requisition with a direct apply link; a marketplace listing can be anyone.
EMPLOYER_SOURCES = frozenset({
    "greenhouse", "lever", "ashby", "smartrecruiters", "workday",
    "google", "amazon", "avature", "juspay", "oracle",
    "microsoft", "apple", "atlassian", "goldman", "ibm", "eightfold",
    "workable_boards", "freshteam", "recruitee", "gem", "rippling", "successfactors", "keka", "zoho_recruit",
})

# Team sizes where an intern gets real ownership and a human reads the CV.
SMALL_TEAM = 50


_LEGAL_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|gmbh|pvt|private|plc|co|company|the|india)\b\.?"
)


def _brand_name(name: str) -> str:
    """Company name for the notable-employer lookup.

    Stricter than dedupe's normalize_company: that one also strips words like
    "solutions" and "technologies", which would let "Confluent Solutions"
    (a small agency) pass as Confluent. Only legal suffixes go here.
    """
    n = re.sub(r"\([^)]*\)", " ", name.lower().replace("&", " "))
    n = _LEGAL_SUFFIX.sub(" ", n)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9. ]", " ", n)).strip(" .")


@dataclass(slots=True)
class ScoreBreakdown:
    total: float = 0.0
    parts: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


class MatchScorer:
    def __init__(self, profile: SearchProfile | None = None) -> None:
        p = profile or get_profile()
        self.strong = {s.lower() for s in (p.stack.get("strong") or [])}
        self.learning = {s.lower() for s in (p.stack.get("learning") or [])}
        self.seeking = set(p.profile.get("seeking") or [])
        self.prefer = str(p.profile.get("prefer") or "internship")
        self.priority = list(p.location.get("priority") or [])
        self.notable = {_brand_name(c) for c in p.notable_companies} - {""}

    def score(self, job: Job, now: datetime | None = None) -> ScoreBreakdown:
        now = now or datetime.now(timezone.utc)
        b = ScoreBreakdown()

        b.parts["reachability"] = self._reachability(job, b)
        b.parts["stage"] = self._stage(job, b)
        b.parts["stack"] = self._stack(job, b)
        b.parts["freshness"] = self._freshness(job, now, b)
        b.parts["company"] = self._company(job, b)
        b.parts["role"] = WEIGHTS["role"] if job.role_category.value != "other" else 0.0
        b.parts["confidence"] = (
            WEIGHTS["confidence"] if job.region_confidence is Confidence.HIGH else 0.0
        )

        b.total = round(sum(b.parts.values()), 2)
        return b

    def _stage(self, job: Job, b: ScoreBreakdown) -> float:
        """How well the role fits someone still six months from graduating.

        An internship is the top of this scale on purpose: it is the thing
        the user can actually start now. A full-time role with no early-career
        signal scores low here even if the stack matches perfectly.
        """
        w = WEIGHTS["stage"]
        preferred = self.prefer

        if job.employment_type is EmploymentType.INTERNSHIP:
            b.reasons.append("internship")
            return w if preferred == "internship" else w * 0.85

        # a 2027 class year is an exact match for this user
        if job.grad_year == 2027:
            b.reasons.append("class of 2027")
            return w * 0.95
        if job.grad_year and job.grad_year != 2027:
            return w * 0.3

        if job.is_new_grad:
            b.reasons.append("new-grad friendly")
            return w * 0.8
        if job.min_yoe == 0:
            b.reasons.append("0 years required")
            return w * 0.6
        if job.min_yoe is None:
            return w * 0.25
        return max(0.0, w * (1 - job.min_yoe / 3) * 0.5)

    def _company(self, job: Job, b: ScoreBreakdown) -> float:
        """Who is hiring, and how trustworthy the listing is.

        Without this, a stack-keyword-dense marketplace listing from an
        unknown three-person firm outranked Google's and EA's India
        internships, whose postings don't enumerate frameworks.

          known MNC / well-funded company (config `notable_companies`)  full
          YC-backed                                                     0.8, +0.2 small team
          posted on the employer's own hiring system                    0.5
          marketplace / aggregator listing                              0.15
        Company metadata rides along in raw_payload, so no extra lookup.
        """
        w = WEIGHTS["company"]
        payload = job.raw_payload or {}
        name = _brand_name(job.company or "")

        if name and name in self.notable:
            b.reasons.append("well-known company")
            return w

        score = 0.5 * w if job.source in EMPLOYER_SOURCES else 0.15 * w
        # Listed on a VC firm's portfolio board (Peak XV, Accel, Sequoia...):
        # a funded startup by construction.
        if payload.get("vc"):
            b.reasons.append(f"backed by {payload['vc']}")
            score = max(score, 0.75 * w)
        if payload.get("yc_batch"):
            b.reasons.append(f"YC {payload['yc_batch']}")
            score = max(score, 0.8 * w)
            team = payload.get("team_size")
            if isinstance(team, int) and 0 < team <= SMALL_TEAM:
                b.reasons.append(f"small team ({team})")
                score += 0.2 * w
        return min(w, score)

    # ----------------------------------------------------------- components

    def _reachability(self, job: Job, b: ScoreBreakdown) -> float:
        """Remote-worldwide first, India second, everything else far behind.

        Sponsorship used to score 0.75 here, which put a Paris hybrid
        internship at the top of the list. It is now worth much less on its
        own: a sponsorship promise is a maybe, while "hires worldwide,
        remote" is a fact - and an onsite role abroad is unreachable whatever
        the posting promises.
        """
        w = WEIGHTS["reachability"]

        if HiringRegion.WORLDWIDE in job.hiring_regions:
            if job.is_remote:
                b.reasons.append("remote, hires worldwide")
                return w
            b.reasons.append("hires worldwide")
            return w * 0.8

        if HiringRegion.INDIA in job.hiring_regions:
            b.reasons.append("remote in India" if job.is_remote else "onsite in India")
            return w * (0.9 if job.is_remote else 0.7)

        # Foreign and onsite: you cannot be there. Sponsorship doesn't change
        # that for an internship, and relocation support is not sponsorship.
        if not job.is_remote:
            b.reasons.append("onsite abroad")
            return 0.0

        if job.visa_sponsorship is True:
            b.reasons.append("remote, sponsors visa")
            return w * 0.45

        if HiringRegion.APAC in job.hiring_regions:
            return w * 0.4

        if job.hiring_regions == [HiringRegion.UNKNOWN]:
            # genuinely uncertain: neither reward nor bury it
            b.reasons.append("region unclear")
            return w * 0.3

        return 0.0

    def _stack(self, job: Job, b: ScoreBreakdown) -> float:
        w = WEIGHTS["stack"]
        if not self.strong and not self.learning:
            # profile not filled in yet - stay neutral rather than zeroing
            # every job and making the ranking meaningless
            return w * 0.5
        stack = {t.lower() for t in job.tech_stack}
        if not stack:
            # Many employer postings name no tools at all (EA's site has no
            # description; Google's are prose). Absence isn't a mismatch, so
            # stay near neutral. Not keyed on the description itself: rescore
            # loads rows without it, and scores must not shift between passes.
            return w * 0.4

        strong_hits = stack & self.strong
        learn_hits = stack & self.learning
        if strong_hits:
            b.reasons.append(f"matches {', '.join(sorted(strong_hits)[:3])}")

        denom = max(1, min(len(self.strong), 4))
        strong_part = w * 0.8 * min(1.0, len(strong_hits) / denom)
        learn_part = w * 0.2 * min(1.0, len(learn_hits) / 2)
        return strong_part + learn_part

    def _freshness(self, job: Job, now: datetime, b: ScoreBreakdown) -> float:
        """First 24h is the real edge - a hot remote req drowns after that.

        Measured from the POSTING date, not from when we first saw the job.
        Using first_seen_at labelled every card "posted today" on a fresh
        database, including roles the employer posted 446 days earlier.
        """
        w = WEIGHTS["freshness"]
        posted = job.posted_at
        if posted is not None and posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)

        # No usable posting date (missing, or implausibly old): we can't claim
        # freshness, so score it low-neutral and say nothing about it.
        if posted is None or job.age_days is None:
            return w * 0.3

        hours = max(0.0, (now - posted).total_seconds() / 3600)
        if hours <= 24:
            b.reasons.append("posted today")
            return w
        if hours <= 72:
            b.reasons.append("posted this week")
            return w * 0.7
        if hours <= 168:
            return w * 0.4
        return w * 0.1

    # -------------------------------------------------------------- bulk API

    def score_all(self, jobs: list[Job]) -> dict[tuple[str, str], float]:
        now = datetime.now(timezone.utc)
        return {
            (j.source, j.source_job_id): self.score(j, now).total for j in jobs
        }

    def score_all_explained(
        self, jobs: list[Job]
    ) -> dict[tuple[str, str], tuple[float, list[str]]]:
        now = datetime.now(timezone.utc)
        out = {}
        for j in jobs:
            b = self.score(j, now)
            out[(j.source, j.source_job_id)] = (b.total, b.reasons[:5])
        return out
