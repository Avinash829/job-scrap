"""LLM extraction of the fields rules can't resolve.

Quality rules baked in here, because they're what make results trustworthy:

  1. The model extracts, it never guesses. "unknown" is an allowed and
     expected answer; a confident wrong region is worse than no region.
  2. Batched (N postings per call) and capped, so a run can't burn quota.
  3. Cached by description_hash, so a posting is paid for exactly once ever.
  4. Descriptions are truncated around the parts that carry the signal -
     requirements and eligibility - not blindly head-truncated.
"""
from __future__ import annotations

import logging
import re

from app.core.config import get_settings
from app.domain.entities import Extraction, Job
from app.domain.enums import (
    Confidence,
    EmploymentType,
    ExtractionSource,
    HiringRegion,
    RoleCategory,
)
from app.services.enrichment.provider import LLMProvider, LLMUnavailable

log = logging.getLogger(__name__)

# --- response schema (Gemini structured output) ---------------------------

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "idx": {"type": "integer"},
        "min_yoe": {"type": "integer", "nullable": True},
        "max_yoe": {"type": "integer", "nullable": True},
        "is_new_grad": {"type": "boolean"},
        "grad_year": {"type": "integer", "nullable": True},
        "hiring_regions": {
            "type": "array",
            "items": {"type": "string", "enum": [r.value for r in HiringRegion]},
        },
        "work_auth_required": {"type": "boolean", "nullable": True},
        "visa_sponsorship": {"type": "boolean", "nullable": True},
        "employment_type": {
            "type": "string",
            "enum": [e.value for e in EmploymentType],
        },
        "role_category": {"type": "string", "enum": [r.value for r in RoleCategory]},
        "confidence": {"type": "string", "enum": [c.value for c in Confidence]},
    },
    "required": ["idx", "hiring_regions", "is_new_grad", "confidence"],
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": _ITEM_SCHEMA}},
    "required": ["results"],
}

SYSTEM_RULES = """\
You extract structured facts from job postings. You are an extractor, not a guesser.

CRITICAL RULES
1. If a posting does not state something, return null (or "unknown" for regions).
   Never infer. A wrong answer is far worse than "unknown".
2. hiring_regions = WHERE THE COMPANY CAN LEGALLY HIRE, not where the office is.
   - "Remote" with no qualifier, and no other clue -> ["unknown"]
   - "Remote (US)", "Remote - United States", "US-based only" -> ["us"]
   - "Remote worldwide/anywhere/global", "hire anywhere" -> ["worldwide"]
   - "Remote (EMEA)" -> ["emea"];  "Remote India"/"Bangalore" -> ["india"]
   - An office location alone (e.g. "San Francisco, CA") is NOT a hiring
     region unless the posting says remote is allowed. Return ["us"] only if
     it is clear that US work authorization is needed.
3. work_auth_required = true only if the posting says the candidate must
   already be authorized to work in a specific country, or says it cannot
   sponsor visas. If it offers sponsorship/relocation, set
   visa_sponsorship = true and work_auth_required = false.
4. min_yoe: the MINIMUM years of professional experience required.
   - "0-2 years", "2 years preferred but not required" -> 0
   - "3+ years", "at least 3 years" -> 3
   - internship / new grad / fresher / entry level -> 0 and is_new_grad = true
   - Ignore years attached to a specific tool when overall experience is
     stated separately. If truly unstated -> null.
5. grad_year: only if the posting names a graduation year or class year.
6. confidence: "high" if stated explicitly, "medium" if strongly implied,
   "low" if you are unsure. Be honest - low confidence results are filtered.

Return one object per posting, echoing its idx. Return every idx given.
"""

# Sections that actually carry eligibility signal.
_SIGNAL_HEADINGS = re.compile(
    r"(requirement|qualification|who\s+you\s+are|what\s+we.?re\s+looking|"
    r"about\s+you|eligib|experience|must\s+have|minimum|basic\s+qualif|"
    r"location|remote|visa|sponsor|authoriz|work\s+permit|time\s?zone)",
    re.I,
)


def condense(description: str | None, budget: int = 1400) -> str:
    """Keep the parts of a description that carry eligibility signal.

    Head-truncating a posting usually keeps the company blurb and throws away
    the requirements - exactly backwards. So score paragraphs and keep the
    ones that mention requirements, location, or visa terms.
    """
    if not description:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", description.strip())
    if len(text) <= budget:
        return text

    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    scored = [(0 if _SIGNAL_HEADINGS.search(p) else 1, i, p) for i, p in enumerate(paras)]
    scored.sort(key=lambda t: (t[0], t[1]))

    out: list[tuple[int, str]] = []
    used = 0
    for _, i, para in scored:
        chunk = para[:600]
        if used + len(chunk) > budget:
            continue
        out.append((i, chunk))
        used += len(chunk)
        if used >= budget:
            break

    out.sort(key=lambda t: t[0])  # restore document order for readability
    return "\n\n".join(p for _, p in out) or text[:budget]


def _to_enum(cls, value, default):
    try:
        return cls(value)
    except (ValueError, TypeError):
        return default


def _allowed_role_categories() -> frozenset[RoleCategory]:
    """Role categories the user's config actually asks for.

    The LLM must not relabel a role into a category that was deliberately
    removed: "Software Engineer - Platform [IC3]" came back as `devops` and a
    Mindtickle infra role as `sre`, both of which the config had commented
    out. Anything outside this set is discarded, leaving the rules' answer.
    """
    from app.core.config import get_profile

    out = set()
    for name in (get_profile().roles.get("include") or {}):
        try:
            out.add(RoleCategory(name))
        except ValueError:
            continue
    return frozenset(out)


ALLOWED_ROLES = _allowed_role_categories()


def _clamp_role(role: RoleCategory) -> RoleCategory:
    return role if role in ALLOWED_ROLES else RoleCategory.OTHER


# --- verification ----------------------------------------------------------
#
# Measured behaviour, not speculation: given `location_raw: REMOTE` with no
# qualifier, both gemini-2.5-flash and gemini-3.5-flash-lite answered
# "worldwide" with HIGH confidence, despite the prompt forbidding exactly that.
# Prompt rules are not enforcement, so worldwide claims are checked against the
# source text before being accepted.

_WORLDWIDE_EVIDENCE = re.compile(
    r"(worldwide|world\s?wide|anywhere\s+in\s+the\s+world|from\s+anywhere|"
    r"work\s+from\s+anywhere|any\s+(country|timezone|time\s?zone)|globally|"
    r"global(ly)?\s+(remote|distributed|team)|remote\s*[,(-]?\s*(global|anywhere)|"
    r"no\s+location\s+requirement|fully\s+distributed|all\s+time\s?zones)",
    re.I,
)
_REGION_EVIDENCE = {
    HiringRegion.INDIA: re.compile(r"\b(india|bangalore|bengaluru|hyderabad|pune|chennai|mumbai|delhi|gurgaon|noida)\b", re.I),
    HiringRegion.US_ONLY: re.compile(r"\b(united\s+states|u\.?s\.?a?\b|us[\s-]based|us\s+only)\b", re.I),
    HiringRegion.EUROPE: re.compile(r"\b(europe|european|eu\b|uk\b|united\s+kingdom|germany|netherlands|france|spain|poland|portugal)\b", re.I),
    HiringRegion.EMEA: re.compile(r"\bemea\b", re.I),
    HiringRegion.APAC: re.compile(r"\b(apac|asia|singapore|japan|australia)\b", re.I),
    HiringRegion.CANADA: re.compile(r"\bcanada|canadian\b", re.I),
    HiringRegion.LATAM: re.compile(r"\b(latam|latin\s+america|brazil|mexico|argentina)\b", re.I),
}


def verify_regions(
    regions: list[HiringRegion], job_text: str
) -> tuple[list[HiringRegion], str | None]:
    """Reject region claims the source text doesn't support.

    Returns (regions, note). Rules, in order:
      1. `unknown` never coexists with a real region - that's incoherent.
      2. `worldwide` requires explicit worldwide wording. A bare "Remote" is
         not evidence, and this is the costliest false positive we can make.
      3. Any other specific region needs a matching token somewhere in the text.
    """
    real = [r for r in regions if r is not HiringRegion.UNKNOWN]
    if not real:
        return [HiringRegion.UNKNOWN], None

    notes: list[str] = []
    kept: list[HiringRegion] = []

    for region in real:
        if region is HiringRegion.WORLDWIDE:
            if _WORLDWIDE_EVIDENCE.search(job_text):
                kept.append(region)
            else:
                notes.append("rejected worldwide: no supporting wording")
            continue

        pattern = _REGION_EVIDENCE.get(region)
        if pattern is None or pattern.search(job_text):
            kept.append(region)
        else:
            notes.append(f"rejected {region.value}: no supporting wording")

    if not kept:
        return [HiringRegion.UNKNOWN], "; ".join(notes) or None
    return kept, "; ".join(notes) or None


class LLMExtractor:
    """Batches postings into structured-output calls."""

    def __init__(self, provider: LLMProvider, batch_size: int | None = None) -> None:
        s = get_settings()
        self.provider = provider
        self.batch_size = batch_size or s.llm_batch_size
        self.max_batches = s.llm_max_batches_per_run

    def _prompt(self, jobs: list[Job]) -> str:
        blocks = []
        for i, job in enumerate(jobs):
            blocks.append(
                f"### idx: {i}\n"
                f"TITLE: {job.title}\n"
                f"COMPANY: {job.company}\n"
                f"LOCATION_FIELD: {job.location_raw or '(none)'}\n"
                f"SOURCE: {job.source}\n"
                f"DESCRIPTION:\n{condense(job.description)}\n"
            )
        return f"{SYSTEM_RULES}\n\nPOSTINGS ({len(jobs)}):\n\n" + "\n".join(blocks)

    async def extract_batch(self, jobs: list[Job]) -> dict[int, Extraction]:
        if not jobs:
            return {}
        payload = await self.provider.generate_json(self._prompt(jobs), RESPONSE_SCHEMA)
        results: dict[int, Extraction] = {}

        for item in (payload or {}).get("results", []):
            idx = item.get("idx")
            if not isinstance(idx, int) or not 0 <= idx < len(jobs):
                continue

            job = jobs[idx]
            regions = [
                _to_enum(HiringRegion, r, HiringRegion.UNKNOWN)
                for r in (item.get("hiring_regions") or [])
            ]
            regions = list(dict.fromkeys(regions)) or [HiringRegion.UNKNOWN]
            conf = _to_enum(Confidence, item.get("confidence"), Confidence.LOW)

            # A low-confidence region is noise; drop it rather than store a
            # guess the UI would present as fact.
            if conf is Confidence.LOW:
                regions = [HiringRegion.UNKNOWN]

            # Verify against the source text. Self-reported confidence is not
            # trustworthy on its own - gemini-3.5-flash-lite reports "high"
            # almost uniformly - so evidence does the real gatekeeping.
            evidence_text = " ".join(
                filter(None, [job.title, job.location_raw, job.description])
            )
            regions, note = verify_regions(regions, evidence_text)
            if note and regions == [HiringRegion.UNKNOWN]:
                conf = Confidence.LOW

            min_yoe = item.get("min_yoe")
            results[idx] = Extraction(
                min_yoe=min_yoe if isinstance(min_yoe, int) else None,
                max_yoe=item.get("max_yoe") if isinstance(item.get("max_yoe"), int) else None,
                is_new_grad=bool(item.get("is_new_grad")),
                grad_year=item.get("grad_year") if isinstance(item.get("grad_year"), int) else None,
                hiring_regions=regions,
                work_auth_required=item.get("work_auth_required"),
                visa_sponsorship=item.get("visa_sponsorship"),
                employment_type=_to_enum(
                    EmploymentType, item.get("employment_type"), EmploymentType.UNKNOWN
                ),
                role_category=_clamp_role(
                    _to_enum(
                        RoleCategory, item.get("role_category"), RoleCategory.OTHER
                    )
                ),
                source=ExtractionSource.LLM,
                confidence=conf,
                notes=note,
            )
        return results

    async def extract(self, jobs: list[Job]) -> dict[str, Extraction]:
        """Extract for many jobs, keyed by description_hash.

        One failing batch must not lose the others, so failures are logged and
        skipped - those rows keep needs_enrichment=True and are retried next run.
        """
        out: dict[str, Extraction] = {}
        batches = [
            jobs[i : i + self.batch_size] for i in range(0, len(jobs), self.batch_size)
        ]
        if len(batches) > self.max_batches:
            log.warning(
                "capping enrichment at %d batches (%d pending); rest retried next run",
                self.max_batches,
                len(batches),
            )
            batches = batches[: self.max_batches]

        for n, batch in enumerate(batches, 1):
            try:
                for idx, ex in (await self.extract_batch(batch)).items():
                    out[batch[idx].description_hash] = ex
            except LLMUnavailable as exc:
                log.warning("enrichment stopped at batch %d/%d: %s", n, len(batches), exc)
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("batch %d failed (%s); continuing", n, exc)
                continue
        return out
