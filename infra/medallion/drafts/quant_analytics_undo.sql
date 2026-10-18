-- Undo of quant_analytics.sql (076 on its day). Drops the gold table quant_analytics_snapshots,
-- its public compatibility view and with them the index, policy, trigger, comments and grants.
-- Nothing else changes: the Desk views (desk_live_views.sql) have their own undo. The stored
-- payloads are gone after this, so keep a copy first if they may be wanted
-- (pg_dump -t gold.quant_analytics_snapshots). Turning the job off (QUANT_ANALYTICS_BUILD=off)
-- and the pages off (QUANT_ANALYTICS_PAGES unset) is the lighter undo.
-- Applied like the migration: as its own numbered file with --only-migration, or by psql. If 076
-- was recorded in schema_migrations and should run again later, delete that row by hand too.

SET LOCAL lock_timeout = '5s';

-- The view in public goes first (it reads the gold table), then the table wherever it is.
DO $$
DECLARE
    kind "char";
BEGIN
    SELECT relkind INTO kind FROM pg_class WHERE oid = to_regclass('public.quant_analytics_snapshots');
    IF kind = 'v' THEN
        DROP VIEW public.quant_analytics_snapshots;
    ELSIF kind IS NOT NULL THEN
        DROP TABLE public.quant_analytics_snapshots;  -- a database without the gold schema
    END IF;
    IF to_regnamespace('gold') IS NOT NULL THEN
        DROP TABLE IF EXISTS gold.quant_analytics_snapshots;
    END IF;
END;
$$;
