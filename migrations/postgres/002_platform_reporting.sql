CREATE TABLE IF NOT EXISTS benchmark_master (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    benchmark_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    provider TEXT,
    currency TEXT NOT NULL,
    return_convention TEXT NOT NULL CHECK (return_convention IN ('price', 'gross_total_return', 'net_total_return', 'fund_total_return')),
    frequency TEXT NOT NULL DEFAULT 'daily',
    license_status TEXT NOT NULL DEFAULT 'pending' CHECK (license_status IN ('pending', 'approved', 'restricted', 'expired')),
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, benchmark_id),
    UNIQUE (tenant_id, symbol, return_convention)
);

CREATE TABLE IF NOT EXISTS benchmark_prices (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    benchmark_id TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    level DOUBLE PRECISION NOT NULL CHECK (level > 0),
    currency TEXT NOT NULL,
    observation_status TEXT NOT NULL CHECK (observation_status IN ('official_close', 'intraday', 'corrected')),
    provider TEXT NOT NULL,
    source_reference TEXT,
    observed_at TEXT,
    fetched_at TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'pending' CHECK (quality_status IN ('pending', 'pass', 'warn', 'fail')),
    raw_sha256 TEXT,
    PRIMARY KEY (tenant_id, benchmark_id, observation_date, observation_status),
    FOREIGN KEY (tenant_id, benchmark_id) REFERENCES benchmark_master(tenant_id, benchmark_id)
);

CREATE TABLE IF NOT EXISTS reference_fx_rates (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    rate_date TEXT NOT NULL,
    from_currency TEXT NOT NULL,
    to_currency TEXT NOT NULL,
    rate DOUBLE PRECISION NOT NULL CHECK (rate > 0),
    provider TEXT NOT NULL,
    observation_status TEXT NOT NULL DEFAULT 'official_close',
    fetched_at TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'pending' CHECK (quality_status IN ('pending', 'pass', 'warn', 'fail')),
    PRIMARY KEY (tenant_id, rate_date, from_currency, to_currency, provider)
);

CREATE TABLE IF NOT EXISTS risk_free_rates (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    rate_date TEXT NOT NULL,
    series_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    tenor TEXT NOT NULL,
    annual_rate DOUBLE PRECISION NOT NULL,
    day_count_convention TEXT NOT NULL,
    provider TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'pending' CHECK (quality_status IN ('pending', 'pass', 'warn', 'fail')),
    PRIMARY KEY (tenant_id, rate_date, series_id)
);

CREATE TABLE IF NOT EXISTS portfolio_benchmark_mapping (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    benchmark_id TEXT NOT NULL,
    mapping_type TEXT NOT NULL CHECK (mapping_type IN ('primary', 'secondary')),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    reporting_currency TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, subject_id, benchmark_id, effective_from),
    FOREIGN KEY (tenant_id, benchmark_id) REFERENCES benchmark_master(tenant_id, benchmark_id)
);

CREATE TABLE IF NOT EXISTS performance_comparison_daily (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    benchmark_id TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    reporting_currency TEXT NOT NULL,
    portfolio_value DOUBLE PRECISION NOT NULL,
    benchmark_level DOUBLE PRECISION NOT NULL,
    normalized_portfolio DOUBLE PRECISION,
    normalized_benchmark DOUBLE PRECISION,
    portfolio_return DOUBLE PRECISION,
    benchmark_return DOUBLE PRECISION,
    excess_return DOUBLE PRECISION,
    calculation_version TEXT NOT NULL,
    quality_status TEXT NOT NULL CHECK (quality_status IN ('pass', 'warn', 'fail')),
    generated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, subject_id, benchmark_id, observation_date, calculation_version),
    FOREIGN KEY (tenant_id, benchmark_id) REFERENCES benchmark_master(tenant_id, benchmark_id)
);

CREATE TABLE IF NOT EXISTS metric_definitions (
    metric_id TEXT NOT NULL,
    version TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL,
    formula_text TEXT NOT NULL,
    annualization_factor DOUBLE PRECISION,
    created_at TEXT NOT NULL,
    PRIMARY KEY (metric_id, version)
);

CREATE TABLE IF NOT EXISTS data_quality_results (
    quality_result_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    run_type TEXT NOT NULL,
    run_reference TEXT NOT NULL,
    subject_id TEXT,
    check_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass', 'warn', 'fail')),
    observed_value TEXT,
    expected_value TEXT,
    message TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reporting_snapshots (
    snapshot_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    reporting_currency TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    source_manifest_json TEXT NOT NULL,
    data_sha256 TEXT NOT NULL,
    quality_status TEXT NOT NULL CHECK (quality_status IN ('pass', 'warn', 'fail')),
    status TEXT NOT NULL CHECK (status IN ('draft', 'frozen', 'approved', 'superseded')),
    created_at TEXT NOT NULL,
    approved_at TEXT,
    approved_by TEXT
);

CREATE TABLE IF NOT EXISTS report_generation_runs (
    generation_run_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    snapshot_id UUID NOT NULL REFERENCES reporting_snapshots(snapshot_id),
    report_type TEXT NOT NULL CHECK (report_type IN ('factsheet', 'presentation', 'dashboard', 'data_export')),
    template_id TEXT,
    template_version TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'success', 'failed', 'awaiting_approval', 'published')),
    output_reference TEXT,
    output_sha256 TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS app_users (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    entra_object_id TEXT NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT,
    role_name TEXT NOT NULL CHECK (role_name IN ('owner', 'administrator', 'analyst', 'viewer')),
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, entra_object_id),
    UNIQUE (tenant_id, email)
);

CREATE TABLE IF NOT EXISTS user_account_access (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    entra_object_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    access_level TEXT NOT NULL CHECK (access_level IN ('read', 'report', 'admin')),
    granted_at TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    PRIMARY KEY (tenant_id, entra_object_id, subject_id),
    FOREIGN KEY (tenant_id, entra_object_id) REFERENCES app_users(tenant_id, entra_object_id)
);

CREATE TABLE IF NOT EXISTS agent_tool_audit (
    audit_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    correlation_id TEXT NOT NULL,
    entra_object_id TEXT,
    tool_name TEXT NOT NULL,
    subject_id TEXT,
    request_sha256 TEXT NOT NULL,
    response_sha256 TEXT,
    approval_required BOOLEAN NOT NULL,
    approval_id BIGINT,
    status TEXT NOT NULL CHECK (status IN ('allowed', 'denied', 'success', 'failed')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS approved_actions (
    approval_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    correlation_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    action_payload_sha256 TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'executed', 'expired', 'failed')),
    decided_by TEXT,
    decided_at TEXT,
    executed_at TEXT,
    result_reference TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_event_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    occurred_at TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'service', 'agent', 'system')),
    actor_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    subject_id TEXT,
    outcome TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_benchmark_prices_date ON benchmark_prices(tenant_id, benchmark_id, observation_date);
CREATE INDEX IF NOT EXISTS idx_reference_fx_date ON reference_fx_rates(tenant_id, rate_date);
CREATE INDEX IF NOT EXISTS idx_risk_free_date ON risk_free_rates(tenant_id, rate_date, currency);
CREATE INDEX IF NOT EXISTS idx_comparison_date ON performance_comparison_daily(tenant_id, subject_id, observation_date);
CREATE INDEX IF NOT EXISTS idx_quality_run ON data_quality_results(tenant_id, run_type, run_reference);
CREATE INDEX IF NOT EXISTS idx_reporting_subject ON reporting_snapshots(tenant_id, subject_id, period_end);
CREATE INDEX IF NOT EXISTS idx_agent_audit_correlation ON agent_tool_audit(tenant_id, correlation_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_time ON audit_events(tenant_id, occurred_at);

ALTER TABLE benchmark_master ENABLE ROW LEVEL SECURITY;
ALTER TABLE benchmark_master FORCE ROW LEVEL SECURITY;
ALTER TABLE benchmark_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE benchmark_prices FORCE ROW LEVEL SECURITY;
ALTER TABLE reference_fx_rates ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference_fx_rates FORCE ROW LEVEL SECURITY;
ALTER TABLE risk_free_rates ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_free_rates FORCE ROW LEVEL SECURITY;
ALTER TABLE portfolio_benchmark_mapping ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_benchmark_mapping FORCE ROW LEVEL SECURITY;
ALTER TABLE performance_comparison_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE performance_comparison_daily FORCE ROW LEVEL SECURITY;
ALTER TABLE data_quality_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_quality_results FORCE ROW LEVEL SECURITY;
ALTER TABLE reporting_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE reporting_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE report_generation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_generation_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_users FORCE ROW LEVEL SECURITY;
ALTER TABLE user_account_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_account_access FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_tool_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_tool_audit FORCE ROW LEVEL SECURITY;
ALTER TABLE approved_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE approved_actions FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'benchmark_master', 'benchmark_prices', 'reference_fx_rates',
        'risk_free_rates', 'portfolio_benchmark_mapping',
        'performance_comparison_daily', 'data_quality_results',
        'reporting_snapshots', 'report_generation_runs', 'app_users',
        'user_account_access', 'agent_tool_audit', 'approved_actions',
        'audit_events'
    ]
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id())',
            table_name
        );
    END LOOP;
END $$;
