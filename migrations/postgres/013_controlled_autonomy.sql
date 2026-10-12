CREATE TABLE IF NOT EXISTS autonomy_control (
    tenant_id UUID PRIMARY KEY DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    enabled BOOLEAN NOT NULL DEFAULT false,
    kill_switch BOOLEAN NOT NULL DEFAULT false,
    max_actions_per_run INTEGER NOT NULL DEFAULT 1 CHECK (max_actions_per_run BETWEEN 0 AND 1),
    cooldown_minutes INTEGER NOT NULL DEFAULT 360 CHECK (cooldown_minutes >= 60),
    policy_version TEXT NOT NULL DEFAULT 'controlled-autonomy-v1',
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS autonomous_agent_runs (
    agent_run_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    correlation_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'healthy', 'degraded', 'paused', 'failed')),
    finding_count INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    action_count INTEGER NOT NULL DEFAULT 0 CHECK (action_count >= 0),
    model_used BOOLEAN NOT NULL DEFAULT false,
    summary TEXT,
    error_code TEXT,
    UNIQUE (tenant_id, correlation_id)
);

CREATE TABLE IF NOT EXISTS system_incidents (
    incident_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    incident_key TEXT NOT NULL,
    check_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    status TEXT NOT NULL CHECK (status IN ('open', 'acknowledged', 'resolved')),
    description TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    diagnosis_json TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    occurrence_count INTEGER NOT NULL DEFAULT 1 CHECK (occurrence_count >= 1),
    UNIQUE (tenant_id, incident_key)
);

CREATE TABLE IF NOT EXISTS autonomous_actions (
    action_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    agent_run_id BIGINT NOT NULL REFERENCES autonomous_agent_runs(agent_run_id),
    incident_id BIGINT REFERENCES system_incidents(incident_id),
    action_type TEXT NOT NULL CHECK (action_type IN (
        'refresh_risk_free_rate', 'refresh_provisional_benchmarks'
    )),
    target_job TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'blocked', 'executing', 'executed', 'failed')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    executed_at TEXT,
    result_reference TEXT,
    error_code TEXT
);

INSERT INTO autonomy_control(
    tenant_id, enabled, kill_switch, max_actions_per_run,
    cooldown_minutes, policy_version, updated_at, updated_by
) VALUES (
    app_tenant_id(), true, false, 1,
    360, 'controlled-autonomy-v1', CURRENT_TIMESTAMP::TEXT, 'migration'
) ON CONFLICT (tenant_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_autonomous_runs_started
    ON autonomous_agent_runs(tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_incidents_status
    ON system_incidents(tenant_id, status, severity, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_autonomous_actions_created
    ON autonomous_actions(tenant_id, action_type, created_at DESC);

ALTER TABLE autonomy_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE autonomy_control FORCE ROW LEVEL SECURITY;
ALTER TABLE autonomous_agent_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE autonomous_agent_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE system_incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_incidents FORCE ROW LEVEL SECURITY;
ALTER TABLE autonomous_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE autonomous_actions FORCE ROW LEVEL SECURITY;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'autonomy_control', 'autonomous_agent_runs',
        'system_incidents', 'autonomous_actions'
    ]
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id())',
            table_name
        );
    END LOOP;
END $$;
