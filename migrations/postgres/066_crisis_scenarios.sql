-- Hypothetical crisis scenarios (Lehman 2008, China 2015-16, COVID 2020, 2022)
-- for every account, portfolio and composite, shown on the website risk page.
-- Separate from crisis_replay_daily so the existing replay and the MS and T
-- qualified replays are unchanged.
CREATE TABLE IF NOT EXISTS crisis_scenario_results (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    holdings_as_of TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    reporting_currency TEXT NOT NULL,
    crisis_start TEXT NOT NULL,
    crisis_end TEXT NOT NULL,
    available BOOLEAN NOT NULL,
    maximum_drawdown DOUBLE PRECISION CHECK (maximum_drawdown IS NULL OR maximum_drawdown BETWEEN -1 AND 0),
    result_json JSONB NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, subject_id, holdings_as_of, scenario_id, calculation_version)
);

CREATE INDEX IF NOT EXISTS idx_crisis_scenario_results_subject
    ON crisis_scenario_results(tenant_id, subject_id, calculation_version, holdings_as_of);

ALTER TABLE crisis_scenario_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE crisis_scenario_results FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON crisis_scenario_results;
CREATE POLICY tenant_isolation ON crisis_scenario_results
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
