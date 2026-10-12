CREATE TABLE IF NOT EXISTS burn_in_acceptance_windows (
    window_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    start_date TEXT NOT NULL,
    earliest_completion_date TEXT NOT NULL,
    required_consecutive_days INTEGER NOT NULL DEFAULT 14
        CHECK (required_consecutive_days = 14),
    consecutive_pass_days INTEGER NOT NULL DEFAULT 0
        CHECK (consecutive_pass_days BETWEEN 0 AND 14),
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'aborted')),
    started_at TEXT NOT NULL,
    started_by TEXT NOT NULL,
    last_result_date TEXT,
    last_failure_date TEXT,
    completed_at TEXT,
    UNIQUE (tenant_id, subject_id, start_date)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_burn_in_one_running_window
    ON burn_in_acceptance_windows(tenant_id, subject_id)
    WHERE status = 'running';

CREATE TABLE IF NOT EXISTS burn_in_daily_results (
    result_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    window_id BIGINT NOT NULL REFERENCES burn_in_acceptance_windows(window_id),
    result_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass', 'fail')),
    result_json TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    UNIQUE (tenant_id, window_id, result_date)
);

CREATE INDEX IF NOT EXISTS idx_burn_in_results_window_date
    ON burn_in_daily_results(tenant_id, window_id, result_date DESC);

ALTER TABLE burn_in_acceptance_windows ENABLE ROW LEVEL SECURITY;
ALTER TABLE burn_in_acceptance_windows FORCE ROW LEVEL SECURITY;
ALTER TABLE burn_in_daily_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE burn_in_daily_results FORCE ROW LEVEL SECURITY;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'burn_in_acceptance_windows', 'burn_in_daily_results'
    ]
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id())',
            table_name
        );
    END LOOP;
END $$;
