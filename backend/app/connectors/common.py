"""Small parsing helpers shared by the career-site connectors."""
from __future__ import annotations

import html as _html
import re
from datetime import datetime, timedelta, timezone

from selectolax.parser import HTMLParser

from app.domain.enums import HiringRegion

# Internship-type titles, for connectors whose source has no employment field.
INTERN_TITLE = re.compile(
    r"\b(intern|internship|trainee|apprentice|summer\s+analyst|co-?op)\b", re.I
)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

_INDIA = re.compile(
    r"\b(india|ind|bengaluru|bangalore|hyderabad|pune|chennai|mumbai|new\s+delhi|"
    r"delhi|gurgaon|gurugram|noida|kolkata|ahmedabad|coimbatore|kochi|jaipur)\b",
    re.I,
)
_WORLDWIDE = re.compile(r"\b(worldwide|anywhere|global)\b", re.I)
# Every US state + DC. "City, IN" is Indiana, not India: no location string
# writes "Mumbai, IN" - Indian locations say "India" (or ISO-3 "IND"). The
# earlier partial list missed IN and tagged "Indianapolis, IN" as unknown.
_US_STATES = (
    "al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|ms|"
    "mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|"
    "wi|wy|dc"
)
_US = re.compile(
    rf"\b(united\s+states|usa|u\.s\.|[a-z .'-]+,\s*(?:{_US_STATES}))\b"
    r"|\b(new\s+york|nyc|sf|san\s+francisco|seattle|austin|boston|chicago|"
    r"new\s+mexico|california|texas)\b",
    re.I,
)
_EUROPE = re.compile(
    r"\b(united\s+kingdom|uk|london|ireland|dublin|germany|berlin|munich|france|"
    r"paris|netherlands|amsterdam|spain|madrid|barcelona|poland|warsaw|portugal|"
    r"lisbon|sweden|stockholm|switzerland|zurich|italy|milan|romania|bucharest|"
    r"czech|prague|europe)\b",
    re.I,
)
_APAC = re.compile(r"\b(singapore|japan|tokyo|australia|sydney|melbourne|korea|seoul|china|shanghai|taiwan|vietnam|philippines|malaysia|indonesia)\b", re.I)
_CANADA = re.compile(r"\b(canada|toronto|vancouver|montreal|ottawa|waterloo)\b", re.I)
_LATAM = re.compile(r"\b(brazil|(?<!new\s)mexico|argentina|colombia|chile|latam)\b", re.I)


def html_to_text(value: str | None) -> str | None:
    """Readable plain text from HTML or HTML-escaped markup."""
    if not value:
        return None
    text = HTMLParser(_html.unescape(value)).text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def regions_from_text(text: str | None) -> list[HiringRegion]:
    """Map a free-text location to hiring regions. India first, since that
    is the question this whole project is organised around."""
    t = text or ""
    hits: list[HiringRegion] = []
    for pattern, region in (
        (_INDIA, HiringRegion.INDIA),
        (_WORLDWIDE, HiringRegion.WORLDWIDE),
        (_US, HiringRegion.US_ONLY),
        (_EUROPE, HiringRegion.EUROPE),
        (_APAC, HiringRegion.APAC),
        (_CANADA, HiringRegion.CANADA),
        (_LATAM, HiringRegion.LATAM),
    ):
        if pattern.search(t) and region not in hits:
            hits.append(region)
    return hits or [HiringRegion.UNKNOWN]


_POSTED_DAYS = re.compile(r"posted\s+(\d+)\+?\s+days?\s+ago", re.I)


def parse_relative_posted(text: str | None, now: datetime | None = None) -> datetime | None:
    """Workday-style "Posted Today" / "Posted Yesterday" / "Posted 14 Days Ago".

    "Posted 30+ Days Ago" is deliberately returned as None: it only says
    "at least 30", so turning it into a date would invent precision.
    """
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    t = text.strip().lower()
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    if "+" in t:
        return None
    if m := _POSTED_DAYS.search(t):
        return now - timedelta(days=int(m.group(1)))
    return None


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
