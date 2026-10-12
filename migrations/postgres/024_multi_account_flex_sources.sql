CREATE TABLE ibkr_flex_credentials (
    credential_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 120),
    key_vault_secret_name TEXT NOT NULL CHECK (
        key_vault_secret_name ~ '^[A-Za-z0-9-]{1,127}$'
    ),
    token_fingerprint TEXT CHECK (
        token_fingerprint IS NULL OR token_fingerprint ~ '^[0-9a-f]{16,64}$'
    ),
    status TEXT NOT NULL CHECK (status IN (
        'pending_validation', 'active', 'suspended', 'expired', 'rotating', 'revoked'
    )),
    last_validated_at TIMESTAMPTZ,
    last_rotated_at TIMESTAMPTZ,
    created_by_identity_sha256 TEXT CHECK (
        created_by_identity_sha256 IS NULL OR created_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    approved_by_identity_sha256 TEXT CHECK (
        approved_by_identity_sha256 IS NULL OR approved_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, credential_id),
    UNIQUE (tenant_id, display_name),
    UNIQUE (tenant_id, key_vault_secret_name)
);

CREATE TABLE ibkr_flex_queries (
    flex_source_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    credential_id UUID NOT NULL,
    query_id TEXT NOT NULL CHECK (query_id ~ '^[0-9]{1,20}$'),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 120),
    schedule_enabled BOOLEAN NOT NULL DEFAULT false,
    schedule_time_zone TEXT NOT NULL DEFAULT 'Asia/Hong_Kong',
    schedule_hour SMALLINT NOT NULL DEFAULT 16 CHECK (schedule_hour BETWEEN 0 AND 23),
    schedule_minute SMALLINT NOT NULL DEFAULT 0 CHECK (schedule_minute BETWEEN 0 AND 59),
    expected_sections_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL CHECK (status IN (
        'pending_validation', 'validated', 'active', 'suspended', 'retired'
    )),
    last_reference_code_sha256 TEXT CHECK (
        last_reference_code_sha256 IS NULL OR last_reference_code_sha256 ~ '^[0-9a-f]{64}$'
    ),
    last_success_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, flex_source_id),
    FOREIGN KEY (tenant_id, credential_id)
        REFERENCES ibkr_flex_credentials(tenant_id, credential_id),
    UNIQUE (tenant_id, credential_id, query_id)
);

CREATE INDEX ibkr_flex_queries_schedule_idx
    ON ibkr_flex_queries(tenant_id, schedule_enabled, status, schedule_hour, schedule_minute);

CREATE TABLE ibkr_flex_source_accounts (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    flex_source_id UUID NOT NULL,
    source_account_id TEXT NOT NULL CHECK (source_account_id ~ '^[A-Z0-9-]{3,32}$'),
    account_type TEXT,
    base_currency TEXT CHECK (base_currency IS NULL OR base_currency ~ '^[A-Z]{3}$'),
    account_name TEXT,
    discovery_status TEXT NOT NULL CHECK (discovery_status IN (
        'discovered', 'selected', 'ignored', 'not_portfolio', 'review_required'
    )),
    row_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, flex_source_id, source_account_id),
    FOREIGN KEY (tenant_id, flex_source_id)
        REFERENCES ibkr_flex_queries(tenant_id, flex_source_id) ON DELETE CASCADE
);

CREATE TABLE subject_flex_source_bindings (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    flex_source_id UUID NOT NULL,
    source_account_id TEXT NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    is_primary BOOLEAN NOT NULL DEFAULT true,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'paused', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subject_id, flex_source_id, source_account_id, effective_from),
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, flex_source_id)
        REFERENCES ibkr_flex_queries(tenant_id, flex_source_id),
    FOREIGN KEY (tenant_id, flex_source_id, source_account_id)
        REFERENCES ibkr_flex_source_accounts(tenant_id, flex_source_id, source_account_id),
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE UNIQUE INDEX subject_flex_source_primary_active_idx
    ON subject_flex_source_bindings(tenant_id, subject_id)
    WHERE is_primary AND status = 'active' AND effective_to IS NULL;

CREATE TABLE ibkr_flex_source_runs (
    source_run_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    flex_source_id UUID NOT NULL,
    requested_from DATE,
    requested_to DATE,
    status TEXT NOT NULL CHECK (status IN (
        'started', 'statement_requested', 'downloaded', 'partitioned',
        'succeeded', 'failed', 'blocked'
    )),
    reference_code_sha256 TEXT CHECK (
        reference_code_sha256 IS NULL OR reference_code_sha256 ~ '^[0-9a-f]{64}$'
    ),
    report_sha256 TEXT CHECK (
        report_sha256 IS NULL OR report_sha256 ~ '^[0-9a-f]{64}$'
    ),
    discovered_account_count INTEGER NOT NULL DEFAULT 0 CHECK (discovered_account_count >= 0),
    processed_account_count INTEGER NOT NULL DEFAULT 0 CHECK (processed_account_count >= 0),
    error_phase TEXT,
    error_code TEXT,
    correlation_id TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, source_run_id),
    CHECK (requested_to IS NULL OR requested_from IS NULL OR requested_to >= requested_from),
    FOREIGN KEY (tenant_id, flex_source_id)
        REFERENCES ibkr_flex_queries(tenant_id, flex_source_id)
);

CREATE INDEX ibkr_flex_source_runs_source_idx
    ON ibkr_flex_source_runs(tenant_id, flex_source_id, started_at DESC);

CREATE TABLE ibkr_flex_source_run_accounts (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    source_run_id UUID NOT NULL,
    source_account_id TEXT NOT NULL,
    subject_id TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'discovered', 'ignored', 'loaded', 'reconciled', 'failed'
    )),
    row_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    quality_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, source_run_id, source_account_id),
    FOREIGN KEY (tenant_id, source_run_id)
        REFERENCES ibkr_flex_source_runs(tenant_id, source_run_id) ON DELETE CASCADE
);

CREATE TABLE ibkr_credential_circuit_state (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    credential_id UUID NOT NULL,
    active_flex_source_id UUID,
    last_send_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error_phase TEXT,
    last_error_code TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    blocked_until TIMESTAMPTZ,
    blocked_reason TEXT,
    requires_manual_reset BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, credential_id),
    FOREIGN KEY (tenant_id, credential_id)
        REFERENCES ibkr_flex_credentials(tenant_id, credential_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, active_flex_source_id)
        REFERENCES ibkr_flex_queries(tenant_id, flex_source_id)
);

COMMENT ON TABLE ibkr_flex_credentials IS
    'Stores Key Vault references and non-secret token fingerprints only; never Flex tokens.';
COMMENT ON TABLE ibkr_credential_circuit_state IS
    'Credential-level circuit shared by every query using one Flex token.';
COMMENT ON TABLE ibkr_flex_source_runs IS
    'One broker SendRequest per Flex query source run, followed by same-reference polling and account partitioning.';

ALTER TABLE ibkr_flex_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE ibkr_flex_credentials FORCE ROW LEVEL SECURITY;
CREATE POLICY ibkr_flex_credentials_tenant_policy ON ibkr_flex_credentials
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE ibkr_flex_queries ENABLE ROW LEVEL SECURITY;
ALTER TABLE ibkr_flex_queries FORCE ROW LEVEL SECURITY;
CREATE POLICY ibkr_flex_queries_tenant_policy ON ibkr_flex_queries
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE ibkr_flex_source_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ibkr_flex_source_accounts FORCE ROW LEVEL SECURITY;
CREATE POLICY ibkr_flex_source_accounts_tenant_policy ON ibkr_flex_source_accounts
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE subject_flex_source_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE subject_flex_source_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY subject_flex_source_bindings_tenant_policy ON subject_flex_source_bindings
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE ibkr_flex_source_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ibkr_flex_source_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY ibkr_flex_source_runs_tenant_policy ON ibkr_flex_source_runs
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE ibkr_flex_source_run_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ibkr_flex_source_run_accounts FORCE ROW LEVEL SECURITY;
CREATE POLICY ibkr_flex_source_run_accounts_tenant_policy ON ibkr_flex_source_run_accounts
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE ibkr_credential_circuit_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE ibkr_credential_circuit_state FORCE ROW LEVEL SECURITY;
CREATE POLICY ibkr_credential_circuit_state_tenant_policy ON ibkr_credential_circuit_state
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
