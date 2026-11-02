"""
SkillMap: map a job title to one of the target roles (or None).

    classify_title(title) -> 'Data Analyst' | 'BI Analyst' | 'Business Operations Analyst'
                             | 'Data Reporting' | None

Built from the titles actually seen on the ATS boards (2026-11-01). Deliberately narrow:
only a few dozen postings per day match, so a handful of false matches would distort
the tool rankings. Not included: Data Scientist / Data Engineer / Analytics Engineer,
Business Analyst (requirements work), Financial/FP&A, risk, fraud, payroll, security,
compliance analysts, and generic "Operations Analyst" (payments, IT, security ops).

Precedence when several match: BI Analyst > Data Reporting > Business Operations > Data Analyst.
English and Spanish; accents ignored.
"""

from __future__ import annotations

import re
import unicodedata

DATA_ANALYST = "Data Analyst"
BI_ANALYST = "BI Analyst"
BIZOPS_ANALYST = "Business Operations Analyst"
DATA_REPORTING = "Data Reporting"
ROLES = (DATA_ANALYST, BI_ANALYST, BIZOPS_ANALYST, DATA_REPORTING)

_SENIORITY = r"(?:(?:senior|sr|junior|jr|lead|staff|principal|associate|entry level|mid level|i|ii|iii|iv|1|2|3)\s+)*"
_ANALYST = r"(?:analyst|analista)"

# A title that names one of these roles is not an analyst posting, unless it also says
# analyst ("Analytics Engineer", "Insights Manager" are out; "Lead Data Analyst" is in).
_NOT_ANALYST = re.compile(
    r"\b(?:engineer|engineering|scientist|manager|director|head|vp|vice president|architect|"
    r"administrator|recruiter|recruiting|gerente|director[a]?|ingeniero|cientifico)\b"
)
_EXCLUDED_PHRASES = re.compile(r"\bdata (?:interchange|entry|operations|center|centre)\b")

_PATTERNS: list[tuple[str, re.Pattern]] = [(role, re.compile(rx)) for role, rx in [
    # BI Analyst
    (BI_ANALYST, rf"\b(?:business intelligence|bi|power bi)\s+{_SENIORITY}"
                 rf"(?:analyst|analista|developer|desarrollador|specialist|especialista|consultant)\b"),
    (BI_ANALYST, rf"\b{_ANALYST}\s+(?:de\s+)?(?:business intelligence|bi|inteligencia de negocios)\b"),
    (BI_ANALYST, r"\b(?:analista|especialista|desarrollador)\s+(?:en\s+|de\s+)?inteligencia de negocios\b"),
    # Data Reporting
    (DATA_REPORTING, rf"\b(?:data\s+)?reporting\s+(?:(?:&|and)\s+analytics\s+)?{_SENIORITY}"
                     rf"(?:analyst|analista|specialist|especialista)\b"),
    (DATA_REPORTING, rf"\b{_ANALYST}\s+(?:de\s+)?(?:reportes|reporteria|reporting)\b"),
    (DATA_REPORTING, r"\bdata reporting\b"),
    # Business Operations Analyst
    (BIZOPS_ANALYST, rf"\b(?:business|biz|revenue|sales|marketing|gtm|go to market|commercial|"
                     rf"strategy\s+(?:&|and))\s+operations\s+{_SENIORITY}{_ANALYST}\b"),
    (BIZOPS_ANALYST, r"\bbiz\s?ops\b"),
    (BIZOPS_ANALYST, rf"\b{_ANALYST}\s+de\s+operaciones\s+(?:de\s+negocio|comerciales)\b"),
    # Data Analyst
    (DATA_ANALYST, rf"\b(?:data|datos|analytics|insights|marketing science|product|growth|marketing|"
                   rf"customer|people analytics)\s+(?:&\s+\w+\s+)?{_SENIORITY}{_ANALYST}\b"),
    (DATA_ANALYST, rf"\b{_ANALYST}\s+(?:\w+\s+)?(?:data|analytics|insights)\b"),
    (DATA_ANALYST, rf"\b{_ANALYST}\s+de\s+datos\b"),
]]


def _clean(title: str) -> str:
    """'Marketing Science, Senior Analyst - Media' -> 'marketing science senior analyst media'."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9&]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def classify_title(title: str | None) -> str | None:
    """Return the role_category for a job title, or None if it isn't a target role."""
    t = _clean(title or "")
    if not t or _EXCLUDED_PHRASES.search(t):
        return None
    if _NOT_ANALYST.search(t) and not re.search(rf"\b{_ANALYST}\b", t):
        return None
    for role, pattern in _PATTERNS:
        if pattern.search(t):
            return role
    return None
