CREATE TABLE IF NOT EXISTS crisis_replay_components (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    holdings_as_of TEXT NOT NULL,
    conid TEXT NOT NULL,
    holding_symbol TEXT NOT NULL,
    description TEXT,
    currency TEXT NOT NULL,
    current_market_value DOUBLE PRECISION NOT NULL,
    opening_weight DOUBLE PRECISION NOT NULL,
    source_symbol TEXT NOT NULL,
    mapping_method TEXT NOT NULL CHECK (mapping_method IN ('direct', 'market_proxy')),
    proxy_reason TEXT,
    price_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, subject_id, holdings_as_of, conid)
);

CREATE TABLE IF NOT EXISTS crisis_replay_daily (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    holdings_as_of TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    normalized_value DOUBLE PRECISION NOT NULL CHECK (normalized_value > 0),
    drawdown DOUBLE PRECISION NOT NULL CHECK (drawdown <= 0),
    crisis_start TEXT NOT NULL,
    crisis_end TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    quality_status TEXT NOT NULL CHECK (quality_status IN ('pass', 'warn', 'fail')),
    generated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, subject_id, holdings_as_of, observation_date)
);

CREATE INDEX IF NOT EXISTS idx_crisis_replay_components_subject
    ON crisis_replay_components(tenant_id, subject_id, holdings_as_of);
CREATE INDEX IF NOT EXISTS idx_crisis_replay_daily_subject
    ON crisis_replay_daily(tenant_id, subject_id, holdings_as_of, observation_date);

ALTER TABLE crisis_replay_components ENABLE ROW LEVEL SECURITY;
ALTER TABLE crisis_replay_components FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON crisis_replay_components;
CREATE POLICY tenant_isolation ON crisis_replay_components
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE crisis_replay_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE crisis_replay_daily FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON crisis_replay_daily;
CREATE POLICY tenant_isolation ON crisis_replay_daily
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
