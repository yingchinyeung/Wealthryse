-- Undo of migrations/postgres/082_kiwoom_bronze.sql. Drops bronze.kiwoom_responses, its
-- compatibility view public.kiwoom_responses and every stored Kiwoom answer with them, so keep
-- a copy first if any rows exist (pg_dump -t bronze.kiwoom_responses). Silver is untouched.
-- Set KIWOOM_BRONZE=false on the jobs first. Run as the migration login in one transaction
-- (psql -1 -f): it also removes 082's row from schema_migrations, so a later bootstrap applies
-- 082 again instead of trusting a table that is gone.
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF to_regclass('bronze.kiwoom_responses') IS NOT NULL THEN
        DROP VIEW IF EXISTS public.kiwoom_responses;
        DROP TABLE bronze.kiwoom_responses;
    ELSE
        DROP TABLE IF EXISTS public.kiwoom_responses;  -- a database without the bronze schema
    END IF;
END;
$$;
DELETE FROM public.schema_migrations WHERE version = '082_kiwoom_bronze.sql';
