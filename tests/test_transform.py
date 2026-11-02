"""
Tests for the transform helpers: roles.py, skills.py, normalize.py.
Titles and text snippets are real, from the 2026-11-01 ATS boards unless marked.

Run:  python -m pytest tests/
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "etl"))
from normalize import clean_html, detect_modality, normalize  # noqa: E402
from roles import (ANALYTICS_INSIGHTS, BI_ANALYST, BIZOPS_ANALYST, BUSINESS_ANALYST, DATA_ANALYST,  # noqa: E402
                   DATA_REPORTING, MARKETING_ANALYST, classify_title)
from skills import SkillExtractor  # noqa: E402

# ---- roles ---------------------------------------------------------------------------------

ROLE_CASES = [
    # in
    ("Marketing Science Analyst", MARKETING_ANALYST),                  # Critical Mass
    ("Marketing Science, Senior Analyst", MARKETING_ANALYST),
    ("Marketing Science, Analyst - Media", MARKETING_ANALYST),
    ("Senior Analyst Data Strategy & Analytics", DATA_ANALYST),        # Kepler
    ("Senior Analyst, Data Analytics, SIU", DATA_ANALYST),             # Oscar
    ("Senior Business Analyst, Revenue Analytics", BUSINESS_ANALYST),  # Reddit
    ("Senior Financial Insights Analyst", DATA_ANALYST),               # Instacart
    ("Contract Senior Data Analyst (SQL,Telemetry,Browser Engine)", DATA_ANALYST),  # Mozilla
    ("Data Analyst II, Business Insights", DATA_ANALYST),              # Garner
    ("Partner Operations Data Analyst (Remote Eligible - Costa Rica)", DATA_ANALYST),  # Smartsheet
    ("Revenue Operations Analyst", BIZOPS_ANALYST),                    # Tebra
    ("Sales Operations Analyst - Public Sector", BIZOPS_ANALYST),      # Elastic
    ("Commercial Operations Analyst", BIZOPS_ANALYST),                 # synthetic
    ("Senior Marketing Operations Analyst", MARKETING_ANALYST),        # Newsela: BizOps is narrow now
    ("Senior Analyst, Digital Media (Paid Social)", MARKETING_ANALYST),  # Kepler
    ("Fashion Paid Media Senior Performance Analyst", MARKETING_ANALYST),  # Power Digital
    ("Digital Business Analyst", BUSINESS_ANALYST),                    # Konrad
    ("Senior Associate, Product Analytics & Applied AI", ANALYTICS_INSIGHTS),  # Owner.com
    ("Principal, Marketing Analytics", ANALYTICS_INSIGHTS),            # Elastic (individual contributor)
    ("Senior Consultant, Analytics Consulting", ANALYTICS_INSIGHTS),   # Komodo Health
    ("Staff Advanced Analytics, Community Blueprint & Quality", ANALYTICS_INSIGHTS),  # Airbnb
    ("Senior Data Analyst, Reporting", DATA_REPORTING),                # Garner: Reporting > Data Analyst
    ("Reporting Analyst, Consumer Services", DATA_REPORTING),          # Power Digital
    ("Business Intelligence Analyst", BI_ANALYST),                     # Snowflake (discovery)
    ("BI Developer", BI_ANALYST),                                      # synthetic
    ("Reporting Analyst", DATA_REPORTING),                             # synthetic
    ("Analista de Datos", DATA_ANALYST),                               # synthetic, Spanish
    ("Analista de Inteligencia de Negocios", BI_ANALYST),              # synthetic, Spanish
    ("Analista de Reportes", DATA_REPORTING),                          # synthetic, Spanish
    ("Analista de Marketing", MARKETING_ANALYST),                      # synthetic, Spanish
    ("Analista de Negocios", BUSINESS_ANALYST),                        # synthetic, Spanish
    # out
    ("Analytics Engineer", None),
    ("Staff Data Scientist, Ads", None),
    ("Senior Data Engineer", None),
    ("Insights Manager II, Retail Insights", None),
    ("Insights Lead, Instacart Business", None),
    ("Lead, GTM Analytics & Applied AI", None),                        # lead: not an individual contributor
    ("Manager, Analytics & Strategy", None),
    ("Principal, Strategic Finance & Analytics, Acquisition", None),   # finance
    ("Data Scientist, Enterprise Analytics", None),
    ("Manager, SEC Reporting", None),
    ("Senior Financial Analyst, FP&A", None),
    ("Payment Operations Analyst", None),
    ("GTM Operations Analyst", None),                                  # synthetic: BizOps is narrow now
    ("Sr. Business Systems Analyst", None),                            # systems analyst
    ("Performance Management Analyst", None),                          # synthetic: HR, not marketing
    ("Associate Audience Analyst", None),                              # Power Digital
    ("Senior Analyst, Digital Media", MARKETING_ANALYST),
    ("Electronic Data Interchange (EDI) Analyst", None),
    ("Senior Analyst, Credentialing Quality & Data Operations", None),
    ("Quantitative Analyst II (Capital Structuring & Analytics)", None),
    ("Senior Product Quality Analyst - AI Voice", None),
    ("Senior Software Engineer, Backend (Loan Origination and Reporting)", None),
    ("Strategic Finance & Analytics Intern (MBA)", None),
]


@pytest.mark.parametrize("title, role", ROLE_CASES)
def test_classify_title(title, role):
    assert classify_title(title) == role


# ---- skills ----------------------------------------------------------------------------------

SYNONYMS = {  # same as sql/02_seed_skills.sql
    "excel": "Excel", "microsoft excel": "Excel", "ms excel": "Excel", "google sheets": "Google Sheets",
    "power bi": "Power BI", "powerbi": "Power BI", "pbi": "Power BI", "tableau": "Tableau",
    "data studio": "Data Studio", "google data studio": "Data Studio", "looker studio": "Data Studio",
    "looker": "Looker", "qlik": "Qlik", "sql": "SQL", "t-sql": "SQL", "tsql": "SQL", "pl/sql": "SQL",
    "postgresql": "PostgreSQL", "postgres": "PostgreSQL", "mysql": "MySQL", "sql server": "SQL Server",
    "mssql": "SQL Server", "oracle": "Oracle", "snowflake": "Snowflake", "bigquery": "BigQuery",
    "python": "Python", "pandas": "Python", "r programming": "R", "rstudio": "R", "dax": "DAX",
    "vba": "VBA", "macros": "VBA", "power query": "Power Query", "dbt": "dbt", "airflow": "Airflow",
    "aws": "AWS", "gcp": "Google Cloud", "sap": "SAP", "salesforce": "Salesforce", "jira": "Jira",
    "git": "Git", "github": "Git", "epic": "Epic", "hl7": "HL7 / FHIR", "fhir": "HL7 / FHIR",
}
EXTRACTOR = SkillExtractor(SYNONYMS)

SKILL_CASES = [
    # real snippets
    ("build CTEs, or proficiency in R or Python, including experience", {"R", "Python"}),
    ("Base Pay Grade - R Equity Grade - 15 Employees", set()),
    ("Brazil Monthly Pay Range R$22.000 — R$27.500 BRL", set()),
    ("You excel in a collaborative environment", set()),
    ("Apply here if you excel at leadership, communication", set()),
    ("To excel as an Account Executive at Elastic", set()),
    ("working with large data sets using excel or a database language", {"Excel"}),
    ("comfort with MS Excel, data analysis and internet tools", {"Excel"}),
    ("using data software such as Excel, SQL, Python or other data analysis", {"Excel", "SQL", "Python"}),
    ("an EMR such as eClinicalWorks, Epic, or Athena", {"Epic"}),
    ("and the quarterly delivery epic. Ensure fast, high quality", set()),
    ("including SwiftUI, Swift Macros, Swift Testing", set()),
    ("SQL queries, Sigma, and Excel Macros (VBA) to automate", {"SQL", "Excel", "VBA"}),
    ("Databases: SAP HANA, Oracle, SQL Server, PostgreSQL", {"SAP", "Oracle", "SQL Server", "PostgreSQL"}),
    ("such as JIRA, Confluence, and Git.", {"Jira", "Git"}),
    # boundary and overlap rules
    ("Experience with PostgreSQL and MySQL; NoSQL a plus", {"PostgreSQL", "MySQL"}),
    ("Dashboards in Looker Studio", {"Data Studio"}),
    ("Looker and Looker Studio", {"Looker", "Data Studio"}),
    ("Advanced T-SQL and PL/SQL", {"SQL"}),
    ("Power BI (DAX, Power Query)", {"Power BI", "DAX", "Power Query"}),
    ("Our R&D team (R) trademark", set()),
    ("Python/R for statistical analysis", {"Python", "R"}),
    ("GitLab is the DevSecOps platform", set()),
]


@pytest.mark.parametrize("text, skills", SKILL_CASES)
def test_extract_skills(text, skills):
    assert EXTRACTOR.extract(text) == skills


# ---- normalize ---------------------------------------------------------------------------------

def test_clean_html_greenhouse_double_escaped():
    raw = "&lt;p&gt;Use &lt;strong&gt;SQL&lt;/strong&gt; &amp;amp; Python&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Tableau&lt;/li&gt;&lt;/ul&gt;"
    assert clean_html(raw) == "Use SQL & Python\n\n• Tableau"


def test_lever_html_fallback_and_lists():
    job = {"id": "x", "text": "Data Analyst", "categories": {"location": "Remote"}, "descriptionPlain": "",
           "description": "<p>Build dashboards.</p>",
           "lists": [{"text": "Who You Are", "content": "<li>SQL</li><li>Tableau</li>"}], "additionalPlain": ""}
    text = normalize("lever", job, "Acme")["description"]
    assert "Build dashboards." in text and "Who You Are" in text and "• SQL" in text


def test_no_text_is_empty_description():
    job = {"id": "y", "text": "Bilingual Junior Workforce Analyst", "categories": {}, "descriptionPlain": "",
           "description": "", "lists": [], "additionalPlain": ""}           # CSC Generation, real
    assert normalize("lever", job, "CSC Generation")["description"] == ""


@pytest.mark.parametrize("market, wt, location, title, description, expected", [
    ("US-remote", "hybrid", "New York, NY", "Data Analyst", "", "remote"),        # remote market wins
    ("CR", "onsite", "Cartago, Costa Rica", "Analyst", "remote team", "onsite"),  # structured wins
    ("CR", "OnSite", "San Jose", "Analyst", "", "onsite"),
    ("CR", None, "Hybrid - Heredia", "Analyst", "", "hybrid"),
    ("CR", None, "San José, Costa Rica", "Analista", "Modalidad híbrida, 3 días presencial", "hybrid"),
    ("CR", None, "San José, Costa Rica", "Analista", "Trabajo 100% remoto", "remote"),
    ("CR", None, "San Jose, Costa Rica", "Analyst", "This role is on-site in our Heredia office", "onsite"),
    ("CR", None, "Costa Rica", "Analyst", "Remote-first team; quarterly on-site visits", "unspecified"),
])
def test_detect_modality(market, wt, location, title, description, expected):
    assert detect_modality(market, wt, location, title, description) == expected


def test_salary_annualised():
    lever = {"id": "a", "text": "t", "categories": {}, "descriptionPlain": "x",
             "salaryRange": {"min": 2000, "max": 3000, "currency": "USD", "interval": "per-month-salary"}}
    assert normalize("lever", lever, "c")["salary_min"] == 24000
    hourly = {**lever, "salaryRange": {"min": 20, "max": 30, "currency": "USD", "interval": "per-hour-wage"}}
    assert normalize("lever", hourly, "c")["salary_min"] is None
    gh = {"id": 1, "title": "t", "location": {"name": "Remote"},
          "content": "&lt;div class=&quot;title&quot;&gt;US Annual Pay Range&lt;/div&gt;&lt;div class=&quot;pay-range&quot;&gt;"
                     "&lt;span&gt;$58,000&lt;/span&gt;&lt;span&gt;&amp;mdash;&lt;/span&gt;&lt;span&gt;$68,000 USD&lt;/span&gt;&lt;/div&gt;"}
    n = normalize("greenhouse", gh, "c")
    assert (n["salary_min"], n["salary_max"], n["salary_currency"]) == (58000, 68000, "USD")


def test_adzuna_url_tracking_removed_and_predicted_salary_skipped():
    job = {"id": "1", "title": "Data Analyst", "location": {"display_name": "US", "area": ["US"]},
           "description": "Remote role", "company": {"display_name": "Acme"},
           "redirect_url": "https://www.adzuna.com/land/ad/1?se=abc&utm_medium=api&utm_source=APPID&v=XYZ",
           "salary_min": 90000, "salary_max": 90000, "salary_is_predicted": "1", "created": "2026-11-01T21:32:54Z"}
    n = normalize("adzuna", job, "")
    assert "utm_" not in n["url"] and "APPID" not in n["url"] and "se=abc" in n["url"]
    assert n["salary_min"] is None and n["company"] == "Acme" and n["description_is_full"] is False
    n = normalize("adzuna", {**job, "salary_is_predicted": "0"}, "")
    assert (n["salary_min"], n["salary_currency"]) == (90000, "USD")


# ---- Remotive (real values from the 2026-11-01 API response) ---------------------------------
from location import classify_posting  # noqa: E402


@pytest.mark.parametrize("location, expected", [
    ("Worldwide", ("LATAM-remote", "worldwide")),
    ("USA, Canada, USA timezones", ("US-remote", "us_remote")),
    ("Europe, USA, Canada, APAC", ("US-remote", "us_remote")),
    ("Europe, USA, Canada, Australia, Singapore, Mexico, Saudi Arabia", ("US-remote", "us_remote")),
    ("Europe", (None, "out_of_scope")),                                 # synthetic
    ("Latin America", ("LATAM-remote", "latam_region")),                # synthetic
])
def test_remotive_location(location, expected):
    job = {"id": 1, "title": "Data Analyst", "candidate_required_location": location, "description": "<p>x</p>"}
    assert classify_posting("remotive", job) == expected


@pytest.mark.parametrize("salary, expected", [
    ("$170k - $200k", (170000, 200000, "USD")),
    ("$3.5k–$5k/month", (42000, 60000, "USD")),
    ("$35,3k- $52k", (35300, 52000, "USD")),
    ("70K-80K", (None, None, None)),             # no currency: skipped
    ("$1000 per task", (None, None, None)),
    ("OTE $25k - $35k", (None, None, None)),
])
def test_remotive_salary(salary, expected):
    job = {"id": 1, "title": "t", "candidate_required_location": "Worldwide", "description": "<p>x</p>",
           "salary": salary, "url": "https://remotive.com/remote-jobs/x", "publication_date": "2026-10-30T12:00:00"}
    n = normalize("remotive", job, "")
    assert (n["salary_min"], n["salary_max"], n["salary_currency"]) == expected
    assert n["description_is_full"] is True and n["url"].startswith("https://remotive.com/")


# ---- placeholder postings --------------------------------------------------------------------
from normalize import is_placeholder  # noqa: E402

CM_BOILERPLATE = (
    "Critical Mass is an equal opportunity employer.\n\n"
    "Critical Mass uses artificial intelligence in our recruitment process to enhance job postings.\n\n"
    "We will never ask you to send us money, technology, or anything else to work for our company.\n"
    "If U.S. based: https://www.consumer.ftc.gov/articles/job-scams#avoid\n"
    "If Costa Rica based: https://www.consumo.go.cr/educacion_consumidor/consejos_practicos.aspx")


@pytest.mark.parametrize("description, expected", [
    ("TBD\n" + CM_BOILERPLATE, True),                                         # Critical Mass, real
    ("Project Manager profiles\n" + CM_BOILERPLATE, True),                    # Critical Mass, real
    ("Thank you for your interest in working with us. If you would like to submit an application for a "
     "future position, please complete the form below and attach your resume. Our recruiting team reviews "
     "every application and will contact you when a role that matches your profile opens up. We hire for "
     "consulting, software and support roles.", False),                       # FreeBalance-like, real-ish, 316+
    ("Resumen General\nEl ensamblador de producción II (PBII) realiza ensamble de complejidad moderada con "
     "supervisión mínima, siguiendo procedimientos documentados, inspecciones de calidad y registros de "
     "producción en un ambiente de cuarto limpio. Opera equipos de manufactura y reporta desviaciones. "
     "Requiere bachillerato y experiencia en manufactura de dispositivos médicos.", False),  # Penumbra, Spanish
])
def test_is_placeholder(description, expected):
    assert is_placeholder(description) is expected


# ---- Recruitee and Personio ----------------------------------------------------------------------
from normalize import parse_board  # noqa: E402

PERSONIO_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs><position><id>1822268</id><office>San Jos\xc3\xa9</office>
<additionalOffices><office>Remote</office></additionalOffices>
<name>Data Analyst</name><createdAt>2026-10-01T10:00:00+00:00</createdAt>
<jobDescriptions><jobDescription><name>Your tasks</name><value>&lt;ul&gt;&lt;li&gt;SQL and Power BI reporting&lt;/li&gt;&lt;/ul&gt;</value></jobDescription></jobDescriptions>
</position></workzag-jobs>"""


def test_personio_parse_and_normalize():
    jobs = parse_board("personio", PERSONIO_XML)
    assert len(jobs) == 1 and jobs[0]["office"] == "San José" and jobs[0]["additionalOffices"] == ["Remote"]
    jobs[0]["_board_token"] = "acme"
    n = normalize("personio", jobs[0], "Acme")
    assert n["title"] == "Data Analyst" and n["location_raw"] == "San José | Remote"
    assert "SQL and Power BI reporting" in n["description"] and n["url"] == "https://acme.jobs.personio.de/job/1822268"
    assert classify_posting("personio", jobs[0]) == ("CR", "cr_location")


def test_recruitee_structured_fields():
    offer = {"id": 1, "title": "BI Analyst", "remote": False, "hybrid": True, "on_site": False,
             "locations": [{"city": "Heredia", "country": "Costa Rica", "country_code": "CR"}],
             "description": "<p>Build Power BI dashboards.</p>", "requirements": "<ul><li>SQL</li></ul>",
             "careers_url": "https://x.recruitee.com/o/bi-analyst", "published_at": "2026-07-20 22:11:58 UTC",
             "salary": {"min": "2000", "max": "3000", "period": "month", "currency": "USD"}}
    n = normalize("recruitee", offer, "X")
    assert n["workplace_type"] == "hybrid" and n["location_raw"] == "Heredia, Costa Rica"
    assert "Power BI" in n["description"] and "SQL" in n["description"]
    assert (n["salary_min"], n["salary_max"]) == (24000, 36000) and n["posted_at"].year == 2026
    assert classify_posting("recruitee", offer) == ("CR", "cr_location")
    remote_do = {**offer, "remote": True, "hybrid": False,
                 "locations": [{"city": "Santo Domingo", "country": "Dominican Republic", "country_code": "DO"}]}
    assert classify_posting("recruitee", remote_do) == (None, "out_of_scope")   # one non-CR LATAM country


# ---- merged per-country copies ------------------------------------------------------------------
from location import classify_group  # noqa: E402


def _pd_copy(job_id, country):
    """Power Digital posts "Reporting Analyst, Consumer Services" once per country (real pattern)."""
    return {"id": job_id, "title": "Reporting Analyst, Consumer Services",
            "location": {"name": f"Remote - {country}"}, "offices": [],
            "content": "&lt;p&gt;" + "Build weekly performance reports in SQL and Looker for consumer brands, "
                       "track campaign KPIs, automate data pulls and present insights to account teams. " * 3 + "&lt;/p&gt;"}


def test_merged_copies_pool_locations():
    copies = [_pd_copy(1, "Colombia"), _pd_copy(2, "Mexico"), _pd_copy(3, "Ecuador"), _pd_copy(4, "Argentina")]
    assert all(classify_posting("greenhouse", c) == (None, "out_of_scope") for c in copies)   # each alone
    assert classify_group("greenhouse", copies) == ("LATAM-remote", "latam_multi_country")    # pooled


def test_transform_counts_copies_once():
    import transform_load as t
    copies = [("Power Digital", _pd_copy(i, c)) for i, c in enumerate(["Colombia", "Mexico", "Ecuador"], 1)]
    copies.append(("Power Digital", _pd_copy(9, "Costa Rica")))
    rows, excluded = t.transform("greenhouse", copies, EXTRACTOR)
    assert len(rows) == 1 and rows[0]["market"] == "CR" and rows[0]["copies"] == 4
    assert rows[0]["external_id"] == "9"                       # the copy located in the winning market
    assert "Remote - Colombia" in rows[0]["location_raw"] and "Remote - Costa Rica" in rows[0]["location_raw"]
    assert excluded == {"duplicate": 3}


# ---- role families ----------------------------------------------------------------------------

from roles import MARKETING_INSIGHTS, ROLE_FAMILIES, ROLES, role_family  # noqa: E402


def test_every_role_has_a_family():
    assert set(ROLE_FAMILIES) == set(ROLES)


@pytest.mark.parametrize("title", ["Marketing Science Analyst", "Marketing Science, Senior Analyst"])
def test_marketing_science_stays_in_marketing(title):
    assert classify_title(title) == MARKETING_ANALYST
    assert role_family(classify_title(title)) == MARKETING_INSIGHTS


def test_role_families_match_migration_08():
    """The SQL seed of role_families must list exactly roles.ROLE_FAMILIES."""
    import re
    sql = (Path(__file__).resolve().parent.parent / "sql" / "08_role_families_and_analysis_views.sql").read_text()
    seeded = dict(re.findall(r"\('([^']+)',\s+'([^']+)',\s+\d+, \d+\)", sql))
    assert seeded == ROLE_FAMILIES
