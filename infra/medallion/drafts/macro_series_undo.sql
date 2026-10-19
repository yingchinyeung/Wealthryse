-- Undo of macro_series.sql (its numbered file on its day). Drops the silver table macro_series, its
-- public compatibility view and with them the policy, comments and grants. Nothing else changes:
-- risk_free_rates (HIBOR, the Treasury 3-month), market_data_prices and institutional_holdings
-- (institutional_holdings.sql, with its own undo) are untouched, and the raw responses stay in
-- bronze-market-data, so the table can be loaded again. Turning the job off (MACRO_SERIES_LOAD=off)
-- is the lighter undo.
-- Applied like the migration: as its own numbered file with --only-migration, or by psql. If the
-- migration was recorded in schema_migrations and should run again later, delete that row by hand too.

SET LOCAL lock_timeout = '5s';

-- The view in public goes first (it reads the silver table), then the table wherever it is.
DO $$
DECLARE
    kind "char";
BEGIN
    SELECT relkind INTO kind FROM pg_class WHERE oid = to_regclass('public.macro_series');
    IF kind = 'v' THEN
        DROP VIEW public.macro_series;
    ELSIF kind IS NOT NULL THEN
        DROP TABLE public.macro_series;  -- a database without the silver schema
    END IF;
    IF to_regnamespace('silver') IS NOT NULL THEN
        DROP TABLE IF EXISTS silver.macro_series;
    END IF;
END;
$$;
