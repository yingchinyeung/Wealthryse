CREATE TABLE portfolio_analyst_assurance_runs (
    assurance_run_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    assurance_date DATE NOT NULL,
    as_of_date DATE,
    policy_version TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running', 'pass', 'warn', 'fail')),
    checks_passed INTEGER NOT NULL DEFAULT 0 CHECK (checks_passed >= 0),
    checks_warned INTEGER NOT NULL DEFAULT 0 CHECK (checks_warned >= 0),
    checks_failed INTEGER NOT NULL DEFAULT 0 CHECK (checks_failed >= 0),
    result_json JSONB,
    model_deployment TEXT,
    model_request_sha256 TEXT,
    model_response_sha256 TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, subject_id, assurance_date)
);

CREATE INDEX portfolio_analyst_assurance_runs_subject_date_idx
    ON portfolio_analyst_assurance_runs(tenant_id, subject_id, assurance_date DESC);

ALTER TABLE portfolio_analyst_assurance_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_analyst_assurance_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_analyst_assurance_runs_tenant_policy
    ON portfolio_analyst_assurance_runs
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

CREATE TABLE portfolio_analyst_assurance_findings (
    finding_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    assurance_run_id BIGINT NOT NULL REFERENCES portfolio_analyst_assurance_runs(assurance_run_id) ON DELETE CASCADE,
    check_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    message TEXT NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, assurance_run_id, check_id)
);

CREATE INDEX portfolio_analyst_assurance_findings_run_idx
    ON portfolio_analyst_assurance_findings(tenant_id, assurance_run_id, severity);

ALTER TABLE portfolio_analyst_assurance_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_analyst_assurance_findings FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_analyst_assurance_findings_tenant_policy
    ON portfolio_analyst_assurance_findings
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());
