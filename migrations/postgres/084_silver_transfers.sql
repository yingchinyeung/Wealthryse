-- Medallion S6: IBKR's Transfers section in silver (silver.transfers), so that every deposit and
-- withdrawal a return figure uses can be read from silver (FLOWS_SOURCE, ibkr_etl.flows_source).
-- Applied alone with bootstrap_postgres.py --only-migration 084_silver_transfers.sql; no job applies
-- it (IBKR_RUN_MIGRATIONS stays false on every job). It creates an empty table. Once it exists the
-- loader writes each Transfers record here and to bronze flex_rows in the same loop, and
-- `python -m ibkr_etl.flows_source fill-transfers --apply`, run with the loader login, fills it
-- from bronze flex_rows with the loader's own function. infra/medallion/undo/084_silver_transfers_undo.sql
-- reverses it.
--
-- One row per Transfers record under the parser's key and digest, the same as its flex_rows row.
-- Dates are ISO text and amounts decimal text, as the loader keeps the other Flex sections.
-- flex_account_id is IBKR's own accountId on the record ("-" on a consolidated copy).
--
-- Created in public, then moved into silver with a security_invoker view of the same name left in
-- public, as 070 moved the other silver tables, so bootstrap's grants, the silver write lock,
-- scripts/medallion_layers.py and its undo treat it alike. Isolated by account like
-- silver.cash_transactions, and watched like every silver table (083).

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.transfers (
    account_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    flex_account_id TEXT,
    transaction_id TEXT,
    transfer_date TEXT,
    report_date TEXT,
    asset_category TEXT,
    transfer_type TEXT,
    direction TEXT,
    description TEXT,
    conid TEXT,
    currency TEXT,
    quantity_text TEXT,
    cash_transfer_text TEXT,
    position_amount_text TEXT,
    position_amount_in_base_text TEXT,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id BIGINT NOT NULL REFERENCES public.ingestion_runs(run_id),
    PRIMARY KEY(account_id, source_key)
);
CREATE INDEX IF NOT EXISTS idx_transfers_date ON public.transfers(transfer_date);

COMMENT ON TABLE public.transfers IS
    'IBKR Transfers records per account, as the loader writes them with their flex_rows row. Read as deposits and withdrawals.';
COMMENT ON COLUMN public.transfers.flex_account_id IS
    'IBKR''s own accountId on the record; "-" on a consolidated copy filed under the statement''s account.';

ALTER TABLE public.transfers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transfers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS account_isolation ON public.transfers;
CREATE POLICY account_isolation ON public.transfers
    USING (account_belongs_to_current_tenant(account_id))
    WITH CHECK (account_belongs_to_current_tenant(account_id));

REVOKE ALL ON public.transfers FROM PUBLIC;

-- Into silver once the layer exists, as 070 moved the other silver tables, with the silver write
-- watch (083) when it exists. A database that has recorded 070 without the silver schema is refused.
-- Only a test database built without the layer migrations (068 to 070) has no silver schema: there
-- the table stays in public.
DO $$
DECLARE
    layer CONSTANT text := 'silver';
    rel CONSTANT text := 'transfers';
    grant_row record;
BEGIN
    IF to_regnamespace(layer) IS NULL THEN
        IF to_regclass('public.schema_migrations') IS NOT NULL THEN
            IF EXISTS (SELECT 1 FROM public.schema_migrations WHERE version = '070_medallion_silver_layer.sql') THEN
                RAISE EXCEPTION '070 is recorded but the silver schema is missing, so 084 changes nothing';
            END IF;
        END IF;
        RAISE NOTICE 'There is no silver schema (the layer migrations were left out), so transfers stays in public';
        RETURN;
    END IF;
    IF to_regclass(format('%I.%I', layer, rel)) IS NULL THEN
        EXECUTE format('ALTER TABLE public.%I SET SCHEMA %I', rel, layer);
        EXECUTE format('CREATE VIEW public.%I WITH (security_invoker = true) AS SELECT * FROM %I.%I',
                       rel, layer, rel);
        EXECUTE format('COMMENT ON VIEW public.%I IS %L', rel,
                       format('Compatibility view over %I.%I (medallion layer).', layer, rel));
        -- Whoever can use the table can use the view, as in 070.
        FOR grant_row IN
            SELECT a.grantee, a.privilege_type, a.is_grantable
              FROM pg_class c, aclexplode(c.relacl) a
             WHERE c.oid = to_regclass(format('%I.%I', layer, rel))
               AND a.grantee <> c.relowner
               AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
        LOOP
            EXECUTE format('GRANT %s ON public.%I TO %s%s', grant_row.privilege_type, rel,
                CASE WHEN grant_row.grantee = 0 THEN 'PUBLIC'
                     ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END,
                CASE WHEN grant_row.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
        END LOOP;
    END IF;
    IF to_regprocedure('silver.watch_write()') IS NOT NULL THEN
        EXECUTE format('DROP TRIGGER IF EXISTS silver_write_watch ON %I.%I', layer, rel);
        EXECUTE format('CREATE TRIGGER silver_write_watch AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE '
                       'ON %I.%I FOR EACH STATEMENT EXECUTE FUNCTION silver.watch_write()', layer, rel);
    END IF;
END;
$$;
