-- Auditable identity-registry revisions and worker recovery controls.
-- The manifest digest is retained; the manifest itself is not stored here so
-- personal information is not duplicated in operational audit records.

CREATE TABLE identity_registry_revisions (
    revision_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    manifest_sha256 TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    apply_mode TEXT NOT NULL CHECK (apply_mode IN ('merge', 'replace')),
    status TEXT NOT NULL CHECK (status IN ('validated', 'applied', 'failed')),
    user_count INTEGER NOT NULL CHECK (user_count >= 0),
    role_count INTEGER NOT NULL CHECK (role_count >= 0),
    entitlement_count INTEGER NOT NULL CHECK (entitlement_count >= 0),
    applied_by_identity_sha256 TEXT NOT NULL CHECK (
        applied_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at TIMESTAMPTZ,
    UNIQUE (tenant_id, manifest_sha256),
    CHECK (
        (status = 'applied' AND applied_at IS NOT NULL)
        OR (status <> 'applied' AND applied_at IS NULL)
    )
);

ALTER TABLE identity_registry_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_registry_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY identity_registry_revisions_tenant_policy
    ON identity_registry_revisions
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

CREATE INDEX institutional_work_items_dependency_idx
    ON institutional_work_items(tenant_id, depends_on_work_item_id, status);

CREATE INDEX institutional_work_items_lease_idx
    ON institutional_work_items(tenant_id, status, lease_expires_at)
    WHERE status = 'running';

COMMENT ON TABLE identity_registry_revisions IS
    'Integrity-bound audit of database authorization manifests; manifest contents and credentials are never stored.';
