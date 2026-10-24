-- Medallion step 3: only the named loaders write the silver layer.
--
-- This migration only watches. It creates the silver_loader group, which
-- bootstrap_postgres.py gives the right to write silver and makes the loader
-- login a member of, and a statement trigger on every silver table that notes
-- each write made by any other login in public.silver_write_watch: who wrote, which
-- table, what kind of write, the run id and the application name. A loader
-- login and the tables' owner (migrations, the approved FX step) are not noted.
-- Nothing is refused here. Once the watch has stayed empty for a full daily
-- cycle with the loader jobs on the loader login, bootstrap with
-- SILVER_WRITE_LOCK=on takes the write rights on silver from the app login.
--
-- The watch holds names, times and counts, never row values. Undo:
-- infra/medallion/undo/083_silver_write_watch.sql.
--
-- Adding a trigger needs a lock that waits for writes to the table (reads go
-- on). Every lock is tried at once without queueing; if a daily job is writing,
-- the migration stops within 5 seconds having changed nothing (SQLSTATE 55P03)
-- and bootstrap tries it again a minute later.
SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'silver_loader') THEN
        CREATE ROLE silver_loader NOLOGIN;
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS public.silver_write_watch (
    watch_id BIGSERIAL PRIMARY KEY,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    login TEXT NOT NULL,
    relation TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')),
    lineage_run_id TEXT,
    application TEXT
);
REVOKE ALL ON TABLE public.silver_write_watch FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.silver_write_watch_watch_id_seq FROM PUBLIC;

-- session_user is the login that connected, whatever role it has set since.
CREATE OR REPLACE FUNCTION silver.watch_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF pg_has_role(session_user, 'silver_loader', 'MEMBER')
       OR pg_has_role(session_user, (SELECT relowner FROM pg_class WHERE oid = TG_RELID), 'MEMBER') THEN
        RETURN NULL;
    END IF;
    INSERT INTO public.silver_write_watch (login, relation, operation, lineage_run_id, application)
    VALUES (session_user, TG_TABLE_NAME, TG_OP, left(NULLIF(current_setting('lineage.run_id', true), ''), 64),
            left(NULLIF(current_setting('application_name', true), ''), 120));
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION silver.watch_write() FROM PUBLIC;

DO $$
DECLARE
    -- The silver tables, without the U8 history (written only by its own trigger).
    tables CONSTANT text[] := ARRAY(
        SELECT c.relname::text FROM pg_class c
         WHERE c.relnamespace = 'silver'::regnamespace AND c.relkind IN ('r', 'p')
           AND right(c.relname, 8) <> '_history'
           AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid AND t.tgname = 'silver_write_watch')
         ORDER BY 1);
    rel text;
    deadline CONSTANT timestamptz := clock_timestamp() + interval '5 seconds';
BEGIN
    IF cardinality(tables) = 0 THEN
        RETURN;  -- every table is watched already
    END IF;
    LOOP
        BEGIN
            EXECUTE 'LOCK TABLE ' || (SELECT string_agg(format('silver.%I', r), ', ') FROM unnest(tables) AS r)
                 || ' IN SHARE ROW EXCLUSIVE MODE NOWAIT';
            EXIT;
        EXCEPTION WHEN lock_not_available THEN
            IF clock_timestamp() >= deadline THEN
                RAISE EXCEPTION 'the silver tables could not be locked within 5 seconds, so no watch was added'
                    USING ERRCODE = 'lock_not_available',
                          HINT = 'Run the same migration again a minute later.';
            END IF;
            PERFORM pg_sleep(0.05);
        END;
    END LOOP;
    FOREACH rel IN ARRAY tables LOOP
        EXECUTE format('CREATE TRIGGER silver_write_watch AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE '
                       'ON silver.%I FOR EACH STATEMENT EXECUTE FUNCTION silver.watch_write()', rel);
    END LOOP;
END;
$$;
