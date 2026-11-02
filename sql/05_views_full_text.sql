-- =============================================================
-- SkillMap: migration 05 — tool views use full descriptions only
-- Run after 04. Safe to re-run (CREATE OR REPLACE VIEW, same columns).
--
-- Why: Adzuna and Jooble descriptions are ~500-character snippets. Counting them in
-- tool demand undercounts every tool: a snippet that doesn't mention SQL isn't a posting
-- that doesn't ask for SQL. Tool views now use only description_is_full = TRUE, in the
-- numerator AND the denominator (total_jobs). Volume views (v_modality_mix) keep all rows.
-- =============================================================


-- Demand for each tool by role, market and modality (active, full-description postings)
CREATE OR REPLACE VIEW v_skill_demand AS
WITH totals AS (
    SELECT role_category, market, modality, COUNT(*) AS total_jobs
    FROM jobs
    WHERE is_active AND description_is_full
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
WHERE j.is_active AND j.description_is_full
GROUP BY j.role_category, j.market, j.modality, s.skill_name, s.category, t.total_jobs;

-- Co-occurrence: "if they ask for X, how often also Y?" (full-description postings only)
CREATE OR REPLACE VIEW v_skill_cooccurrence AS
SELECT
    sa.skill_name AS skill_a,
    sb.skill_name AS skill_b,
    COUNT(*)      AS jobs_with_both
FROM job_skills a
JOIN job_skills b ON a.job_id = b.job_id AND a.skill_id < b.skill_id
JOIN skills sa    ON sa.skill_id = a.skill_id
JOIN skills sb    ON sb.skill_id = b.skill_id
JOIN jobs j       ON j.job_id = a.job_id
WHERE j.is_active AND j.description_is_full
GROUP BY sa.skill_name, sb.skill_name;

-- v_modality_mix is unchanged: it measures volume, so snippet sources stay in.

