"""Match scoring - what makes the list *yours* rather than a job dump.

Deliberately transparent: a weighted sum of explainable components, each
capped, returning both a score and the reasons. A black-box score you can't
interrogate is useless for deciding what to apply to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.config import SearchProfile, get_profile
from app.domain.entities import Job
from app.domain.enums import Confidence, EmploymentType, HiringRegion

# component -> max contribution. Reachability dominates because an
# unreachable role is worth zero regardless of how good the match looks.
WEIGHTS = {
    "reachability": 35.0,
    "experience": 25.0,
    "stack": 20.0,
    "freshness": 12.0,
    "role": 5.0,
    "confidence": 3.0,
}


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
        self.priority = list(p.location.get("priority") or [])

    def score(self, job: Job, now: datetime | None = None) -> ScoreBreakdown:
        now = now or datetime.now(timezone.utc)
        b = ScoreBreakdown()

        b.parts["reachability"] = self._reachability(job, b)
        b.parts["experience"] = self._experience(job, b)
        b.parts["stack"] = self._stack(job, b)
        b.parts["freshness"] = self._freshness(job, now, b)
        b.parts["role"] = WEIGHTS["role"] if job.role_category.value != "other" else 0.0
        b.parts["confidence"] = (
            WEIGHTS["confidence"] if job.region_confidence is Confidence.HIGH else 0.0
        )

        b.total = round(sum(b.parts.values()), 2)
        return b

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

    def _experience(self, job: Job, b: ScoreBreakdown) -> float:
        w = WEIGHTS["experience"]
        if job.employment_type is EmploymentType.INTERNSHIP and "internship" in self.seeking:
            b.reasons.append("internship")
            return w
        if job.is_new_grad:
            b.reasons.append("new-grad friendly")
            return w
        if job.min_yoe == 0:
            return w * 0.85
        if job.min_yoe is None:
            return w * 0.4
        return max(0.0, w * (1 - job.min_yoe / 3))

    def _stack(self, job: Job, b: ScoreBreakdown) -> float:
        w = WEIGHTS["stack"]
        if not self.strong and not self.learning:
            # profile not filled in yet - stay neutral rather than zeroing
            # every job and making the ranking meaningless
            return w * 0.5
        stack = {t.lower() for t in job.tech_stack}
        if not stack:
            return w * 0.3

        strong_hits = stack & self.strong
        learn_hits = stack & self.learning
        if strong_hits:
            b.reasons.append(f"matches {', '.join(sorted(strong_hits)[:3])}")

        denom = max(1, min(len(self.strong), 4))
        strong_part = w * 0.8 * min(1.0, len(strong_hits) / denom)
        learn_part = w * 0.2 * min(1.0, len(learn_hits) / 2)
        return strong_part + learn_part

    def _freshness(self, job: Job, now: datetime, b: ScoreBreakdown) -> float:
        """First 24h is the real edge - a hot remote req drowns after that."""
        w = WEIGHTS["freshness"]
        seen = job.first_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        hours = max(0.0, (now - seen).total_seconds() / 3600)

        if hours <= 24:
            b.reasons.append("posted today")
            return w
        if hours <= 72:
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
