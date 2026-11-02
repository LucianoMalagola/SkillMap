-- =============================================================
-- SkillMap: migration 06 — allow the Recruitee and Personio ATS sources
-- Run after 05. Safe to re-run.
--
-- Both were checked against the source compliance rule on 2026-11-02 (official,
-- documented public endpoints; robots.txt allows them): see docs/SOURCES.md.
-- SmartRecruiters, Workable, Breezy and BambooHR were rejected and are not added.
-- =============================================================

BEGIN;

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_source_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_source_check
    CHECK (source IN ('jooble', 'adzuna', 'remotive', 'greenhouse', 'lever', 'ashby', 'recruitee', 'personio'));

COMMIT;
