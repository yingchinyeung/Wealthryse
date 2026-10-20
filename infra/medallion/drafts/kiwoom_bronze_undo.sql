-- Undo of kiwoom_bronze.sql (082). Drops bronze.kiwoom_responses and every stored Kiwoom answer
-- with it, so keep a copy first if any rows exist (pg_dump -t bronze.kiwoom_responses).
-- Silver is untouched; the collector stops writing bronze when KIWOOM_BRONZE is not 'on'.
-- Applied like the migration: as its own numbered file with --only-migration, or by psql.
SET LOCAL lock_timeout = '5s';
DROP TABLE IF EXISTS bronze.kiwoom_responses;
