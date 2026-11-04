-- SkillMap: migration 10 — open_status: how much we trust "open now"
--
-- 'verified'  : open, and the source is re-checked every run (ATS boards, Adzuna, Remotive, Jooble).
-- 'estimated' : open by rule only. TheirStack postings are fetched once, so they count as open for
--               30 days after posted_at (first_seen if missing), or until TheirStack reports them
--               closed. transform_load enforces this; the 14-day stale rule doesn't apply to them.
-- NULL        : closed.
-- A generated column, so it can't disagree with is_active or source.
--
-- The *_90d windows use last_seen, not is_active, and the 30-day rule doesn't touch last_seen:
-- TheirStack postings stay in the 90-day tool views whatever their open status.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS open_status TEXT GENERATED ALWAYS AS (
    CASE WHEN NOT is_active THEN NULL
         WHEN source = 'theirstack' THEN 'estimated'
         ELSE 'verified' END) STORED;

COMMENT ON COLUMN jobs.open_status IS
  'verified = open and re-checked every run; estimated = open by rule (TheirStack: 30 days after posted_at); NULL = closed.';

CREATE OR REPLACE VIEW v_posting_counts AS
SELECT
    market,
    role_category,
    COUNT(*)                                                            AS postings_seen_90d,
    COUNT(*) FILTER (WHERE is_active)                                   AS postings_open_now,
    COUNT(*) FILTER (WHERE description_is_full)                         AS postings_full_text_seen_90d,
    COUNT(*) FILTER (WHERE description_is_full AND is_active)           AS postings_full_text_open_now,
    role_family,
    COUNT(*) FILTER (WHERE open_status = 'verified')                    AS postings_open_verified,
    COUNT(*) FILTER (WHERE open_status = 'estimated')                   AS postings_open_estimated
FROM jobs
WHERE last_seen >= now() - INTERVAL '90 days'
GROUP BY market, role_category, role_family;

COMMENT ON COLUMN v_posting_counts.postings_open_now IS
  'Label: "Open postings now". postings_open_verified + postings_open_estimated.';
COMMENT ON COLUMN v_posting_counts.postings_open_verified IS
  'Label: "Open now (verified)". Open postings from sources re-checked every run.';
COMMENT ON COLUMN v_posting_counts.postings_open_estimated IS
  'Label: "Open now (estimated)". TheirStack postings counted as open for 30 days after posted_at; not re-checked.';

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
    j.open_status
FROM jobs j;

COMMENT ON COLUMN v_postings.source IS 'Label: "Source". greenhouse, lever, ashby, recruitee, personio (ATS boards), adzuna, jooble, remotive, theirstack.';
COMMENT ON COLUMN v_postings.open_now IS 'Label: "Open now". Still listed as of the latest run, or (open_status = estimated) within 30 days of posting.';
COMMENT ON COLUMN v_postings.open_status IS 'Label: "Open status". verified = re-checked every run; estimated = open by rule (TheirStack, 30 days after posting); empty = closed.';

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
    j.open_status
FROM job_skills js
JOIN jobs j   ON j.job_id = js.job_id
JOIN skills s ON s.skill_id = js.skill_id;

COMMENT ON COLUMN v_posting_skills.open_status IS 'Label: "Open status". verified = re-checked every run; estimated = open by rule (TheirStack, 30 days after posting); empty = closed.';
