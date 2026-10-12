CREATE TABLE ibkr_flex_source_validation_requests (
    validation_request_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    flex_source_id UUID NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'running', 'succeeded', 'failed', 'blocked', 'cancelled'
    )),
    requested_from DATE NOT NULL,
    requested_to DATE NOT NULL,
    requested_by_identity_sha256 TEXT CHECK (
        requested_by_identity_sha256 IS NULL
        OR requested_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    attempt_count SMALLINT NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 1),
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_phase TEXT,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, validation_request_id),
    FOREIGN KEY (tenant_id, flex_source_id)
        REFERENCES ibkr_flex_queries(tenant_id, flex_source_id) ON DELETE CASCADE,
    CHECK (requested_to >= requested_from)
);

CREATE UNIQUE INDEX ibkr_flex_source_validation_open_idx
    ON ibkr_flex_source_validation_requests(tenant_id, flex_source_id)
    WHERE status IN ('pending', 'running');

CREATE INDEX ibkr_flex_source_validation_status_idx
    ON ibkr_flex_source_validation_requests(tenant_id, status, created_at);

ALTER TABLE ibkr_flex_source_validation_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE ibkr_flex_source_validation_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY ibkr_flex_source_validation_requests_tenant_policy
    ON ibkr_flex_source_validation_requests
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

COMMENT ON TABLE ibkr_flex_source_validation_requests IS
    'Explicit one-attempt queue; the discovery worker never validates an unrequested source.';
