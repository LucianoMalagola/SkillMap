-- SkillMap: migration 09 — TheirStack source + API usage per run
--
-- TheirStack (https://theirstack.com) was checked against the source compliance rule on
-- 2026-11-02 (docs/SOURCES.md): documented key-based API whose terms allow public display of
-- job postings and of aggregates. We request only postings that have a final_url (the
-- employer's career site or ATS page), not LinkedIn/Indeed-only listings.
--
-- etl_runs.api_units_used: API units this run spent (TheirStack credits; NULL for sources
-- without a metered quota). The authoritative monthly counter is TheirStack's own
-- /v0/billing/credit-balance, which the extractor reads before every run.

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_source_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_source_check
    CHECK (source IN ('jooble', 'adzuna', 'remotive', 'greenhouse', 'lever', 'ashby', 'recruitee', 'personio',
                      'theirstack'));

ALTER TABLE etl_runs ADD COLUMN IF NOT EXISTS api_units_used INT;
COMMENT ON COLUMN etl_runs.api_units_used IS
    'API units spent by this run (TheirStack: credits = jobs returned). NULL = source without a metered quota.';
