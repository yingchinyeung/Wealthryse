-- Medallion step S3b: move the bronze tables into the bronze schema and make
-- them append-only.
--
-- SET SCHEMA copies no data. Each table keeps its rows, indexes, constraints,
-- row-level security policies, triggers, owned sequences and grants. Views and
-- functions bound to it (the agent views among them) keep working, because they
-- refer to it by id.
--
-- A view with the old name stays in public so that SQL naming public.<name>
-- keeps working. It is security_invoker, so grants and row-level security are
-- checked for whoever reads it, exactly as on the table. The table's grants,
-- column grants included, are copied onto the view, with USAGE on the schema.
--
-- A view fixes its column list when it is created. A later migration that
-- changes one of these tables names it as bronze.<table> and re-creates its view
-- in public; tests/test_medallion_layers_db.py fails otherwise. Objects already
-- moved are skipped, so the file is safe to run again. Undo:
-- scripts/medallion_layers.py undo-sql bronze. infra/medallion/catalog.json lists
-- the layer of every table.

-- Any other lock the batch needs (owned sequences, the new views) waits at
-- most 5 seconds.
SET LOCAL lock_timeout = '5s';

CREATE SCHEMA IF NOT EXISTS bronze;
REVOKE ALL ON SCHEMA bronze FROM PUBLIC;

DO $$
DECLARE
    layer CONSTANT text := 'bronze';
    moved CONSTANT text[] := ARRAY[
        'factsheet_design_files',
        'flex_rows',
        'fx_evidence_batches',
        'fx_evidence_legacy_aliases',
        'fx_evidence_observations',
        'fx_evidence_runs',
        'fx_evidence_versions',
        'live_account_values',
        'live_contracts',
        'live_daily_bars',
        'live_executions',
        'live_positions',
        'live_quotes',
        'raw_reports'
    ];
    rel text;
    kind "char";
    owner_name text;
    grant_row record;
    targets text;
    deadline CONSTANT timestamptz := clock_timestamp() + interval '5 seconds';
BEGIN
    -- Take every lock the batch needs at once, without joining the lock queue.
    -- A try that cannot get all of them straight away holds nothing, so website
    -- queries never wait behind it. After 5 seconds of trying, the batch gives up
    -- with SQLSTATE 55P03 having changed nothing, and bootstrap tries again a
    -- minute later. Locking a view also locks the tables it reads.
    SELECT string_agg(format('public.%I', r), ', ' ORDER BY r) INTO targets
      FROM unnest(moved) AS r
     WHERE to_regclass(format('public.%I', r)) IS NOT NULL
       AND to_regclass(format('%I.%I', layer, r)) IS NULL;
    WHILE targets IS NOT NULL LOOP
        BEGIN
            EXECUTE format('LOCK TABLE %s IN ACCESS EXCLUSIVE MODE NOWAIT', targets);
            EXIT;
        EXCEPTION WHEN lock_not_available THEN
            IF clock_timestamp() >= deadline THEN
                RAISE EXCEPTION 'the % batch could not lock its tables within 5 seconds, so nothing was moved', layer
                    USING ERRCODE = 'lock_not_available',
                          HINT = 'Run the same migration again a minute later.';
            END IF;
            PERFORM pg_sleep(0.05);
        END;
    END LOOP;
    FOREACH rel IN ARRAY moved LOOP
        IF to_regclass(format('%I.%I', layer, rel)) IS NOT NULL THEN
            CONTINUE;  -- already moved
        END IF;
        SELECT c.relkind, pg_get_userbyid(c.relowner) INTO kind, owner_name
          FROM pg_class c WHERE c.oid = to_regclass(format('public.%I', rel));
        IF kind IS NULL THEN
            RAISE NOTICE 'public.% does not exist, so it is not moved', rel;
            CONTINUE;
        END IF;
        EXECUTE format('ALTER %s public.%I SET SCHEMA %I',
                       CASE WHEN kind = 'v' THEN 'VIEW' ELSE 'TABLE' END, rel, layer);
        EXECUTE format('CREATE VIEW public.%I WITH (security_invoker = true) AS SELECT * FROM %I.%I',
                       rel, layer, rel);
        IF owner_name <> current_user THEN
            EXECUTE format('ALTER VIEW public.%I OWNER TO %I', rel, owner_name);
        END IF;
        EXECUTE format('COMMENT ON VIEW public.%I IS %L', rel,
                       format('Compatibility view over %I.%I (medallion layer).', layer, rel));
        -- Whoever could use the table can use the view and reach the schema.
        FOR grant_row IN
            SELECT a.grantee, a.privilege_type, a.is_grantable, NULL::text AS column_name
              FROM pg_class c, aclexplode(c.relacl) a
             WHERE c.oid = to_regclass(format('%I.%I', layer, rel))
               AND a.grantee <> c.relowner
               AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
            UNION ALL
            SELECT a.grantee, a.privilege_type, a.is_grantable, att.attname::text
              FROM pg_attribute att, aclexplode(att.attacl) a
             WHERE att.attrelid = to_regclass(format('%I.%I', layer, rel))
               AND att.attnum > 0 AND NOT att.attisdropped
               AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE')
        LOOP
            EXECUTE format('GRANT %s%s ON public.%I TO %s%s',
                grant_row.privilege_type,
                CASE WHEN grant_row.column_name IS NULL THEN ''
                     ELSE format(' (%I)', grant_row.column_name) END,
                rel,
                CASE WHEN grant_row.grantee = 0 THEN 'PUBLIC'
                     ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END,
                CASE WHEN grant_row.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO %s', layer,
                CASE WHEN grant_row.grantee = 0 THEN 'PUBLIC'
                     ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END);
        END LOOP;
    END LOOP;
END;
$$;

-- Bronze is append-only. Each bronze table refuses UPDATE, DELETE and TRUNCATE,
-- except the changes today's code makes:
--   flex_rows: the loader's upsert (src/ibkr_etl/loader.py) stamps last_seen_run_id
--     and replaces the content columns when IBKR re-sends a record under the same key
--     (an FX rate correction, for example). The key, the row id and first_seen_run_id
--     never change. Two loads can overlap, so last_seen_run_id may also go back.
--   live_executions: the collector fills in commission and realized P&L when the
--     commission report arrives after the execution.
--   live_contracts and live_daily_bars: the collector's upserts refresh contract
--     details and revise a day's bar; the key columns never change.
--   Retention (src/ibkr_etl/live_collector.py purge): live_account_values,
--     live_positions (by cascade) and live_quotes rows older than 29 days, and
--     live_executions rows older than 365 days, may be deleted. The collector keeps
--     30 and 366 days; the extra day absorbs clock differences.
-- The other bronze tables only gain rows (factsheet_design_files, for example, is
-- written with INSERT ... ON CONFLICT DO NOTHING, which needs no exception).
-- The table owner can still disable a trigger for an approved repair.

CREATE OR REPLACE FUNCTION bronze.refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    changeable text[] := ARRAY[]::text[];
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF TG_TABLE_NAME IN ('live_account_values', 'live_positions', 'live_quotes') THEN
            IF OLD.observed_at < now() - interval '29 days' THEN
                RETURN OLD;
            END IF;
        ELSIF TG_TABLE_NAME = 'live_executions' THEN
            IF OLD.executed_at < now() - interval '365 days' THEN
                RETURN OLD;
            END IF;
        END IF;
        RAISE EXCEPTION 'bronze.% is append-only: this row cannot be deleted', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    ELSIF TG_OP = 'UPDATE' THEN
        changeable := CASE TG_TABLE_NAME
            WHEN 'flex_rows' THEN ARRAY['report_date', 'conid', 'currency', 'row_sha256',
                                        'data_json', 'last_seen_run_id']
            WHEN 'live_executions' THEN ARRAY['commission', 'commission_currency',
                                              'realized_pnl', 'updated_at']
            WHEN 'live_contracts' THEN ARRAY['symbol', 'local_symbol', 'sec_type', 'currency',
                                             'primary_exchange', 'long_name', 'industry',
                                             'category', 'subcategory', 'is_benchmark',
                                             'refreshed_at']
            WHEN 'live_daily_bars' THEN ARRAY['open_price', 'high_price', 'low_price',
                                              'close_price', 'volume', 'currency', 'fetched_at']
            ELSE ARRAY[]::text[]
        END;
        IF (to_jsonb(NEW) - changeable) IS DISTINCT FROM (to_jsonb(OLD) - changeable) THEN
            RAISE EXCEPTION 'bronze.% is append-only: this change is refused', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'bronze.% is append-only: TRUNCATE is refused', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$;
REVOKE ALL ON FUNCTION bronze.refuse_change() FROM PUBLIC;

DO $$
DECLARE
    rel text;
BEGIN
    FOR rel IN SELECT c.relname FROM pg_class c
                WHERE c.relnamespace = 'bronze'::regnamespace AND c.relkind IN ('r', 'p')
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS bronze_append_only ON bronze.%I', rel);
        EXECUTE format('CREATE TRIGGER bronze_append_only BEFORE UPDATE OR DELETE ON bronze.%I '
                       'FOR EACH ROW EXECUTE FUNCTION bronze.refuse_change()', rel);
        EXECUTE format('DROP TRIGGER IF EXISTS bronze_no_truncate ON bronze.%I', rel);
        EXECUTE format('CREATE TRIGGER bronze_no_truncate BEFORE TRUNCATE ON bronze.%I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION bronze.refuse_change()', rel);
    END LOOP;
END;
$$;
