-- Review item U8, the FX step: one row per FX event. HELD MIGRATION, after
-- u8_silver_history.sql has run in shadow. Not in migrations/postgres, so nothing applies it
-- until its approved day, when it moves there under the next free number and runs alone.
-- In the same change, silver/fx_transactions.yaml gains event_key (1.1.0).
--
-- event_key is an FX row's identity without the two amounts IBKR restates (proceeds and
-- realised P&L): fx-event:v1:<sha256 of the identity>:<occurrence in the statement>
-- (ibkr_etl.loader.fx_event_key). With IBKR_FX_EVENT_KEY=true the loader updates the row
-- with the same event_key instead of adding a second one; source_key keeps naming the
-- bronze row of the latest version. Rows stay NULL until scripts/medallion_silver_history.py
-- fx-merge fills them and merges the re-sent duplicates; each merged row stays in the
-- history as a closed version. u8_fx_event_key_undo.sql reverses this.

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
                RAISE EXCEPTION 'the FX step could not lock its tables within 5 seconds, so nothing was changed'
                    USING ERRCODE = 'lock_not_available', HINT = 'Run it again a minute later.';
            END IF;
            PERFORM pg_sleep(0.05);
        END;
    END LOOP;
END;
$$;

ALTER TABLE silver.fx_transactions ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE silver.fx_transactions_history ADD COLUMN IF NOT EXISTS event_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS fx_transactions_event_key
    ON silver.fx_transactions (account_id, event_key) WHERE event_key IS NOT NULL;
COMMENT ON COLUMN silver.fx_transactions.event_key IS
    'The FX event this row is the latest version of; NULL until the FX step fills it (review item U8).';
-- A view fixes its column list when it is created (tests/test_medallion_layers_db.py).
CREATE OR REPLACE VIEW public.fx_transactions WITH (security_invoker = true) AS SELECT * FROM silver.fx_transactions;
DO $$
DECLARE
    columns text;
BEGIN
    SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) INTO columns
      FROM pg_attribute WHERE attrelid = 'silver.fx_transactions'::regclass AND attnum > 0 AND NOT attisdropped;
    DROP FUNCTION IF EXISTS silver.fx_transactions_as_known(TIMESTAMPTZ);
    EXECUTE format(
        'CREATE FUNCTION silver.fx_transactions_as_known(at TIMESTAMPTZ) RETURNS SETOF silver.fx_transactions '
        'LANGUAGE sql STABLE AS $f$ SELECT %s FROM silver.fx_transactions_history '
        'WHERE recorded_from <= at AND (recorded_to IS NULL OR recorded_to > at) $f$', columns);
    COMMENT ON FUNCTION silver.fx_transactions_as_known(TIMESTAMPTZ) IS
        'silver.fx_transactions as it stood at the given moment (review item U8).';
END;
$$;
