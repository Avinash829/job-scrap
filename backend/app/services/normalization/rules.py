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
    "profile": _PROFILE.profile,
}

# Aggregators and staffing agencies, by normalized name (see config.yaml).
_EXCLUDED_COMPANIES = frozenset(
    normalized for normalized in (
        re.sub(r"[^a-z0-9 ]", " ", str(c).lower()).strip() for c in _PROFILE.exclude_companies
    ) if normalized
)

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
# Graduation-year phrasings, including the Indian "2027 batch" form. All
# matches are collected: "2026/2027 batch" and "class of 2026 or 2027" mean
# both years are eligible.
_GRAD_YEARS = re.compile(
    r"(?:\b(?:class\s+of|graduat(?:ing|e|ion)(?:\s+(?:in|by|year))?|passing\s+out(?:\s+in)?|"
    r"batch\s+of)\s*[:\s]*((?:20\d\d)(?:\s*(?:,|/|-|–|or|and|to)\s*20\d\d)*)"
    r"|\b((?:20\d\d)(?:\s*(?:,|/|-|–|or|and|to)\s*20\d\d)*)\s*(?:batch|passouts?|pass\s*outs?|graduates?)\b)",
    re.I,
)
_YEAR = re.compile(r"20\d\d")

# A PhD or Master's stated as the requirement, with no bachelor's alternative,
# is not applicable to a B.Tech student. Google lists "Software Engineering
# PhD Intern" - an internship in name only.
_ADVANCED_DEGREE = re.compile(
    r"\b(ph\.?\s?d|doctoral|doctorate|post[\s-]?doc\w*)\b"
    r"|\b(?:m\.?s\.?|m\.?tech|m\.?e\.?|master(?:'?s)?)\s+(?:degree\s+)?"
    r"(?:is\s+)?(?:required|mandatory|must|only)\b"
    r"|\b(?:currently\s+)?(?:pursuing|enrolled\s+in)\s+(?:an?\s+)?"
    r"(?:m\.?s\.?|m\.?tech|master(?:'?s)?|ph\.?\s?d|doctoral)\b",
    re.I,
)
# ... unless a bachelor's degree is explicitly acceptable ("BS/MS", "Bachelor's
# or Master's", "B.Tech/M.Tech"), which most enterprise postings say.
_BACHELOR_OK = re.compile(
    r"\b(bachelor(?:'?s)?|b\.?\s?tech|b\.?\s?e\.?\b|b\.?\s?s\.?c?\b|bs\b|undergraduate|"
    r"under[\s-]?grad|final[\s-]?year|pre[\s-]?final)\b",
    re.I,
)

# Eligibility fenced to the US: a campus programme for US-enrolled students,
# or a role that requires US work authorization outright.
_US_ONLY_ELIGIBILITY = re.compile(
    r"(enrolled\s+(?:in|at)\s+(?:an?\s+)?(?:accredited\s+)?(?:us|u\.s\.|american)\s+"
    r"(?:university|college|institution)"
    r"|(?:us|u\.s\.)\s+citizen(?:ship)?\s*(?:is\s*)?(?:required|only)"
    r"|must\s+be\s+(?:a\s+)?(?:us|u\.s\.)\s+citizen"
    r"|(?:authorized|authorised|eligible)\s+to\s+work\s+in\s+the\s+(?:us|u\.s\.|united\s+states)"
    r"\s*(?:without\s+sponsorship)?"
    r"|work\s+authorization\s+in\s+the\s+(?:us|united\s+states))",
    re.I,
)

# Pay. Indian postings state a monthly stipend ("₹25,000/month", "Rs 20000 per
# month") or an annual CTC in lakhs ("12 LPA", "₹12,00,000 per annum"); global
# ones state a monthly or annual figure in dollars.
_MONEY = r"(?:₹|rs\.?|inr|usd|\$)\s*([\d][\d,.]{2,12})"
_PAY_MONTHLY = re.compile(_MONEY + r"\s*(?:-|–|to)?\s*(?:(?:₹|rs\.?|inr|usd|\$)?\s*([\d][\d,.]{2,12}))?"
                          r"\s*(?:/|per\s+|a\s+)?\s*(?:month|mo\b|pm\b|monthly)", re.I)
_PAY_LPA = re.compile(r"([\d]{1,3}(?:\.\d{1,2})?)\s*(?:-|–|to)?\s*([\d]{1,3}(?:\.\d{1,2})?)?\s*"
                      r"(?:lpa|lakhs?\s*(?:per\s+annum|p\.?a\.?)?|l\.?p\.?a)", re.I)
_PAY_ANNUAL = re.compile(_MONEY + r"\s*(?:-|–|to)?\s*(?:(?:₹|rs\.?|inr|usd|\$)?\s*([\d][\d,.]{2,12}))?"
                         r"\s*(?:/|per\s+|a\s+)?\s*(?:year|annum|yr\b|pa\b|annually)", re.I)
_STIPEND_NEAR = re.compile(r"stipend|salary|compensation|ctc|pay\b", re.I)
_UNPAID = re.compile(r"\b(unpaid|no\s+stipend|without\s+stipend|stipend\s*[:\-]?\s*(?:none|nil|0|unpaid))\b", re.I)

_SENIORITY_PATTERNS = [
    re.compile(p, re.I) for p in CONFIG["experience"]["exclude_title_patterns"]
]


# Unambiguous seniority words. Checked before any junior/level signal, since
# a numeric suffix never outranks an explicit rank.
_STRONG_SENIORITY = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|director|head\s+of|vp|cto|"
    r"architect|distinguished|fellow|manager|"
    # banking titles: "Vice President - Data Science" slipped through as
    # in-scope because only the abbreviation `vp` was listed
    r"vice\s+president|avp|svp|evp|managing\s+director|executive\s+director)\b",
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


def extract_grad_years(text: str) -> list[int]:
    """Every graduation year the posting names, e.g. [2026, 2027].

    Plausibility bound: a posting can only mean years near the present, and
    stray years ("founded 2012", "2020 batch of funding") must not become an
    eligibility rule.
    """
    now = datetime.now(timezone.utc).year
    years: list[int] = []
    for m in _GRAD_YEARS.finditer(text or ""):
        for raw in _YEAR.findall(m.group(1) or m.group(2) or ""):
            year = int(raw)
            if now - 1 <= year <= now + 6 and year not in years:
                years.append(year)
    return years


def needs_advanced_degree(title: str, description: str | None) -> bool:
    """PhD/Master's demanded with no bachelor's path - not applicable to a B.Tech."""
    if _ADVANCED_DEGREE.search(title):
        return True
    text = description or ""
    if not text:
        return False
    for m in _ADVANCED_DEGREE.finditer(text):
        window = text[max(0, m.start() - 160) : m.end() + 160]
        if not _BACHELOR_OK.search(window):
            return True
    return False


def requires_us_eligibility(text: str) -> bool:
    return bool(_US_ONLY_ELIGIBILITY.search(text or ""))


def _to_number(raw: str) -> int | None:
    """"25,000" -> 25000, "12,00,000" -> 1200000, "8.5" -> 8 (lakh handled by caller)."""
    cleaned = raw.replace(",", "").rstrip(".")
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def extract_pay(text: str | None) -> tuple[int | None, int | None, str | None, str | None]:
    """Return (min, max, currency, human label) for a stated stipend or salary.

    Only read near a pay word, so "25,000 users" or "raised $5M" can't be
    mistaken for compensation. Monthly and annual figures are both kept as
    stated; the label is what the card shows.
    """
    if not text:
        return None, None, None, None
    for pattern, period in ((_PAY_MONTHLY, "month"), (_PAY_LPA, "lpa"), (_PAY_ANNUAL, "year")):
        for m in pattern.finditer(text):
            window = text[max(0, m.start() - 120) : m.end() + 40]
            if not _STIPEND_NEAR.search(window):
                continue
            lo_raw, hi_raw = m.group(1), m.group(2)
            if period == "lpa":
                lo = _to_number(lo_raw)
                hi = _to_number(hi_raw) if hi_raw else None
                if lo is None or not (1 <= lo <= 200):
                    continue
                label = f"₹{lo_raw}{'-' + hi_raw if hi_raw else ''} LPA"
                return lo * 100000, (hi * 100000 if hi else None), "INR", label
            lo, hi = _to_number(lo_raw), (_to_number(hi_raw) if hi_raw else None)
            if lo is None or lo < 1000:
                continue
            dollars = bool(re.search(r"(usd|\$)", m.group(0), re.I))
            unit = "$" if dollars else "₹"
            per = "month" if period == "month" else "year"
            label = f"{unit}{lo_raw}{'-' + hi_raw if hi_raw else ''}/{per}"
            return lo, hi, ("USD" if dollars else "INR"), label
    return None, None, None, None


def is_unpaid(text: str | None) -> bool:
    return bool(_UNPAID.search(text or ""))


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
    r"\b(inc|llc|ltd|limited|corp|corporation|gmbh|pvt|private|technologies|technology"
    r"|labs|software|solutions|services|group|co|company|the|india)\b\.?",
    re.I,
)


def display_company(name: str) -> str:
    """Readable label for slug-shaped names from ATS ids ("merkle-science" -> "Merkle Science").

    Only all-lowercase names are touched; "eBay" or "IBM" stay as the board wrote them.
    """
    n = (name or "").strip()
    if n and n == n.lower() and " " not in n:
        return n.replace("-", " ").replace("_", " ").title()
    return n


def normalize_company(name: str) -> str:
    # "Electronic Arts Inc. (EA)" and "Electronic Arts" are one employer;
    # without this, dedupe keeps a job-board copy next to the employer's own posting.
    n = _COMPANY_NOISE.sub("", re.sub(r"\([^)]*\)", " ", name.lower()))
    return _WS.sub(" ", re.sub(r"[^a-z0-9 ]", " ", n)).strip()


def normalize_title(title: str) -> str:
    t = re.sub(r"[^a-z0-9 +#.]", " ", title.lower())
    return _WS.sub(" ", t).strip()


def _normalize_out_of_scope(raw: RawJob) -> Job:
    """Cheap path for titles the role gate will reject anyway.

    Measured on a real run: 86% of ~33k postings are role `other` (sales,
    recruiting, ops...). The full path runs ~80 skill regexes plus YoE, region,
    work-auth and sponsorship scans over each description - several KB apiece.
    None of that output is ever read for an `other` row: passes_filters()
    rejects it on role before looking at anything else, and storage drops its
    description. So only identity and title-derived fields are computed here.
    """
    return Job(
        source=raw.source,
        source_job_id=raw.source_job_id,
        url=raw.url,
        description_hash=raw.description_hash,
        title=raw.title.strip(),
        title_normalized=normalize_title(raw.title),
        company=display_company(raw.company),
        company_normalized=normalize_company(raw.company),
        description=None,
        location_raw=raw.location_raw,
        ats=raw.raw_payload.get("ats"),
        role_category=RoleCategory.OTHER,
        employment_type=raw.employment_type_hint,
        hiring_regions=[r for r in raw.hiring_regions_hint] or [HiringRegion.UNKNOWN],
        posted_at=raw.posted_at,
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        needs_enrichment=False,
        raw_payload=raw.raw_payload,
    )


def normalize(raw: RawJob) -> Job:
    """Rules-only pass. Flags needs_enrichment when the LLM could add something."""
    # Classify first: it only reads the title, and it decides whether any of
    # the expensive description scans below can possibly matter.
    role = classify_role(raw.title, raw.tags)
    if role is RoleCategory.OTHER:
        return _normalize_out_of_scope(raw)

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

    grad_years = extract_grad_years(text)
    pay_min, pay_max, pay_currency, pay_label = extract_pay(raw.description)
    payload = dict(raw.raw_payload or {})
    if pay_label:
        payload["pay"] = pay_label
    if grad_years:
        payload["grad_years"] = grad_years
    if needs_advanced_degree(raw.title, raw.description):
        payload["advanced_degree"] = True
    if requires_us_eligibility(text):
        payload["us_eligibility"] = True
    if is_unpaid(raw.description):
        payload["unpaid"] = True

    return Job(
        source=raw.source,
        source_job_id=raw.source_job_id,
        url=apply_url,
        description_hash=raw.description_hash,
        title=raw.title.strip(),
        title_normalized=normalize_title(raw.title),
        company=display_company(raw.company),
        company_normalized=normalize_company(raw.company),
        description=raw.description,
        location_raw=raw.location_raw,
        ats=ats,
        role_category=role,
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
        grad_year=(grad_years[0] if grad_years else None),
        yoe_source=yoe_src,
        is_remote=detect_remote(raw.title, raw.location_raw, raw.tags),
        hiring_regions=regions,
        work_auth_required=work_auth,
        visa_sponsorship=sponsors,
        region_source=region_src,
        region_confidence=region_conf,
        salary_min=pay_min,
        salary_max=pay_max,
        salary_currency=pay_currency,
        posted_at=raw.posted_at,
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        needs_enrichment=needs_llm,
        raw_payload=payload,
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
ATS_SOURCES = frozenset({
    "greenhouse", "lever", "ashby", "smartrecruiters",
    "workday", "google", "amazon", "avature", "juspay", "oracle",
    "microsoft", "apple", "atlassian", "goldman", "ibm", "eightfold",
    "workable_boards", "freshteam", "recruitee", "gem", "rippling", "successfactors", "keka", "zoho_recruit",
    # cross-company searches over live postings only
    "workable", "vc_getro", "vc_consider", "instahyre", "yc_jobs",
    # Simplify maintains an `active` flag per listing, so its rows are
    # open-until-closed too; the connector itself drops anything >90 days old.
    "simplify",
})

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

    # --- eligibility -------------------------------------------------------
    # A posting you cannot apply to is worse than no posting: it costs
    # attention and hides something real further down the list.
    if job.company_normalized in _EXCLUDED_COMPANIES:
        return False, "aggregator / staffing agency"

    payload = job.raw_payload or {}
    if exp.get("exclude_advanced_degree", True) and payload.get("advanced_degree"):
        return False, "requires PhD / Master's"
    if exp.get("exclude_us_eligibility", True) and payload.get("us_eligibility") \
            and HiringRegion.INDIA not in job.hiring_regions:
        return False, "requires US work eligibility"
    if exp.get("exclude_unpaid", True) and payload.get("unpaid"):
        return False, "unpaid"

    years = payload.get("grad_years") or []
    graduating = CONFIG["profile"].get("graduating")
    my_year = int(str(graduating)[:4]) if graduating else None
    # A posting that names eligible batches and doesn't name yours is closed
    # to you ("2025 & 2026 batch only"). Naming your year is fine, and naming
    # none - the common case - says nothing either way.
    if my_year and years and my_year not in years:
        return False, f"class of {'/'.join(str(y) for y in years[:2])} only"

    # --- freshness ---------------------------------------------------------
    fresh = CONFIG["freshness"]
    age_days = job.age_days

    # job.age_days hides dates past the plausibility bound (a board once
    # reported 2998 days). For the staleness gate the raw date matters: a
    # "Trainee" posting from 2021 that a board still lists as open is an
    # evergreen ghost req, not a data error, and must not pass as undated.
    if job.posted_at is not None:
        posted = job.posted_at if job.posted_at.tzinfo else job.posted_at.replace(tzinfo=timezone.utc)
        age_days = max(0, (datetime.now(timezone.utc) - posted).days)

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
