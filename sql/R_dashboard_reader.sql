-- =============================================================
-- SkillMap: read-only role for the dashboards (repeatable; etl/migrate.py runs it after
-- every migrate, so views recreated by a later migration get their grant back).
--
-- dashboard_reader can SELECT the v_* views (and EXECUTE fn_skill_match) and nothing else:
-- no tables, no writes. Views run with their owner's rights, so the role never needs
-- access to jobs / job_skills (which hold the raw descriptions).
--
-- The role is created WITHOUT a password: none is ever stored in the repo or printed.
-- Set it in the Neon console: Branch -> Roles & Databases -> dashboard_reader -> Reset password.
-- =============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_reader') THEN
        CREATE ROLE dashboard_reader LOGIN;
    END IF;
END $$;

-- CREATE ROLE defaults are already NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
-- (Neon refuses to restate SUPERUSER from a non-superuser); the audit checks them.
ALTER ROLE dashboard_reader CONNECTION LIMIT 10;
ALTER ROLE dashboard_reader SET default_transaction_read_only = on;
ALTER ROLE dashboard_reader SET statement_timeout = '60s';

DO $$
DECLARE
    r record;
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO dashboard_reader', current_database());
    GRANT USAGE ON SCHEMA public TO dashboard_reader;
    -- start from nothing on tables/sequences/functions, then grant views only
    REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM dashboard_reader;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM dashboard_reader;
    FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'v' AND c.relname LIKE 'v\_%'
    LOOP
        EXECUTE format('GRANT SELECT ON public.%I TO dashboard_reader', r.relname);
    END LOOP;
    IF to_regprocedure('public.fn_skill_match(text[], text)') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION public.fn_skill_match(TEXT[], TEXT) TO dashboard_reader;
    END IF;
END $$;
