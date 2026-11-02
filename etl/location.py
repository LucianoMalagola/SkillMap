"""
SkillMap: classify a job posting's location into a market.

    classify_posting(source, job, description=None) -> (market, reason)
        A raw posting exactly as the API returned it (greenhouse/lever/ashby/jooble).
        Prefers structured fields: Lever workplaceType + country, Ashby workplaceType +
        addressCountry per location. Free text is parsed only where those are missing.

    classify_location(location, title, description, workplace_type=..., is_remote=..., country_code=...)
        The same rules on plain fields (for sources without structured data).

market is 'CR', 'US-remote', 'LATAM-remote', or None (excluded).
When market is None, reason is 'out_of_scope' or 'ambiguous_location' (the keys
logged in etl_runs.exclusion_reasons). Otherwise reason says which rule matched.

Rules (see CLAUDE.md, "Location scope and filtering"):
  * 'CR'           any location in Costa Rica (on-site, hybrid or remote)
  * 'LATAM-remote' remote and open to LATAM / the Americas / worldwide, or to 2+ LATAM countries
  * 'US-remote'    remote and open to US-based workers
  * Precedence when several apply: CR > LATAM-remote > US-remote.
  * Remote restricted to a single non-CR country (e.g. "Remote - Mexico") is out of scope.
  * A structured country decides the country; regions written in the text (LATAM,
    North America, EMEA) are still added, and "Costa Rica" in the text always counts.
  * "Remote" with no geography, from most to least specific: geography in the title
    ("(France)", "North America"), then Greenhouse offices, then the description (US,
    2+ US state names, LATAM/Americas or Costa Rica mentioned); else 'ambiguous_location'.
  * Country-level US with no remote flag ("United States", "Texas, United States"):
    US-remote if the description says remote/distributed, else out of scope.

Handles English and Spanish, with or without accents. No dependencies beyond the
standard library, so transform_load.py (or anything else) can import it.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable

CR, US_REMOTE, LATAM_REMOTE = "CR", "US-remote", "LATAM-remote"
OUT_OF_SCOPE, AMBIGUOUS = "out_of_scope", "ambiguous_location"

# Geography labels for one location part
G_CR, G_US, G_LATAM_REGION, G_WORLD, G_OTHER = "cr", "us", "latam_region", "world", "other"
# LATAM countries are labelled "latam:<country>" so distinct countries can be counted.


def _norm(text: str) -> str:
    """Lowercase and strip accents: 'San José' -> 'san jose', 'Remoto' -> 'remoto'."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _words(*terms: str) -> re.Pattern:
    """Regex matching any of the terms as whole words (terms are already normalized)."""
    return re.compile(r"(?<![a-z])(?:" + "|".join(terms) + r")(?![a-z])")


# ---- remote / work-arrangement words ------------------------------------------
REMOTE_RE = _words(r"remote", r"remoto", r"remota", r"teletrabajo", r"work from home", r"wfh",
                   r"home office", r"desde casa", r"flexible", r"virtual", r"anywhere", r"distributed")
# Used on descriptions only for country-level US locations ("United States" with no city).
TEXT_REMOTE_RE = _words(r"remote", r"remoto", r"remota", r"teletrabajo", r"work from home",
                        r"distributed (?:team|company|workforce)", r"fully distributed")

# ---- countries ------------------------------------------------------------------
COSTA_RICA_RE = _words(r"costa rica")
US_COUNTRY_RE = re.compile(
    r"(?<![a-z])(?:united states(?: of america)?|usa|us|estados unidos|eeuu|ee\.? ?uu\.?)(?![a-z])"
    r"|(?<![a-z])u\.s\.(?:a\.)?"
)
LATAM_COUNTRIES = {
    "mexico": "mexico", "guatemala": "guatemala", "belize": "belize", "honduras": "honduras",
    "el salvador": "el salvador", "nicaragua": "nicaragua", "panama": "panama",
    "colombia": "colombia", "venezuela": "venezuela", "ecuador": "ecuador", "peru": "peru",
    "bolivia": "bolivia", "chile": "chile", "argentina": "argentina", "uruguay": "uruguay",
    "paraguay": "paraguay", "brazil": "brazil", "brasil": "brazil", "guyana": "guyana",
    "suriname": "suriname", "dominican republic": "dominican republic",
    "republica dominicana": "dominican republic", "jamaica": "jamaica",
    "trinidad and tobago": "trinidad and tobago", "haiti": "haiti",
    # Major cities that often appear without their country
    "buenos aires": "argentina", "sao paulo": "brazil", "rio de janeiro": "brazil",
    "bogota": "colombia", "medellin": "colombia", "mexico city": "mexico", "cdmx": "mexico",
    "ciudad de mexico": "mexico", "guadalajara": "mexico", "monterrey": "mexico",
    "lima": "peru", "montevideo": "uruguay",
}
LATAM_COUNTRY_RE = re.compile(
    r"(?<![a-z])(" + "|".join(sorted(map(re.escape, LATAM_COUNTRIES), key=len, reverse=True)) + r")(?![a-z])"
)
OTHER_COUNTRY_RE = _words(
    r"canada", r"united kingdom", r"uk", r"u\.k\.", r"gbr", r"england", r"scotland", r"ireland", r"germany", r"france",
    r"spain", r"portugal", r"italy", r"netherlands", r"the netherlands", r"belgium", r"poland",
    r"romania", r"serbia", r"greece", r"switzerland", r"austria", r"sweden", r"norway", r"denmark",
    r"finland", r"hungary", r"czech republic", r"czechia", r"bulgaria", r"ukraine", r"turkey",
    r"israel", r"india", r"pakistan", r"japan", r"singapore", r"australia", r"new zealand",
    r"south korea", r"korea", r"china", r"taiwan", r"hong kong", r"philippines", r"indonesia",
    r"vietnam", r"malaysia", r"thailand", r"uae", r"united arab emirates", r"ksa", r"saudi arabia",
    r"egypt", r"nigeria", r"kenya", r"south africa", r"ethiopia", r"morocco", r"ghana",
)
# Foreign cities seen without a country. Weaker than a country name: they only count
# when no US state/abbreviation is present ("Melbourne, FL" is Florida, not Australia).
OTHER_CITY_RE = _words(
    r"london", r"toronto", r"montreal", r"vancouver", r"ottawa", r"ontario", r"quebec", r"berlin",
    r"munich", r"paris", r"stockholm", r"dublin", r"amsterdam", r"warsaw", r"krakow", r"belgrade",
    r"barcelona", r"madrid", r"lisbon", r"turin", r"milan", r"zurich", r"bangalore", r"bengaluru",
    r"gurgaon", r"gurugram", r"mumbai", r"delhi", r"hyderabad", r"pune", r"tokyo", r"sydney",
    r"melbourne", r"dubai", r"tel aviv", r"ho chi minh", r"jakarta", r"manila",
)

# ---- regions --------------------------------------------------------------------
LATAM_REGION_RE = _words(r"latam", r"latin america", r"latinoamerica", r"america latina",
                         r"south america", r"sudamerica", r"suramerica", r"central america",
                         r"centroamerica", r"americas", r"amer", r"lac", r"caribbean", r"caribe")
NORTH_AMERICA_RE = _words(r"north america", r"namer", r"norteamerica", r"america del norte")
OTHER_REGION_RE = _words(r"emea", r"international", r"europe", r"european union", r"eu", r"apac", r"asia",
                         r"asia pacific", r"africa", r"middle east", r"mena", r"oceania")
WORLD_RE = _words(r"anywhere", r"worldwide", r"global", r"globally", r"any location",
                  r"cualquier lugar", r"todo el mundo")

# ---- US states and cities -------------------------------------------------------
US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "newmexicostate", "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
]
US_STATE_RE = _words(*US_STATES)
US_STATE_ABBR_RE = re.compile(  # matched on the original (case-preserved) text: ", CA", ", NY (HQ)"
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ"
    r"|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b"
)
US_CITY_RE = _words(r"san francisco", r"new york city", r"nyc", r"seattle", r"boston", r"chicago",
                    r"los angeles", r"austin", r"denver", r"miami", r"atlanta", r"dallas", r"houston",
                    r"raleigh", r"washington,? d\.?c\.?", r"silicon valley", r"bay area")
US_REGION_LEVEL_RE = re.compile(  # "United States", "Colorado, United States", "Texas"
    r"^(?:(?:" + "|".join(US_STATES) + r"),?\s*)?(?:united states(?: of america)?|usa|us|u\.s\.(?:a\.)?)?$"
)

# ---- Costa Rica places (no country name needed) ----------------------------------
# "San Jose" alone is treated as Costa Rica unless the same part points to the US
# ("San Jose, CA" / "San Jose, California"): US checks run first.
CR_PLACE_RE = _words(r"san jose", r"heredia", r"alajuela", r"cartago", r"escazu", r"guanacaste",
                     r"puntarenas", r"limon", r"belen", r"curridabat", r"la sabana")

ISO_LATAM = {"MX": "mexico", "GT": "guatemala", "BZ": "belize", "HN": "honduras", "SV": "el salvador",
             "NI": "nicaragua", "PA": "panama", "CO": "colombia", "VE": "venezuela", "EC": "ecuador",
             "PE": "peru", "BO": "bolivia", "CL": "chile", "AR": "argentina", "UY": "uruguay",
             "PY": "paraguay", "BR": "brazil", "GY": "guyana", "SR": "suriname",
             "DO": "dominican republic", "JM": "jamaica", "TT": "trinidad and tobago", "HT": "haiti"}

# ---- text fallback (title + description) ------------------------------------------
TEXT_US_RE = re.compile(r"\bunited states\b|\bu\.s\.(?:a\.)?|\busa\b|\bestados unidos\b", re.I)
TEXT_US_CASED_RE = re.compile(r"\bUS\b")   # case-sensitive: avoids "join us", "contact us"
TEXT_LATAM_RE = _words(r"latam", r"latin america", r"latinoamerica", r"america latina",
                       r"south america", r"central america", r"centroamerica", r"americas",
                       r"costa rica")

SPLIT_RE = re.compile(r"[;|\n]")


def _part_geos(part: str) -> set[str]:
    """Geography labels found in one location part."""
    raw = part
    p = _norm(part).replace("new mexico", "newmexicostate")  # keep the state away from "mexico"
    geos: set[str] = set()

    if COSTA_RICA_RE.search(p):
        geos.add(G_CR)
    if US_COUNTRY_RE.search(p):
        geos.add(G_US)
    geos.update("latam:" + LATAM_COUNTRIES[m] for m in LATAM_COUNTRY_RE.findall(p))
    if OTHER_COUNTRY_RE.search(p):
        geos.add(G_OTHER)

    if not geos:  # no country name: fall back to states, cities and places
        if US_STATE_RE.search(p) or US_STATE_ABBR_RE.search(raw) or US_CITY_RE.search(p):
            geos.add(G_US)
        elif CR_PLACE_RE.search(p):
            geos.add(G_CR)
        elif OTHER_CITY_RE.search(p):
            geos.add(G_OTHER)

    if NORTH_AMERICA_RE.search(p):
        geos.add(G_US)
    if LATAM_REGION_RE.search(p):
        geos.add(G_LATAM_REGION)
    if OTHER_REGION_RE.search(p):
        geos.add(G_OTHER)
    if WORLD_RE.search(p) and not geos:  # "Anywhere in the US" is US, not worldwide
        geos.add(G_WORLD)
    return geos


def _normalize_workplace(workplace_type: str | None) -> str | None:
    if not workplace_type:
        return None
    wt = re.sub(r"[^a-z]", "", workplace_type.lower())   # "OnSite", "on-site" -> "onsite"
    return {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "inoffice": "onsite"}.get(wt)


def _clean_text(*texts: str | None) -> str:
    joined = " ".join(t for t in texts if t)
    return re.sub(r"<[^>]+>", " ", html.unescape(joined))


def _title_geography(title: str) -> tuple[str | None, str] | None:
    """Geography named in the title: "Account Executive (France)", "Payroll Analyst, North
    America". More specific than the description, which often carries boilerplate such as
    "authorized to work in the United States". 'Global'/'Anywhere' are ignored here: a
    "Global Payroll Analyst" is not a worldwide-remote role."""
    geos = {g for g in _part_geos(title) if g != G_WORLD}
    if not geos:
        return None
    if G_CR in geos:
        return CR, "cr_location"
    latam_countries = {g for g in geos if g.startswith("latam:")}
    if G_LATAM_REGION in geos or len(latam_countries) >= 2:
        return LATAM_REMOTE, "ambiguous_resolved_title"
    if G_US in geos:
        return US_REMOTE, "ambiguous_resolved_title"
    return None, OUT_OF_SCOPE   # the title places it elsewhere ("(France)", "EMEA", "Mexico")


def _text_fallback(text: str) -> tuple[str | None, str]:
    """Last resort for remote postings with no usable geography: the description."""
    if TEXT_LATAM_RE.search(_norm(text)):
        return LATAM_REMOTE, "ambiguous_resolved_latam_text"
    if TEXT_US_RE.search(text) or TEXT_US_CASED_RE.search(text):
        return US_REMOTE, "ambiguous_resolved_us_text"
    # e.g. "Licensed in a minimum of 2 core Oscar States ... Texas, Florida". One state
    # name alone is too risky ("Georgia" the country, "Washington" the surname).
    if len(set(US_STATE_RE.findall(_norm(text)))) >= 2:
        return US_REMOTE, "ambiguous_resolved_us_states"
    return None, AMBIGUOUS


def _region_geos(part: str) -> set[str]:
    """Regions named in a location text ("Remote - LATAM", "NAMER", "EMEA")."""
    p = _norm(part)
    geos = set()
    if NORTH_AMERICA_RE.search(p):
        geos.add(G_US)
    if LATAM_REGION_RE.search(p):
        geos.add(G_LATAM_REGION)
    if OTHER_REGION_RE.search(p):
        geos.add(G_OTHER)
    return geos


def _country_geos(country: str) -> set[str]:
    """Geography of a structured country field: ISO-2 ('US', 'CR', 'MX') or a name
    ('United States', 'USA', 'Costa Rica'). A country we don't list is still a country:
    'other'."""
    c = country.strip()
    if len(c) == 2 and c.isalpha():
        c = c.upper()
        return {G_CR} if c == "CR" else {G_US} if c == "US" else \
               {"latam:" + ISO_LATAM[c]} if c in ISO_LATAM else {G_OTHER}
    geos = {g for g in _part_geos(c) if g not in (G_LATAM_REGION, G_WORLD)}
    return geos or {G_OTHER}


def _part_geography(text: str, country: str | None) -> set[str]:
    """Geography of one location part.

    With a structured country (Lever `country`, Ashby `addressCountry`), that country
    decides; regions written in the text are added because a single country field can't
    express them ("South America" + BR), and an explicit "Costa Rica" in the text always
    counts. Without one, the text is parsed.
    """
    if not country or not country.strip():
        return _part_geos(text)
    geos = _country_geos(country) | _region_geos(text)
    if COSTA_RICA_RE.search(_norm(text)):
        geos.add(G_CR)
    return geos


def _classify_parts(
    parts: list[tuple[str, str | None]],
    title: str,
    description: str,
    workplace_type: str | None,
    is_remote: bool | None,
    fallback_parts: list[tuple[str, str | None]] | None = None,
) -> tuple[str | None, str]:
    """Core rules. parts = [(location text, structured country or None), ...].
    fallback_parts (Greenhouse offices) are consulted only when the location is "Remote"
    with no geography and the title doesn't name one."""
    wt = _normalize_workplace(workplace_type)
    posting_remote = wt == "remote" or (wt is None and bool(is_remote))

    remote_geos: set[str] = set()
    remote_without_geo = False
    us_region_level_onsite = False

    for part_text, country in parts:
        geos = _part_geography(part_text, country)
        if G_CR in geos:
            return CR, "cr_location"   # highest precedence: stop here
        part_remote = posting_remote or bool(REMOTE_RE.search(_norm(part_text)))
        if part_remote:
            if geos:
                remote_geos |= geos
            else:
                remote_without_geo = True
        elif geos == {G_US} and US_REGION_LEVEL_RE.match(_norm(part_text).strip(" ,.-")):
            us_region_level_onsite = True

    latam_countries = {g for g in remote_geos if g.startswith("latam:")}
    if G_LATAM_REGION in remote_geos:
        return LATAM_REMOTE, "latam_region"
    if G_WORLD in remote_geos:
        return LATAM_REMOTE, "worldwide"
    if len(latam_countries) >= 2:
        return LATAM_REMOTE, "latam_multi_country"
    if G_US in remote_geos:
        return US_REMOTE, "us_remote"

    if remote_without_geo or not parts:
        # "Remote" (or nothing at all) with no country. From most to least specific:
        # the title, then Greenhouse offices, then the description.
        by_title = _title_geography(title)
        if by_title:
            return by_title
        if fallback_parts:
            market, reason = _classify_parts(fallback_parts, "", "", "remote", None)
            if market:
                return market, "ambiguous_resolved_gh_office"
            if reason == OUT_OF_SCOPE:      # e.g. office "Remote France"
                return None, OUT_OF_SCOPE
        return _text_fallback(f"{title} {description}")
    if us_region_level_onsite:
        # "United States" / "Colorado, United States" with no remote flag: many boards
        # use this for remote roles, so check the description.
        if TEXT_REMOTE_RE.search(_norm(f"{title} {description}")):
            return US_REMOTE, "us_region_remote_in_text"
    return None, OUT_OF_SCOPE


def _split(location: str | Iterable[str] | None) -> list[str]:
    if location is None:
        return []
    chunks = [location] if isinstance(location, str) else list(location)
    return [p.strip() for chunk in chunks for p in SPLIT_RE.split(chunk or "") if p.strip()]


def classify_location(
    location: str | Iterable[str] | None,
    title: str | None = "",
    description: str | None = "",
    *,
    workplace_type: str | None = None,
    is_remote: bool | None = None,
    country_code: str | None = None,
) -> tuple[str | None, str]:
    """Text-level classifier. Return (market, reason); market is None when excluded.

    location        the posting's location text; a list for multi-location postings
    title/description used only when the location alone can't decide
    workplace_type  'remote' / 'hybrid' / 'onsite'; wins over is_remote
    is_remote       remote flag, used only when workplace_type is missing
    country_code    structured country (ISO-2 or name) of the PRIMARY (first) location,
                    as Lever's `country` field is

    For raw API postings use classify_posting(), which pulls these fields itself.
    """
    texts = _split(location)
    parts = [(t, country_code if i == 0 else None) for i, t in enumerate(texts)]
    if not parts and country_code:
        parts = [("", country_code)]
    return _classify_parts(parts, _clean_text(title), _clean_text(description), workplace_type, is_remote)


# ---- raw postings ---------------------------------------------------------------------

def _strip_html(value: str | None) -> str:
    """HTML (possibly entity-escaped twice, as Greenhouse sends it) -> plain text."""
    text = html.unescape(html.unescape(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def posting_text(source: str, job: dict) -> str:
    """Plain-text description of a raw posting (minimal cleanup, enough for location rules).

    Lever: descriptionPlain falls back to the HTML description when empty, and the
    requirements in lists[] are included.
    """
    if source == "greenhouse":
        return _strip_html(job.get("content"))
    if source == "lever":
        body = job.get("descriptionPlain") or _strip_html(job.get("description"))
        lists = " ".join(f"{item.get('text', '')}: {_strip_html(item.get('content'))}"
                         for item in job.get("lists") or [])
        extra = job.get("additionalPlain") or _strip_html(job.get("additional"))
        return " ".join(t for t in (body, lists, extra) if t).strip()
    if source == "ashby":
        return job.get("descriptionPlain") or _strip_html(job.get("descriptionHtml"))
    if source == "jooble":
        return _strip_html(job.get("snippet"))
    raise ValueError(f"unknown source: {source}")


def _ashby_country(entry: dict) -> str | None:
    return ((entry.get("address") or {}).get("postalAddress") or {}).get("addressCountry")


def classify_posting(source: str, job: dict, description: str | None = None) -> tuple[str | None, str]:
    """Classify one raw posting, exactly as the API returned it.

    source       'greenhouse' | 'lever' | 'ashby' | 'jooble'
    job          one job object from the raw response
    description  already-cleaned text (transform passes its own); computed if omitted

    Structured fields are preferred where they exist:
      Lever      workplaceType; country (applies to the primary location); allLocations
      Ashby      workplaceType (over isRemote); addressCountry of each location
      Greenhouse location.name is free text; `offices` is often an HQ, not where the job
                 is (e.g. "Remote - United States" -> office "San Francisco"), so it is
                 used only when the location is a bare "Remote" and the title names no
                 geography (then it beats the description, which carries boilerplate).
    """
    text = description if description is not None else posting_text(source, job)
    title = job.get("title") or job.get("text") or ""

    if source == "lever":
        cats = job.get("categories") or {}
        texts = cats.get("allLocations") or _split(cats.get("location"))
        primary = cats.get("location") or (texts[0] if texts else "")
        # Lever's country belongs to the primary location; keep that one first.
        ordered = ([primary] + [t for t in texts if t != primary]) if primary else texts
        parts = [(t, job.get("country") if i == 0 else None) for i, t in enumerate(ordered)]
        return _classify_parts(parts, title, text, job.get("workplaceType"), None)

    if source == "ashby":
        parts = [(job.get("location") or "", _ashby_country(job))]
        parts += [(s.get("location") or "", _ashby_country(s)) for s in job.get("secondaryLocations") or []]
        parts = [(t, c) for t, c in parts if t.strip() or c]
        return _classify_parts(parts, title, text, job.get("workplaceType"), job.get("isRemote"))

    if source == "greenhouse":
        parts = [(t, None) for t in _split((job.get("location") or {}).get("name"))]
        offices = [o.get("location") or o.get("name") or "" for o in job.get("offices") or []]
        office_parts = [(t, None) for t in offices if t.strip()]
        return _classify_parts(parts, title, text, None, None, fallback_parts=office_parts)

    if source == "jooble":
        return _classify_parts([(t, None) for t in _split(job.get("location"))], title, text, None, None)

    raise ValueError(f"unknown source: {source}")
