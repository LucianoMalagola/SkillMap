"""
SkillMap: map a job title to one of the target roles (or None).

    classify_title(title) -> role_category | None

Role categories (decided 2026-11-02):
  Data Analyst                 data / analytics / insights / product / growth / customer analysts
  BI Analyst                   business intelligence (BI, Power BI) analysts and developers
  Data Reporting               reporting analysts / specialists
  Business Operations Analyst  business / revenue / sales / commercial operations analysts, BizOps
                               (narrow: not marketing/GTM/strategy ops, not generic "Operations Analyst")
  Marketing Analyst            marketing / media / paid media / performance / campaign / SEO / social
                               analysts, incl. "Marketing Science Analyst", "Senior Analyst, Digital Media"
  Business Analyst             "Business Analyst" titles (not business systems / systems analysts)
  Analytics & Insights         titles with analytics/insights but no "analyst", individual contributors
                               only ("Senior Associate, Product Analytics", "Analytics Specialist");
                               no managers/directors/heads/leads/VPs, engineers, scientists or finance

Left out for now: Data Scientist, Data/Analytics Engineer, Financial/FP&A analysts, systems analysts,
risk/fraud/payroll/security/compliance analysts, generic Operations Analyst (payments, IT, security ops).

Precedence when several match: BI > Data Reporting > Business Operations > Marketing > Business Analyst
> Data Analyst > Analytics & Insights. English and Spanish; accents ignored.
"""

from __future__ import annotations

import re
import unicodedata

DATA_ANALYST = "Data Analyst"
BI_ANALYST = "BI Analyst"
BIZOPS_ANALYST = "Business Operations Analyst"
DATA_REPORTING = "Data Reporting"
MARKETING_ANALYST = "Marketing Analyst"
BUSINESS_ANALYST = "Business Analyst"
ANALYTICS_INSIGHTS = "Analytics & Insights"
ROLES = (DATA_ANALYST, BI_ANALYST, DATA_REPORTING, BIZOPS_ANALYST, MARKETING_ANALYST, BUSINESS_ANALYST,
         ANALYTICS_INSIGHTS)

# Role families (decided 2026-11-02): dashboards group related roles when single categories
# have too few postings. Marketing Science titles stay under Marketing Analyst.
# Mirrored in the role_families table (sql/08); transform_load refuses to run if they differ.
DATA_ANALYTICS = "Data & Analytics"
MARKETING_INSIGHTS = "Marketing & Insights"
BUSINESS_OPERATIONS = "Business & Operations"
ROLE_FAMILIES: dict[str, str] = {
    DATA_ANALYST: DATA_ANALYTICS,
    BI_ANALYST: DATA_ANALYTICS,
    DATA_REPORTING: DATA_ANALYTICS,
    ANALYTICS_INSIGHTS: DATA_ANALYTICS,
    MARKETING_ANALYST: MARKETING_INSIGHTS,
    BIZOPS_ANALYST: BUSINESS_OPERATIONS,
    BUSINESS_ANALYST: BUSINESS_OPERATIONS,
}


def role_family(role_category: str) -> str:
    """'BI Analyst' -> 'Data & Analytics'. Raises KeyError for an unknown category."""
    return ROLE_FAMILIES[role_category]

_SENIORITY = r"(?:(?:senior|sr|junior|jr|lead|staff|principal|associate|entry level|mid level|i|ii|iii|iv|1|2|3)\s+)*"
_ANALYST = r"(?:analyst|analista)"

# A title that names one of these roles is not an analyst posting, unless it also says
# analyst ("Analytics Engineer", "Insights Manager" are out; "Lead Data Analyst" is in).
_NOT_ANALYST = re.compile(
    r"\b(?:engineer|engineering|scientist|manager|director|head|vp|vice president|architect|"
    r"administrator|recruiter|recruiting|gerente|director[a]?|ingeniero|cientifico)\b"
)
_EXCLUDED_PHRASES = re.compile(r"\bdata (?:interchange|entry|operations|center|centre)\b")

_MARKETING_QUALIFIER = (r"(?:marketing science|marketing|digital marketing|growth marketing|media|digital media|"
                        r"paid media|paid social|paid search|social media|campaign|seo|sem|mercadeo|medios)")

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
    # Business Operations Analyst (narrow)
    (BIZOPS_ANALYST, rf"\b(?:business|biz|revenue|sales|commercial)\s+operations\s+{_SENIORITY}{_ANALYST}\b"),
    (BIZOPS_ANALYST, r"\bbiz\s?ops\b"),
    (BIZOPS_ANALYST, rf"\b{_ANALYST}\s+de\s+operaciones\s+(?:de\s+negocio|comerciales|de\s+ventas)\b"),
    # Marketing Analyst
    (MARKETING_ANALYST, rf"\b{_MARKETING_QUALIFIER}\s+(?:\w+\s+)?{_SENIORITY}{_ANALYST}\b"),
    (MARKETING_ANALYST, rf"\b{_ANALYST}\s+(?:\w+\s+)?(?:media|medios)\b"),          # "Senior Analyst, Digital Media"
    (MARKETING_ANALYST, rf"\b{_ANALYST}\s+de\s+(?:marketing|mercadeo|medios)\b"),
    # "performance" only right before analyst: not "Performance Management Analyst" (HR)
    (MARKETING_ANALYST, rf"\bperformance\s+(?:marketing\s+)?{_SENIORITY}{_ANALYST}\b"),
    # Business Analyst (not systems analysts: "business analyst" must be adjacent)
    (BUSINESS_ANALYST, r"\bbusiness analyst\b"),
    (BUSINESS_ANALYST, rf"\b{_ANALYST}\s+de\s+negocios?\b"),
    # Data Analyst
    (DATA_ANALYST, rf"\b(?:data|datos|analytics|insights|product|growth|customer|people analytics)"
                   rf"\s+(?:&\s+\w+\s+)?{_SENIORITY}{_ANALYST}\b"),
    (DATA_ANALYST, rf"\b{_ANALYST}\s+(?:\w+\s+)?(?:data|analytics|insights)\b"),
    (DATA_ANALYST, rf"\b{_ANALYST}\s+de\s+datos\b"),
]]

# Analytics & Insights: no "analyst" in the title, individual contributors only.
_ANALYTICS_WORD = re.compile(r"\b(?:analytics|insights|analitica|analitico)\b")
_NOT_IC = re.compile(r"\b(?:manager|director|head|lead|vp|vice president|chief|president|gerente|jefe|"
                     r"engineer|engineering|scientist|science|architect|developer|finance|financial|"
                     r"intern|internship|sales|account executive|recruiter|product manager)\b")


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
    has_analyst = bool(re.search(rf"\b{_ANALYST}\b", t))
    if _NOT_ANALYST.search(t) and not has_analyst:
        return None
    for role, pattern in _PATTERNS:
        if pattern.search(t):
            return role
    if not has_analyst and _ANALYTICS_WORD.search(t) and not _NOT_IC.search(t):
        return ANALYTICS_INSIGHTS
    return None
