CREATE TABLE subject_report_template_assets (
    template_asset_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    report_type TEXT NOT NULL CHECK (report_type IN ('factsheet', 'presentation')),
    asset_role TEXT NOT NULL CHECK (asset_role IN ('template', 'completed_example')),
    version_label TEXT NOT NULL,
    container_name TEXT NOT NULL,
    blob_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    byte_count BIGINT NOT NULL CHECK (byte_count > 0 AND byte_count <= 52428800),
    approval_status TEXT NOT NULL CHECK (approval_status IN (
        'intake', 'validated', 'active', 'superseded', 'rejected'
    )),
    is_current BOOLEAN NOT NULL DEFAULT false,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE,
    UNIQUE (tenant_id, subject_id, report_type, asset_role, version_label),
    UNIQUE (tenant_id, container_name, blob_name)
);

CREATE UNIQUE INDEX subject_report_template_assets_current_idx
    ON subject_report_template_assets(tenant_id, subject_id, report_type, asset_role)
    WHERE is_current;

CREATE INDEX subject_report_template_assets_subject_idx
    ON subject_report_template_assets(
        tenant_id, subject_id, report_type, asset_role, updated_at DESC
    );

ALTER TABLE subject_report_template_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE subject_report_template_assets FORCE ROW LEVEL SECURITY;
CREATE POLICY subject_report_template_assets_tenant_policy
    ON subject_report_template_assets
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());
