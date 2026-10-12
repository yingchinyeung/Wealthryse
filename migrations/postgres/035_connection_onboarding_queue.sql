CREATE TABLE account_onboarding_queues (
    queue_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    flex_source_id UUID NOT NULL,
    queue_status TEXT NOT NULL CHECK (queue_status IN (
        'draft', 'active', 'completed', 'cancelled'
    )),
    created_by_identity_sha256 TEXT CHECK (
        created_by_identity_sha256 IS NULL
        OR created_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, queue_id),
    FOREIGN KEY (tenant_id, flex_source_id)
        REFERENCES ibkr_flex_queries(tenant_id, flex_source_id) ON DELETE CASCADE,
    CHECK (
        (queue_status = 'completed' AND completed_at IS NOT NULL)
        OR (queue_status <> 'completed' AND completed_at IS NULL)
    )
);

CREATE UNIQUE INDEX account_onboarding_queues_open_source_idx
    ON account_onboarding_queues(tenant_id, flex_source_id)
    WHERE queue_status IN ('draft', 'active');

CREATE TABLE account_onboarding_queue_items (
    queue_item_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    queue_id UUID NOT NULL,
    flex_source_id UUID NOT NULL,
    source_account_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL CHECK (sequence_number BETWEEN 1 AND 100),
    item_status TEXT NOT NULL CHECK (item_status IN (
        'current', 'queued', 'completed', 'deferred', 'cancelled'
    )),
    onboarding_case_id UUID REFERENCES account_onboarding_cases(case_id),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, queue_item_id),
    UNIQUE (tenant_id, queue_id, source_account_id),
    UNIQUE (tenant_id, queue_id, sequence_number),
    FOREIGN KEY (tenant_id, queue_id)
        REFERENCES account_onboarding_queues(tenant_id, queue_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, flex_source_id, source_account_id)
        REFERENCES ibkr_flex_source_accounts(tenant_id, flex_source_id, source_account_id),
    CHECK (
        (item_status = 'current' AND started_at IS NOT NULL AND completed_at IS NULL)
        OR (item_status = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL)
        OR (item_status IN ('queued', 'deferred', 'cancelled') AND completed_at IS NULL)
    )
);

CREATE UNIQUE INDEX account_onboarding_queue_one_current_idx
    ON account_onboarding_queue_items(tenant_id, queue_id)
    WHERE item_status = 'current';

CREATE INDEX account_onboarding_queue_items_order_idx
    ON account_onboarding_queue_items(tenant_id, queue_id, sequence_number);

ALTER TABLE account_onboarding_queues ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_onboarding_queues FORCE ROW LEVEL SECURITY;
CREATE POLICY account_onboarding_queues_tenant_policy
    ON account_onboarding_queues
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE account_onboarding_queue_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_onboarding_queue_items FORCE ROW LEVEL SECURITY;
CREATE POLICY account_onboarding_queue_items_tenant_policy
    ON account_onboarding_queue_items
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

COMMENT ON TABLE account_onboarding_queues IS
    'One ordered, connection-scoped account onboarding queue; only one open queue is allowed per Flex source.';
COMMENT ON TABLE account_onboarding_queue_items IS
    'Strictly sequential account onboarding. A partial unique index permits only one current account per queue.';
