ALTER TABLE account_onboarding_cases
    DROP CONSTRAINT IF EXISTS account_onboarding_cases_stage_check;

ALTER TABLE account_onboarding_cases
    ADD CONSTRAINT account_onboarding_cases_stage_check
    CHECK (stage IN (
        'discovered', 'data_import', 'financial_contract',
        'analytics_configuration', 'template_intake', 'reconciliation',
        'historical_reporting', 'website_preview', 'activation_ready', 'active'
    ));

ALTER TABLE account_onboarding_approvals
    DROP CONSTRAINT IF EXISTS account_onboarding_approvals_approval_type_check;

ALTER TABLE account_onboarding_approvals
    ADD CONSTRAINT account_onboarding_approvals_approval_type_check
    CHECK (approval_type IN (
        'external_market_data', 'financial_contract',
        'historical_report_batch', 'website_preview', 'owner_activation'
    ));

CREATE TABLE onboarding_historical_report_batches (
    batch_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    case_id UUID NOT NULL REFERENCES account_onboarding_cases(case_id) ON DELETE CASCADE,
    subject_id TEXT NOT NULL,
    reporting_start DATE NOT NULL,
    reporting_end DATE NOT NULL,
    expected_month_count INTEGER NOT NULL CHECK (expected_month_count > 0),
    expected_artifact_count INTEGER NOT NULL CHECK (
        expected_artifact_count = expected_month_count * 2
    ),
    template_evidence_sha256 TEXT NOT NULL CHECK (
        template_evidence_sha256 ~ '^[0-9a-f]{64}$'
    ),
    source_evidence_sha256 TEXT NOT NULL CHECK (
        source_evidence_sha256 ~ '^[0-9a-f]{64}$'
    ),
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'freezing_snapshots', 'rendering', 'validating',
        'awaiting_owner_approval', 'approved', 'failed', 'superseded'
    )),
    manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    manifest_sha256 TEXT CHECK (
        manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'
    ),
    rendered_artifact_count INTEGER NOT NULL DEFAULT 0 CHECK (
        rendered_artifact_count >= 0
    ),
    validated_artifact_count INTEGER NOT NULL DEFAULT 0 CHECK (
        validated_artifact_count >= 0
    ),
    error_code TEXT,
    error_message TEXT,
    requested_by_identity_sha256 TEXT NOT NULL CHECK (
        requested_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    approved_at TIMESTAMPTZ,
    approved_by_identity_sha256 TEXT CHECK (
        approved_by_identity_sha256 IS NULL
        OR approved_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE,
    CHECK (reporting_end >= reporting_start),
    CHECK (rendered_artifact_count <= expected_artifact_count),
    CHECK (validated_artifact_count <= rendered_artifact_count),
    CHECK (
        (status = 'approved' AND approved_at IS NOT NULL
            AND approved_by_identity_sha256 IS NOT NULL)
        OR status <> 'approved'
    )
);

CREATE UNIQUE INDEX onboarding_historical_report_batches_current_idx
    ON onboarding_historical_report_batches(tenant_id, subject_id)
    WHERE status NOT IN ('failed', 'superseded');

CREATE INDEX onboarding_historical_report_batches_status_idx
    ON onboarding_historical_report_batches(tenant_id, status, requested_at);

CREATE TABLE onboarding_historical_report_items (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    batch_id UUID NOT NULL REFERENCES onboarding_historical_report_batches(batch_id)
        ON DELETE CASCADE,
    period_end DATE NOT NULL,
    holdings_as_of DATE NOT NULL,
    report_type TEXT NOT NULL CHECK (report_type IN ('factsheet', 'presentation')),
    snapshot_id UUID REFERENCES reporting_snapshots(snapshot_id),
    generation_run_id BIGINT REFERENCES report_generation_runs(generation_run_id),
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'rendered', 'validated', 'failed'
    )),
    container_name TEXT,
    blob_name TEXT,
    artifact_sha256 TEXT CHECK (
        artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'
    ),
    byte_count BIGINT CHECK (byte_count IS NULL OR byte_count > 0),
    template_sha256 TEXT NOT NULL CHECK (template_sha256 ~ '^[0-9a-f]{64}$'),
    validation_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, batch_id, period_end, report_type),
    CHECK (
        (status = 'validated'
            AND snapshot_id IS NOT NULL
            AND generation_run_id IS NOT NULL
            AND container_name IS NOT NULL
            AND blob_name IS NOT NULL
            AND artifact_sha256 IS NOT NULL
            AND byte_count IS NOT NULL)
        OR status <> 'validated'
    )
);

CREATE INDEX onboarding_historical_report_items_batch_idx
    ON onboarding_historical_report_items(
        tenant_id, batch_id, period_end, report_type
    );

ALTER TABLE onboarding_historical_report_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding_historical_report_batches FORCE ROW LEVEL SECURITY;
CREATE POLICY onboarding_historical_report_batches_tenant_policy
    ON onboarding_historical_report_batches
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE onboarding_historical_report_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding_historical_report_items FORCE ROW LEVEL SECURITY;
CREATE POLICY onboarding_historical_report_items_tenant_policy
    ON onboarding_historical_report_items
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

INSERT INTO account_onboarding_checks(
    tenant_id, case_id, check_id, status, message, evidence_json, checked_at
)
SELECT
    tenant_id, case_id, 'historical_reports_backfilled', 'pending',
    'All completed reporting months must have a validated private factsheet and presentation before website-preview approval.',
    '{}'::jsonb, now()
FROM account_onboarding_cases
WHERE status NOT IN ('active', 'cancelled')
ON CONFLICT (tenant_id, case_id, check_id) DO NOTHING;

UPDATE account_onboarding_cases AS cases
SET stage = 'historical_reporting', status = 'in_progress', updated_at = now()
WHERE cases.status NOT IN ('active', 'cancelled')
  AND cases.stage IN ('reconciliation', 'website_preview')
  AND NOT EXISTS (
      SELECT 1 FROM account_onboarding_approvals approvals
      WHERE approvals.tenant_id = cases.tenant_id
        AND approvals.case_id = cases.case_id
        AND approvals.approval_type = 'website_preview'
        AND approvals.approval_status = 'approved'
  );
