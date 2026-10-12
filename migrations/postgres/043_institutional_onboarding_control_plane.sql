-- Registry-driven operating capabilities and durable workflow records.
-- No credential values, raw broker payloads, or personal information belong in
-- these tables.  Workers resolve secrets through their managed identities.

CREATE TABLE app_user_roles (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    entra_object_id TEXT NOT NULL,
    role_name TEXT NOT NULL CHECK (role_name IN (
        'owner', 'platform_admin', 'onboarding_operator',
        'portfolio_analyst_reviewer', 'compliance_reviewer',
        'final_approver', 'viewer'
    )),
    role_status TEXT NOT NULL DEFAULT 'active' CHECK (role_status IN (
        'active', 'suspended', 'revoked'
    )),
    effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_to TIMESTAMPTZ,
    granted_by_identity_sha256 TEXT CHECK (
        granted_by_identity_sha256 IS NULL
        OR granted_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    reason TEXT NOT NULL DEFAULT 'Institutional role migration',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, entra_object_id, role_name, effective_from),
    FOREIGN KEY (tenant_id, entra_object_id)
        REFERENCES app_users(tenant_id, entra_object_id) ON DELETE CASCADE,
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE UNIQUE INDEX app_user_roles_current_idx
    ON app_user_roles(tenant_id, entra_object_id, role_name)
    WHERE effective_to IS NULL AND role_status = 'active';

ALTER TABLE app_user_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user_roles FORCE ROW LEVEL SECURITY;
CREATE POLICY app_user_roles_tenant_policy
    ON app_user_roles
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

INSERT INTO app_user_roles(
    tenant_id, entra_object_id, role_name, role_status, reason
)
SELECT
    tenant_id, entra_object_id,
    CASE role_name
        WHEN 'administrator' THEN 'platform_admin'
        WHEN 'analyst' THEN 'portfolio_analyst_reviewer'
        ELSE role_name
    END,
    CASE status WHEN 'active' THEN 'active' ELSE 'suspended' END,
    'Migrated from the legacy single-role registry'
FROM app_users
ON CONFLICT DO NOTHING;

ALTER TABLE user_account_access
    DROP CONSTRAINT IF EXISTS user_account_access_access_level_check;

ALTER TABLE user_account_access
    ADD CONSTRAINT user_account_access_access_level_check
    CHECK (access_level IN (
        'read', 'report', 'analyze', 'onboard', 'approve', 'admin'
    ));

ALTER TABLE user_account_access
    ADD CONSTRAINT user_account_access_subject_fk
    FOREIGN KEY (tenant_id, subject_id)
    REFERENCES platform_subjects(tenant_id, subject_id)
    NOT VALID;

CREATE TABLE subject_operating_registrations (
    registration_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    capability TEXT NOT NULL CHECK (capability IN (
        'daily_ingestion', 'benchmark_refresh', 'sector_refresh',
        'month_end_reporting', 'analyst_assurance', 'viewer_directory'
    )),
    operating_state TEXT NOT NULL CHECK (operating_state IN (
        'preview', 'active', 'paused', 'disabled'
    )),
    effective_from DATE NOT NULL,
    effective_to DATE,
    configuration_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    configuration_sha256 TEXT NOT NULL CHECK (
        configuration_sha256 ~ '^[0-9a-f]{64}$'
    ),
    activation_approval_id UUID,
    changed_by_identity_sha256 TEXT CHECK (
        changed_by_identity_sha256 IS NULL
        OR changed_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE,
    FOREIGN KEY (activation_approval_id)
        REFERENCES account_onboarding_approvals(approval_id),
    UNIQUE (tenant_id, subject_id, capability, effective_from),
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE UNIQUE INDEX subject_operating_registrations_current_idx
    ON subject_operating_registrations(tenant_id, subject_id, capability)
    WHERE effective_to IS NULL;

CREATE INDEX subject_operating_registrations_worker_idx
    ON subject_operating_registrations(
        tenant_id, capability, operating_state, effective_from, subject_id
    );

CREATE TABLE workflow_outbox (
    event_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    aggregate_type TEXT NOT NULL CHECK (aggregate_type IN (
        'flex_source', 'onboarding_case', 'subject', 'report_batch'
    )),
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (length(trim(event_type)) BETWEEN 3 AND 100),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 16 AND 200),
    ordering_key TEXT NOT NULL CHECK (length(ordering_key) BETWEEN 3 AND 200),
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'dispatching', 'published', 'dead_letter'
    )),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    maximum_attempts INTEGER NOT NULL DEFAULT 5 CHECK (maximum_attempts BETWEEN 1 AND 20),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    UNIQUE (tenant_id, idempotency_key),
    CHECK (
        (status = 'published' AND published_at IS NOT NULL)
        OR (status <> 'published' AND published_at IS NULL)
    ),
    CHECK (
        (status = 'dispatching' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR status <> 'dispatching'
    )
);

CREATE INDEX workflow_outbox_dispatch_idx
    ON workflow_outbox(tenant_id, status, available_at, ordering_key, created_at);

CREATE TABLE institutional_work_items (
    work_item_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT,
    flex_source_id UUID,
    depends_on_work_item_id UUID,
    work_kind TEXT NOT NULL CHECK (work_kind IN (
        'flex_statement_request', 'source_partition', 'account_import',
        'benchmark_refresh', 'sector_refresh', 'analytics_rebuild',
        'historical_report', 'month_end_report', 'quality_assurance'
    )),
    ordering_key TEXT NOT NULL CHECK (length(ordering_key) BETWEEN 3 AND 200),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 16 AND 200),
    priority SMALLINT NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
        'queued', 'running', 'succeeded', 'failed', 'dead_letter', 'cancelled'
    )),
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    maximum_attempts INTEGER NOT NULL DEFAULT 3 CHECK (maximum_attempts BETWEEN 1 AND 20),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, flex_source_id)
        REFERENCES ibkr_flex_queries(tenant_id, flex_source_id),
    FOREIGN KEY (depends_on_work_item_id)
        REFERENCES institutional_work_items(work_item_id),
    CHECK (
        (status = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR status <> 'running'
    ),
    CHECK (
        (status IN ('succeeded', 'failed', 'dead_letter', 'cancelled')
            AND completed_at IS NOT NULL)
        OR status NOT IN ('succeeded', 'failed', 'dead_letter', 'cancelled')
    )
);

CREATE INDEX institutional_work_items_claim_idx
    ON institutional_work_items(
        tenant_id, status, priority DESC, available_at, ordering_key, created_at
    );

ALTER TABLE subject_operating_registrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE subject_operating_registrations FORCE ROW LEVEL SECURITY;
CREATE POLICY subject_operating_registrations_tenant_policy
    ON subject_operating_registrations
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE workflow_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY workflow_outbox_tenant_policy
    ON workflow_outbox
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE institutional_work_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE institutional_work_items FORCE ROW LEVEL SECURITY;
CREATE POLICY institutional_work_items_tenant_policy
    ON institutional_work_items
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

COMMENT ON TABLE subject_operating_registrations IS
    'Database source of truth for scheduled subject capabilities; account IDs must not be embedded in job infrastructure.';
COMMENT ON TABLE workflow_outbox IS
    'Transactional state-change events awaiting durable dispatch; payloads must exclude credentials, raw statements, and personal information.';
COMMENT ON TABLE institutional_work_items IS
    'Idempotent asynchronous work. Flex statement requests use a credential ordering key and maximum_attempts=1.';

-- Preserve the current approved pilot behaviour without activating any new
-- account.  U16088606 receives preview refreshes only while onboarding.
WITH registrations(
    subject_id, capability, operating_state, effective_from,
    configuration_json, configuration_sha256
) AS (
    VALUES
        ('U16088571', 'daily_ingestion', 'active', '2025-01-20'::date,
            '{"start_date":"2025-01-20","source":"existing_v11"}'::jsonb,
            '1043acbd3ff44ed1b1c4041734912c2bbc559ec4bf3bf6db6bd951fcf4458041'),
        ('U16088571', 'benchmark_refresh', 'active', '2025-01-20'::date,
            '{"start_date":"2025-01-20"}'::jsonb,
            'b6bae9233bd61e5815203b181af7743e90ab653884920bfeeba2a87100524dcd'),
        ('U16088571', 'sector_refresh', 'active', '2025-01-20'::date,
            '{"external_approval_required":true}'::jsonb,
            'cb2355c983d282adcb239bc5381ab041d1fe7e04a8886e4a973f423e6798af40'),
        ('U16088571', 'month_end_reporting', 'active', '2025-01-20'::date,
            '{"approval_required":true}'::jsonb,
            '9651ac187bac82842493bea8651a30d38b07b0a418197c7ddb954b005003a533'),
        ('U16088571', 'analyst_assurance', 'active', '2025-01-20'::date,
            '{}'::jsonb,
            '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'),
        ('U16088571', 'viewer_directory', 'active', '2025-01-20'::date,
            '{}'::jsonb,
            '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'),
        ('U16088606', 'daily_ingestion', 'preview', '2025-01-20'::date,
            '{"start_date":"2025-01-20","source":"existing_v11"}'::jsonb,
            '1043acbd3ff44ed1b1c4041734912c2bbc559ec4bf3bf6db6bd951fcf4458041'),
        ('U16088606', 'benchmark_refresh', 'preview', '2025-01-20'::date,
            '{"start_date":"2025-01-20"}'::jsonb,
            'b6bae9233bd61e5815203b181af7743e90ab653884920bfeeba2a87100524dcd'),
        ('U16088606', 'sector_refresh', 'preview', '2025-01-20'::date,
            '{"external_approval_required":true}'::jsonb,
            'cb2355c983d282adcb239bc5381ab041d1fe7e04a8886e4a973f423e6798af40')
)
INSERT INTO subject_operating_registrations(
    registration_id, tenant_id, subject_id, capability, operating_state,
    effective_from, configuration_json, configuration_sha256
)
SELECT
    md5(subject_id || ':' || capability || ':' || effective_from::text)::uuid,
    app_tenant_id(), subject_id, capability, operating_state,
    effective_from, configuration_json, configuration_sha256
FROM registrations
WHERE EXISTS (
    SELECT 1 FROM platform_subjects subjects
    WHERE subjects.tenant_id = app_tenant_id()
      AND subjects.subject_id = registrations.subject_id
)
ON CONFLICT (tenant_id, subject_id, capability, effective_from) DO NOTHING;
