"""
SkillMap: turn one raw posting (exactly as an API returned it) into the fields of the
`jobs` table, plus the helpers transform_load.py needs.

    normalize(source, job, company) -> dict
    detect_modality(market, workplace_type, location, title, description) -> str
    clean_html(value) -> str

Sources: greenhouse, lever, ashby (full descriptions) and jooble (snippets).
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ATS_SOURCES = ("greenhouse", "lever", "ashby", "recruitee", "personio")
FULL_TEXT_SOURCES = ATS_SOURCES + ("remotive",)     # description_is_full = TRUE
SNIPPET_SOURCES = ("jooble", "adzuna")

# ---- raw board responses -> list of jobs --------------------------------------------------

def _personio_positions(raw: bytes | str) -> list[dict]:
    """Personio XML feed (<workzag-jobs><position>...) -> list of dicts."""
    root = ET.fromstring(raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8"))
    jobs = []
    for pos in root.findall("position"):
        job = {child.tag: (child.text or "").strip() for child in pos if len(child) == 0}
        job["additionalOffices"] = [o.text.strip() for o in pos.findall("additionalOffices/office") if o.text]
        job["jobDescriptions"] = [
            {"name": (d.findtext("name") or "").strip(), "value": (d.findtext("value") or "").strip()}
            for d in pos.findall("jobDescriptions/jobDescription")
        ]
        jobs.append(job)
    return jobs


def parse_board(source: str, raw: bytes | str) -> list[dict]:
    """The list of jobs in one raw ATS board response, whatever the platform's format."""
    if source == "personio":
        return _personio_positions(raw)
    payload = json.loads(raw)
    if source == "lever":
        return payload
    if source == "recruitee":
        return payload["offers"]
    if source in ("greenhouse", "ashby"):
        return payload["jobs"]
    raise ValueError(f"unknown ATS source: {source}")


# ---- HTML -> text ------------------------------------------------------------------------

_BLOCK_END = re.compile(r"</(?:p|div|li|h[1-6]|ul|ol|tr|section)\s*>|<br\s*/?>", re.I)
_LIST_ITEM = re.compile(r"<li[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def clean_html(value: str | None) -> str:
    """HTML (Greenhouse sends it entity-escaped twice) -> readable plain text with line
    breaks and bullets kept."""
    if not value:
        return ""
    text = html.unescape(html.unescape(value))
    text = _LIST_ITEM.sub("\n• ", text)
    text = _BLOCK_END.sub("\n", text)
    text = html.unescape(_TAG.sub(" ", text))
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---- placeholder postings ----------------------------------------------------------------------

# Paragraphs that are legal/HR boilerplate rather than a description of the job.
_BOILERPLATE = re.compile(
    r"equal (?:opportunity|employment)|\beeo\b|discriminat|protected (?:veteran|class|characteristic)|"
    r"race, colou?r|disabilit|accommodation|e-verify|background check|privacy (?:policy|notice)|"
    r"personal (?:data|information)|\bgdpr\b|\bscam|fraud|phishing|consumer protection|artificial intelligence|"
    r"\bai\b.{0,40}(?:recruit|hiring)|recruit\w* agenc|unsolicited|pay (?:transparency|range)|salary range|"
    r"compensation (?:range|package)|base (?:pay|salary)|\bbenefits?\b|401\(?k|paid time off|\bpto\b|"
    r"health insurance|we will (?:never|not) ask|greenhouse-mail|^https?://\S+$|^if [a-z. ]+ based:",
    re.I)
_PLACEHOLDER_START = re.compile(r"^\W*(?:tbd|tba|to be (?:determined|announced|defined)|coming soon|placeholder|"
                                r"por definir|n/a)\b", re.I)
# Calibrated on the 5,400 ATS postings of 2026-11-01: the two placeholders had 3 and 24
# characters left after removing boilerplate; the shortest real posting had 316.
PLACEHOLDER_MIN_CHARS = 200


def substantive_text(description: str) -> str:
    """The description without boilerplate paragraphs (EEO, privacy, scam warnings, benefits...)."""
    paragraphs = [p.strip() for p in re.split(r"\n+", description or "") if p.strip()]
    return " ".join(p for p in paragraphs if not _BOILERPLATE.search(p))


def is_placeholder(description: str) -> bool:
    """True for postings with no real job description: "TBD", or only legal/HR boilerplate."""
    if _PLACEHOLDER_START.search(description or ""):
        return True
    return len(substantive_text(description)) < PLACEHOLDER_MIN_CHARS


# ---- modality ------------------------------------------------------------------------------

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


_HYBRID = re.compile(r"\b(?:hybrid|hibrido|hibrida|semi[- ]?presencial)\b")
_REMOTE = re.compile(r"\b(?:remote|remoto|remota|teletrabajo|work from home|wfh|home[- ]based)\b")
_ONSITE = re.compile(r"\b(?:on[- ]?site|in[- ]office|in the office|office[- ]based|in[- ]person|presencial|en sitio)\b")

_STRUCTURED = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "inoffice": "onsite"}


def _text_modality(text: str) -> str | None:
    t = _norm(text)
    if _HYBRID.search(t):
        return "hybrid"
    remote, onsite = bool(_REMOTE.search(t)), bool(_ONSITE.search(t))
    if remote and not onsite:
        return "remote"
    if onsite and not remote:
        return "onsite"
    return None


def detect_modality(market: str, workplace_type: str | None, location: str, title: str, description: str) -> str:
    """remote / hybrid / onsite / unspecified.

    US-remote and LATAM-remote are remote by definition. Otherwise: the structured
    workplace field (Lever/Ashby) if present, then the location text, then the title,
    then the description (only if it points one way)."""
    if market in ("US-remote", "LATAM-remote"):
        return "remote"
    if workplace_type:
        structured = _STRUCTURED.get(re.sub(r"[^a-z]", "", workplace_type.lower()))
        if structured:
            return structured
    for text in (location, title, description):
        found = _text_modality(text or "")
        if found:
            return found
    return "unspecified"


# ---- dates and salary -----------------------------------------------------------------------

def _parse_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):                         # Lever: epoch milliseconds
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)                  # Jooble: 7-digit fractions
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# Annualise so salary_min/max are comparable. Hourly is skipped: it would need an
# assumption about hours per year.
_LEVER_INTERVALS = {"per-year-salary": 1, "per-month-salary": 12, "bi-week-salary": 26}
_RECRUITEE_PERIODS = {"year": 1, "yearly": 1, "month": 12, "monthly": 12}
_ASHBY_INTERVALS = {"1 YEAR": 1, "1 MONTH": 12, "2 WEEK": 26}
_GH_PAY_RANGE = re.compile(r'<div class="pay-range">(.*?)</div>', re.S)
_GH_TITLE = re.compile(r'<div class="title">(.*?)</div>', re.S)
_SPAN = re.compile(r"<span[^>]*>(.*?)</span>", re.S)
_CURRENCY = re.compile(r"\b([A-Z]{3})\b")


def _number(text: str) -> float | None:
    digits = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _strip_tracking(url: str | None) -> str | None:
    """Adzuna puts the API app_id in every redirect_url (utm_source=...). Drop the utm_*
    parameters before storing the link: the link still works and no credential reaches
    the database or the public dashboard."""
    if not url:
        return url
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


_MONEY = re.compile(r"\$\s*(\d+(?:[.,]\d+)?)\s*(k)?", re.I)


def _salary_from_text(text: str | None) -> tuple[float | None, float | None, str | None]:
    """Remotive's free-text salary: "$170k - $200k", "$3.5k-$5k/month". Only dollar amounts;
    monthly is annualised; hourly, per-task and OTE (sales commission) are skipped."""
    if not text or re.search(r"hour|/hr|\bhr\b|task|\bote\b|commission", text, re.I):
        return None, None, None
    amounts = []
    for number, k in _MONEY.findall(text):
        value = float(number.replace(",", "."))          # "$35,3k" is 35.3k
        amounts.append(value * 1000 if k else value)
    if not amounts:
        return None, None, None
    factor = 12 if re.search(r"month|/mo\b|mensual", text, re.I) else 1
    return min(amounts) * factor, max(amounts) * factor, "USD"


def _salary(source: str, job: dict) -> tuple[float | None, float | None, str | None]:
    if source == "recruitee":
        sal = job.get("salary") or {}
        factor = _RECRUITEE_PERIODS.get(str(sal.get("period") or "").lower())
        low = _number(str(sal.get("min"))) if sal.get("min") is not None else None
        if factor and low:
            high = _number(str(sal.get("max"))) if sal.get("max") is not None else low
            return low * factor, (high or low) * factor, sal.get("currency")
        return None, None, None
    if source == "remotive":
        return _salary_from_text(job.get("salary"))
    if source == "adzuna":
        # Most Adzuna salaries are Adzuna's own estimates (salary_is_predicted = "1"): skip those.
        if str(job.get("salary_is_predicted")) == "0" and job.get("salary_min"):
            area = ((job.get("location") or {}).get("area") or [""])[0]
            return job["salary_min"], job.get("salary_max") or job["salary_min"], "USD" if area == "US" else None
        return None, None, None
    if source == "lever":
        sr = job.get("salaryRange") or {}
        factor = _LEVER_INTERVALS.get(sr.get("interval"))
        if factor and sr.get("min") is not None:
            return sr["min"] * factor, (sr.get("max") or sr["min"]) * factor, sr.get("currency")
    elif source == "ashby":
        for tier in (job.get("compensation") or {}).get("compensationTiers") or []:
            for comp in tier.get("components") or []:
                factor = _ASHBY_INTERVALS.get(comp.get("interval"))
                if comp.get("compensationType") == "Salary" and factor and comp.get("minValue") is not None:
                    return (comp["minValue"] * factor, (comp.get("maxValue") or comp["minValue"]) * factor,
                            comp.get("currencyCode"))
    elif source == "greenhouse":
        content = html.unescape(job.get("content") or "")
        block = _GH_PAY_RANGE.search(content)
        if block:
            title = _GH_TITLE.search(content[max(0, block.start() - 400):block.start()])
            if not (title and re.search(r"hour", title.group(1), re.I)):
                spans = [html.unescape(s) for s in _SPAN.findall(block.group(1))]
                if len(spans) >= 3:
                    low, high = _number(spans[0]), _number(spans[-1])
                    currency = _CURRENCY.search(spans[-1])
                    if low and high:
                        return low, high, currency.group(1) if currency else ("USD" if "$" in spans[0] else None)
    return None, None, None


# ---- per-source mapping -------------------------------------------------------------------

def _lever_text(job: dict) -> str:
    body = job.get("descriptionPlain") or clean_html(job.get("description"))
    lists = "\n\n".join(f"{item.get('text', '')}\n{clean_html(item.get('content'))}".strip()
                        for item in job.get("lists") or [])
    extra = job.get("additionalPlain") or clean_html(job.get("additional"))
    return "\n\n".join(t.strip() for t in (body, lists, extra) if t and t.strip())


def _recruitee_workplace(job: dict) -> str | None:
    if job.get("hybrid"):
        return "hybrid"
    if job.get("remote") and not job.get("on_site"):
        return "remote"
    if job.get("on_site") and not job.get("remote"):
        return "onsite"
    return None


def _recruitee_locations(job: dict) -> str:
    locs = []
    for loc in job.get("locations") or []:
        parts = (loc.get("city") or loc.get("name") or "", loc.get("country") or "")
        place = ", ".join(x.strip() for x in parts if x and x.strip())
        if place and place not in locs:
            locs.append(place)
    if not locs:
        place = ", ".join(x.strip() for x in (job.get("city") or "", job.get("country") or "") if x and x.strip())
        locs = [place or (job.get("location") or "")]
    return " | ".join(loc for loc in locs if loc)


def _personio_text(job: dict) -> str:
    return "\n\n".join(f"{d['name']}\n{clean_html(d['value'])}".strip() for d in job.get("jobDescriptions") or [])


def _ashby_locations(job: dict) -> str:
    locs = [job.get("location") or ""] + [s.get("location") or "" for s in job.get("secondaryLocations") or []]
    return " | ".join(loc.strip() for loc in locs if loc.strip())


def normalize(source: str, job: dict, company: str) -> dict:
    """Fields for the jobs table (market/role/modality are added by transform_load)."""
    if source == "greenhouse":
        fields = dict(external_id=str(job["id"]), title=job.get("title"),
                      location_raw=(job.get("location") or {}).get("name"),
                      description=clean_html(job.get("content")), url=job.get("absolute_url"),
                      posted_at=_parse_datetime(job.get("first_published") or job.get("updated_at")),
                      workplace_type=None)
    elif source == "lever":
        cats = job.get("categories") or {}
        fields = dict(external_id=str(job["id"]), title=job.get("text"),
                      location_raw=" | ".join(cats.get("allLocations") or [cats.get("location") or ""]),
                      description=_lever_text(job), url=job.get("hostedUrl"),
                      posted_at=_parse_datetime(job.get("createdAt")),
                      workplace_type=job.get("workplaceType"))
    elif source == "ashby":
        fields = dict(external_id=str(job["id"]), title=job.get("title"), location_raw=_ashby_locations(job),
                      description=job.get("descriptionPlain") or clean_html(job.get("descriptionHtml")),
                      url=job.get("jobUrl"), posted_at=_parse_datetime(job.get("publishedAt")),
                      workplace_type=job.get("workplaceType"))
    elif source == "jooble":
        fields = dict(external_id=str(job["id"]), title=job.get("title"), location_raw=job.get("location"),
                      description=clean_html(job.get("snippet")), url=job.get("link"),
                      posted_at=_parse_datetime(job.get("updated")), workplace_type=None)
        company = job.get("company") or company
    elif source == "recruitee":
        fields = dict(external_id=str(job["id"]), title=job.get("title"), location_raw=_recruitee_locations(job),
                      description="\n\n".join(t for t in (clean_html(job.get("description")),
                                                            clean_html(job.get("requirements"))) if t),
                      url=job.get("careers_url"),
                      posted_at=_parse_datetime(str(job.get("published_at") or "").replace(" UTC", "+00:00")),
                      workplace_type=_recruitee_workplace(job))
    elif source == "personio":
        offices = [job.get("office") or ""] + list(job.get("additionalOffices") or [])
        token = job.get("_board_token")
        fields = dict(external_id=str(job["id"]), title=job.get("name"),
                      location_raw=" | ".join(o for o in offices if o),
                      description=_personio_text(job),
                      url=f"https://{token}.jobs.personio.de/job/{job['id']}" if token else None,
                      posted_at=_parse_datetime(job.get("createdAt")), workplace_type=None)
    elif source == "remotive":
        fields = dict(external_id=str(job["id"]), title=job.get("title"),
                      location_raw=job.get("candidate_required_location"),
                      description=clean_html(job.get("description")), url=job.get("url"),
                      posted_at=_parse_datetime(job.get("publication_date")), workplace_type="remote")
        company = job.get("company_name") or company
    elif source == "adzuna":
        fields = dict(external_id=str(job["id"]), title=job.get("title"),
                      location_raw=(job.get("location") or {}).get("display_name"),
                      description=clean_html(job.get("description")), url=_strip_tracking(job.get("redirect_url")),
                      posted_at=_parse_datetime(job.get("created")), workplace_type=None)
        company = (job.get("company") or {}).get("display_name") or company
    else:
        raise ValueError(f"unknown source: {source}")

    salary_min, salary_max, currency = _salary(source, job)
    fields.update(source=source, company=company, salary_min=salary_min, salary_max=salary_max,
                  salary_currency=currency, description_is_full=source in FULL_TEXT_SOURCES,
                  title=(fields["title"] or "").strip(), description=(fields["description"] or "").strip())
    return fields
