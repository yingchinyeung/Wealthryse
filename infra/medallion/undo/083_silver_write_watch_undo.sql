-- Undo of 083_silver_write_watch.sql (medallion step 3). Run bootstrap with
-- SILVER_WRITE_LOCK=off first if the lock is on, so the app login can write silver again.
-- This drops the watch triggers, their function and the watch table (what it noted is gone
-- after this). The silver_loader group stays: a role belongs to the whole server and may hold
-- grants in other databases. Bootstrap's grants to it are harmless without its members; to
-- remove it, REVOKE silver_loader FROM the loader login, DROP OWNED BY silver_loader in each
-- database, then DROP ROLE silver_loader.
-- Run it in one transaction (psql -1 -f). It also removes 083's row from schema_migrations, so a
-- later bootstrap applies 083 again.

SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    tables CONSTANT text[] := ARRAY(
        SELECT c.relname::text FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
         WHERE t.tgname = 'silver_write_watch' AND c.relnamespace = 'silver'::regnamespace ORDER BY 1);
    rel text;
    deadline CONSTANT timestamptz := clock_timestamp() + interval '5 seconds';
BEGIN
    IF cardinality(tables) > 0 THEN
        LOOP
            BEGIN
                EXECUTE 'LOCK TABLE ' || (SELECT string_agg(format('silver.%I', r), ', ') FROM unnest(tables) AS r)
                     || ' IN SHARE ROW EXCLUSIVE MODE NOWAIT';
                EXIT;
            EXCEPTION WHEN lock_not_available THEN
                IF clock_timestamp() >= deadline THEN
                    RAISE EXCEPTION 'the undo could not lock its tables within 5 seconds, so nothing was changed'
                        USING ERRCODE = 'lock_not_available', HINT = 'Run it again a minute later.';
                END IF;
                PERFORM pg_sleep(0.05);
            END;
        END LOOP;
        FOREACH rel IN ARRAY tables LOOP
            EXECUTE format('DROP TRIGGER silver_write_watch ON silver.%I', rel);
        END LOOP;
    END IF;
END;
$$;

DROP FUNCTION IF EXISTS silver.watch_write();
DROP TABLE IF EXISTS public.silver_write_watch;
DELETE FROM public.schema_migrations WHERE version = '083_silver_write_watch.sql';
