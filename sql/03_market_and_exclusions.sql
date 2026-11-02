-- =============================================================
-- SkillMap: migration 03 — market column, ATS sources, exclusion logging
-- Run after 01_schema.sql and 02_seed_skills.sql:
--     psql -d skillmap -f 03_market_and_exclusions.sql
-- Safe to re-run: every step checks whether it has already been applied,
-- and etl/migrate.py runs the whole file in one transaction (all or nothing).
-- =============================================================


-- -------------------------------------------------------------
-- 0. Drop the views that depend on jobs.country (recreated in step 5).
--    Their column lists change (country -> market), which CREATE OR REPLACE
--    VIEW cannot do, so they are dropped and created again.
-- -------------------------------------------------------------
DROP VIEW IF EXISTS v_skill_demand;
DROP VIEW IF EXISTS v_modality_mix;

-- -------------------------------------------------------------
-- 1. jobs.market replaces jobs.country
--    'CR'           located in Costa Rica (on-site, hybrid or remote)
--    'US-remote'    remote, open to US-based workers
--    'LATAM-remote' remote, open to Latin America / Americas / Costa Rica
--    country could not represent LATAM-remote honestly and would duplicate
--    market for the other two; the original text stays in location_raw.
-- -------------------------------------------------------------
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS market TEXT;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'jobs' AND column_name = 'country') THEN
        -- Backfill only where the mapping is certain.
        UPDATE jobs SET market = 'CR'        WHERE market IS NULL AND country = 'CR';
        UPDATE jobs SET market = 'US-remote' WHERE market IS NULL AND country = 'US' AND modality = 'remote';
    END IF;

    -- Anything else must be reclassified from data/raw by the transform step,
    -- not guessed here. Abort (and roll back) rather than mislabel rows.
    IF EXISTS (SELECT 1 FROM jobs WHERE market IS NULL) THEN
        RAISE EXCEPTION 'jobs has % row(s) whose market cannot be derived from country; '
                        'reprocess them from data/raw or delete them, then re-run this migration',
                        (SELECT COUNT(*) FROM jobs WHERE market IS NULL);
    END IF;
END $$;

ALTER TABLE jobs ALTER COLUMN market SET NOT NULL;
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_market_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_market_check
    CHECK (market IN ('CR', 'US-remote', 'LATAM-remote'));

DROP INDEX IF EXISTS idx_jobs_filters;            -- was (role_category, country, modality)
ALTER TABLE jobs DROP COLUMN IF EXISTS country;   -- also drops jobs_country_check
CREATE INDEX idx_jobs_filters ON jobs (role_category, market, modality);

-- -------------------------------------------------------------
-- 2. Allow the ATS sources
-- -------------------------------------------------------------
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_source_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_source_check
    CHECK (source IN ('jooble', 'adzuna', 'remotive', 'greenhouse', 'lever', 'ashby'));

-- -------------------------------------------------------------
-- 3. Exclusion logging per run
--    exclusion_reasons example:
--    {"out_of_scope": 120, "ambiguous_location": 15, "title_filter": 40}
-- -------------------------------------------------------------
ALTER TABLE etl_runs ADD COLUMN IF NOT EXISTS jobs_excluded     INT   DEFAULT 0;
ALTER TABLE etl_runs ADD COLUMN IF NOT EXISTS exclusion_reasons JSONB DEFAULT '{}'::jsonb;

-- -------------------------------------------------------------
-- 4. (v_skill_cooccurrence does not use country: left unchanged.)
-- 5. Views grouped by market
-- -------------------------------------------------------------

-- Demand for each tool by role, market and modality (active postings only)
CREATE VIEW v_skill_demand AS
WITH totals AS (
    SELECT role_category, market, modality, COUNT(*) AS total_jobs
    FROM jobs
    WHERE is_active
    GROUP BY role_category, market, modality
)
SELECT
    j.role_category,
    j.market,
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
                  AND t.market   = j.market
                  AND t.modality = j.modality
WHERE j.is_active
GROUP BY j.role_category, j.market, j.modality, s.skill_name, s.category, t.total_jobs;

-- Modality mix per market
CREATE VIEW v_modality_mix AS
SELECT
    market,
    modality,
    COUNT(*) AS jobs,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY market), 1) AS pct
FROM jobs
WHERE is_active
GROUP BY market, modality;

