-- =============================================================
-- SkillMap: esquema de base de datos (PostgreSQL)
-- Ejecutar sobre una base vacía:  psql -d skillmap -f 01_schema.sql
-- =============================================================

-- -------------------------------------------------------------
-- 1. Catálogo de herramientas (skills)
-- -------------------------------------------------------------
CREATE TABLE skills (
    skill_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    skill_name  TEXT NOT NULL UNIQUE,          -- nombre canónico: 'Power BI'
    category    TEXT NOT NULL                  -- 'BI', 'Database', 'Language', 'Cloud', ...
);

-- Sinónimos: todas las formas en que una herramienta aparece en una oferta.
-- El extractor en Python busca cada sinónimo (en minúsculas, con límites de palabra).
CREATE TABLE skill_synonyms (
    synonym     TEXT PRIMARY KEY,              -- siempre en minúsculas: 'powerbi', 'pbi'
    skill_id    INT NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE
);

-- -------------------------------------------------------------
-- 2. Ofertas laborales
-- -------------------------------------------------------------
CREATE TABLE jobs (
    job_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source              TEXT NOT NULL CHECK (source IN ('jooble', 'adzuna', 'remotive')),
    external_id         TEXT NOT NULL,         -- ID que da la API
    title               TEXT NOT NULL,
    company             TEXT,
    location_raw        TEXT,                  -- ubicación tal como viene de la API
    country             TEXT NOT NULL CHECK (country IN ('CR', 'US')),
    modality            TEXT NOT NULL DEFAULT 'unspecified'
                        CHECK (modality IN ('remote', 'hybrid', 'onsite', 'unspecified')),
    role_category       TEXT,                  -- 'Data Analyst', 'BI Analyst', ...
    salary_min          NUMERIC(12,2),
    salary_max          NUMERIC(12,2),
    salary_currency     TEXT,
    description         TEXT,
    description_is_full BOOLEAN NOT NULL DEFAULT FALSE,  -- FALSE si la API la recorta
    url                 TEXT,
    posted_at           TIMESTAMPTZ,
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen           TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (source, external_id)               -- evita duplicados de la misma fuente
);

CREATE INDEX idx_jobs_filters ON jobs (role_category, country, modality);
CREATE INDEX idx_jobs_last_seen ON jobs (last_seen);

-- Tabla puente: qué herramientas pide cada oferta
CREATE TABLE job_skills (
    job_id      BIGINT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    skill_id    INT    NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE,
    is_required BOOLEAN,                       -- NULL = no se pudo determinar
    PRIMARY KEY (job_id, skill_id)
);

-- -------------------------------------------------------------
-- 3. Bitácora de ejecuciones del pipeline
--    (sirve para mostrar "Última actualización" en el dashboard)
-- -------------------------------------------------------------
CREATE TABLE etl_runs (
    run_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source        TEXT NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    jobs_fetched  INT DEFAULT 0,
    jobs_new      INT DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'success', 'failed')),
    error_message TEXT
);

-- -------------------------------------------------------------
-- 4. Vistas para los dashboards (Looker Studio y Power BI)
-- -------------------------------------------------------------

-- Demanda de cada herramienta por rol, país y modalidad (solo ofertas activas)
CREATE VIEW v_skill_demand AS
WITH totals AS (
    SELECT role_category, country, modality, COUNT(*) AS total_jobs
    FROM jobs
    WHERE is_active
    GROUP BY role_category, country, modality
)
SELECT
    j.role_category,
    j.country,
    j.modality,
    s.skill_name,
    s.category                                    AS skill_category,
    COUNT(*)                                      AS jobs_mentioning,
    t.total_jobs,
    ROUND(100.0 * COUNT(*) / t.total_jobs, 1)     AS pct_of_jobs
FROM jobs j
JOIN job_skills js ON js.job_id = j.job_id
JOIN skills s      ON s.skill_id = js.skill_id
JOIN totals t      ON t.role_category IS NOT DISTINCT FROM j.role_category
                  AND t.country  = j.country
                  AND t.modality = j.modality
WHERE j.is_active
GROUP BY j.role_category, j.country, j.modality, s.skill_name, s.category, t.total_jobs;

-- Co-ocurrencia: "si piden X, ¿qué % también pide Y?"
CREATE VIEW v_skill_cooccurrence AS
SELECT
    sa.skill_name AS skill_a,
    sb.skill_name AS skill_b,
    COUNT(*)      AS jobs_with_both
FROM job_skills a
JOIN job_skills b ON a.job_id = b.job_id AND a.skill_id < b.skill_id
JOIN skills sa    ON sa.skill_id = a.skill_id
JOIN skills sb    ON sb.skill_id = b.skill_id
JOIN jobs j       ON j.job_id = a.job_id
WHERE j.is_active
GROUP BY sa.skill_name, sb.skill_name;

-- Mezcla de modalidades por país
CREATE VIEW v_modality_mix AS
SELECT
    country,
    modality,
    COUNT(*) AS jobs,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY country), 1) AS pct
FROM jobs
WHERE is_active
GROUP BY country, modality;
