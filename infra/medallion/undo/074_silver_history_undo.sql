-- Undo of u8_silver_history.sql (review item U8). The tracked tables, their rows and their
-- readers are untouched; this drops the triggers, the as-known functions, the history tables
-- and the setting. Turning recording off is the lighter undo (the history stays, unread):
--   UPDATE public.silver_history_settings SET recording = false, changed_at = now(), changed_by = current_user;
-- Run u8_fx_event_key_undo.sql first if the FX step has run. The history is gone after this,
-- so keep a copy first if it may be wanted (pg_dump -t 'silver.*_history').
-- Applied like the migration: as its own numbered file with --only-migration, or by psql.

SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    deadline CONSTANT timestamptz := clock_timestamp() + interval '5 seconds';
BEGIN
    IF to_regclass('silver.fx_transactions_history') IS NOT NULL AND EXISTS (
        SELECT 1 FROM pg_attribute WHERE attrelid = 'silver.fx_transactions'::regclass
           AND attname = 'event_key' AND NOT attisdropped) THEN
        RAISE EXCEPTION 'the FX step is still on: run u8_fx_event_key_undo.sql first';
    END IF;
    LOOP
        BEGIN
            LOCK TABLE silver.trades, silver.cash_transactions, silver.fx_transactions, silver.fx_rates,
                silver.nav_snapshots IN SHARE ROW EXCLUSIVE MODE NOWAIT;
            EXIT;
        EXCEPTION WHEN lock_not_available THEN
            IF clock_timestamp() >= deadline THEN
                RAISE EXCEPTION 'the undo could not lock its tables within 5 seconds, so nothing was changed'
                    USING ERRCODE = 'lock_not_available', HINT = 'Run it again a minute later.';
            END IF;
            PERFORM pg_sleep(0.05);
        END;
    END LOOP;
END;
$$;

DO $$
DECLARE
    rel text;
BEGIN
    FOREACH rel IN ARRAY ARRAY['trades', 'cash_transactions', 'fx_transactions', 'fx_rates', 'nav_snapshots'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS record_version ON silver.%I', rel);
        EXECUTE format('DROP TRIGGER IF EXISTS history_no_truncate ON silver.%I', rel);
        EXECUTE format('DROP FUNCTION IF EXISTS silver.%I(TIMESTAMPTZ)', rel || '_as_known');
        EXECUTE format('DROP TABLE IF EXISTS silver.%I', rel || '_history');
    END LOOP;
END;
$$;

DROP FUNCTION IF EXISTS silver.record_version();
DROP FUNCTION IF EXISTS silver.refuse_truncate_while_recording();
DROP FUNCTION IF EXISTS silver.refuse_history_change();
DROP FUNCTION IF EXISTS silver.history_recording();
DROP FUNCTION IF EXISTS silver.history_date(TEXT);
DROP TABLE IF EXISTS public.silver_history_settings;
