-- Gold quant analytics (wiring design section 1, docs/QUANT_ANALYTICS.md). HELD MIGRATION.
-- Not in migrations/postgres, so no bootstrap or job applies it. On its approved day (Wed 11 Nov)
-- it moves to migrations/postgres/076_quant_analytics.sql and runs alone
-- (bootstrap_postgres.py --only-migration 076_quant_analytics.sql). quant_analytics_undo.sql
-- reverses it. The Desk page's two silver views are a separate held draft (desk_live_views.sql).
--
-- quant_analytics_snapshots: one row per tenant, subject, date, section and analytics version,
-- so a section that cannot be computed never holds back the others. Written only by
-- scripts/build_quant_analytics.py (job ibkrv2stg-quant-analytics), which does nothing unless
-- QUANT_ANALYTICS_BUILD=on; read by the website's quant cards once their pages are switched on.
--
-- Created in public, then moved into gold with a security_invoker view of the same name left in
-- public, exactly as 073 did for the certified figures, so bootstrap's grants, the layer checks
-- and the layer undo treat it like every other gold table. Without a gold schema it stays in
-- public. Grants: none here. bootstrap_postgres.py, which runs this file, then gives the job's
-- app login SELECT, INSERT, UPDATE and DELETE, the website login SELECT, and the approval login
-- SELECT on as_of_date only (layer_quality.COUNT_COLUMNS), on the table and on the view, as it
-- does for risk_analytics_snapshots. Additive: no existing table or view changes.

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.quant_analytics_snapshots (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL CHECK (length(subject_id) BETWEEN 1 AND 200),
    as_of_date TEXT NOT NULL CHECK (as_of_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    section TEXT NOT NULL CHECK (section IN (
        'skill', 'decisions', 'currency', 'tail', 'regime', 'holdings_risk', 'correlation',
        'concentration', 'execution')),
    analytics_version TEXT NOT NULL CHECK (length(analytics_version) BETWEEN 1 AND 200),
    model_id TEXT NOT NULL CHECK (model_id ~ '^[a-z][a-z0-9-]*$'),
    model_version TEXT NOT NULL CHECK (length(model_version) BETWEEN 1 AND 200),
    source_data_sha256 TEXT NOT NULL CHECK (source_data_sha256 ~ '^[0-9a-f]{64}$'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    quality_status TEXT NOT NULL CHECK (quality_status IN ('pass', 'warn', 'fail')),
    generated_at TEXT NOT NULL,
    duration_ms DOUBLE PRECISION NOT NULL CHECK (duration_ms >= 0),
    timings_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    lineage_run_id UUID,
    PRIMARY KEY (tenant_id, subject_id, as_of_date, section, analytics_version)
);
COMMENT ON TABLE public.quant_analytics_snapshots IS
    'Quant analytics payload per subject, date and section (wiring design section 1). Written by the '
    'quant-analytics job; a row whose quality_status is fail is never shown.';
COMMENT ON COLUMN public.quant_analytics_snapshots.lineage_run_id IS
    'The recorded job run that last inserted or updated this row (lineage_runs); NULL when that run was '
    'not recorded.';

-- The reader asks for the latest date on or before a day, per subject and section.
CREATE INDEX IF NOT EXISTS quant_analytics_snapshots_lookup
    ON public.quant_analytics_snapshots(tenant_id, subject_id, section, as_of_date DESC);

ALTER TABLE public.quant_analytics_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.quant_analytics_snapshots FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.quant_analytics_snapshots;
CREATE POLICY tenant_isolation ON public.quant_analytics_snapshots
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

-- The run that last wrote the row (071's function). The job upserts, so on insert and update.
DROP TRIGGER IF EXISTS lineage_stamp_run ON public.quant_analytics_snapshots;
CREATE TRIGGER lineage_stamp_run BEFORE INSERT OR UPDATE ON public.quant_analytics_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.lineage_stamp_run();

-- Into gold once the layer exists, as 073 moved the certified figures.
DO $$
DECLARE
    layer CONSTANT text := 'gold';
    rel CONSTANT text := 'quant_analytics_snapshots';
    owner_name text;
    grant_row record;
BEGIN
    IF to_regnamespace(layer) IS NULL THEN
        RAISE NOTICE 'There is no gold schema yet, so quant_analytics_snapshots stays in public';
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
    -- Whoever can use the table can use the view, as in 068 and 073.
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
