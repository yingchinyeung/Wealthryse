-- DRAFT for review item U8, "as known on" history. NOT A MIGRATION.
-- bootstrap_postgres.py and db.py read only migrations/postgres, so nothing applies this
-- file. tests/test_as_known_on_draft_db.py applies it to a throwaway database to show the
-- design working. docs/MEDALLION_AS_KNOWN_ON.md has the design; once approved, this
-- becomes the step-1 migration (part A) and the FX step's migration (part B).
--
-- Part A changes nothing readers see. Each tracked silver table keeps its name, columns,
-- key and writers. Beside it, <table>_history keeps every version of every row:
--   valid_date     when it happened (the row's own trade, report, rate or NAV date)
--   recorded_from  when we learned this version (the loading transaction's start time)
--   recorded_to    when a newer version replaced it, or IBKR's row was removed (NULL: current)
-- A trigger on the table writes the history, whoever writes the table. A row seen again
-- unchanged (every daily pull re-sends 14 days) adds nothing. <table>_as_known(at) returns
-- the table exactly as it stood at that moment, with the table's own columns.
--
-- Recording is off until public.silver_history_settings.recording is true. The switch-on
-- step sets it in the same transaction as the backfill, so no write falls between them.
-- Only the owner can change the setting or write the history directly; the jobs' logins
-- get SELECT on the history, and their writes reach it only through the trigger.
-- Undo: set recording to false (the history stays and nothing reads it), or drop the
-- triggers, functions and _history tables (the current tables are untouched).

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.silver_history_settings (
    id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    recording BOOLEAN NOT NULL DEFAULT false,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by TEXT NOT NULL DEFAULT current_user,
    note TEXT
);
INSERT INTO public.silver_history_settings (id) VALUES (true) ON CONFLICT (id) DO NOTHING;
REVOKE ALL ON public.silver_history_settings FROM PUBLIC;
COMMENT ON TABLE public.silver_history_settings IS
    'One row. recording=true turns on the silver _history triggers (review item U8).';

CREATE OR REPLACE FUNCTION silver.history_recording() RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT COALESCE((SELECT recording FROM public.silver_history_settings WHERE id), false)
$$;

-- A date stored as text, or NULL when it is not a valid yyyy-mm-dd date. Never raises.
CREATE OR REPLACE FUNCTION silver.history_date(value TEXT) RETURNS DATE
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
        RETURN value::date;
    END IF;
    RETURN NULL;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$$;

-- The history writer. TG_ARGV[0] is the valid-date column, the rest are the key columns.
-- Never changes the row being written; returns NULL as an AFTER trigger.
CREATE OR REPLACE FUNCTION silver.record_version() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, silver, public AS $$
DECLARE
    history TEXT := format('silver.%I', TG_TABLE_NAME || '_history');
    stamp TIMESTAMPTZ := transaction_timestamp();
    run_setting TEXT := current_setting('lineage.run_id', true);
    lineage UUID;
    note TEXT := left(NULLIF(current_setting('silver_history.note', true), ''), 200);
    -- An approved step that removes rows says why: 'merged' (the FX step) or 'withdrawn'
    -- (IBKR stopped reporting the row). Anything else closes the version as 'deleted'.
    reason TEXT := current_setting('silver_history.reason', true);
    old_row JSONB;
    new_row JSONB;
    match TEXT;
BEGIN
    IF NOT silver.history_recording() THEN
        RETURN NULL;
    END IF;
    IF run_setting ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        lineage := run_setting::uuid;
    END IF;
    IF TG_OP <> 'INSERT' THEN
        old_row := to_jsonb(OLD);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_row := to_jsonb(NEW);
    END IF;
    -- Seen again with nothing changed but the run that saw it: no new version.
    IF TG_OP = 'UPDATE' AND (new_row - 'last_seen_run_id') = (old_row - 'last_seen_run_id') THEN
        RETURN NULL;
    END IF;
    IF old_row IS NOT NULL THEN
        SELECT string_agg(format('%I = ($3 ->> %L)', key, key), ' AND ')
          INTO match FROM unnest(TG_ARGV[1:]) AS key;
        EXECUTE format('UPDATE %s SET recorded_to = $1, closed_by = $2, closed_note = $4 '
                       'WHERE recorded_to IS NULL AND %s', history, match)
          USING stamp, CASE WHEN TG_OP = 'UPDATE' THEN 'restated'
                            WHEN reason IN ('merged', 'withdrawn') THEN reason ELSE 'deleted' END, old_row, note;
    END IF;
    IF new_row IS NOT NULL THEN
        EXECUTE format('INSERT INTO %s SELECT nextval(%L), $2, $3, NULL, $4, NULL, $5, $6, $7, NULL, r.* '
                       'FROM jsonb_populate_record(NULL::%s, $1) AS r',
                       history, history || '_version_id_seq', TG_RELID::regclass)
          USING new_row, silver.history_date(new_row ->> TG_ARGV[0]), stamp,
                CASE WHEN TG_OP = 'INSERT' THEN 'first' ELSE 'restated' END,
                (new_row ->> 'last_seen_run_id')::bigint, lineage, note;
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION silver.record_version() FROM PUBLIC;

-- A TRUNCATE would bypass the row trigger, so it is refused while recording is on.
CREATE OR REPLACE FUNCTION silver.refuse_truncate_while_recording() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, silver, public AS $$
BEGIN
    IF silver.history_recording() THEN
        RAISE EXCEPTION 'silver.% keeps history: TRUNCATE is refused while recording is on', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NULL;
END;
$$;

-- History rows never change, apart from closing an open version once.
CREATE OR REPLACE FUNCTION silver.refuse_history_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    closing CONSTANT text[] := ARRAY['recorded_to', 'closed_by', 'closed_note'];
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.recorded_to IS NULL AND NEW.recorded_to IS NOT NULL
       AND (to_jsonb(NEW) - closing) = (to_jsonb(OLD) - closing) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'silver.% is append-only: this change is refused', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

DO $$
DECLARE
    -- table, valid-date column, key columns (the table's primary key)
    tracked CONSTANT text[][] := ARRAY[
        ['trades', 'trade_date', 'account_id,source_key'],
        ['cash_transactions', 'effective_date', 'account_id,source_key'],
        ['fx_transactions', 'report_date', 'account_id,source_key'],
        ['fx_rates', 'rate_date', 'account_id,rate_date,from_currency,to_currency'],
        ['nav_snapshots', 'snapshot_date', 'account_id,snapshot_date,currency,asset_class']
    ];
    i int;
    rel text;
    valid text;
    keys text[];
    history text;
    columns text;
    grantee text;
    targets text;
    deadline CONSTANT timestamptz := clock_timestamp() + interval '5 seconds';
BEGIN
    -- As in 068 to 071: take every lock at once without joining the lock queue, so website
    -- queries never wait behind this; give up after 5 seconds having changed nothing.
    SELECT string_agg(format('silver.%I', tracked[n][1]), ', ') INTO targets
      FROM generate_subscripts(tracked, 1) AS n;
    LOOP
        BEGIN
            EXECUTE format('LOCK TABLE %s IN SHARE ROW EXCLUSIVE MODE NOWAIT', targets);
            EXIT;
        EXCEPTION WHEN lock_not_available THEN
            IF clock_timestamp() >= deadline THEN
                RAISE EXCEPTION 'the history step could not lock its tables within 5 seconds, so nothing was changed'
                    USING ERRCODE = 'lock_not_available', HINT = 'Run it again a minute later.';
            END IF;
            PERFORM pg_sleep(0.05);
        END;
    END LOOP;
    FOR i IN 1 .. array_length(tracked, 1) LOOP
        rel := tracked[i][1];
        valid := tracked[i][2];
        keys := string_to_array(tracked[i][3], ',');
        history := rel || '_history';
        -- History columns first, then the table's own columns in the table's order, so a
        -- column a later migration adds to both tables stays aligned.
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS silver.%I ('
            ' version_id BIGSERIAL PRIMARY KEY,'
            ' valid_date DATE,'
            ' recorded_from TIMESTAMPTZ NOT NULL,'
            ' recorded_to TIMESTAMPTZ,'
            ' opened_by TEXT NOT NULL CHECK (opened_by IN (''first'', ''restated'', ''baseline'')),'
            ' closed_by TEXT CHECK (closed_by IN (''restated'', ''merged'', ''withdrawn'', ''deleted'')),'
            ' recorded_run_id BIGINT,'
            ' lineage_run_id UUID,'
            ' opened_note TEXT,'
            ' closed_note TEXT,'
            ' LIKE silver.%I,'
            ' CHECK (recorded_to IS NULL OR recorded_to >= recorded_from),'
            ' CHECK ((recorded_to IS NULL) = (closed_by IS NULL)))', history, rel);
        EXECUTE format('CREATE UNIQUE INDEX IF NOT EXISTS %I ON silver.%I (%s) WHERE recorded_to IS NULL',
                       history || '_open', history, array_to_string(keys, ', '));
        EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON silver.%I (%s, recorded_from)',
                       history || '_key', history, array_to_string(keys, ', '));
        EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON silver.%I (account_id, valid_date)',
                       history || '_valid', history);
        EXECUTE format('COMMENT ON TABLE silver.%I IS %L', history,
                       format('Every version of silver.%s rows (review item U8). Written only by the '
                              'record_version trigger while recording is on.', rel));
        -- The same isolation as the table: by account, through silver.accounts.
        EXECUTE format('ALTER TABLE silver.%I ENABLE ROW LEVEL SECURITY', history);
        EXECUTE format('ALTER TABLE silver.%I FORCE ROW LEVEL SECURITY', history);
        EXECUTE format('DROP POLICY IF EXISTS account_isolation ON silver.%I', history);
        EXECUTE format('CREATE POLICY account_isolation ON silver.%I '
                       'USING (account_belongs_to_current_tenant(account_id)) '
                       'WITH CHECK (account_belongs_to_current_tenant(account_id))', history);
        EXECUTE format('DROP TRIGGER IF EXISTS history_append_only ON silver.%I', history);
        EXECUTE format('CREATE TRIGGER history_append_only BEFORE UPDATE OR DELETE ON silver.%I '
                       'FOR EACH ROW EXECUTE FUNCTION silver.refuse_history_change()', history);
        EXECUTE format('DROP TRIGGER IF EXISTS history_no_truncate ON silver.%I', history);
        EXECUTE format('CREATE TRIGGER history_no_truncate BEFORE TRUNCATE ON silver.%I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION silver.refuse_history_change()', history);
        -- Whoever can read the table can read its history; nobody else writes it.
        FOR grantee IN
            SELECT DISTINCT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(a.grantee)) END
              FROM pg_class c, aclexplode(c.relacl) a
             WHERE c.oid = format('silver.%I', rel)::regclass
               AND a.grantee <> c.relowner AND a.privilege_type = 'SELECT'
        LOOP
            EXECUTE format('GRANT SELECT ON silver.%I TO %s', history, grantee);
        END LOOP;
        EXECUTE format('DROP TRIGGER IF EXISTS record_version ON silver.%I', rel);
        EXECUTE format('CREATE TRIGGER record_version AFTER INSERT OR UPDATE OR DELETE ON silver.%I '
                       'FOR EACH ROW EXECUTE FUNCTION silver.record_version(%s)',
                       rel, array_to_string(array_prepend(quote_literal(valid),
                                                          ARRAY(SELECT quote_literal(k) FROM unnest(keys) k)), ', '));
        EXECUTE format('DROP TRIGGER IF EXISTS history_no_truncate ON silver.%I', rel);
        EXECUTE format('CREATE TRIGGER history_no_truncate BEFORE TRUNCATE ON silver.%I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION silver.refuse_truncate_while_recording()', rel);
        -- <table>_as_known(at): the table as it stood at that moment, with its own columns.
        SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) INTO columns
          FROM pg_attribute WHERE attrelid = format('silver.%I', rel)::regclass AND attnum > 0 AND NOT attisdropped;
        EXECUTE format(
            'CREATE OR REPLACE FUNCTION silver.%I(at TIMESTAMPTZ) RETURNS SETOF silver.%I '
            'LANGUAGE sql STABLE AS $f$ SELECT %s FROM silver.%I '
            'WHERE recorded_from <= at AND (recorded_to IS NULL OR recorded_to > at) $f$',
            rel || '_as_known', rel, columns, history);
        EXECUTE format('COMMENT ON FUNCTION silver.%I(TIMESTAMPTZ) IS %L', rel || '_as_known',
                       format('silver.%s as it stood at the given moment (review item U8).', rel));
    END LOOP;
END;
$$;

-- Part B, the FX step (a separate migration, after part A has run in shadow): an FX row's
-- identity without the amounts IBKR restates. The loader (behind IBKR_FX_EVENT_KEY=true)
-- fills it as fx-event:v1:<sha256 of the identity>:<occurrence in the statement> and
-- updates the row with the same event_key instead of adding a second one. source_key
-- keeps naming the bronze row of the latest version, so lineage still joins flex_rows.
-- The switch-on step fills event_key for existing rows from the replay and merges the
-- re-sent duplicates; each merged row stays in the history as a closed version.
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
END;
$$;
