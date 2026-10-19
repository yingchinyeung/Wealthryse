-- Silver 13F institutional holdings (phase 3 data, docs/PHASE3_DATA_MACRO_13F.md). HELD MIGRATION.
-- Not in migrations/postgres, so no bootstrap or job applies it. On its approved day (one free day
-- after Wed 25 Nov, after the macro series day) it moves to
-- migrations/postgres/<next free number>_institutional_holdings.sql and runs alone
-- (bootstrap_postgres.py --only-migration <that file>). institutional_holdings_undo.sql reverses it.
--
-- institutional_holdings: one row per tenant, filer, quarter, CUSIP, put/call and accession, from
-- the information tables of SEC EDGAR 13F-HR and 13F-HR/A filings of a reviewed list of large US
-- filers. Each filing keeps its own accession; readers take each filer's latest accession for a
-- quarter (the newest filed_at, then the highest accession). A restatement amendment holds its
-- complete table; a new-holdings amendment is stored as the earlier holdings plus the new ones, so
-- the latest accession is always complete (docs/PHASE3_DATA_MACRO_13F.md, "Amendments"). Written
-- only by ibkr_etl.sec_13f (job ibkrv2stg-institutional-holdings,
-- scripts/load_institutional_holdings.py), which does nothing unless INSTITUTIONAL_HOLDINGS_LOAD=on.
-- Raw filings land first in bronze-market-data (sec-13f/), so the table can be rebuilt from bronze.
--
-- Created in public, then moved into silver with a security_invoker view of the same name left in
-- public, as 070 moved the other silver tables. Without a silver schema it stays in public.
-- Grants: none here. bootstrap_postgres.py, which runs this file, then gives the job's app login
-- SELECT, INSERT, UPDATE and DELETE, the website login SELECT, and the approval login SELECT on
-- period only (layer_quality.COUNT_COLUMNS), on the table and on the view. Additive: no existing
-- table or view changes.

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.institutional_holdings (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    filer_cik TEXT NOT NULL CHECK (filer_cik ~ '^[0-9]{10}$'),
    filer_name TEXT NOT NULL CHECK (length(filer_name) BETWEEN 1 AND 300),
    period TEXT NOT NULL CHECK (period ~ '^[0-9]{4}-(03-31|06-30|09-30|12-31)$'),
    cusip TEXT NOT NULL CHECK (cusip ~ '^[0-9A-Z]{9}$'),
    issuer TEXT NOT NULL CHECK (length(issuer) <= 300),
    -- Finite and not negative: 13F reports long positions only (NaN sorts above Infinity).
    shares DOUBLE PRECISION NOT NULL CHECK (shares >= 0 AND shares < 'Infinity'::float8),
    shares_type TEXT NOT NULL CHECK (shares_type IN ('SH', 'PRN')),
    value_usd DOUBLE PRECISION NOT NULL CHECK (value_usd >= 0 AND value_usd < 'Infinity'::float8),
    put_call TEXT NOT NULL DEFAULT '' CHECK (put_call IN ('', 'PUT', 'CALL')),
    accession TEXT NOT NULL CHECK (accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
    filed_at TEXT NOT NULL CHECK (filed_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    fetched_at TEXT NOT NULL CHECK (fetched_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}'),
    PRIMARY KEY (tenant_id, filer_cik, period, cusip, put_call, accession)
);
COMMENT ON TABLE public.institutional_holdings IS
    '13F-HR holdings of large US filers, one row per filer, quarter, CUSIP, put/call and accession; readers '
    'take each filer''s latest accession for a quarter. Written by the institutional-holdings job '
    '(docs/PHASE3_DATA_MACRO_13F.md).';
COMMENT ON COLUMN public.institutional_holdings.tenant_id IS 'Tenant that owns the row; row-level security filters on it.';
COMMENT ON COLUMN public.institutional_holdings.filer_cik IS 'The filer''s SEC CIK, 10 digits, zero padded.';
COMMENT ON COLUMN public.institutional_holdings.filer_name IS 'The filer''s name as SEC EDGAR has it.';
COMMENT ON COLUMN public.institutional_holdings.period IS 'The quarter-end date the filing reports (yyyy-MM-dd).';
COMMENT ON COLUMN public.institutional_holdings.cusip IS
    'CUSIP of the security, 9 characters, upper case. Our instruments map through instruments.isin: for a US '
    'ISIN, cusip = isin[2:11].';
COMMENT ON COLUMN public.institutional_holdings.issuer IS 'Name of the issuer as the filing gives it.';
COMMENT ON COLUMN public.institutional_holdings.shares IS
    'Shares (SH) or principal amount (PRN) held, summed over the filing''s lines for the CUSIP and put/call.';
COMMENT ON COLUMN public.institutional_holdings.shares_type IS 'SH (shares) or PRN (principal amount).';
COMMENT ON COLUMN public.institutional_holdings.value_usd IS
    'Market value in US dollars. Filings made before 3 Jan 2023 report thousands of dollars: multiplied by 1000.';
COMMENT ON COLUMN public.institutional_holdings.put_call IS 'Empty for the security itself, PUT or CALL for options on it.';
COMMENT ON COLUMN public.institutional_holdings.accession IS 'The filing''s SEC accession number (0000000000-00-000000).';
COMMENT ON COLUMN public.institutional_holdings.filed_at IS 'The date SEC EDGAR accepted the filing (yyyy-MM-dd).';
COMMENT ON COLUMN public.institutional_holdings.fetched_at IS 'UTC time (ISO 8601) the row was last written.';

-- The crowding reader asks for the holders of our CUSIPs in a quarter.
CREATE INDEX IF NOT EXISTS institutional_holdings_by_cusip
    ON public.institutional_holdings(tenant_id, cusip, period);

ALTER TABLE public.institutional_holdings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.institutional_holdings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.institutional_holdings;
CREATE POLICY tenant_isolation ON public.institutional_holdings
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

-- Into silver, as 070 moved the other silver tables.
DO $$
DECLARE
    layer CONSTANT text := 'silver';
    rel CONSTANT text := 'institutional_holdings';
    owner_name text;
    grant_row record;
BEGIN
    IF to_regnamespace(layer) IS NULL THEN
        RAISE NOTICE 'There is no silver schema yet, so institutional_holdings stays in public';
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
