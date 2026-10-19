-- Daily traded volumes in silver (phase 3, docs/PHASE3_DATA.md). HELD MIGRATION.
-- Not in migrations/postgres, so no bootstrap or job applies it. On its approved day (one free day
-- after Wed 25 Nov) it moves to migrations/postgres under the next free number and runs alone
-- (bootstrap_postgres.py --only-migration). daily_volumes_undo.sql reverses it.
--
-- daily_volumes: one row per tenant, instrument and exchange trading date, with the shares traded
-- that day as Yahoo reports them. Written only by scripts/load_daily_volumes.py (job
-- ibkrv2stg-daily-volumes), which reads the Yahoo chart responses already kept in
-- bronze-market-data and does nothing unless DAILY_VOLUMES_LOAD=on. Rebuildable from bronze.
-- Read by the gold liquidity section once that is switched on.
--
-- Created in public, then moved into silver with a security_invoker view of the same name left in
-- public, as 070 did for market_data_prices, so bootstrap's grants, the layer checks and the layer
-- undo treat it like every other silver table. Without a silver schema it stays in public.
-- Grants: none here. bootstrap_postgres.py, which runs this file, then gives the job's app login
-- SELECT, INSERT, UPDATE and DELETE, the website login SELECT, and the approval login SELECT on
-- price_date only (layer_quality.COUNT_COLUMNS), as it does for market_data_prices.
-- Additive: no existing table or view changes.

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.daily_volumes (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    instrument_id TEXT NOT NULL CHECK (length(instrument_id) BETWEEN 1 AND 200),
    price_date TEXT NOT NULL CHECK (price_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    -- Zero or more and finite (NaN sorts above Infinity in PostgreSQL, so it fails too).
    volume DOUBLE PRECISION NOT NULL CHECK (volume >= 0 AND volume < 'Infinity'::double precision),
    source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 200),
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL CHECK (fetched_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}'),
    PRIMARY KEY (tenant_id, instrument_id, price_date)
);
COMMENT ON TABLE public.daily_volumes IS
    'Shares traded per instrument and exchange trading date, from the Yahoo chart responses kept in '
    'bronze-market-data (docs/PHASE3_DATA.md). Written by the daily-volumes job; internal use only.';
COMMENT ON COLUMN public.daily_volumes.volume IS
    'Shares (units) traded that day as Yahoo reports them; zero on a day without trading. A day '
    'without a volume is not stored.';
COMMENT ON COLUMN public.daily_volumes.fetched_at IS
    'When the capture that gave this volume was requested from Yahoo (UTC, ISO 8601).';

ALTER TABLE public.daily_volumes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_volumes FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.daily_volumes;
CREATE POLICY tenant_isolation ON public.daily_volumes
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

-- Into silver once the layer exists, as 070 moved market_data_prices.
DO $$
DECLARE
    layer CONSTANT text := 'silver';
    rel CONSTANT text := 'daily_volumes';
    owner_name text;
    grant_row record;
BEGIN
    IF to_regnamespace(layer) IS NULL THEN
        RAISE NOTICE 'There is no silver schema yet, so daily_volumes stays in public';
        RETURN;
    END IF;
    IF to_regclass(format('%I.%I', layer, rel)) IS NOT NULL THEN
        RETURN;  -- already moved
    END IF;
    SELECT pg_get_userbyid(c.relowner) INTO owner_name
      FROM pg_class c WHERE c.oid = to_regclass(format('public.%I', rel));
    EXECUTE format('ALTER TABLE public.%I SET SCHEMA %I', rel, layer);
    EXECUTE format('CREATE VIEW public.%I WITH (security_invoker = true) AS SELECT * FROM %I.%I',
                   rel, layer, rel);
    IF owner_name <> current_user THEN
        EXECUTE format('ALTER VIEW public.%I OWNER TO %I', rel, owner_name);
    END IF;
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
END;
$$;
