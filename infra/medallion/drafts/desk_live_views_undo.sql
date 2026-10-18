-- Undo of desk_live_views.sql (077 on its day). Drops the silver views subject_live_executions
-- and benchmark_live_quotes and their public compatibility views; nothing else. The live tables
-- and their rows, the other live views and the gold quant table (quant_analytics.sql, with its
-- own undo) are untouched. Turning the Desk page off (DESK_PAGE=off) is the lighter undo.
-- Applied like the migration: as its own numbered file with --only-migration, or by psql. If 077
-- was recorded in schema_migrations and should run again later, delete that row by hand too.

SET LOCAL lock_timeout = '5s';

DROP VIEW IF EXISTS public.subject_live_executions;
DROP VIEW IF EXISTS public.benchmark_live_quotes;
DO $$
BEGIN
    IF to_regnamespace('silver') IS NOT NULL THEN
        DROP VIEW IF EXISTS silver.subject_live_executions;
        DROP VIEW IF EXISTS silver.benchmark_live_quotes;
    END IF;
END;
$$;
