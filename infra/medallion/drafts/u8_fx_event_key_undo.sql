-- Undo of u8_fx_event_key.sql (review item U8, the FX step). In order:
--   1. take IBKR_FX_EVENT_KEY off institutional-daily (the loader refuses to run with it and no column);
--   2. scripts/medallion_silver_history.py fx-unmerge --apply puts the merged rows back from their
--      closed versions in the history, with recording on, for every tenant;
--   3. this file drops event_key, its index, and re-creates the view and the as-known function.
-- It refuses, changing nothing, while a merged row is still missing (for the tenant it runs as).

SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    deadline CONSTANT timestamptz := clock_timestamp() + interval '5 seconds';
BEGIN
    LOOP
        BEGIN
            LOCK TABLE silver.fx_transactions, silver.fx_transactions_history IN ACCESS EXCLUSIVE MODE NOWAIT;
            EXIT;
        EXCEPTION WHEN lock_not_available THEN
            IF clock_timestamp() >= deadline THEN
                RAISE EXCEPTION 'the FX undo could not lock its tables within 5 seconds, so nothing was changed'
                    USING ERRCODE = 'lock_not_available', HINT = 'Run it again a minute later.';
            END IF;
            PERFORM pg_sleep(0.05);
        END;
    END LOOP;
    IF EXISTS (
        SELECT 1 FROM (
            SELECT DISTINCT ON (account_id, source_key) account_id, source_key, closed_by
              FROM silver.fx_transactions_history
             ORDER BY account_id, source_key, recorded_from DESC, version_id DESC) v
         WHERE v.closed_by = 'merged' AND NOT EXISTS (
            SELECT 1 FROM silver.fx_transactions t WHERE t.account_id = v.account_id AND t.source_key = v.source_key)) THEN
        RAISE EXCEPTION 'merged FX rows are still missing: run scripts/medallion_silver_history.py fx-unmerge --apply first';
    END IF;
END;
$$;

DROP INDEX IF EXISTS silver.fx_transactions_event_key;
DROP FUNCTION IF EXISTS silver.fx_transactions_as_known(TIMESTAMPTZ);
-- The view selects every column, so it goes first and comes back without event_key.
DROP VIEW IF EXISTS public.fx_transactions;
ALTER TABLE silver.fx_transactions DROP COLUMN IF EXISTS event_key;
ALTER TABLE silver.fx_transactions_history DROP COLUMN IF EXISTS event_key;
CREATE VIEW public.fx_transactions WITH (security_invoker = true) AS SELECT * FROM silver.fx_transactions;
DO $$
DECLARE
    columns text;
    grantee text;
    privileges text;
BEGIN
    SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) INTO columns
      FROM pg_attribute WHERE attrelid = 'silver.fx_transactions'::regclass AND attnum > 0 AND NOT attisdropped;
    EXECUTE format(
        'CREATE FUNCTION silver.fx_transactions_as_known(at TIMESTAMPTZ) RETURNS SETOF silver.fx_transactions '
        'LANGUAGE sql STABLE AS $f$ SELECT %s FROM silver.fx_transactions_history '
        'WHERE recorded_from <= at AND (recorded_to IS NULL OR recorded_to > at) $f$', columns);
    -- The re-created view gets the table's grants back; bootstrap_postgres.py keeps them equal anyway.
    FOR grantee, privileges IN
        SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(a.grantee)) END,
               string_agg(DISTINCT a.privilege_type, ', ')
          FROM pg_class c, aclexplode(c.relacl) a
         WHERE c.oid = 'silver.fx_transactions'::regclass AND a.grantee <> c.relowner
           AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
         GROUP BY 1
    LOOP
        EXECUTE format('GRANT %s ON public.fx_transactions TO %s', privileges, grantee);
    END LOOP;
END;
$$;
