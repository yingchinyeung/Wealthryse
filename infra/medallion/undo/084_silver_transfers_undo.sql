-- Undo of migrations/postgres/084_silver_transfers.sql. Drops silver.transfers and its compatibility
-- view public.transfers. Bronze flex_rows keeps every Transfers record, so nothing is lost: a later
-- 084 and `python -m ibkr_etl.flows_source fill-transfers --apply` rebuild it. Set FLOWS_SOURCE off
-- (or remove it) on every app and job first. Run as the migration login in one transaction
-- (psql -1 -f): it also removes 084's row from schema_migrations, so a later bootstrap applies 084
-- again instead of trusting a table that is gone.
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF to_regclass('silver.transfers') IS NOT NULL THEN
        DROP VIEW IF EXISTS public.transfers;
        DROP TABLE silver.transfers;
    ELSE
        DROP TABLE IF EXISTS public.transfers;  -- a database without the silver schema
    END IF;
END;
$$;
DELETE FROM public.schema_migrations WHERE version = '084_silver_transfers.sql';
