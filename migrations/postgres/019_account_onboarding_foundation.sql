CREATE TABLE platform_subjects (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    subject_type TEXT NOT NULL CHECK (subject_type IN (
        'physical_account', 'master_account', 'filtered_portfolio',
        'composite_portfolio', 'benchmark'
    )),
    display_name TEXT NOT NULL,
    source_account_id TEXT,
    account_type TEXT,
    base_currency TEXT,
    reporting_currency TEXT,
    history_start_date DATE,
    inception_date DATE,
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
        'draft', 'onboarding', 'preview', 'active', 'paused', 'archived'
    )),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subject_id)
);

CREATE INDEX platform_subjects_status_idx
    ON platform_subjects(tenant_id, lifecycle_status, subject_type, subject_id);

CREATE TABLE onboarding_source_accounts (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    source_system TEXT NOT NULL CHECK (source_system IN ('existing_v11', 'ibkr_flex')),
    source_account_id TEXT NOT NULL,
    account_type TEXT,
    base_currency TEXT,
    account_name TEXT,
    nav_start_date DATE,
    nav_end_date DATE,
    row_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_export_sha256 TEXT NOT NULL CHECK (source_export_sha256 ~ '^[0-9a-f]{64}$'),
    eligibility_status TEXT NOT NULL CHECK (eligibility_status IN (
        'eligible', 'review_required', 'not_portfolio'
    )),
    eligibility_reason TEXT NOT NULL,
    first_discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source_system, source_account_id)
);

CREATE INDEX onboarding_source_accounts_eligibility_idx
    ON onboarding_source_accounts(tenant_id, source_system, eligibility_status, source_account_id);

CREATE TABLE account_onboarding_cases (
    case_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    target_subject_id TEXT NOT NULL,
    source_system TEXT NOT NULL CHECK (source_system IN ('existing_v11', 'ibkr_flex')),
    source_account_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN (
        'discovered', 'data_import', 'financial_contract',
        'analytics_configuration', 'template_intake', 'reconciliation',
        'website_preview', 'activation_ready', 'active'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'draft', 'in_progress', 'blocked', 'ready', 'active', 'cancelled'
    )),
    requested_history_start DATE,
    reporting_currency TEXT,
    configuration_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_identity_sha256 TEXT CHECK (
        created_by_identity_sha256 IS NULL OR created_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at TIMESTAMPTZ,
    UNIQUE (tenant_id, source_system, source_account_id),
    FOREIGN KEY (tenant_id, target_subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id)
);

CREATE INDEX account_onboarding_cases_status_idx
    ON account_onboarding_cases(tenant_id, status, stage, updated_at DESC);

CREATE TABLE account_onboarding_checks (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    case_id UUID NOT NULL REFERENCES account_onboarding_cases(case_id) ON DELETE CASCADE,
    check_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'pass', 'warn', 'fail')),
    message TEXT NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, case_id, check_id)
);

CREATE INDEX account_onboarding_checks_status_idx
    ON account_onboarding_checks(tenant_id, case_id, status, check_id);

CREATE TABLE subject_feature_states (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    feature_key TEXT NOT NULL CHECK (feature_key IN (
        'data_ingestion', 'website_analytics', 'ai_analyst',
        'factsheet', 'presentation', 'viewer_access'
    )),
    feature_state TEXT NOT NULL CHECK (feature_state IN (
        'disabled', 'preview', 'active', 'blocked'
    )),
    reason TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subject_id, feature_key),
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE
);

CREATE TABLE subject_dependencies (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    depends_on_subject_id TEXT NOT NULL,
    dependency_type TEXT NOT NULL CHECK (dependency_type IN (
        'source_account', 'portfolio_component', 'benchmark', 'fx', 'cash_ledger'
    )),
    effective_from DATE NOT NULL,
    effective_to DATE,
    configuration_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (
        tenant_id, subject_id, depends_on_subject_id, dependency_type, effective_from
    ),
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE,
    CHECK (subject_id <> depends_on_subject_id),
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE INDEX subject_dependencies_parent_idx
    ON subject_dependencies(tenant_id, depends_on_subject_id, effective_from);

ALTER TABLE platform_subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_subjects FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_subjects_tenant_policy ON platform_subjects
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE onboarding_source_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding_source_accounts FORCE ROW LEVEL SECURITY;
CREATE POLICY onboarding_source_accounts_tenant_policy ON onboarding_source_accounts
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE account_onboarding_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_onboarding_cases FORCE ROW LEVEL SECURITY;
CREATE POLICY account_onboarding_cases_tenant_policy ON account_onboarding_cases
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE account_onboarding_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_onboarding_checks FORCE ROW LEVEL SECURITY;
CREATE POLICY account_onboarding_checks_tenant_policy ON account_onboarding_checks
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE subject_feature_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE subject_feature_states FORCE ROW LEVEL SECURITY;
CREATE POLICY subject_feature_states_tenant_policy ON subject_feature_states
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE subject_dependencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE subject_dependencies FORCE ROW LEVEL SECURITY;
CREATE POLICY subject_dependencies_tenant_policy ON subject_dependencies
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());
