"""Rules-first normalization. No LLM calls here.

Resolves YoE, role category, tech stack and hiring region for the large
majority of postings with regex + dictionaries, leaving only an ambiguous
residue for the cached, batched LLM pass. Every derived field records its
provenance so the UI can show confidence and we can re-run only the guesses.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.core.config import get_profile
from app.domain.entities import Job, RawJob
from app.domain.enums import (
    Confidence,
    EmploymentType,
    ExtractionSource,
    HiringRegion,
    RoleCategory,
)
from app.services.normalization.urls import canonical_apply_url

_PROFILE = get_profile()
CONFIG = {
    "experience": _PROFILE.experience,
    "roles": _PROFILE.roles,
    "location": _PROFILE.location,
    "employment": _PROFILE.employment,
    "freshness": _PROFILE.freshness,
}

# ----------------------------------------------------------------- experience

# "3+ years", "2-4 years", "minimum of 5 years", "at least 2 yrs"
_YOE_RANGE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)",
    re.I,
)
_YOE_MIN = re.compile(
    r"(?:at\s+least|minimum\s+(?:of\s+)?|min\.?\s*|over\s+)?(\d{1,2})\s*\+\s*(?:years?|yrs?)",
    re.I,
)
_YOE_PLAIN = re.compile(
    r"(\d{1,2})\s*(?:years?|yrs?)\s+(?:of\s+)?(?:relevant\s+)?experience", re.I
)

_NEW_GRAD = re.compile(
    r"\b(new\s*grad(uate)?s?|recent\s+graduate|entry[\s-]?level|fresher|"
    r"campus\s+hire|university\s+grad|graduate\s+program(me)?|"
    r"no\s+experience\s+required|0\s*[-–to]*\s*1\s*years?)\b",
    re.I,
)
_INTERN = re.compile(
    r"\b(intern|internship|co-?op|summer\s+20\d\d|trainee|apprentice)\b", re.I
)
_GRAD_YEAR = re.compile(
    r"\b(?:class\s+of|graduating\s+(?:in\s+)?(?:by\s+)?)\s*(20\d\d)\b", re.I
)

_SENIORITY_PATTERNS = [
    re.compile(p, re.I) for p in CONFIG["experience"]["exclude_title_patterns"]
]


# Unambiguous seniority words. Checked before any junior/level signal, since
# a numeric suffix never outranks an explicit rank.
_STRONG_SENIORITY = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|director|head\s+of|vp|cto|"
    r"architect|distinguished|fellow|manager)\b",
    re.I,
)

# Junior signals IN THE TITLE. These outrank both seniority patterns and any
# metadata hint: "Data Scientist I" at Swiggy was being dropped as 3+ YoE
# because SmartRecruiters had tagged it mid_senior_level, even though the
# title says level one. A title is written by the hiring team for candidates;
# the level enum is internal bookkeeping and is often wrong.
_JUNIOR_TITLE = re.compile(
    r"(\b(junior|jr\.?|graduate|grad|campus|university|early\s*career|"
    r"entry[\s-]?level|trainee|apprentice|fresher|rotational)\b"
    r"|\b(associate)\s+(software|data|ai|ml|backend|frontend|full|engineer|developer|scientist|analyst)"
    r"|\b(engineer|developer|scientist|analyst|programmer)\s*(-|–|,)?\s*(i|1)\b"
    r"|\b(i|1)\s*$)",
    re.I,
)


def extract_yoe(
    title: str, description: str | None
) -> tuple[int | None, int | None, ExtractionSource]:
    """Return (min_yoe, max_yoe, provenance).

    None means the posting states nothing - deliberately distinct from 0.

    Precedence, highest first:
      1. intern / new-grad / junior signal in the title  -> 0
      2. seniority signal in the title                   -> 3 (out of scope)
      3. a number mined from the description
    A 'Senior Engineer' whose body mentions '2+ years' is still not entry
    level, but a 'Data Scientist I' is entry level whatever its body says.
    """
    text = f"{title}\n{description or ''}"

    # An explicit seniority WORD outranks everything, including a numeric
    # level. "Principal Software Engineer 1" was being read as entry level
    # because of the trailing 1 - the word "Principal" is the real signal.
    if _STRONG_SENIORITY.search(title):
        return 3, None, ExtractionSource.TITLE

    if _INTERN.search(title) or _NEW_GRAD.search(title) or _JUNIOR_TITLE.search(title):
        return 0, None, ExtractionSource.TITLE

    for pat in _SENIORITY_PATTERNS:
        if pat.search(title):
            return 3, None, ExtractionSource.TITLE

    if m := _YOE_RANGE.search(text):
        lo, hi = int(m.group(1)), int(m.group(2))
        return min(lo, hi), max(lo, hi), ExtractionSource.REGEX

    if m := _YOE_MIN.search(text):
        return int(m.group(1)), None, ExtractionSource.REGEX

    if m := _YOE_PLAIN.search(text):
        return int(m.group(1)), None, ExtractionSource.REGEX

    if _NEW_GRAD.search(text) or _INTERN.search(text):
        return 0, None, ExtractionSource.REGEX

    return None, None, ExtractionSource.NONE


def extract_grad_year(text: str) -> int | None:
    m = _GRAD_YEAR.search(text)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------- roles

_ROLE_KEYWORDS = {
    RoleCategory(cat): [k.lower() for k in kws]
    for cat, kws in CONFIG["roles"]["include"].items()
}
_ROLE_EXCLUDE = [k.lower() for k in CONFIG["roles"]["exclude"]]

# most specific first - "full stack" must beat a bare "backend" mention
_ROLE_ORDER = [
    RoleCategory.FULLSTACK,
    RoleCategory.ML,
    RoleCategory.SRE,
    RoleCategory.DEVOPS,
    RoleCategory.DATA,
    RoleCategory.FRONTEND,
    RoleCategory.BACKEND,
    RoleCategory.SWE,
]


_TITLES_ONLY = bool(CONFIG["roles"].get("match_titles_only", True))


def classify_role(title: str, tags: list[str] | None = None) -> RoleCategory:
    """Classify from the TITLE by default.

    Tag-based fallback used to label "Product Tester for Books and Ebooks" as
    `data` and "IT-Systemadministrator" as `sre`, because their tag soup
    mentioned analytics and systems. A role you would actually apply to says
    so in its title.
    """
    t = title.lower()
    if any(bad in t for bad in _ROLE_EXCLUDE):
        return RoleCategory.OTHER

    for cat in _ROLE_ORDER:
        for kw in _ROLE_KEYWORDS.get(cat, []):
            if kw in t:
                return cat

    if _TITLES_ONLY:
        return RoleCategory.OTHER

    haystack = " ".join(tags or []).lower()
    for cat in _ROLE_ORDER:
        for kw in _ROLE_KEYWORDS.get(cat, []):
            if kw in haystack:
                return cat
    return RoleCategory.OTHER


# ----------------------------------------------------------------- tech stack

_SKILLS = {
    "python", "java", "javascript", "typescript", "go", "golang", "rust", "c++",
    "c#", "ruby", "php", "kotlin", "swift", "scala", "elixir", "sql",
    "react", "next.js", "vue", "angular", "svelte", "tailwind", "redux",
    "node.js", "express", "django", "flask", "fastapi", "spring", "rails",
    "graphql", "rest", "grpc",
    "postgres", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "kafka", "rabbitmq", "dynamodb", "cassandra",
    "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "ansible",
    "jenkins", "github actions", "ci/cd", "linux",
    "pytorch", "tensorflow", "scikit-learn", "pandas", "numpy", "spark",
    "airflow", "dbt", "snowflake", "databricks", "llm", "nlp",
    # the user's stack - these must be detectable or scoring is meaningless
    "langchain", "langgraph", "socket.io", "firebase", "power bi", "postman",
    "git", "github", "prompt engineering", "ai agents", "generative ai",
    "genai", "rag", "vector database", "openai", "huggingface", "mern",
    "streamlit", "jupyter", "matplotlib", "selenium", "supabase",
}
_SKILL_PATTERNS = [
    (skill, re.compile(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])"))
    for skill in sorted(_SKILLS)
]
_ALIASES = {"golang": "go", "postgresql": "postgres"}


def extract_tech_stack(text: str, tags: list[str] | None = None) -> list[str]:
    """Patterns are compiled once at import - this runs on every posting."""
    hay = f"{text} {' '.join(tags or [])}".lower()
    found = {skill for skill, pat in _SKILL_PATTERNS if pat.search(hay)}
    for alias, canonical in _ALIASES.items():
        if alias in found:
            found.discard(alias)
            found.add(canonical)
    return sorted(found)


# -------------------------------------------------------------------- regions

_WORK_AUTH = re.compile(
    r"(must\s+be\s+(legally\s+)?authoriz|authorized\s+to\s+work|"
    r"work\s+authorization|no\s+visa\s+sponsorship|"
    r"cannot\s+sponsor|unable\s+to\s+sponsor|not\s+able\s+to\s+sponsor|"
    r"eligible\s+to\s+work\s+in\s+the\s+(us|united\s+states))",
    re.I,
)
# Must mention visas or work permits explicitly. An earlier version accepted
# "relocation support", which let a Paris-hybrid internship past the Europe
# region block - relocation help is usually for domestic or intra-EU moves and
# says nothing about sponsoring a work permit for an Indian candidate.
_SPONSORS = re.compile(
    r"(visa\s+sponsorship\s+(is\s+)?(available|provided|offered)|"
    r"(we|company)\s+(can\s+|will\s+)?sponsor(s|ship)?\s+(visa|work\s+permit|"
    r"employment\s+visa|h-?1b)|"
    r"sponsor\s+(your\s+)?(visa|work\s+permit)|"
    r"visa\s+(and\s+relocation\s+)?support\s+(is\s+)?(available|provided)|"
    r"we\s+sponsor\s+visas|"
    r"eligible\s+for\s+visa\s+sponsorship|"
    r"work\s+permit\s+sponsorship)",
    re.I,
)
# Weaker signal: nice to surface in the UI, but never treated as sponsorship.
_RELOCATION = re.compile(
    r"relocation\s+(package|assistance|support|benefits?)", re.I
)
_WORLDWIDE = re.compile(
    r"\b(hire\s+(from\s+)?anywhere|work\s+from\s+anywhere|"
    r"fully\s+remote\s+worldwide|remote\s*[,(]?\s*(worldwide|global|anywhere))\b",
    re.I,
)
_US_REMOTE = re.compile(r"\bremote\s*\(?\s*(us|usa|united\s+states)\b", re.I)

# A city name in the location field is strong evidence of where a role sits.
# Without this, "München" / "Bad Homburg" / "Chemnitz" resolved to `unknown`
# and slipped straight past the foreign-onsite filter.
_CITY_REGIONS: tuple[tuple[re.Pattern[str], HiringRegion], ...] = (
    (
        re.compile(
            r"\b(bangalore|bengaluru|hyderabad|pune|chennai|mumbai|new\s+delhi|"
            r"delhi|gurgaon|gurugram|noida|kolkata|ahmedabad|jaipur|kochi|"
            r"coimbatore|indore|india)\b",
            re.I,
        ),
        HiringRegion.INDIA,
    ),
    (
        re.compile(
            r"\b(m(ü|u)nchen|munich|berlin|hamburg|frankfurt|stuttgart|cologne|"
            r"k(ö|o)ln|chemnitz|leipzig|dresden|bad\s+homburg|amsterdam|"
            r"rotterdam|paris|lyon|madrid|barcelona|lisbon|warsaw|krak(ó|o)w|"
            r"prague|vienna|zurich|z(ü|u)rich|geneva|stockholm|copenhagen|oslo|"
            r"helsinki|dublin|brussels|milan|rome|london|manchester|edinburgh|"
            r"germany|deutschland|netherlands|france|spain|poland|portugal|"
            r"belgium|italy|sweden|denmark|norway|finland|ireland|austria|"
            r"switzerland|united\s+kingdom)\b",
            re.I,
        ),
        HiringRegion.EUROPE,
    ),
    (
        re.compile(
            r"\b(san\s+francisco|new\s+york|nyc|seattle|austin|boston|chicago|"
            r"denver|atlanta|los\s+angeles|san\s+diego|portland|palo\s+alto|"
            r"mountain\s+view|irvine|brooklyn|united\s+states|usa)\b",
            re.I,
        ),
        HiringRegion.US_ONLY,
    ),
    (
        re.compile(r"\b(toronto|vancouver|montreal|canada)\b", re.I),
        HiringRegion.CANADA,
    ),
    (
        re.compile(
            r"\b(singapore|tokyo|seoul|sydney|melbourne|hong\s+kong|japan|"
            r"australia)\b",
            re.I,
        ),
        HiringRegion.APAC,
    ),
    (
        re.compile(
            r"\b(dubai|abu\s+dhabi|uae|emirates|riyadh|saudi|qatar|doha)\b", re.I
        ),
        HiringRegion.EMEA,
    ),
)

# A German-language posting body is almost certainly a Germany-based role.
_GERMAN_TEXT = re.compile(
    r"(wir\s+(suchen|sind|bieten)|deine\s+aufgaben|dein\s+profil|unser\s+team|"
    r"das\s+bieten\s+wir|berufserfahrung|abgeschlossenes\s+studium|"
    r"m/w/d|w/m/d|\(m/w)",
    re.I,
)


def regions_from_location(
    location_raw: str | None, description: str | None = None
) -> HiringRegion | None:
    """Infer a region from city names, or from the language of the posting."""
    text = location_raw or ""
    for pattern, region in _CITY_REGIONS:
        if pattern.search(text):
            return region
    if description and _GERMAN_TEXT.search(description):
        return HiringRegion.EUROPE
    return None
_REMOTE = re.compile(r"\b(remote|anywhere|distributed|work\s+from\s+home|wfh)\b", re.I)
_ONSITE = re.compile(r"\b(on-?site|in-?office|hybrid)\b", re.I)


def detect_work_auth(description: str | None) -> bool | None:
    """True = local authorization needed. None = the posting is silent."""
    if not description:
        return None
    if _SPONSORS.search(description):
        return False
    if _WORK_AUTH.search(description):
        return True
    return None


def detect_sponsorship(description: str | None) -> bool | None:
    """True only for explicit visa/work-permit sponsorship.

    Relocation support deliberately does NOT count - see _SPONSORS.
    """
    if not description:
        return None
    if _SPONSORS.search(description):
        return True
    if re.search(r"(no\s+visa\s+sponsorship|cannot\s+sponsor|"
                 r"unable\s+to\s+sponsor)", description, re.I):
        return False
    return None


def detect_relocation(description: str | None) -> bool:
    return bool(description and _RELOCATION.search(description))


def detect_remote(title: str, location_raw: str | None, tags: list[str]) -> bool:
    hay = f"{title} {location_raw or ''} {' '.join(tags)}".lower()
    if _ONSITE.search(hay) and "remote" not in hay:
        return False
    return bool(_REMOTE.search(hay))


def resolve_regions(
    raw: RawJob,
) -> tuple[list[HiringRegion], ExtractionSource, Confidence]:
    """Trust an explicit source hint; else try prose; else admit unknown.

    Admitting unknown is the point - a confident wrong region is worse than
    none, because the UI would present it as fact.
    """
    hint = [r for r in raw.hiring_regions_hint if r is not HiringRegion.UNKNOWN]
    if hint:
        return hint, ExtractionSource.SOURCE_METADATA, Confidence.HIGH

    desc = raw.description or ""
    if _WORLDWIDE.search(desc) or _WORLDWIDE.search(raw.location_raw or ""):
        return [HiringRegion.WORLDWIDE], ExtractionSource.REGEX, Confidence.MEDIUM
    if _US_REMOTE.search(desc) or _US_REMOTE.search(raw.location_raw or ""):
        return [HiringRegion.US_ONLY], ExtractionSource.REGEX, Confidence.MEDIUM

    if geo := regions_from_location(raw.location_raw, desc):
        return [geo], ExtractionSource.REGEX, Confidence.MEDIUM

    return [HiringRegion.UNKNOWN], ExtractionSource.NONE, Confidence.LOW


# ------------------------------------------------------------------ normalize

_WS = re.compile(r"\s+")
_COMPANY_NOISE = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|gmbh|pvt|private|technologies|labs)\b\.?",
    re.I,
)


def normalize_company(name: str) -> str:
    n = _COMPANY_NOISE.sub("", name.lower())
    return _WS.sub(" ", re.sub(r"[^a-z0-9 ]", " ", n)).strip()


def normalize_title(title: str) -> str:
    t = re.sub(r"[^a-z0-9 +#.]", " ", title.lower())
    return _WS.sub(" ", t).strip()


def normalize(raw: RawJob) -> Job:
    """Rules-only pass. Flags needs_enrichment when the LLM could add something."""
    text = f"{raw.title}\n{raw.description or ''}"
    min_yoe, max_yoe, yoe_src = extract_yoe(raw.title, raw.description)

    # SmartRecruiters ships experienceLevel as a real enum ("entry_level",
    # "mid_senior_level", ...). That beats regex-mining prose, so prefer it -
    # but never over an explicit seniority signal in the title, since a
    # "Senior Engineer" tagged entry_level is a tagging mistake, not a fresher role.
    hint = raw.raw_payload.get("min_yoe_hint")
    if isinstance(hint, int) and yoe_src is not ExtractionSource.TITLE:
        min_yoe, max_yoe = hint, None
        yoe_src = ExtractionSource.SOURCE_METADATA

    # Employment type comes from the TITLE (or the source's own field), never
    # from the description. Matching the body tagged Elastic's "Senior
    # Software Engineer" as an internship because its description mentioned
    # mentoring interns, and a Cloudflare audit role via "Internal".
    emp = raw.employment_type_hint
    if emp is EmploymentType.UNKNOWN and _INTERN.search(raw.title):
        emp = EmploymentType.INTERNSHIP

    regions, region_src, region_conf = resolve_regions(raw)
    work_auth = detect_work_auth(raw.description)
    sponsors = detect_sponsorship(raw.description)

    # Prefer the employer's own apply link over an aggregator wrapper, and
    # note which ATS they use so the company can be fetched directly later.
    apply_url, ats = canonical_apply_url(raw.url, raw.description, raw.company)

    needs_llm = (
        regions == [HiringRegion.UNKNOWN] or min_yoe is None or work_auth is None
    )

    return Job(
        source=raw.source,
        source_job_id=raw.source_job_id,
        url=apply_url,
        description_hash=raw.description_hash,
        title=raw.title.strip(),
        title_normalized=normalize_title(raw.title),
        company=raw.company.strip(),
        company_normalized=normalize_company(raw.company),
        description=raw.description,
        location_raw=raw.location_raw,
        ats=ats,
        role_category=classify_role(raw.title, raw.tags),
        employment_type=emp,
        tech_stack=extract_tech_stack(text, raw.tags),
        min_yoe=min_yoe,
        max_yoe=max_yoe,
        # Title first. The description is only trusted for phrases that
        # unambiguously describe the CANDIDATE ("new graduate", "fresher"),
        # never for a bare "intern" that may just be describing the team.
        is_new_grad=bool(
            _NEW_GRAD.search(raw.title)
            or _INTERN.search(raw.title)
            or _NEW_GRAD.search(raw.description or "")
        ),
        grad_year=extract_grad_year(text),
        yoe_source=yoe_src,
        is_remote=detect_remote(raw.title, raw.location_raw, raw.tags),
        hiring_regions=regions,
        work_auth_required=work_auth,
        visa_sponsorship=sponsors,
        region_source=region_src,
        region_confidence=region_conf,
        posted_at=raw.posted_at,
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        needs_enrichment=needs_llm,
        raw_payload=raw.raw_payload,
    )


# --------------------------------------------------------------------- filters

_BLOCKING_REGIONS = frozenset(
    {
        HiringRegion.US_ONLY,
        HiringRegion.CANADA,
        HiringRegion.EMEA,
        HiringRegion.EUROPE,
        HiringRegion.LATAM,
    }
)


# Sources that return ONLY open requisitions, so posting age says nothing
# about whether the role is still available.
ATS_SOURCES = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters"})

_EMPLOYMENT_EXCLUDE = [
    re.compile(p, re.I)
    for p in CONFIG["employment"].get("exclude_title_patterns", [])
]
_EMPLOYMENT_ALLOWED = set(CONFIG["employment"].get("allowed", []))


def passes_filters(job: Job) -> tuple[bool, str]:
    """The config gate. Returns (keep, reason_if_dropped).

    Applied after enrichment, so an `unknown` here means genuinely unknowable
    rather than merely not-yet-processed.
    """
    exp = CONFIG["experience"]
    loc = CONFIG["location"]

    if job.role_category is RoleCategory.OTHER:
        return False, "role not in scope"

    # --- freshness ---------------------------------------------------------
    fresh = CONFIG["freshness"]
    age_days = job.age_days

    # An implausible date is a data error, not a stale job - ignore it rather
    # than drop a live posting because its board reported 2998 days.
    implausible = fresh.get("max_plausible_age_days")
    if age_days is not None and implausible and age_days > implausible:
        age_days = None

    if age_days is not None:
        max_age = (
            fresh.get("ats_max_posting_age_days")
            if job.source in ATS_SOURCES
            else fresh.get("max_posting_age_days")
        )
        if max_age and age_days > max_age:
            return False, f"posted {age_days}d ago"

    # --- employment type ---------------------------------------------------
    haystack = f"{job.title} {job.location_raw or ''}"
    for pat in _EMPLOYMENT_EXCLUDE:
        if pat.search(haystack):
            return False, f"employment type excluded ({pat.pattern.strip(chr(92) + 'b')})"

    if _EMPLOYMENT_ALLOWED and job.employment_type.value not in _EMPLOYMENT_ALLOWED:
        return False, f"employment type {job.employment_type.value}"

    if job.min_yoe is None:
        if not exp["include_unknown_yoe"]:
            return False, "yoe unknown"
    elif job.min_yoe > exp["max_min_yoe"]:
        return False, f"requires {job.min_yoe}+ yoe"

    if loc.get("exclude_us_only_remote"):
        if (
            job.hiring_regions
            and set(job.hiring_regions).issubset(_BLOCKING_REGIONS)
            and job.visa_sponsorship is not True
        ):
            return False, f"region not reachable ({job.hiring_regions[0].value})"

        if (
            job.work_auth_required is True
            and job.visa_sponsorship is not True
            and not job.reachable_from_india
        ):
            return False, "requires local work authorization"

    # --- reachability: the hard gate --------------------------------------
    # You can work remotely from India, or onsite within India. Everything
    # else is out of scope, so this is a whitelist rather than a blacklist of
    # blocking regions - a blacklist kept letting new region values through.
    if loc.get("require_reachable"):
        allowed = {
            HiringRegion(r) for r in loc.get("allowed_regions", ["worldwide", "india"])
        }
        regions = set(job.hiring_regions)
        unknown_only = regions == {HiringRegion.UNKNOWN}

        if unknown_only:
            if not loc.get("allow_unknown_region", True):
                return False, "region unresolved"
            # Unresolved AND onsite is the worst combination: no evidence it's
            # reachable and no possibility of working from India.
            if not job.is_remote:
                return False, "onsite, region unresolved"
        elif not (regions & allowed):
            # No sponsorship escape hatch. It let "REMOTE (US)" through on a
            # visa_sponsorship flag, but a US-only remote role cannot be done
            # from India no matter what it sponsors - and you only want onsite
            # roles inside India. Sponsorship stays a display field and a
            # small scoring nudge, never a way past this gate.
            label = ",".join(
                sorted(r.value for r in regions if r is not HiringRegion.UNKNOWN)
            )
            reason = "onsite" if not job.is_remote else "remote"
            return False, f"{reason} in {label or 'unknown'}, not reachable"

    return True, ""
