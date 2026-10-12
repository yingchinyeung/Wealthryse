-- Qualified designs are registered by the release service, never by browser input.
CREATE TABLE IF NOT EXISTS qualified_report_designs (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    design_version TEXT NOT NULL,
    runtime_image_sha256 TEXT NOT NULL CHECK (runtime_image_sha256 ~ '^[0-9a-f]{64}$'),
    template_versions_json TEXT NOT NULL,
    qualification_json TEXT NOT NULL,
    qualified_at TEXT NOT NULL,
    retired_at TEXT,
    PRIMARY KEY (tenant_id, subject_id, design_version)
);

CREATE TABLE IF NOT EXISTS report_design_update_requests (
    request_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    description TEXT NOT NULL CHECK (length(description) BETWEEN 10 AND 4000),
    status TEXT NOT NULL CHECK (status IN ('requested','queued','running','awaiting_review','completed','failed')),
    design_version TEXT,
    plan_json TEXT,
    plan_sha256 TEXT CHECK (plan_sha256 IS NULL OR plan_sha256 ~ '^[0-9a-f]{64}$'),
    updated_at TEXT NOT NULL,
    result_json TEXT,
    lease_token UUID,
    lease_until TEXT,
    UNIQUE (tenant_id, subject_id, request_id),
    CHECK (status='requested' OR
           (design_version IS NOT NULL AND plan_json IS NOT NULL AND plan_sha256 IS NOT NULL)),
    FOREIGN KEY (tenant_id, subject_id, design_version)
        REFERENCES qualified_report_designs(tenant_id, subject_id, design_version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_live_report_design_update
    ON report_design_update_requests(tenant_id, subject_id)
    WHERE status IN ('queued','running','awaiting_review');

CREATE TABLE IF NOT EXISTS active_report_designs (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    design_version TEXT NOT NULL,
    request_id UUID NOT NULL,
    activated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, subject_id),
    FOREIGN KEY (tenant_id, subject_id, request_id)
        REFERENCES report_design_update_requests(tenant_id, subject_id, request_id),
    FOREIGN KEY (tenant_id, subject_id, design_version)
        REFERENCES qualified_report_designs(tenant_id, subject_id, design_version)
);

ALTER TABLE report_artifact_releases
    ADD COLUMN IF NOT EXISTS superseded_by_artifact_release_id BIGINT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_report_release_tenant_identity
    ON report_artifact_releases(tenant_id, artifact_release_id);
ALTER TABLE report_artifact_releases
    ADD CONSTRAINT report_supersession_tenant_fk
    FOREIGN KEY (tenant_id, superseded_by_artifact_release_id)
    REFERENCES report_artifact_releases(tenant_id, artifact_release_id);
ALTER TABLE report_artifact_releases
    ADD CONSTRAINT report_artifact_no_self_supersession
    CHECK (superseded_by_artifact_release_id IS NULL OR
           superseded_by_artifact_release_id <> artifact_release_id);
ALTER TABLE report_artifact_releases
    ADD CONSTRAINT superseded_report_is_withdrawn
    CHECK (superseded_by_artifact_release_id IS NULL OR revoked_at IS NOT NULL);

ALTER TABLE qualified_report_designs ENABLE ROW LEVEL SECURITY;
ALTER TABLE qualified_report_designs FORCE ROW LEVEL SECURITY;
ALTER TABLE report_design_update_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_design_update_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE active_report_designs ENABLE ROW LEVEL SECURITY;
ALTER TABLE active_report_designs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON qualified_report_designs
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
CREATE POLICY tenant_isolation ON report_design_update_requests
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
CREATE POLICY tenant_isolation ON active_report_designs
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
