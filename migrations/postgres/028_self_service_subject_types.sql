-- Typed, versioned contracts for self-service master and derived subjects.
-- Definitions are data, never executable Python/SQL supplied by a user.

CREATE TABLE subject_calculation_definitions (
    definition_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    subject_type TEXT NOT NULL CHECK (subject_type IN (
        'filtered_portfolio', 'composite_portfolio'
    )),
    calculation_kind TEXT NOT NULL CHECK (calculation_kind IN (
        'filtered_account_with_reconstructed_cash',
        'fixed_unit_normalized_composite'
    )),
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
    UNIQUE (tenant_id, definition_id),
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

CREATE UNIQUE INDEX subject_calculation_definitions_current_approved_idx
    ON subject_calculation_definitions(tenant_id, subject_id)
    WHERE approval_status = 'approved' AND effective_to IS NULL;

CREATE TABLE master_account_memberships (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    master_subject_id TEXT NOT NULL,
    member_subject_id TEXT NOT NULL,
    reporting_mode TEXT NOT NULL CHECK (reporting_mode = 'consolidated_children'),
    eliminate_internal_transfers BOOLEAN NOT NULL DEFAULT true,
    effective_from DATE NOT NULL,
    effective_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (
        tenant_id, master_subject_id, member_subject_id, effective_from
    ),
    FOREIGN KEY (tenant_id, master_subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, member_subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE RESTRICT,
    CHECK (master_subject_id <> member_subject_id),
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE INDEX master_account_memberships_member_idx
    ON master_account_memberships(tenant_id, member_subject_id, effective_from);

CREATE TABLE subject_calculation_runs (
    calculation_run_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    definition_id UUID NOT NULL,
    requested_from DATE NOT NULL,
    requested_to DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'validated', 'published', 'failed', 'blocked'
    )),
    input_watermark_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, definition_id)
        REFERENCES subject_calculation_definitions(tenant_id, definition_id),
    CHECK (requested_to >= requested_from)
);

CREATE INDEX subject_calculation_runs_subject_idx
    ON subject_calculation_runs(tenant_id, subject_id, created_at DESC);

ALTER TABLE subject_calculation_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE subject_calculation_definitions FORCE ROW LEVEL SECURITY;
CREATE POLICY subject_calculation_definitions_tenant_policy
    ON subject_calculation_definitions
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE master_account_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE master_account_memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY master_account_memberships_tenant_policy
    ON master_account_memberships
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE subject_calculation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE subject_calculation_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY subject_calculation_runs_tenant_policy
    ON subject_calculation_runs
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

COMMENT ON TABLE subject_calculation_definitions IS
    'Versioned, owner-approved typed portfolio calculations; never executable user code.';
COMMENT ON TABLE master_account_memberships IS
    'Effective-dated child membership for a consolidated master without double-counting master totals.';
