-- =============================================================
-- SkillMap: migration 07 — views over "seen in the last 90 days" + "open now"
-- Run after 06. Safe to re-run (views are dropped and recreated).
--
-- Definitions used in every view (also stored as COMMENTs below):
--   seen in last 90 days : jobs.last_seen >= now() - 90 days. Includes postings that have
--                          since closed: demand over a quarter, more stable than one day.
--   open now             : jobs.is_active. Still listed as of the latest pipeline run.
-- Tool views (v_skill_demand, v_skill_cooccurrence) count only full descriptions
-- (description_is_full = TRUE); volume views (v_modality_mix, v_posting_counts) count every source.
-- Column names carry their window (_90d / _open_now) so dashboard labels can't be mixed up.
-- =============================================================

BEGIN;

DROP VIEW IF EXISTS v_skill_demand;
DROP VIEW IF EXISTS v_skill_cooccurrence;
DROP VIEW IF EXISTS v_modality_mix;
DROP VIEW IF EXISTS v_posting_counts;

-- -------------------------------------------------------------
-- Tool demand by role, market and modality (full descriptions only)
-- -------------------------------------------------------------
CREATE VIEW v_skill_demand AS
WITH scope AS (
    SELECT job_id, role_category, market, modality, is_active
    FROM jobs
    WHERE description_is_full AND last_seen >= now() - INTERVAL '90 days'
),
totals AS (
    SELECT role_category, market, modality,
           COUNT(*)                              AS postings_total_90d,
           COUNT(*) FILTER (WHERE is_active)     AS postings_total_open_now
    FROM scope
    GROUP BY role_category, market, modality
)
SELECT
    sc.role_category,
    sc.market,
    sc.modality,
    s.skill_name,
    s.category                                                         AS skill_category,
    COUNT(*)                                                           AS postings_mentioning_90d,
    t.postings_total_90d,
    ROUND(100.0 * COUNT(*) / t.postings_total_90d, 1)                  AS pct_postings_90d,
    COUNT(*) FILTER (WHERE sc.is_active)                               AS postings_mentioning_open_now,
    t.postings_total_open_now
FROM scope sc
JOIN job_skills js ON js.job_id = sc.job_id
JOIN skills s      ON s.skill_id = js.skill_id
JOIN totals t      ON t.role_category IS NOT DISTINCT FROM sc.role_category
                  AND t.market   = sc.market
                  AND t.modality = sc.modality
GROUP BY sc.role_category, sc.market, sc.modality, s.skill_name, s.category,
         t.postings_total_90d, t.postings_total_open_now;

COMMENT ON VIEW v_skill_demand IS
  'Tool demand per role/market/modality. Full-description postings only (Adzuna/Jooble snippets excluded). Window: postings seen in the last 90 days; *_open_now columns restrict to postings still listed.';
COMMENT ON COLUMN v_skill_demand.postings_mentioning_90d IS
  'Label: "Postings mentioning the tool (last 90 days)". Full-description postings seen in the last 90 days that mention this tool.';
COMMENT ON COLUMN v_skill_demand.postings_total_90d IS
  'Label: "Postings in segment (last 90 days)". Denominator for pct_postings_90d.';
COMMENT ON COLUMN v_skill_demand.pct_postings_90d IS
  'Label: "% of postings asking for the tool (last 90 days)". postings_mentioning_90d / postings_total_90d.';
COMMENT ON COLUMN v_skill_demand.postings_mentioning_open_now IS
  'Label: "Open postings mentioning the tool". Same, restricted to postings still listed (is_active).';
COMMENT ON COLUMN v_skill_demand.postings_total_open_now IS
  'Label: "Open postings in segment". Denominator for an open-now percentage (can be 0).';

-- -------------------------------------------------------------
-- Co-occurrence: "if a posting asks for X, how often also Y?" (full descriptions only)
-- -------------------------------------------------------------
CREATE VIEW v_skill_cooccurrence AS
SELECT
    sa.skill_name                                AS skill_a,
    sb.skill_name                                AS skill_b,
    COUNT(*)                                     AS postings_with_both_90d,
    COUNT(*) FILTER (WHERE j.is_active)          AS postings_with_both_open_now
FROM job_skills a
JOIN job_skills b ON a.job_id = b.job_id AND a.skill_id < b.skill_id
JOIN skills sa    ON sa.skill_id = a.skill_id
JOIN skills sb    ON sb.skill_id = b.skill_id
JOIN jobs j       ON j.job_id = a.job_id
WHERE j.description_is_full AND j.last_seen >= now() - INTERVAL '90 days'
GROUP BY sa.skill_name, sb.skill_name;

COMMENT ON VIEW v_skill_cooccurrence IS
  'Pairs of tools asked for in the same posting. Full-description postings seen in the last 90 days.';
COMMENT ON COLUMN v_skill_cooccurrence.postings_with_both_90d IS
  'Label: "Postings asking for both (last 90 days)".';
COMMENT ON COLUMN v_skill_cooccurrence.postings_with_both_open_now IS
  'Label: "Open postings asking for both".';

-- -------------------------------------------------------------
-- Work modality mix per market (all sources: this is volume, not tools)
-- -------------------------------------------------------------
CREATE VIEW v_modality_mix AS
SELECT
    market,
    modality,
    COUNT(*)                                                                    AS postings_seen_90d,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY market), 1)       AS pct_of_market_90d,
    COUNT(*) FILTER (WHERE is_active)                                           AS postings_open_now
FROM jobs
WHERE last_seen >= now() - INTERVAL '90 days'
GROUP BY market, modality;

COMMENT ON VIEW v_modality_mix IS
  'Work modality per market, all sources (incl. snippet sources). US-remote and LATAM-remote are remote by definition.';
COMMENT ON COLUMN v_modality_mix.postings_seen_90d IS 'Label: "Postings (last 90 days)".';
COMMENT ON COLUMN v_modality_mix.pct_of_market_90d IS 'Label: "% of the market''s postings (last 90 days)".';
COMMENT ON COLUMN v_modality_mix.postings_open_now IS 'Label: "Open postings now".';

-- -------------------------------------------------------------
-- Posting counts per market and role: the "open now" vs "last 90 days" headline numbers
-- -------------------------------------------------------------
CREATE VIEW v_posting_counts AS
SELECT
    market,
    role_category,
    COUNT(*)                                                            AS postings_seen_90d,
    COUNT(*) FILTER (WHERE is_active)                                   AS postings_open_now,
    COUNT(*) FILTER (WHERE description_is_full)                         AS postings_full_text_seen_90d,
    COUNT(*) FILTER (WHERE description_is_full AND is_active)           AS postings_full_text_open_now
FROM jobs
WHERE last_seen >= now() - INTERVAL '90 days'
GROUP BY market, role_category;

COMMENT ON VIEW v_posting_counts IS
  'Headline counts per market and role. All sources; the *_full_text_* columns are the subset used by the tool views.';
COMMENT ON COLUMN v_posting_counts.postings_seen_90d IS
  'Label: "Postings seen in the last 90 days" (includes postings that have since closed).';
COMMENT ON COLUMN v_posting_counts.postings_open_now IS
  'Label: "Open postings now" (still listed as of the latest run).';
COMMENT ON COLUMN v_posting_counts.postings_full_text_seen_90d IS
  'Label: "Postings with full description (last 90 days)". Base of the tool rankings.';
COMMENT ON COLUMN v_posting_counts.postings_full_text_open_now IS
  'Label: "Open postings with full description".';

COMMIT;
