-- New account onboarding: broker connections, one case per broker account
-- or derived portfolio, and one security identity across brokers.
--
-- Additive only. No existing table, constraint or row changes, and nothing
-- reads these tables until ONBOARDING_V2_ENABLED is set to true.

CREATE TABLE broker_connections (
    connection_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    broker TEXT NOT NULL CHECK (broker IN ('ibkr_flex', 'kiwoom_rest')),
    environment TEXT NOT NULL CHECK (environment IN ('live', 'mock')),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 120),
    -- The secret stays in Key Vault; only its name and a short fingerprint live here.
    key_vault_secret_name TEXT NOT NULL CHECK (key_vault_secret_name ~ (
        '^(ibkr-flex-source-|broker-kiwoom-rest-)[0-9a-f]{32}$'
    )),
    credential_fingerprint TEXT NOT NULL CHECK (credential_fingerprint ~ '^[0-9a-f]{16}$'),
    credential_expires_on DATE,
    ip_locked_to INET,
    status TEXT NOT NULL CHECK (status IN (
        'pending_check', 'healthy', 'failing', 'suspended', 'retired'
    )),
    last_checked_at TIMESTAMPTZ,
    last_check_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_identity_sha256 TEXT CHECK (
        created_by_identity_sha256 IS NULL OR created_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, connection_id),
    UNIQUE (tenant_id, display_name),
    UNIQUE (tenant_id, key_vault_secret_name),
    CHECK (broker <> 'ibkr_flex' OR (
        environment = 'live'
        AND credential_expires_on IS NOT NULL
        AND key_vault_secret_name LIKE 'ibkr-flex-source-%'
    )),
    CHECK (broker <> 'kiwoom_rest' OR key_vault_secret_name LIKE 'broker-kiwoom-rest-%')
);

COMMENT ON TABLE broker_connections IS
    'One broker login. IBKR rows share their ID with ibkr_flex_credentials so the existing Flex fetch reads the same secret.';

CREATE TABLE broker_connection_accounts (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    connection_id UUID NOT NULL,
    broker_account_id TEXT NOT NULL CHECK (broker_account_id ~ '^(U[0-9]{6,12}|[0-9]{8}|[0-9]{10})$'),
    account_name TEXT,
    base_currency TEXT CHECK (base_currency IS NULL OR base_currency ~ '^[A-Z]{3}$'),
    discovery_status TEXT NOT NULL CHECK (discovery_status IN (
        'discovered', 'case_started', 'live', 'ignored', 'no_longer_seen'
    )),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, connection_id, broker_account_id),
    FOREIGN KEY (tenant_id, connection_id)
        REFERENCES broker_connections(tenant_id, connection_id) ON DELETE CASCADE
);

CREATE TABLE broker_onboarding_cases (
    case_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    -- A broker account comes from a connection; a filtered or composite
    -- portfolio is calculated from live subjects by a typed definition.
    source_kind TEXT NOT NULL DEFAULT 'broker_account' CHECK (source_kind IN (
        'broker_account', 'filtered_portfolio', 'composite_portfolio'
    )),
    connection_id UUID,
    broker TEXT CHECK (broker IN ('ibkr_flex', 'kiwoom_rest')),
    broker_account_id TEXT,
    definition_json JSONB,
    definition_sha256 TEXT CHECK (definition_sha256 ~ '^[0-9a-f]{64}$'),
    -- The platform subject this case creates, reserved from the start.
    target_subject_id TEXT NOT NULL CHECK (target_subject_id ~ '^[A-Za-z0-9][A-Za-z0-9-]{2,63}$'),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 160),
    reporting_currency TEXT NOT NULL CHECK (reporting_currency ~ '^[A-Z]{3}$'),
    history_start_date DATE NOT NULL,
    -- Registered in platform_subjects at approval 1, not before.
    subject_id TEXT,
    stage TEXT NOT NULL CHECK (stage IN (
        'request', 'load_and_reconcile', 'build_history', 'first_month_end', 'closed'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'platform_working', 'waiting_on_owner', 'blocked', 'live', 'closed', 'cancelled'
    )),
    created_by_identity_sha256 TEXT CHECK (
        created_by_identity_sha256 IS NULL OR created_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    went_live_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, case_id),
    FOREIGN KEY (tenant_id, connection_id, broker_account_id)
        REFERENCES broker_connection_accounts(tenant_id, connection_id, broker_account_id),
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id),
    CHECK (
        (source_kind = 'broker_account'
         AND connection_id IS NOT NULL AND broker IS NOT NULL AND broker_account_id IS NOT NULL
         AND definition_json IS NULL AND definition_sha256 IS NULL)
        OR (source_kind <> 'broker_account'
         AND connection_id IS NULL AND broker IS NULL AND broker_account_id IS NULL
         AND definition_json IS NOT NULL AND definition_sha256 IS NOT NULL)
    ),
    CHECK (subject_id IS NULL OR subject_id = target_subject_id),
    CHECK ((stage = 'closed') = (status = 'closed')),
    CHECK (stage IN ('request', 'load_and_reconcile') OR status = 'cancelled' OR subject_id IS NOT NULL),
    CHECK (went_live_at IS NOT NULL OR stage NOT IN ('first_month_end', 'closed')),
    CHECK (closed_at IS NOT NULL OR stage <> 'closed')
);

-- One account at a time: at most one case before go-live per tenant.
CREATE UNIQUE INDEX broker_onboarding_one_pre_live_case_idx
    ON broker_onboarding_cases(tenant_id)
    WHERE stage IN ('request', 'load_and_reconcile', 'build_history') AND status <> 'cancelled';

-- Never the same broker account twice, whichever connection found it.
CREATE UNIQUE INDEX broker_onboarding_one_case_per_account_idx
    ON broker_onboarding_cases(tenant_id, broker, broker_account_id)
    WHERE status <> 'cancelled' AND source_kind = 'broker_account';

-- Never two cases for the same platform subject.
CREATE UNIQUE INDEX broker_onboarding_one_case_per_subject_idx
    ON broker_onboarding_cases(tenant_id, target_subject_id)
    WHERE status <> 'cancelled';

CREATE INDEX broker_onboarding_cases_status_idx
    ON broker_onboarding_cases(tenant_id, status, stage, updated_at DESC);

COMMENT ON TABLE broker_onboarding_cases IS
    'One onboarding case per broker account or derived portfolio, four stages, three owner approvals.';

CREATE TABLE broker_onboarding_checks (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    case_id UUID NOT NULL,
    check_id TEXT NOT NULL CHECK (check_id ~ '^[a-z][a-z0-9_]{2,63}$'),
    status TEXT NOT NULL CHECK (status IN ('pending', 'pass', 'warn', 'fail')),
    message TEXT NOT NULL CHECK (length(message) BETWEEN 1 AND 1000),
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    warning_acknowledged_at TIMESTAMPTZ,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, case_id, check_id),
    FOREIGN KEY (tenant_id, case_id)
        REFERENCES broker_onboarding_cases(tenant_id, case_id) ON DELETE CASCADE
);

CREATE TABLE broker_onboarding_approvals (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    case_id UUID NOT NULL,
    approval_kind TEXT NOT NULL CHECK (approval_kind IN (
        'account_terms', 'historical_reports', 'go_live'
    )),
    terms_json JSONB NOT NULL,
    terms_sha256 TEXT NOT NULL CHECK (terms_sha256 ~ '^[0-9a-f]{64}$'),
    approved_by_identity_sha256 TEXT NOT NULL CHECK (approved_by_identity_sha256 ~ '^[0-9a-f]{64}$'),
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, case_id, approval_kind),
    FOREIGN KEY (tenant_id, case_id)
        REFERENCES broker_onboarding_cases(tenant_id, case_id) ON DELETE RESTRICT
);

CREATE TABLE broker_onboarding_events (
    event_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    case_id UUID NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'created', 'check_recorded', 'warning_acknowledged', 'approved',
        'advanced', 'cancelled', 'closed'
    )),
    from_stage TEXT NOT NULL,
    to_stage TEXT NOT NULL,
    actor_identity_sha256 TEXT CHECK (
        actor_identity_sha256 IS NULL OR actor_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, case_id)
        REFERENCES broker_onboarding_cases(tenant_id, case_id) ON DELETE RESTRICT
);

CREATE INDEX broker_onboarding_events_case_idx
    ON broker_onboarding_events(tenant_id, case_id, created_at);

CREATE FUNCTION reject_broker_onboarding_history_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Onboarding approvals and events are append-only';
END;
$$;
REVOKE ALL ON FUNCTION reject_broker_onboarding_history_change() FROM PUBLIC;
CREATE TRIGGER broker_onboarding_approvals_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON broker_onboarding_approvals
    FOR EACH STATEMENT EXECUTE FUNCTION reject_broker_onboarding_history_change();
CREATE TRIGGER broker_onboarding_events_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON broker_onboarding_events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_broker_onboarding_history_change();

ALTER TABLE broker_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE broker_connections FORCE ROW LEVEL SECURITY;
CREATE POLICY broker_connections_tenant_policy ON broker_connections
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE broker_connection_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE broker_connection_accounts FORCE ROW LEVEL SECURITY;
CREATE POLICY broker_connection_accounts_tenant_policy ON broker_connection_accounts
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE broker_onboarding_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE broker_onboarding_cases FORCE ROW LEVEL SECURITY;
CREATE POLICY broker_onboarding_cases_tenant_policy ON broker_onboarding_cases
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE broker_onboarding_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE broker_onboarding_checks FORCE ROW LEVEL SECURITY;
CREATE POLICY broker_onboarding_checks_tenant_policy ON broker_onboarding_checks
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE broker_onboarding_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE broker_onboarding_approvals FORCE ROW LEVEL SECURITY;
CREATE POLICY broker_onboarding_approvals_tenant_policy ON broker_onboarding_approvals
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE broker_onboarding_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE broker_onboarding_events FORCE ROW LEVEL SECURITY;
CREATE POLICY broker_onboarding_events_tenant_policy ON broker_onboarding_events
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

-- Security identity is reference data shared by every tenant, like instruments.
CREATE TABLE security_master (
    security_key TEXT PRIMARY KEY CHECK (security_key ~ (
        '^([0-9]{1,12}|KRX:[0-9A-Z]{6}|ISIN:[A-Z]{2}[A-Z0-9]{9}[0-9])$'
    )),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 200),
    asset_class TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    primary_exchange TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE security_identifiers (
    scheme TEXT NOT NULL CHECK (scheme IN ('ibkr_conid', 'isin', 'krx_code')),
    identifier TEXT NOT NULL,
    security_key TEXT NOT NULL REFERENCES security_master(security_key),
    source TEXT NOT NULL CHECK (source IN ('ibkr_flex', 'kiwoom_rest', 'owner')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scheme, identifier),
    CHECK (
        (scheme = 'ibkr_conid' AND identifier ~ '^[0-9]{1,12}$')
        OR (scheme = 'isin' AND identifier ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$')
        OR (scheme = 'krx_code' AND identifier ~ '^[0-9A-Z]{6}$')
    )
);

CREATE INDEX security_identifiers_key_idx ON security_identifiers(security_key);

COMMENT ON TABLE security_master IS
    'One identity per security across brokers. IBKR securities keep their conid as the key; others use a namespaced key such as KRX:005930.';
