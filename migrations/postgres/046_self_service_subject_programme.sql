-- Complete the database contract for website-created derived and master
-- onboarding cases.  Typed definitions remain data, never executable code.

ALTER TABLE account_onboarding_cases
    DROP CONSTRAINT IF EXISTS account_onboarding_cases_source_system_check;

ALTER TABLE account_onboarding_cases
    ADD CONSTRAINT account_onboarding_cases_source_system_check
    CHECK (source_system IN (
        'existing_v11', 'ibkr_flex', 'derived_definition', 'master_definition'
    ));

ALTER TABLE account_onboarding_approvals
    DROP CONSTRAINT IF EXISTS account_onboarding_approvals_approval_type_check;

ALTER TABLE account_onboarding_approvals
    ADD CONSTRAINT account_onboarding_approvals_approval_type_check
    CHECK (approval_type IN (
        'external_market_data', 'financial_contract', 'calculation_contract',
        'presentation_identity', 'historical_report_batch',
        'website_preview', 'owner_activation'
    ));

CREATE TABLE master_account_definitions (
    definition_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    definition_version INTEGER NOT NULL CHECK (definition_version > 0),
    configuration_json JSONB NOT NULL,
    configuration_sha256 TEXT NOT NULL CHECK (
        configuration_sha256 ~ '^[0-9a-f]{64}$'
    ),
    approval_status TEXT NOT NULL CHECK (approval_status IN (
        'draft', 'validated', 'approved', 'superseded', 'rejected'
    )),
    effective_from DATE NOT NULL,
    effective_to DATE,
    created_by_identity_sha256 TEXT CHECK (
        created_by_identity_sha256 IS NULL
        OR created_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    approved_by_identity_sha256 TEXT CHECK (
        approved_by_identity_sha256 IS NULL
        OR approved_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    UNIQUE (tenant_id, subject_id, definition_version),
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE,
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CHECK (
        (approval_status = 'approved' AND approved_at IS NOT NULL
         AND approved_by_identity_sha256 IS NOT NULL)
        OR approval_status <> 'approved'
    )
);

CREATE UNIQUE INDEX master_account_definitions_current_approved_idx
    ON master_account_definitions(tenant_id, subject_id)
    WHERE approval_status = 'approved' AND effective_to IS NULL;

ALTER TABLE master_account_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE master_account_definitions FORCE ROW LEVEL SECURITY;
CREATE POLICY master_account_definitions_tenant_policy
    ON master_account_definitions
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

COMMENT ON TABLE master_account_definitions IS
    'Versioned owner-approved master reporting contracts, including explicit anti-double-counting policy.';
