CREATE TABLE institutional_shadow_reconciliations (
    reconciliation_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    run_date DATE NOT NULL,
    authoritative_manifest_sha256 TEXT NOT NULL CHECK (
        authoritative_manifest_sha256 ~ '^[0-9a-f]{64}$'
    ),
    registry_sha256 TEXT NOT NULL CHECK (registry_sha256 ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('pass', 'fail')),
    active_subject_count INTEGER NOT NULL CHECK (active_subject_count >= 0),
    preview_subject_count INTEGER NOT NULL CHECK (preview_subject_count >= 0),
    planned_capability_count INTEGER NOT NULL CHECK (planned_capability_count >= 0),
    mismatch_count INTEGER NOT NULL CHECK (mismatch_count >= 0),
    evidence_json JSONB NOT NULL,
    mismatches_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (
        tenant_id, run_date, authoritative_manifest_sha256, registry_sha256
    ),
    CHECK (
        (status = 'pass' AND mismatch_count = 0)
        OR (status = 'fail' AND mismatch_count > 0)
    )
);

CREATE INDEX institutional_shadow_reconciliations_latest_idx
    ON institutional_shadow_reconciliations(tenant_id, run_date DESC, created_at DESC);

ALTER TABLE institutional_shadow_reconciliations ENABLE ROW LEVEL SECURITY;
ALTER TABLE institutional_shadow_reconciliations FORCE ROW LEVEL SECURITY;
CREATE POLICY institutional_shadow_reconciliations_tenant_policy
    ON institutional_shadow_reconciliations
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

COMMENT ON TABLE institutional_shadow_reconciliations IS
    'Read-only comparison of registry-planned capabilities with the proven Azure schedules; never enqueues work or contacts IBKR.';
