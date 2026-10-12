CREATE TABLE IF NOT EXISTS risk_analytics_snapshots (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    analytics_version TEXT NOT NULL,
    comparison_version TEXT NOT NULL,
    source_data_sha256 TEXT NOT NULL CHECK (source_data_sha256 ~ '^[0-9a-f]{64}$'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    payload_json JSONB NOT NULL,
    quality_status TEXT NOT NULL CHECK (quality_status IN ('pass', 'warn', 'fail')),
    generated_at TEXT NOT NULL,
    duration_ms DOUBLE PRECISION NOT NULL CHECK (duration_ms >= 0),
    timings_json JSONB NOT NULL,
    PRIMARY KEY (tenant_id, subject_id, as_of_date, analytics_version)
);

CREATE INDEX IF NOT EXISTS idx_risk_snapshot_lookup
    ON risk_analytics_snapshots(tenant_id, subject_id, as_of_date DESC, analytics_version);

CREATE INDEX IF NOT EXISTS idx_reference_fx_pair_date
    ON reference_fx_rates(tenant_id, from_currency, to_currency, rate_date DESC)
    WHERE quality_status IN ('pass', 'warn');

ALTER TABLE risk_analytics_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_analytics_snapshots FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON risk_analytics_snapshots;
CREATE POLICY tenant_isolation ON risk_analytics_snapshots
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());
