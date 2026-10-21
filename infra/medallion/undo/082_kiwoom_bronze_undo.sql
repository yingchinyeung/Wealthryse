-- Undo of migrations/postgres/082_kiwoom_bronze.sql. Drops bronze.kiwoom_responses and every
-- stored Kiwoom answer with it, so keep a copy first if any rows exist
-- (pg_dump -t bronze.kiwoom_responses). Silver is untouched.
-- Set KIWOOM_BRONZE=false on the jobs first. Run as the migration login in one transaction
-- (psql -1 -f): it also removes 082's row from schema_migrations, so a later bootstrap applies
-- 082 again instead of trusting a table that is gone.
SET LOCAL lock_timeout = '5s';
DROP TABLE IF EXISTS bronze.kiwoom_responses;
DELETE FROM public.schema_migrations WHERE version = '082_kiwoom_bronze.sql';
