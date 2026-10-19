-- Undo of institutional_holdings.sql (its numbered file on its day). Drops the silver table
-- institutional_holdings, its public compatibility view and with them the index, policy, comments
-- and grants. Nothing else changes: macro_series (macro_series.sql, with its own undo) is untouched,
-- and the raw filings stay in bronze-market-data (sec-13f/), so the table can be loaded again.
-- Turning the job off (INSTITUTIONAL_HOLDINGS_LOAD=off) is the lighter undo.
-- Applied like the migration: as its own numbered file with --only-migration, or by psql. If the
-- migration was recorded in schema_migrations and should run again later, delete that row by hand too.

SET LOCAL lock_timeout = '5s';

-- The view in public goes first (it reads the silver table), then the table wherever it is.
DO $$
DECLARE
    kind "char";
BEGIN
    SELECT relkind INTO kind FROM pg_class WHERE oid = to_regclass('public.institutional_holdings');
    IF kind = 'v' THEN
        DROP VIEW public.institutional_holdings;
    ELSIF kind IS NOT NULL THEN
        DROP TABLE public.institutional_holdings;  -- a database without the silver schema
    END IF;
    IF to_regnamespace('silver') IS NOT NULL THEN
        DROP TABLE IF EXISTS silver.institutional_holdings;
    END IF;
END;
$$;
