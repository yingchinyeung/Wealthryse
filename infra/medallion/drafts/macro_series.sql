-- Silver macro series (phase 3 data, docs/PHASE3_DATA_MACRO_13F.md). HELD MIGRATION.
-- Not in migrations/postgres, so no bootstrap or job applies it. On its approved day (one free day
-- after Wed 25 Nov) it moves to migrations/postgres/<next free number>_macro_series.sql and runs
-- alone (bootstrap_postgres.py --only-migration <that file>). macro_series_undo.sql reverses it.
--
-- macro_series: one row per tenant, series, observation date and vintage. The vintage is the UTC
-- date a value was fetched; a value is stored again under a new vintage only when the source
-- revised it, so every vintage is kept and readers take the latest vintage per date. Written only
-- by ibkr_etl.macro_series (job ibkrv2stg-macro-series, scripts/load_macro_series.py), which does
-- nothing unless MACRO_SERIES_LOAD=on. Raw responses land first in bronze-market-data
-- (fred-series/ and yahoo-chart/), so the table can be rebuilt from bronze.
--
-- Created in public, then moved into silver with a security_invoker view of the same name left in
-- public, as 070 moved market_data_prices and risk_free_rates, so bootstrap's grants, the layer
-- checks and the layer undo treat it like every other silver table. Without a silver schema it
-- stays in public. Grants: none here. bootstrap_postgres.py, which runs this file, then gives the
-- job's app login SELECT, INSERT, UPDATE and DELETE, the website login SELECT, and the approval
-- login SELECT on observation_date only (layer_quality.COUNT_COLUMNS), on the table and on the
-- view. Additive: no existing table or view changes. HIBOR stays in risk_free_rates.

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.macro_series (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    series_id TEXT NOT NULL CHECK (series_id ~ '^[A-Z][A-Z0-9_]{0,39}$'),
    observation_date TEXT NOT NULL CHECK (observation_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    -- Finite only: PostgreSQL orders NaN above Infinity, so this keeps out NaN as well.
    value DOUBLE PRECISION NOT NULL CHECK (value > '-Infinity'::float8 AND value < 'Infinity'::float8),
    units TEXT NOT NULL CHECK (units IN ('percent', 'index', 'usd_per_barrel', 'cnh_per_usd', 'krw_per_usd')),
    source TEXT NOT NULL CHECK (source IN ('fred', 'yahoo-chart')),
    vintage TEXT NOT NULL CHECK (vintage ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    fetched_at TEXT NOT NULL CHECK (fetched_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}'),
    PRIMARY KEY (tenant_id, series_id, observation_date, vintage)
);
COMMENT ON TABLE public.macro_series IS
    'Daily macro series (FRED and Yahoo USD/CNH), every vintage kept; readers take the latest vintage per '
    'date. Written by the macro-series job (docs/PHASE3_DATA_MACRO_13F.md).';
COMMENT ON COLUMN public.macro_series.tenant_id IS 'Tenant that owns the row; row-level security filters on it.';
COMMENT ON COLUMN public.macro_series.series_id IS
    'FRED id as FRED names it (DGS10, DGS2, DTWEXBGS, DCOILBRENTEU, DEXKOUS, VIXCLS once licensed) or USDCNH (Yahoo CNH=X).';
COMMENT ON COLUMN public.macro_series.observation_date IS 'Date of the observation (yyyy-MM-dd), as the source dates it.';
COMMENT ON COLUMN public.macro_series.value IS
    'The observation in units: yields in percent as published (4.25 is 4.25%), index levels, US dollars per '
    'barrel, offshore renminbi or South Korean won per US dollar.';
COMMENT ON COLUMN public.macro_series.units IS 'percent, index, usd_per_barrel, cnh_per_usd or krw_per_usd.';
COMMENT ON COLUMN public.macro_series.source IS 'fred (keyless CSV download) or yahoo-chart.';
COMMENT ON COLUMN public.macro_series.vintage IS
    'UTC date (yyyy-MM-dd) the value was fetched. A revised value gets a new vintage; an unchanged one is not '
    'stored again.';
COMMENT ON COLUMN public.macro_series.fetched_at IS 'UTC time (ISO 8601) the row was last written.';

ALTER TABLE public.macro_series ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.macro_series FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.macro_series;
CREATE POLICY tenant_isolation ON public.macro_series
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

-- Into silver, as 070 moved the other silver tables.
DO $$
DECLARE
    layer CONSTANT text := 'silver';
    rel CONSTANT text := 'macro_series';
    owner_name text;
    grant_row record;
BEGIN
    IF to_regnamespace(layer) IS NULL THEN
        RAISE NOTICE 'There is no silver schema yet, so macro_series stays in public';
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
    -- Whoever can use the table can use the view and reach the schema, as in 070.
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
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO %s', layer,
            CASE WHEN grant_row.grantee = 0 THEN 'PUBLIC'
                 ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END);
    END LOOP;
END;
$$;
