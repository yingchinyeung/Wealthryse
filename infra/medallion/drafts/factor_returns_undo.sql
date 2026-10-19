-- Undo of factor_returns.sql (phase 3, docs/PHASE3_DATA.md). Drops the silver table factor_returns,
-- its public compatibility view and with them the policy, comments and grants. Nothing else
-- changes: the bronze captures (kenneth-french) stay, and daily_volumes has its own undo. Earlier
-- vintages that can no longer be downloaded live on only in those captures, so keep a copy first if
-- the rows may be wanted (pg_dump -t silver.factor_returns). Turning the job off
-- (FACTOR_RETURNS_LOAD=off) is the lighter undo.
-- Applied like the migration: as its own numbered file with --only-migration, or by psql. If the
-- migration was recorded in schema_migrations and should run again later, delete that row by hand.

SET LOCAL lock_timeout = '5s';

-- The view in public goes first (it reads the silver table), then the table wherever it is.
DO $$
DECLARE
    kind "char";
BEGIN
    SELECT relkind INTO kind FROM pg_class WHERE oid = to_regclass('public.factor_returns');
    IF kind = 'v' THEN
        DROP VIEW public.factor_returns;
    ELSIF kind IS NOT NULL THEN
        DROP TABLE public.factor_returns;  -- a database without the silver schema
    END IF;
    IF to_regnamespace('silver') IS NOT NULL THEN
        DROP TABLE IF EXISTS silver.factor_returns;
    END IF;
END;
$$;
