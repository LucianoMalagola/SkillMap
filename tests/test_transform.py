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
from roles import BI_ANALYST, BIZOPS_ANALYST, DATA_ANALYST, DATA_REPORTING, classify_title  # noqa: E402
from skills import SkillExtractor  # noqa: E402

# ---- roles ---------------------------------------------------------------------------------

ROLE_CASES = [
    # in
    ("Marketing Science Analyst", DATA_ANALYST),                       # Critical Mass
    ("Marketing Science, Senior Analyst", DATA_ANALYST),
    ("Marketing Science, Analyst - Media", DATA_ANALYST),
    ("Senior Analyst Data Strategy & Analytics", DATA_ANALYST),        # Kepler
    ("Senior Analyst, Data Analytics, SIU", DATA_ANALYST),             # Oscar
    ("Senior Business Analyst, Revenue Analytics", DATA_ANALYST),      # Reddit
    ("Senior Financial Insights Analyst", DATA_ANALYST),               # Instacart
    ("Contract Senior Data Analyst (SQL,Telemetry,Browser Engine)", DATA_ANALYST),  # Mozilla
    ("Data Analyst II, Business Insights", DATA_ANALYST),              # Garner
    ("Revenue Operations Analyst", BIZOPS_ANALYST),                    # Tebra
    ("Sales Operations Analyst - Public Sector", BIZOPS_ANALYST),      # Elastic
    ("Senior Marketing Operations Analyst", BIZOPS_ANALYST),
    ("Senior Data Analyst, Reporting", DATA_REPORTING),                # Garner: Reporting > Data Analyst
    ("Business Intelligence Analyst", BI_ANALYST),                     # Snowflake (discovery)
    ("BI Developer", BI_ANALYST),                                      # synthetic
    ("Reporting Analyst", DATA_REPORTING),                             # synthetic
    ("Analista de Datos", DATA_ANALYST),                               # synthetic, Spanish
    ("Analista de Inteligencia de Negocios", BI_ANALYST),              # synthetic, Spanish
    ("Analista de Reportes", DATA_REPORTING),                          # synthetic, Spanish
    # out
    ("Analytics Engineer", None),
    ("Staff Data Scientist, Ads", None),
    ("Senior Data Engineer", None),
    ("Insights Manager II, Retail Insights", None),
    ("Manager, SEC Reporting", None),
    ("Digital Business Analyst", None),
    ("Senior Financial Analyst, FP&A", None),
    ("Payment Operations Analyst", None),
    ("Senior Analyst, Digital Media (Paid Social)", None),
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
