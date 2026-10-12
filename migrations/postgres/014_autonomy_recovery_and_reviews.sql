ALTER TABLE autonomy_control
    ADD COLUMN IF NOT EXISTS production_active BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS production_activated_at TEXT,
    ADD COLUMN IF NOT EXISTS automatic_rollback_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS rollback_failure_threshold INTEGER NOT NULL DEFAULT 3
        CHECK (rollback_failure_threshold = 3),
    ADD COLUMN IF NOT EXISTS monthly_review_due_at TEXT;

ALTER TABLE autonomous_actions
    DROP CONSTRAINT IF EXISTS autonomous_actions_action_type_check;
ALTER TABLE autonomous_actions
    ADD CONSTRAINT autonomous_actions_action_type_check CHECK (action_type IN (
        'refresh_risk_free_rate',
        'refresh_provisional_benchmarks',
        'rollback_web_to_last_known_good'
    ));

CREATE TABLE IF NOT EXISTS autonomy_release_baselines (
    baseline_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    app_name TEXT NOT NULL,
    revision_name TEXT NOT NULL,
    image_digest TEXT NOT NULL CHECK (image_digest ~ '^sha256:[0-9a-f]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('last_known_good', 'retired')),
    established_at TEXT NOT NULL,
    established_by TEXT NOT NULL,
    retired_at TEXT,
    UNIQUE (tenant_id, app_name, revision_name)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_autonomy_one_last_known_good
    ON autonomy_release_baselines(tenant_id, app_name)
    WHERE status = 'last_known_good';

CREATE TABLE IF NOT EXISTS autonomy_recovery_state (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    app_name TEXT NOT NULL,
    consecutive_portal_failures INTEGER NOT NULL DEFAULT 0
        CHECK (consecutive_portal_failures >= 0),
    latest_checked_revision TEXT,
    last_failure_at TEXT,
    last_success_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, app_name)
);

CREATE TABLE IF NOT EXISTS autonomy_authority_reviews (
    review_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    review_month TEXT NOT NULL CHECK (review_month ~ '^[0-9]{4}-[0-9]{2}$'),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('awaiting_owner_review', 'approved', 'overdue')),
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    approved_by TEXT,
    UNIQUE (tenant_id, review_month)
);

CREATE INDEX IF NOT EXISTS idx_autonomy_reviews_due
    ON autonomy_authority_reviews(tenant_id, status, due_at DESC);

ALTER TABLE autonomy_release_baselines ENABLE ROW LEVEL SECURITY;
ALTER TABLE autonomy_release_baselines FORCE ROW LEVEL SECURITY;
ALTER TABLE autonomy_recovery_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE autonomy_recovery_state FORCE ROW LEVEL SECURITY;
ALTER TABLE autonomy_authority_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE autonomy_authority_reviews FORCE ROW LEVEL SECURITY;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'autonomy_release_baselines',
        'autonomy_recovery_state',
        'autonomy_authority_reviews'
    ]
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id())',
            table_name
        );
    END LOOP;
END $$;
