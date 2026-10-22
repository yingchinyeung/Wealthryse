-- Layer quality history (medallion review item 7): every quality result the per-layer targets
-- and the weekly quality report are measured from. Written only by src/ibkr_etl/layer_quality.py
-- when LAYER_QUALITY_MODE is shadow or warn (source 'monitor'), and by the publish gate for the
-- contract rules it runs on a gold build (source 'publish_gate'). Nothing else reads it.
--
-- One row per check result: readiness (a layer's data for the last business day in by its target
-- time), freshness, volume (row counts against what is typical), schema (drift from the data
-- contracts), value (NAV moves, positions dropping to zero) and rule (a contract quality rule).
-- observed is the measured count; detail holds the evidence. An append-only control-plane (ops)
-- table in public. Additive: no existing table or view changes.

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS quality_check_results (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    result_id UUID NOT NULL DEFAULT gen_random_uuid(),
    checked_at TIMESTAMPTZ NOT NULL,
    cycle_date DATE NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('monitor', 'publish_gate')),
    mode TEXT NOT NULL CHECK (mode IN ('shadow', 'warn', 'enforce')),
    kind TEXT NOT NULL CHECK (kind IN ('readiness', 'freshness', 'volume', 'schema', 'value', 'rule')),
    check_id TEXT NOT NULL CHECK (length(check_id) BETWEEN 1 AND 300),
    layer TEXT NOT NULL CHECK (layer IN ('bronze', 'silver', 'gold')),
    target TEXT CHECK (target IS NULL OR length(target) BETWEEN 1 AND 200),
    state TEXT NOT NULL CHECK (state IN ('pass', 'fail', 'ready', 'late', 'learning', 'recorded',
                                         'skipped', 'unavailable')),
    severity TEXT CHECK (severity IS NULL OR severity IN ('error', 'warning')),
    observed NUMERIC,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, result_id)
);
COMMENT ON TABLE quality_check_results IS
    'Layer quality history: one row per check result (layer_quality.py, the publish gate). Append-only.';
COMMENT ON COLUMN quality_check_results.cycle_date IS
    'The UTC day of the daily cycle the result belongs to.';

-- The administrator reads today's rows on each run; the weekly report reads a date range.
CREATE INDEX IF NOT EXISTS quality_check_results_cycle
    ON quality_check_results(tenant_id, cycle_date, kind, check_id);
CREATE INDEX IF NOT EXISTS quality_check_results_checked
    ON quality_check_results(tenant_id, checked_at DESC);

ALTER TABLE quality_check_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE quality_check_results FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON quality_check_results;
CREATE POLICY tenant_isolation ON quality_check_results
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

CREATE OR REPLACE FUNCTION reject_quality_result_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Quality results are append-only';
END;
$$;
REVOKE ALL ON FUNCTION reject_quality_result_change() FROM PUBLIC;
DROP TRIGGER IF EXISTS quality_check_results_append_only ON quality_check_results;
CREATE TRIGGER quality_check_results_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON quality_check_results
    FOR EACH STATEMENT EXECUTE FUNCTION reject_quality_result_change();
