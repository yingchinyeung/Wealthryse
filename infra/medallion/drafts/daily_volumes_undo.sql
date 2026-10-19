-- Undo of daily_volumes.sql (phase 3, docs/PHASE3_DATA.md). Drops the silver table daily_volumes,
-- its public compatibility view and with them the policy, comments and grants. Nothing else
-- changes: market_data_prices and the bronze captures the job reads are untouched, and
-- factor_returns has its own undo. The rows can be rebuilt from bronze (load_daily_volumes.py
-- --from), so no copy is needed first. Turning the job off (DAILY_VOLUMES_LOAD=off) is the
-- lighter undo.
-- Applied like the migration: as its own numbered file with --only-migration, or by psql. If the
-- migration was recorded in schema_migrations and should run again later, delete that row by hand.

SET LOCAL lock_timeout = '5s';

-- The view in public goes first (it reads the silver table), then the table wherever it is.
DO $$
DECLARE
    kind "char";
BEGIN
    SELECT relkind INTO kind FROM pg_class WHERE oid = to_regclass('public.daily_volumes');
    IF kind = 'v' THEN
        DROP VIEW public.daily_volumes;
    ELSIF kind IS NOT NULL THEN
        DROP TABLE public.daily_volumes;  -- a database without the silver schema
    END IF;
    IF to_regnamespace('silver') IS NOT NULL THEN
        DROP TABLE IF EXISTS silver.daily_volumes;
    END IF;
END;
$$;
