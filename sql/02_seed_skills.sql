-- =============================================================
-- SkillMap: diccionario inicial de herramientas y sinónimos
-- Ejecutar después de 01_schema.sql
-- Ampliar con el tiempo: cada sinónimo nuevo mejora la extracción.
-- =============================================================

INSERT INTO skills (skill_name, category) VALUES
    ('Excel',            'Spreadsheet'),
    ('Google Sheets',    'Spreadsheet'),
    ('Power BI',         'BI'),
    ('Tableau',          'BI'),
    ('Data Studio',      'BI'),
    ('Looker',           'BI'),
    ('Qlik',             'BI'),
    ('SQL',              'Database'),
    ('PostgreSQL',       'Database'),
    ('MySQL',            'Database'),
    ('SQL Server',       'Database'),
    ('Oracle',           'Database'),
    ('Snowflake',        'Database'),
    ('BigQuery',         'Database'),
    ('Python',           'Language'),
    ('R',                'Language'),
    ('DAX',              'Language'),
    ('VBA',              'Language'),
    ('Power Query',      'ETL'),
    ('SSIS',             'ETL'),
    ('dbt',              'ETL'),
    ('Airflow',          'ETL'),
    ('Azure',            'Cloud'),
    ('AWS',              'Cloud'),
    ('Google Cloud',     'Cloud'),
    ('SAP',              'ERP'),
    ('Salesforce',       'CRM'),
    ('Power Automate',   'Automation'),
    ('Jira',             'Project Mgmt'),
    ('Git',              'DevTools'),
    ('Epic',             'Healthcare'),
    ('HL7 / FHIR',       'Healthcare');

-- Sinónimos (siempre en minúsculas)
INSERT INTO skill_synonyms (synonym, skill_id)
SELECT v.synonym, s.skill_id
FROM (VALUES
    ('excel',               'Excel'),
    ('microsoft excel',     'Excel'),
    ('ms excel',            'Excel'),
    ('google sheets',       'Google Sheets'),
    ('power bi',            'Power BI'),
    ('powerbi',             'Power BI'),
    ('pbi',                 'Power BI'),
    ('tableau',             'Tableau'),
    ('data studio',         'Data Studio'),   -- nombre actual (abr 2026)
    ('google data studio',  'Data Studio'),
    ('looker studio',       'Data Studio'),   -- nombre 2022-2026
    ('looker',              'Looker'),
    ('qlik',                'Qlik'),
    ('qlikview',            'Qlik'),
    ('qlik sense',          'Qlik'),
    ('sql',                 'SQL'),
    ('t-sql',               'SQL'),
    ('tsql',                'SQL'),
    ('pl/sql',              'SQL'),
    ('postgresql',          'PostgreSQL'),
    ('postgres',            'PostgreSQL'),
    ('mysql',               'MySQL'),
    ('sql server',          'SQL Server'),
    ('mssql',               'SQL Server'),
    ('oracle',              'Oracle'),
    ('snowflake',           'Snowflake'),
    ('bigquery',            'BigQuery'),
    ('python',              'Python'),
    ('pandas',              'Python'),
    ('r programming',       'R'),
    ('rstudio',             'R'),
    ('dax',                 'DAX'),
    ('vba',                 'VBA'),
    ('macros',              'VBA'),
    ('power query',         'Power Query'),
    ('ssis',                'SSIS'),
    ('dbt',                 'dbt'),
    ('airflow',             'Airflow'),
    ('azure',               'Azure'),
    ('aws',                 'AWS'),
    ('amazon web services', 'AWS'),
    ('gcp',                 'Google Cloud'),
    ('google cloud',        'Google Cloud'),
    ('sap',                 'SAP'),
    ('salesforce',          'Salesforce'),
    ('power automate',      'Power Automate'),
    ('jira',                'Jira'),
    ('git',                 'Git'),
    ('github',              'Git'),
    ('epic',                'Epic'),
    ('hl7',                 'HL7 / FHIR'),
    ('fhir',                'HL7 / FHIR')
) AS v(synonym, skill_name)
JOIN skills s ON s.skill_name = v.skill_name;

-- Nota: 'R' y 'Epic' generan falsos positivos ("R&D", "an epic journey").
-- Por eso 'R' solo se detecta con sinónimos explícitos ('r programming', 'rstudio')
-- y el extractor en Python debe tratar 'epic' con contexto (p. ej. cerca de 'ehr' o 'emr').
