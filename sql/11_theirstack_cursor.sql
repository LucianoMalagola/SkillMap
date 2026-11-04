-- SkillMap: migration 11 — where the next TheirStack run starts, per query
--
-- {"cr": "2026-11-04T11:00:00Z", "latam_remote": "..."}: the next run asks each query for postings
-- discovered at/after its timestamp. A query that finished moves to this run's start (minus an hour of
-- overlap); one stopped by a cap moves only to the last posting fetched, so nothing is skipped.
-- Written only when the run extracted and loaded successfully. NULL: older runs and other sources.

ALTER TABLE etl_runs ADD COLUMN IF NOT EXISTS source_cursor JSONB;

COMMENT ON COLUMN etl_runs.source_cursor IS
  'TheirStack: discovered_at per query where the next run starts. NULL for other sources and for runs that did not extract.';
