-- Daily factor returns in silver (phase 3, docs/PHASE3_DATA.md). HELD MIGRATION.
-- Not in migrations/postgres, so no bootstrap or job applies it. On its approved day (one free day
-- after Wed 25 Nov, after daily_volumes) it moves to migrations/postgres under the next free number
-- and runs alone (bootstrap_postgres.py --only-migration). factor_returns_undo.sql reverses it.
--
-- factor_returns: one row per tenant, region, factor, return date and vintage. Kenneth French's
-- daily five factors and momentum for the regions fama_french.py uses, as fractions (the files are
-- in percent). Every vintage is kept; a vintage holds the rows it added or changed, and readers take
-- the latest vintage per date. Written only by scripts/load_factor_returns.py (job
-- ibkrv2stg-factor-returns), which stores each file in bronze-market-data (feed kenneth-french)
-- before parsing it and does nothing unless FACTOR_RETURNS_LOAD=on. Rebuildable from bronze. The
-- library's terms for commercial use are not yet checked: internal use only.
--
-- Created in public, then moved into silver with a security_invoker view of the same name left in
-- public, as 070 did for market_data_prices. Without a silver schema it stays in public.
-- Grants: none here. bootstrap_postgres.py, which runs this file, then gives the job's app login
-- SELECT, INSERT, UPDATE and DELETE, the website login SELECT, and the approval login SELECT on
-- return_date only (layer_quality.COUNT_COLUMNS), as it does for market_data_prices.
-- Additive: no existing table or view changes.

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.factor_returns (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    region TEXT NOT NULL CHECK (region ~ '^[a-z][a-z0-9_]{0,62}$'),
    factor TEXT NOT NULL CHECK (factor IN ('Mkt-RF', 'SMB', 'HML', 'RMW', 'CMA', 'RF', 'MOM')),
    return_date TEXT NOT NULL CHECK (return_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    -- Finite (NaN sorts above Infinity in PostgreSQL, so it fails too).
    value DOUBLE PRECISION NOT NULL CHECK (value > '-Infinity'::double precision
                                           AND value < 'Infinity'::double precision),
    source TEXT NOT NULL CHECK (source IN ('kenneth-french', 'etf-proxy')),
    vintage TEXT NOT NULL CHECK (vintage ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    fetched_at TEXT NOT NULL CHECK (fetched_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}'),
    PRIMARY KEY (tenant_id, region, factor, return_date, vintage)
);
COMMENT ON TABLE public.factor_returns IS
    'Daily factor returns per region, factor, date and vintage, from Kenneth French''s data library '
    '(docs/PHASE3_DATA.md). Written by the factor-returns job; internal use only until the library''s '
    'terms are checked. Readers take the latest vintage per date.';
COMMENT ON COLUMN public.factor_returns.value IS
    'The daily simple return as a fraction (the library''s percent divided by 100).';
COMMENT ON COLUMN public.factor_returns.vintage IS
    'The day the file was published (its Last-Modified date), or fetched when the server does not say.';

ALTER TABLE public.factor_returns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.factor_returns FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.factor_returns;
CREATE POLICY tenant_isolation ON public.factor_returns
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

-- Into silver once the layer exists, as 070 moved market_data_prices.
DO $$
DECLARE
    layer CONSTANT text := 'silver';
    rel CONSTANT text := 'factor_returns';
    owner_name text;
    grant_row record;
BEGIN
    IF to_regnamespace(layer) IS NULL THEN
        RAISE NOTICE 'There is no silver schema yet, so factor_returns stays in public';
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
