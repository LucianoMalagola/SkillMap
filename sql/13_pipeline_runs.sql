-- SkillMap: migration 13 — pipeline_runs: complete pipeline runs that ended successfully
--
-- etl_runs logs one row per source and can't say the whole run passed (extractors and the
-- data-quality checks fail after those rows are written). The backup schedule reads this table to
-- skip a day that already has a good run. Not exposed to the dashboards.

CREATE TABLE IF NOT EXISTS pipeline_runs (
    pipeline_run_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE pipeline_runs IS
  'One row per complete run (all sources, extraction and checks) that ended successfully. Written by etl/run_pipeline.py.';
