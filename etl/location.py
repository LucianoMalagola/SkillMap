"""
SkillMap: classify a job posting's location into a market.

    classify_location(location, title, description, workplace_type=..., is_remote=..., country_code=...)
        -> (market, reason)

market is 'CR', 'US-remote', 'LATAM-remote', or None (excluded).
When market is None, reason is 'out_of_scope' or 'ambiguous_location' (the keys
logged in etl_runs.exclusion_reasons). Otherwise reason says which rule matched.

Rules (see CLAUDE.md, "Location scope and filtering"):
  * 'CR'           any location in Costa Rica (on-site, hybrid or remote)
  * 'LATAM-remote' remote and open to LATAM / the Americas / worldwide, or to 2+ LATAM countries
  * 'US-remote'    remote and open to US-based workers
  * Precedence when several apply: CR > LATAM-remote > US-remote.
  * Remote restricted to a single non-CR country (e.g. "Remote - Mexico") is out of scope.
  * "Remote" with no geography: decided from title/description (US, 2+ US state names,
    LATAM/Americas or Costa Rica mentioned), else 'ambiguous_location'.
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
    r"canada", r"united kingdom", r"uk", r"england", r"scotland", r"ireland", r"germany", r"france",
    r"spain", r"portugal", r"italy", r"netherlands", r"the netherlands", r"belgium", r"poland",
    r"romania", r"serbia", r"greece", r"switzerland", r"austria", r"sweden", r"norway", r"denmark",
    r"finland", r"hungary", r"czech republic", r"czechia", r"bulgaria", r"ukraine", r"turkey",
    r"israel", r"india", r"pakistan", r"japan", r"singapore", r"australia", r"new zealand",
    r"south korea", r"korea", r"china", r"taiwan", r"hong kong", r"philippines", r"indonesia",
    r"vietnam", r"malaysia", r"thailand", r"uae", r"united arab emirates", r"ksa", r"saudi arabia",
    r"egypt", r"nigeria", r"kenya", r"south africa", r"ethiopia", r"morocco", r"ghana",
    # cities seen without a country
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
OTHER_REGION_RE = _words(r"emea", r"europe", r"european union", r"eu", r"apac", r"asia",
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


def _text_fallback(text: str) -> tuple[str | None, str]:
    """For remote postings with no usable geography."""
    if TEXT_LATAM_RE.search(_norm(text)):
        return LATAM_REMOTE, "ambiguous_resolved_latam_text"
    if TEXT_US_RE.search(text) or TEXT_US_CASED_RE.search(text):
        return US_REMOTE, "ambiguous_resolved_us_text"
    # e.g. "Licensed in a minimum of 2 core Oscar States ... Texas, Florida". One state
    # name alone is too risky ("Georgia" the country, "Washington" the surname).
    if len(set(US_STATE_RE.findall(_norm(text)))) >= 2:
        return US_REMOTE, "ambiguous_resolved_us_states"
    return None, AMBIGUOUS


def classify_location(
    location: str | Iterable[str] | None,
    title: str | None = "",
    description: str | None = "",
    *,
    workplace_type: str | None = None,
    is_remote: bool | None = None,
    country_code: str | None = None,
) -> tuple[str | None, str]:
    """Return (market, reason). market is None when the posting is excluded.

    location        the posting's location text; a list for multi-location postings
    title/description used only when the location alone can't decide
    workplace_type  'remote' / 'hybrid' / 'onsite' (Lever, Ashby); wins over is_remote
    is_remote       Ashby's isRemote flag (used only when workplace_type is missing)
    country_code    ISO-2 country from the API (Lever), used for parts with no geography
    """
    if location is None:
        location = ""
    parts_raw = [location] if isinstance(location, str) else list(location)
    parts = [p.strip() for chunk in parts_raw for p in SPLIT_RE.split(chunk or "") if p.strip()]

    wt = _normalize_workplace(workplace_type)
    posting_remote = wt == "remote" or (wt is None and bool(is_remote))
    cc = (country_code or "").strip().upper()
    text = _clean_text(title, description)

    remote_geos: set[str] = set()
    remote_without_geo = False
    us_region_level_onsite = False

    for part in parts:
        geos = _part_geos(part)
        if not geos and cc:
            geos = {G_CR} if cc == "CR" else {G_US} if cc == "US" else \
                   {"latam:" + ISO_LATAM[cc]} if cc in ISO_LATAM else {G_OTHER}
        if G_CR in geos:
            return CR, "cr_location"   # highest precedence: stop here
        part_remote = posting_remote or bool(REMOTE_RE.search(_norm(part)))
        if part_remote:
            if geos:
                remote_geos |= geos
            else:
                remote_without_geo = True
        elif geos == {G_US} and US_REGION_LEVEL_RE.match(_norm(part).strip(" ,.-")):
            us_region_level_onsite = True

    if not parts and cc == "CR":
        return CR, "cr_location"

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
        # "Remote" (or nothing at all) with no country: let the text decide.
        return _text_fallback(text)
    if us_region_level_onsite:
        # "United States" / "Colorado, United States" with no remote flag: many boards
        # use this for remote roles, so check the description.
        if TEXT_REMOTE_RE.search(_norm(text)):
            return US_REMOTE, "us_region_remote_in_text"
    return None, OUT_OF_SCOPE
