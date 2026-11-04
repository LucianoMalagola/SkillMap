-- SkillMap: migration 12 — CR means located specifically in Costa Rica; "Available to CR" grouping
--
-- 1. One-time move of the 7 TheirStack postings open to several countries (they are fetched once, so the
--    new rule in etl/location.py never sees them again). The 8th, Power Digital, is re-read every run.
-- 2. market_groups: the single definition of which markets count as "Available to CR".
-- 3. The market-grouped views gain rows with market = 'Available to CR' (CR + LATAM-remote, computed per
--    posting, so percentages and n are right) and an appended is_rollup column. fn_skill_match gets them
--    from v_skill_demand_segment. Each posting has one market, so it is counted once in the rollup.

UPDATE jobs SET market = 'LATAM-remote'
WHERE source = 'theirstack' AND market = 'CR'
  AND external_id IN ('855537299', '829454095', '825509931', '800621365', '844481512', '801026578', '853753058');

-- Not a v_ view: internal, not part of the dashboard contract.
CREATE OR REPLACE VIEW market_groups AS
SELECT * FROM (VALUES
    ('CR',           'CR',              FALSE),
    ('US-remote',    'US-remote',       FALSE),
    ('LATAM-remote', 'LATAM-remote',    FALSE),
    ('CR',           'Available to CR', TRUE),
    ('LATAM-remote', 'Available to CR', TRUE)
) AS t (job_market, market, is_rollup);

COMMENT ON VIEW market_groups IS
  'Which view market(s) each jobs.market counts in. is_rollup rows repeat postings of other rows.';

CREATE OR REPLACE VIEW v_skill_demand AS
WITH scope AS (
    SELECT j.job_id, j.role_category, j.role_family, g.market, g.is_rollup, j.modality, j.is_active
    FROM jobs j
    JOIN market_groups g ON g.job_market = j.market
    WHERE j.description_is_full AND j.last_seen >= now() - INTERVAL '90 days'
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
    t.postings_total_open_now,
    sc.role_family,
    t.postings_total_90d                                               AS n,
    t.postings_total_90d >= 10                                         AS enough_data,
    sc.is_rollup
FROM scope sc
JOIN job_skills js ON js.job_id = sc.job_id
JOIN skills s      ON s.skill_id = js.skill_id
JOIN totals t      ON t.role_category = sc.role_category
                  AND t.market   = sc.market
                  AND t.modality = sc.modality
GROUP BY sc.role_category, sc.role_family, sc.market, sc.is_rollup, sc.modality, s.skill_name, s.category,
         t.postings_total_90d, t.postings_total_open_now;

CREATE OR REPLACE VIEW v_modality_mix AS
SELECT
    g.market,
    j.modality,
    COUNT(*)                                                                    AS postings_seen_90d,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY g.market), 1)     AS pct_of_market_90d,
    COUNT(*) FILTER (WHERE j.is_active)                                         AS postings_open_now,
    g.is_rollup
FROM jobs j
JOIN market_groups g ON g.job_market = j.market
WHERE j.last_seen >= now() - INTERVAL '90 days'
GROUP BY g.market, g.is_rollup, j.modality;

CREATE OR REPLACE VIEW v_posting_counts AS
SELECT
    g.market,
    j.role_category,
    COUNT(*)                                                            AS postings_seen_90d,
    COUNT(*) FILTER (WHERE j.is_active)                                 AS postings_open_now,
    COUNT(*) FILTER (WHERE j.description_is_full)                       AS postings_full_text_seen_90d,
    COUNT(*) FILTER (WHERE j.description_is_full AND j.is_active)       AS postings_full_text_open_now,
    j.role_family,
    COUNT(*) FILTER (WHERE j.open_status = 'verified')                  AS postings_open_verified,
    COUNT(*) FILTER (WHERE j.open_status = 'estimated')                 AS postings_open_estimated,
    g.is_rollup
FROM jobs j
JOIN market_groups g ON g.job_market = j.market
WHERE j.last_seen >= now() - INTERVAL '90 days'
GROUP BY g.market, g.is_rollup, j.role_category, j.role_family;

CREATE OR REPLACE VIEW v_skill_demand_segment AS
WITH scope AS (
    SELECT j.job_id, j.role_category, j.role_family, g.market, g.is_rollup
    FROM jobs j
    JOIN market_groups g ON g.job_market = j.market
    WHERE j.description_is_full AND j.last_seen >= now() - INTERVAL '90 days'
),
seg AS (
    SELECT 'role_category'::text AS grain, role_category AS segment, role_family, role_category, market, is_rollup, job_id
    FROM scope
    UNION ALL
    SELECT 'role_family', role_family, role_family, NULL, market, is_rollup, job_id
    FROM scope
),
totals AS (
    SELECT grain, segment, market, COUNT(*) AS n FROM seg GROUP BY grain, segment, market
),
mentions AS (
    SELECT seg.grain, seg.segment, seg.role_family, seg.role_category, seg.market, seg.is_rollup, js.skill_id,
           COUNT(*) AS m
    FROM seg JOIN job_skills js ON js.job_id = seg.job_id
    GROUP BY seg.grain, seg.segment, seg.role_family, seg.role_category, seg.market, seg.is_rollup, js.skill_id
)
SELECT
    m.grain,
    m.segment,
    m.role_family,
    m.role_category,
    m.market,
    s.skill_name,
    s.category                                                                       AS skill_category,
    m.m                                                                              AS postings_mentioning_90d,
    t.n                                                                              AS postings_total_90d,
    ROUND(100.0 * m.m / t.n, 1)                                                      AS pct_postings_90d,
    ROUND(100.0 * m.m / SUM(m.m) OVER (PARTITION BY m.grain, m.segment, m.market), 1) AS mention_share_pct,
    RANK() OVER (PARTITION BY m.grain, m.segment, m.market ORDER BY m.m DESC)        AS demand_rank,
    t.n,
    t.n >= 10                                                                        AS enough_data,
    m.is_rollup
FROM mentions m
JOIN totals t ON t.grain = m.grain AND t.segment = m.segment AND t.market = m.market
JOIN skills s ON s.skill_id = m.skill_id;

CREATE OR REPLACE VIEW v_weekly_postings AS
WITH weeks AS (
    SELECT generate_series(date_trunc('week', MIN(first_seen)), date_trunc('week', now()),
                           INTERVAL '1 week') AS week_start
    FROM jobs
)
SELECT
    w.week_start::date                                                               AS week_start,
    g.market,
    j.role_family,
    j.role_category,
    COUNT(*) FILTER (WHERE j.first_seen >= w.week_start
                       AND j.first_seen <  w.week_start + INTERVAL '1 week')         AS new_postings,
    COUNT(*)                                                                         AS postings_listed,
    COUNT(*) FILTER (WHERE j.description_is_full)                                    AS postings_listed_full_text,
    g.is_rollup
FROM weeks w
JOIN jobs j ON j.first_seen < w.week_start + INTERVAL '1 week' AND j.last_seen >= w.week_start
JOIN market_groups g ON g.job_market = j.market
GROUP BY w.week_start, g.market, g.is_rollup, j.role_family, j.role_category;

CREATE OR REPLACE VIEW v_weekly_skill_trend AS
WITH weeks AS (
    SELECT generate_series(date_trunc('week', MIN(first_seen)), date_trunc('week', now()),
                           INTERVAL '1 week') AS week_start
    FROM jobs
),
listed AS (
    SELECT w.week_start, j.job_id, g.market, g.is_rollup, j.role_family
    FROM weeks w
    JOIN jobs j ON j.first_seen < w.week_start + INTERVAL '1 week' AND j.last_seen >= w.week_start
    JOIN market_groups g ON g.job_market = j.market
    WHERE j.description_is_full
),
totals AS (
    SELECT week_start, market, role_family, COUNT(*) AS n FROM listed GROUP BY 1, 2, 3
)
SELECT
    l.week_start::date                                                                AS week_start,
    l.market,
    l.role_family,
    s.skill_name,
    s.category                                                                        AS skill_category,
    COUNT(*)                                                                          AS postings_mentioning,
    t.n                                                                               AS postings_listed_full_text,
    ROUND(100.0 * COUNT(*) / t.n, 1)                                                  AS pct_postings,
    RANK() OVER (PARTITION BY l.week_start, l.market, l.role_family ORDER BY COUNT(*) DESC) AS rank_in_week,
    t.n,
    t.n >= 10                                                                         AS enough_data,
    l.is_rollup
FROM listed l
JOIN job_skills js ON js.job_id = l.job_id
JOIN skills s      ON s.skill_id = js.skill_id
JOIN totals t      ON t.week_start = l.week_start AND t.market = l.market AND t.role_family = l.role_family
GROUP BY l.week_start, l.market, l.is_rollup, l.role_family, s.skill_name, s.category, t.n;

-- One row per posting already, so a flag instead of extra rows.
CREATE OR REPLACE VIEW v_postings AS
SELECT
    j.job_id,
    j.source,
    j.company,
    j.title,
    j.role_family,
    j.role_category,
    j.market,
    j.modality,
    j.location_raw,
    j.salary_min,
    j.salary_max,
    j.salary_currency,
    CASE WHEN j.salary_min IS NOT NULL AND j.salary_max IS NOT NULL
         THEN ROUND((j.salary_min + j.salary_max) / 2, 0)
         ELSE COALESCE(j.salary_min, j.salary_max) END                         AS salary_mid,
    j.description_is_full,
    j.posted_at,
    j.first_seen,
    j.last_seen,
    j.is_active                                                                AS open_now,
    j.last_seen >= now() - INTERVAL '90 days'                                  AS seen_90d,
    (j.last_seen::date - j.first_seen::date) + 1                               AS days_listed,
    COALESCE(length(j.description), 0)                                         AS description_length,
    (SELECT COUNT(*) FROM job_skills js WHERE js.job_id = j.job_id)            AS n_tools,
    j.url,
    j.open_status,
    j.market IN ('CR', 'LATAM-remote')                                         AS available_to_cr
FROM jobs j;

CREATE OR REPLACE VIEW v_posting_skills AS
SELECT
    j.job_id,
    s.skill_name,
    s.category                                     AS skill_category,
    j.role_family,
    j.role_category,
    j.market,
    j.modality,
    j.source,
    j.description_is_full,
    j.is_active                                    AS open_now,
    j.last_seen >= now() - INTERVAL '90 days'      AS seen_90d,
    j.first_seen,
    j.last_seen,
    j.open_status,
    j.market IN ('CR', 'LATAM-remote')             AS available_to_cr
FROM job_skills js
JOIN jobs j   ON j.job_id = js.job_id
JOIN skills s ON s.skill_id = js.skill_id;

-- A new output column means a new signature for Postgres. Nothing depends on the function;
-- R_dashboard_reader.sql grants EXECUTE again after every migrate.
DROP FUNCTION IF EXISTS fn_skill_match(TEXT[], TEXT);
CREATE FUNCTION fn_skill_match(my_skills TEXT[], match_grain TEXT DEFAULT 'role_category')
RETURNS TABLE (
    grain               TEXT,
    segment             TEXT,
    role_family         TEXT,
    market              TEXT,
    n                   BIGINT,
    enough_data         BOOLEAN,
    match_pct           NUMERIC,
    skills_matched      TEXT[],
    top_missing_skills  TEXT[],
    is_rollup           BOOLEAN
)
LANGUAGE sql STABLE AS $$
    WITH d AS (
        SELECT v.*, lower(v.skill_name) = ANY (SELECT lower(x) FROM unnest(my_skills) AS x) AS mine
        FROM v_skill_demand_segment v
        WHERE v.grain = match_grain
    )
    SELECT d.grain, d.segment, d.role_family, d.market, MAX(d.n), BOOL_AND(d.enough_data),
           ROUND(100.0 * COALESCE(SUM(d.postings_mentioning_90d) FILTER (WHERE d.mine), 0)
                 / SUM(d.postings_mentioning_90d), 1),
           COALESCE(ARRAY_AGG(d.skill_name ORDER BY d.postings_mentioning_90d DESC, d.skill_name)
                    FILTER (WHERE d.mine), '{}'),
           COALESCE((ARRAY_AGG(d.skill_name ORDER BY d.postings_mentioning_90d DESC, d.skill_name)
                    FILTER (WHERE NOT d.mine))[1:5], '{}'),
           d.is_rollup
    FROM d
    GROUP BY d.grain, d.segment, d.role_family, d.market, d.is_rollup
    ORDER BY d.market, d.segment
$$;

COMMENT ON FUNCTION fn_skill_match(TEXT[], TEXT) IS
  'Skill match per segment and market. match_pct = share of tool requests in the segment''s full-description postings (last 90 days) covered by my_skills (case-insensitive tool names). top_missing_skills = the 5 most-requested tools you do not have. match_grain: role_category (default) or role_family. Rows with is_rollup = TRUE are the "Available to CR" market (CR + LATAM-remote, each posting once): one row per market, so read them on their own and never add them to the others.';

COMMENT ON COLUMN v_skill_demand.market IS 'Label: "Market". CR, US-remote, LATAM-remote, or the grouping Available to CR (see is_rollup).';
COMMENT ON COLUMN v_skill_demand.is_rollup IS
  'Label: "Is a grouping". TRUE on Available to CR rows, which repeat CR and LATAM-remote postings. Filter is_rollup = FALSE by default; summing across markets otherwise double-counts.';
COMMENT ON COLUMN v_modality_mix.market IS 'Label: "Market". CR, US-remote, LATAM-remote, or the grouping Available to CR (see is_rollup).';
COMMENT ON COLUMN v_modality_mix.is_rollup IS
  'Label: "Is a grouping". TRUE on Available to CR rows, which repeat CR and LATAM-remote postings. Filter is_rollup = FALSE by default; summing across markets otherwise double-counts.';
COMMENT ON COLUMN v_posting_counts.market IS 'Label: "Market". CR, US-remote, LATAM-remote, or the grouping Available to CR (see is_rollup).';
COMMENT ON COLUMN v_posting_counts.is_rollup IS
  'Label: "Is a grouping". TRUE on Available to CR rows, which repeat CR and LATAM-remote postings. Filter is_rollup = FALSE by default; summing across markets otherwise double-counts.';
COMMENT ON COLUMN v_skill_demand_segment.market IS 'Label: "Market". CR, US-remote, LATAM-remote, or the grouping Available to CR (see is_rollup).';
COMMENT ON COLUMN v_skill_demand_segment.is_rollup IS
  'Label: "Is a grouping". TRUE on Available to CR rows, which repeat CR and LATAM-remote postings. Filter is_rollup = FALSE by default; summing across markets otherwise double-counts.';
COMMENT ON COLUMN v_skill_demand_segment.postings_mentioning_90d IS
  'Label: "Postings mentioning the tool (last 90 days)". Additive across segments of one grain, with is_rollup = FALSE.';
COMMENT ON COLUMN v_weekly_postings.market IS 'Label: "Market". CR, US-remote, LATAM-remote, or the grouping Available to CR (see is_rollup).';
COMMENT ON COLUMN v_weekly_postings.is_rollup IS
  'Label: "Is a grouping". TRUE on Available to CR rows, which repeat CR and LATAM-remote postings. Filter is_rollup = FALSE by default; summing across markets otherwise double-counts.';
COMMENT ON VIEW v_weekly_postings IS
  'Postings per ISO week (Monday start, UTC), market and role. All sources. A posting is "listed" in every week between its first_seen and last_seen. Additive across markets and roles when is_rollup = FALSE.';
COMMENT ON COLUMN v_weekly_skill_trend.market IS 'Label: "Market". CR, US-remote, LATAM-remote, or the grouping Available to CR (see is_rollup).';
COMMENT ON COLUMN v_weekly_skill_trend.is_rollup IS
  'Label: "Is a grouping". TRUE on Available to CR rows, which repeat CR and LATAM-remote postings. Filter is_rollup = FALSE by default; summing across markets otherwise double-counts.';
COMMENT ON VIEW v_weekly_skill_trend IS
  'Tool demand per ISO week, market and role family. Full-description postings listed during the week. postings_mentioning and n are additive across markets/families when is_rollup = FALSE; percentages are not.';
COMMENT ON COLUMN v_postings.available_to_cr IS
  'Label: "Available to Costa Rica candidates". TRUE when market is CR or LATAM-remote. One row per posting, so counting it never double-counts.';
COMMENT ON COLUMN v_posting_skills.available_to_cr IS
  'Label: "Available to Costa Rica candidates". TRUE when market is CR or LATAM-remote.';
