-- =============================================================
-- SkillMap: migration 08 — role families, minimum sample size, analysis views
-- Applied by etl/migrate.py (one transaction, recorded in schema_migrations).
-- Re-runnable: IF NOT EXISTS / ON CONFLICT / CREATE OR REPLACE throughout.
--
-- 1. role_families lookup + jobs.role_family (FK keeps the pair consistent).
--    Mirrors etl/roles.py ROLE_FAMILIES; transform_load checks they match.
-- 2. etl_runs.jobs_loaded: rows loaded per run (for the "unusual drop" check).
-- 3. Minimum sample size: every tool-demand view gets n (postings in the denominator)
--    and enough_data (n >= 10). Dashboards show percentages only when enough_data.
-- 4. New views: v_roles, v_skills, v_skill_demand_segment, fn_skill_match,
--    v_weekly_postings, v_weekly_skill_trend, v_last_updated, v_postings, v_posting_skills.
--
-- Contract: view columns are only ever APPENDED (CREATE OR REPLACE VIEW enforces this).
-- Renaming or removing one is a breaking change for the dashboards (see DATA_DICTIONARY.md).
-- =============================================================

-- -------------------------------------------------------------
-- 1. Role families
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS role_families (
    role_category  TEXT PRIMARY KEY,
    role_family    TEXT NOT NULL,
    family_sort    INT  NOT NULL,
    category_sort  INT  NOT NULL,
    UNIQUE (role_category, role_family)
);

INSERT INTO role_families (role_category, role_family, family_sort, category_sort) VALUES
    ('Data Analyst',                'Data & Analytics',      1, 1),
    ('BI Analyst',                  'Data & Analytics',      1, 2),
    ('Data Reporting',              'Data & Analytics',      1, 3),
    ('Analytics & Insights',        'Data & Analytics',      1, 4),
    ('Marketing Analyst',           'Marketing & Insights',  2, 5),
    ('Business Operations Analyst', 'Business & Operations', 3, 6),
    ('Business Analyst',            'Business & Operations', 3, 7)
ON CONFLICT (role_category) DO UPDATE
    SET role_family = EXCLUDED.role_family, family_sort = EXCLUDED.family_sort,
        category_sort = EXCLUDED.category_sort;

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS role_family TEXT;
UPDATE jobs j SET role_family = rf.role_family
FROM role_families rf
WHERE rf.role_category = j.role_category AND j.role_family IS DISTINCT FROM rf.role_family;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM jobs WHERE role_category IS NULL OR role_family IS NULL) THEN
        RAISE EXCEPTION '% job(s) have no role_category or an unknown one; reprocess them first',
                        (SELECT COUNT(*) FROM jobs WHERE role_category IS NULL OR role_family IS NULL);
    END IF;
END $$;

ALTER TABLE jobs ALTER COLUMN role_category SET NOT NULL;
ALTER TABLE jobs ALTER COLUMN role_family   SET NOT NULL;
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_role_family_fk;
ALTER TABLE jobs ADD CONSTRAINT jobs_role_family_fk FOREIGN KEY (role_category, role_family)
    REFERENCES role_families (role_category, role_family) ON UPDATE CASCADE;

-- -------------------------------------------------------------
-- 2. Rows loaded per run
-- -------------------------------------------------------------
ALTER TABLE etl_runs ADD COLUMN IF NOT EXISTS jobs_loaded INT;
COMMENT ON COLUMN etl_runs.jobs_loaded IS 'In-scope postings upserted by this run (NULL for runs before migration 08).';

-- -------------------------------------------------------------
-- 3. Existing views: append role_family / n / enough_data
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW v_skill_demand AS
WITH scope AS (
    SELECT job_id, role_category, role_family, market, modality, is_active
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
    t.postings_total_open_now,
    sc.role_family,
    t.postings_total_90d                                               AS n,
    t.postings_total_90d >= 10                                         AS enough_data
FROM scope sc
JOIN job_skills js ON js.job_id = sc.job_id
JOIN skills s      ON s.skill_id = js.skill_id
JOIN totals t      ON t.role_category = sc.role_category
                  AND t.market   = sc.market
                  AND t.modality = sc.modality
GROUP BY sc.role_category, sc.role_family, sc.market, sc.modality, s.skill_name, s.category,
         t.postings_total_90d, t.postings_total_open_now;

COMMENT ON COLUMN v_skill_demand.role_family IS
  'Label: "Role family". Group of related role categories (see v_roles).';
COMMENT ON COLUMN v_skill_demand.n IS
  'Label: "Sample size (postings)". Same as postings_total_90d: the denominator of pct_postings_90d.';
COMMENT ON COLUMN v_skill_demand.enough_data IS
  'Label: "Enough data". TRUE when n >= 10. Show percentages only when TRUE.';

-- Co-occurrence becomes directed (both (A,B) and (B,A)) so a dashboard can filter on one
-- tool and read "of postings asking for A, % that also ask for B".
CREATE OR REPLACE VIEW v_skill_cooccurrence AS
WITH scope AS (
    SELECT js.job_id, js.skill_id, j.is_active
    FROM job_skills js
    JOIN jobs j ON j.job_id = js.job_id
    WHERE j.description_is_full AND j.last_seen >= now() - INTERVAL '90 days'
),
per_skill AS (
    SELECT skill_id, COUNT(*) AS postings_with_a_90d FROM scope GROUP BY skill_id
)
SELECT
    sa.skill_name                                                   AS skill_a,
    sb.skill_name                                                   AS skill_b,
    COUNT(*)                                                        AS postings_with_both_90d,
    COUNT(*) FILTER (WHERE a.is_active)                             AS postings_with_both_open_now,
    p.postings_with_a_90d,
    ROUND(100.0 * COUNT(*) / p.postings_with_a_90d, 1)              AS pct_with_b_given_a_90d,
    p.postings_with_a_90d                                           AS n,
    p.postings_with_a_90d >= 10                                     AS enough_data
FROM scope a
JOIN scope b      ON b.job_id = a.job_id AND b.skill_id <> a.skill_id
JOIN skills sa    ON sa.skill_id = a.skill_id
JOIN skills sb    ON sb.skill_id = b.skill_id
JOIN per_skill p  ON p.skill_id = a.skill_id
GROUP BY sa.skill_name, sb.skill_name, p.postings_with_a_90d;

COMMENT ON VIEW v_skill_cooccurrence IS
  'Tools asked for in the same posting, directed: one row for (A,B) and one for (B,A). Full-description postings seen in the last 90 days. Filter skill_a to read "postings asking for A that also ask for B".';
COMMENT ON COLUMN v_skill_cooccurrence.skill_a IS 'Label: "Tool".';
COMMENT ON COLUMN v_skill_cooccurrence.skill_b IS 'Label: "Also asked for".';
COMMENT ON COLUMN v_skill_cooccurrence.postings_with_both_90d IS
  'Label: "Postings asking for both (last 90 days)".';
COMMENT ON COLUMN v_skill_cooccurrence.postings_with_both_open_now IS
  'Label: "Open postings asking for both".';
COMMENT ON COLUMN v_skill_cooccurrence.postings_with_a_90d IS
  'Label: "Postings asking for the tool (last 90 days)". Denominator of pct_with_b_given_a_90d.';
COMMENT ON COLUMN v_skill_cooccurrence.pct_with_b_given_a_90d IS
  'Label: "% that also ask for it (last 90 days)". postings_with_both_90d / postings_with_a_90d.';
COMMENT ON COLUMN v_skill_cooccurrence.n IS
  'Label: "Sample size (postings)". Same as postings_with_a_90d.';
COMMENT ON COLUMN v_skill_cooccurrence.enough_data IS
  'Label: "Enough data". TRUE when n >= 10.';

CREATE OR REPLACE VIEW v_posting_counts AS
SELECT
    market,
    role_category,
    COUNT(*)                                                            AS postings_seen_90d,
    COUNT(*) FILTER (WHERE is_active)                                   AS postings_open_now,
    COUNT(*) FILTER (WHERE description_is_full)                         AS postings_full_text_seen_90d,
    COUNT(*) FILTER (WHERE description_is_full AND is_active)           AS postings_full_text_open_now,
    role_family
FROM jobs
WHERE last_seen >= now() - INTERVAL '90 days'
GROUP BY market, role_category, role_family;

COMMENT ON COLUMN v_posting_counts.market IS 'Label: "Market". CR, US-remote or LATAM-remote.';
COMMENT ON COLUMN v_posting_counts.role_category IS 'Label: "Role".';
COMMENT ON COLUMN v_posting_counts.role_family IS 'Label: "Role family".';

-- -------------------------------------------------------------
-- 4a. Dimensions
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW v_roles AS
SELECT role_family, role_category, family_sort, category_sort
FROM role_families;

COMMENT ON VIEW v_roles IS 'Role categories and the family each belongs to. Use as a dimension / for sort order.';
COMMENT ON COLUMN v_roles.role_family IS 'Label: "Role family".';
COMMENT ON COLUMN v_roles.role_category IS 'Label: "Role".';
COMMENT ON COLUMN v_roles.family_sort IS 'Sort order for role_family.';
COMMENT ON COLUMN v_roles.category_sort IS 'Sort order for role_category.';

CREATE OR REPLACE VIEW v_skills AS
SELECT
    s.skill_name,
    s.category                                                                   AS skill_category,
    COUNT(DISTINCT j.job_id)                                                     AS postings_mentioning_90d
FROM skills s
LEFT JOIN job_skills js ON js.skill_id = s.skill_id
LEFT JOIN jobs j        ON j.job_id = js.job_id
                       AND j.description_is_full AND j.last_seen >= now() - INTERVAL '90 days'
GROUP BY s.skill_name, s.category;

COMMENT ON VIEW v_skills IS
  'Every tool in the dictionary, including ones no posting mentions yet. Use as the "My skills" slicer table for the skill-match page.';
COMMENT ON COLUMN v_skills.skill_name IS 'Label: "Tool".';
COMMENT ON COLUMN v_skills.skill_category IS 'Label: "Tool category" (BI, Database, Language, Cloud, ...).';
COMMENT ON COLUMN v_skills.postings_mentioning_90d IS
  'Label: "Postings mentioning the tool (last 90 days)". Full-description postings, all roles and markets.';

-- -------------------------------------------------------------
-- 4b. Tool demand per segment, at two grains (role category / role family) x market.
--     Base of the skill-match page. Additive columns: postings_mentioning_90d can be summed
--     across segments of the same grain; percentages cannot.
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW v_skill_demand_segment AS
WITH scope AS (
    SELECT job_id, role_category, role_family, market
    FROM jobs
    WHERE description_is_full AND last_seen >= now() - INTERVAL '90 days'
),
seg AS (
    SELECT 'role_category'::text AS grain, role_category AS segment, role_family, role_category, market, job_id
    FROM scope
    UNION ALL
    SELECT 'role_family', role_family, role_family, NULL, market, job_id
    FROM scope
),
totals AS (
    SELECT grain, segment, market, COUNT(*) AS n FROM seg GROUP BY grain, segment, market
),
mentions AS (
    SELECT seg.grain, seg.segment, seg.role_family, seg.role_category, seg.market, js.skill_id,
           COUNT(*) AS m
    FROM seg JOIN job_skills js ON js.job_id = seg.job_id
    GROUP BY seg.grain, seg.segment, seg.role_family, seg.role_category, seg.market, js.skill_id
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
    t.n >= 10                                                                        AS enough_data
FROM mentions m
JOIN totals t ON t.grain = m.grain AND t.segment = m.segment AND t.market = m.market
JOIN skills s ON s.skill_id = m.skill_id;

COMMENT ON VIEW v_skill_demand_segment IS
  'Tool demand per segment = (role category or role family) x market, all modalities. Full-description postings seen in the last 90 days. Filter grain to one value: rows of the two grains overlap.';
COMMENT ON COLUMN v_skill_demand_segment.grain IS
  'Which grouping the row belongs to: ''role_category'' or ''role_family''. Always filter to one.';
COMMENT ON COLUMN v_skill_demand_segment.segment IS
  'Label: "Role / role family". The role category (grain = role_category) or role family (grain = role_family).';
COMMENT ON COLUMN v_skill_demand_segment.role_family IS 'Label: "Role family".';
COMMENT ON COLUMN v_skill_demand_segment.role_category IS
  'Label: "Role". NULL at grain = role_family.';
COMMENT ON COLUMN v_skill_demand_segment.market IS 'Label: "Market".';
COMMENT ON COLUMN v_skill_demand_segment.skill_name IS 'Label: "Tool".';
COMMENT ON COLUMN v_skill_demand_segment.skill_category IS 'Label: "Tool category".';
COMMENT ON COLUMN v_skill_demand_segment.postings_mentioning_90d IS
  'Label: "Postings mentioning the tool (last 90 days)". Additive across segments of one grain.';
COMMENT ON COLUMN v_skill_demand_segment.postings_total_90d IS
  'Label: "Postings in segment (last 90 days)". Denominator of pct_postings_90d.';
COMMENT ON COLUMN v_skill_demand_segment.pct_postings_90d IS
  'Label: "% of postings asking for the tool (last 90 days)".';
COMMENT ON COLUMN v_skill_demand_segment.mention_share_pct IS
  'Label: "Share of all tool requests (%)". This tool''s mentions / all tool mentions in the segment. Sums to ~100 per segment; the skill-match % is the sum of this over the tools you have.';
COMMENT ON COLUMN v_skill_demand_segment.demand_rank IS
  'Label: "Rank in segment". 1 = most requested tool (ties share a rank).';
COMMENT ON COLUMN v_skill_demand_segment.n IS
  'Label: "Sample size (postings)". Same as postings_total_90d.';
COMMENT ON COLUMN v_skill_demand_segment.enough_data IS
  'Label: "Enough data". TRUE when n >= 10.';

-- -------------------------------------------------------------
-- 4c. Skill match: fn_skill_match(ARRAY['Excel','SQL',...], grain)
--     match_pct = share of all tool requests in the segment's postings that your tools cover
--               = SUM(mentions of your tools) / SUM(mentions of all tools).
--     For SQL / notebooks / Data Studio custom queries. Power BI computes the same thing
--     with DAX over v_skill_demand_segment + v_skills (see docs/DATA_DICTIONARY.md).
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_skill_match(my_skills TEXT[], match_grain TEXT DEFAULT 'role_category')
RETURNS TABLE (
    grain               TEXT,
    segment             TEXT,
    role_family         TEXT,
    market              TEXT,
    n                   BIGINT,
    enough_data         BOOLEAN,
    match_pct           NUMERIC,
    skills_matched      TEXT[],
    top_missing_skills  TEXT[]
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
                    FILTER (WHERE NOT d.mine))[1:5], '{}')
    FROM d
    GROUP BY d.grain, d.segment, d.role_family, d.market
    ORDER BY d.market, d.segment
$$;

COMMENT ON FUNCTION fn_skill_match(TEXT[], TEXT) IS
  'Skill match per segment and market. match_pct = share of tool requests in the segment''s full-description postings (last 90 days) covered by my_skills (case-insensitive tool names). top_missing_skills = the 5 most-requested tools you do not have. match_grain: role_category (default) or role_family.';

-- -------------------------------------------------------------
-- 4d. Weekly trend (ISO weeks, Monday start, UTC)
--     A posting counts as listed in a week if first_seen <= week end and last_seen >= week start.
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW v_weekly_postings AS
WITH weeks AS (
    SELECT generate_series(date_trunc('week', MIN(first_seen)), date_trunc('week', now()),
                           INTERVAL '1 week') AS week_start
    FROM jobs
)
SELECT
    w.week_start::date                                                               AS week_start,
    j.market,
    j.role_family,
    j.role_category,
    COUNT(*) FILTER (WHERE j.first_seen >= w.week_start
                       AND j.first_seen <  w.week_start + INTERVAL '1 week')         AS new_postings,
    COUNT(*)                                                                         AS postings_listed,
    COUNT(*) FILTER (WHERE j.description_is_full)                                    AS postings_listed_full_text
FROM weeks w
JOIN jobs j ON j.first_seen < w.week_start + INTERVAL '1 week' AND j.last_seen >= w.week_start
GROUP BY w.week_start, j.market, j.role_family, j.role_category;

COMMENT ON VIEW v_weekly_postings IS
  'Postings per ISO week (Monday start, UTC), market and role. All sources. A posting is "listed" in every week between its first_seen and last_seen. Additive across markets and roles.';
COMMENT ON COLUMN v_weekly_postings.week_start IS 'Label: "Week (starting Monday)".';
COMMENT ON COLUMN v_weekly_postings.market IS 'Label: "Market".';
COMMENT ON COLUMN v_weekly_postings.role_family IS 'Label: "Role family".';
COMMENT ON COLUMN v_weekly_postings.role_category IS 'Label: "Role".';
COMMENT ON COLUMN v_weekly_postings.new_postings IS 'Label: "New postings". First seen during the week.';
COMMENT ON COLUMN v_weekly_postings.postings_listed IS 'Label: "Postings listed". Listed at any point during the week.';
COMMENT ON COLUMN v_weekly_postings.postings_listed_full_text IS
  'Label: "Postings listed (full description)". Subset used for tool trends.';

CREATE OR REPLACE VIEW v_weekly_skill_trend AS
WITH weeks AS (
    SELECT generate_series(date_trunc('week', MIN(first_seen)), date_trunc('week', now()),
                           INTERVAL '1 week') AS week_start
    FROM jobs
),
listed AS (
    SELECT w.week_start, j.job_id, j.market, j.role_family
    FROM weeks w
    JOIN jobs j ON j.first_seen < w.week_start + INTERVAL '1 week' AND j.last_seen >= w.week_start
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
    t.n >= 10                                                                         AS enough_data
FROM listed l
JOIN job_skills js ON js.job_id = l.job_id
JOIN skills s      ON s.skill_id = js.skill_id
JOIN totals t      ON t.week_start = l.week_start AND t.market = l.market AND t.role_family = l.role_family
GROUP BY l.week_start, l.market, l.role_family, s.skill_name, s.category, t.n;

COMMENT ON VIEW v_weekly_skill_trend IS
  'Tool demand per ISO week, market and role family. Full-description postings listed during the week. postings_mentioning and n are additive across markets/families; percentages are not.';
COMMENT ON COLUMN v_weekly_skill_trend.week_start IS 'Label: "Week (starting Monday)".';
COMMENT ON COLUMN v_weekly_skill_trend.market IS 'Label: "Market".';
COMMENT ON COLUMN v_weekly_skill_trend.role_family IS 'Label: "Role family".';
COMMENT ON COLUMN v_weekly_skill_trend.skill_name IS 'Label: "Tool".';
COMMENT ON COLUMN v_weekly_skill_trend.skill_category IS 'Label: "Tool category".';
COMMENT ON COLUMN v_weekly_skill_trend.postings_mentioning IS 'Label: "Postings mentioning the tool".';
COMMENT ON COLUMN v_weekly_skill_trend.postings_listed_full_text IS
  'Label: "Postings in segment". Denominator of pct_postings.';
COMMENT ON COLUMN v_weekly_skill_trend.pct_postings IS 'Label: "% of postings asking for the tool".';
COMMENT ON COLUMN v_weekly_skill_trend.rank_in_week IS 'Label: "Rank that week". 1 = most requested.';
COMMENT ON COLUMN v_weekly_skill_trend.n IS 'Label: "Sample size (postings)". Same as postings_listed_full_text.';
COMMENT ON COLUMN v_weekly_skill_trend.enough_data IS 'Label: "Enough data". TRUE when n >= 10.';

-- -------------------------------------------------------------
-- 4e. Last updated: latest successful run per source
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW v_last_updated AS
WITH last_ok AS (
    SELECT DISTINCT ON (source) source, finished_at, jobs_fetched, jobs_loaded, jobs_new, jobs_excluded
    FROM etl_runs
    WHERE status = 'success'
    ORDER BY source, finished_at DESC
),
last_any AS (
    SELECT DISTINCT ON (source) source, started_at, status
    FROM etl_runs
    ORDER BY source, started_at DESC
)
SELECT
    a.source,
    o.finished_at                                                            AS last_success_at,
    ROUND(EXTRACT(EPOCH FROM now() - o.finished_at) / 3600.0, 1)             AS hours_since_success,
    o.jobs_fetched                                                           AS last_success_jobs_fetched,
    o.jobs_loaded                                                            AS last_success_jobs_loaded,
    o.jobs_new                                                               AS last_success_jobs_new,
    o.jobs_excluded                                                          AS last_success_jobs_excluded,
    a.started_at                                                             AS last_run_at,
    a.status                                                                 AS last_run_status,
    MAX(o.finished_at) OVER ()                                               AS pipeline_last_success_at
FROM last_any a
LEFT JOIN last_ok o ON o.source = a.source;

COMMENT ON VIEW v_last_updated IS
  'Freshness per source from etl_runs. pipeline_last_success_at is the "Last updated" value for a dashboard header.';
COMMENT ON COLUMN v_last_updated.source IS 'Label: "Source".';
COMMENT ON COLUMN v_last_updated.last_success_at IS 'Label: "Last successful update".';
COMMENT ON COLUMN v_last_updated.hours_since_success IS 'Label: "Hours since last update".';
COMMENT ON COLUMN v_last_updated.last_success_jobs_fetched IS 'Label: "Postings fetched (last update)". Before any filtering.';
COMMENT ON COLUMN v_last_updated.last_success_jobs_loaded IS 'Label: "Postings loaded (last update)". In scope after filtering; NULL before migration 08.';
COMMENT ON COLUMN v_last_updated.last_success_jobs_new IS 'Label: "New postings (last update)".';
COMMENT ON COLUMN v_last_updated.last_success_jobs_excluded IS 'Label: "Postings excluded (last update)". Out of scope, not a target role, duplicates...';
COMMENT ON COLUMN v_last_updated.last_run_at IS 'Label: "Last run started".';
COMMENT ON COLUMN v_last_updated.last_run_status IS 'Label: "Last run status". success / failed / running.';
COMMENT ON COLUMN v_last_updated.pipeline_last_success_at IS 'Label: "Last updated". Latest successful run across all sources.';

-- -------------------------------------------------------------
-- 4f. EDA: one row per posting, WITHOUT the description text (personal-data rule)
-- -------------------------------------------------------------
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
    j.url
FROM jobs j;

COMMENT ON VIEW v_postings IS
  'One row per loaded posting, for EDA. All sources, all time (filter seen_90d / open_now). The description text is deliberately left out: it can contain recruiter names and emails.';
COMMENT ON COLUMN v_postings.job_id IS 'Posting key (joins v_posting_skills).';
COMMENT ON COLUMN v_postings.source IS 'Label: "Source". greenhouse, lever, ashby, recruitee, personio (ATS boards), adzuna, jooble, remotive.';
COMMENT ON COLUMN v_postings.company IS 'Label: "Company".';
COMMENT ON COLUMN v_postings.title IS 'Label: "Job title".';
COMMENT ON COLUMN v_postings.role_family IS 'Label: "Role family".';
COMMENT ON COLUMN v_postings.role_category IS 'Label: "Role".';
COMMENT ON COLUMN v_postings.market IS 'Label: "Market". CR, US-remote or LATAM-remote.';
COMMENT ON COLUMN v_postings.modality IS 'Label: "Work modality". remote, hybrid, onsite or unspecified.';
COMMENT ON COLUMN v_postings.location_raw IS 'Label: "Location (as posted)". Copies of one job in several cities are joined with " | ".';
COMMENT ON COLUMN v_postings.salary_min IS 'Label: "Salary min (annual)". Only salaries stated in the posting; hourly pay is not stored.';
COMMENT ON COLUMN v_postings.salary_max IS 'Label: "Salary max (annual)".';
COMMENT ON COLUMN v_postings.salary_currency IS 'Label: "Currency". ISO code, e.g. USD.';
COMMENT ON COLUMN v_postings.salary_mid IS 'Label: "Salary midpoint (annual)". Mean of min and max, or whichever exists.';
COMMENT ON COLUMN v_postings.description_is_full IS 'Label: "Full description". FALSE for snippet sources (Adzuna, Jooble): not used for tool demand.';
COMMENT ON COLUMN v_postings.posted_at IS 'Label: "Posted (per source)". Can be the last update date on some ATSs.';
COMMENT ON COLUMN v_postings.first_seen IS 'Label: "First seen by SkillMap".';
COMMENT ON COLUMN v_postings.last_seen IS 'Label: "Last seen by SkillMap".';
COMMENT ON COLUMN v_postings.open_now IS 'Label: "Open now". Still listed as of the latest run.';
COMMENT ON COLUMN v_postings.seen_90d IS 'Label: "Seen in the last 90 days". The window the dashboard views use.';
COMMENT ON COLUMN v_postings.days_listed IS 'Label: "Days listed". Calendar days from first_seen to last_seen, inclusive.';
COMMENT ON COLUMN v_postings.description_length IS 'Label: "Description length (characters)". Snippets are ~500.';
COMMENT ON COLUMN v_postings.n_tools IS 'Label: "Tools mentioned".';
COMMENT ON COLUMN v_postings.url IS 'Label: "Posting link". Tracking parameters removed.';

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
    j.last_seen
FROM job_skills js
JOIN jobs j   ON j.job_id = js.job_id
JOIN skills s ON s.skill_id = js.skill_id;

COMMENT ON VIEW v_posting_skills IS
  'Long format: one row per (posting, tool). Postings with no tools have no rows here (use v_postings.n_tools). Filter description_is_full = TRUE for tool demand.';
COMMENT ON COLUMN v_posting_skills.job_id IS 'Posting key (joins v_postings).';
COMMENT ON COLUMN v_posting_skills.skill_name IS 'Label: "Tool".';
COMMENT ON COLUMN v_posting_skills.skill_category IS 'Label: "Tool category".';
COMMENT ON COLUMN v_posting_skills.role_family IS 'Label: "Role family".';
COMMENT ON COLUMN v_posting_skills.role_category IS 'Label: "Role".';
COMMENT ON COLUMN v_posting_skills.market IS 'Label: "Market".';
COMMENT ON COLUMN v_posting_skills.modality IS 'Label: "Work modality".';
COMMENT ON COLUMN v_posting_skills.source IS 'Label: "Source".';
COMMENT ON COLUMN v_posting_skills.description_is_full IS 'Label: "Full description".';
COMMENT ON COLUMN v_posting_skills.open_now IS 'Label: "Open now".';
COMMENT ON COLUMN v_posting_skills.seen_90d IS 'Label: "Seen in the last 90 days".';
COMMENT ON COLUMN v_posting_skills.first_seen IS 'Label: "First seen by SkillMap".';
COMMENT ON COLUMN v_posting_skills.last_seen IS 'Label: "Last seen by SkillMap".';

-- -------------------------------------------------------------
-- 5. Labels for columns from migration 07 that had none
-- -------------------------------------------------------------
COMMENT ON COLUMN v_skill_demand.role_category IS 'Label: "Role".';
COMMENT ON COLUMN v_skill_demand.market IS 'Label: "Market". CR, US-remote or LATAM-remote.';
COMMENT ON COLUMN v_skill_demand.modality IS 'Label: "Work modality". remote, hybrid, onsite or unspecified.';
COMMENT ON COLUMN v_skill_demand.skill_name IS 'Label: "Tool".';
COMMENT ON COLUMN v_skill_demand.skill_category IS 'Label: "Tool category".';
COMMENT ON COLUMN v_modality_mix.market IS 'Label: "Market".';
COMMENT ON COLUMN v_modality_mix.modality IS 'Label: "Work modality".';
