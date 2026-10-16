-- Factsheet designs built by the design agents (reader, builder, checker, editor).
-- A design is a JSON spec in the closed vocabulary of ibkr_etl.factsheet_spec;
-- every change is a new version, so undo and audit read the version list.
-- Additive only. Nothing here qualifies a design or changes a report: a
-- submitted design becomes an ordinary report design request (062), and the
-- release service still qualifies it.

CREATE TABLE IF NOT EXISTS factsheet_design_files (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    content_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count BETWEEN 1 AND 52428800),
    content BYTEA NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, sha256)
);

CREATE TABLE IF NOT EXISTS factsheet_design_drafts (
    draft_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    subject_id TEXT NOT NULL,
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
    template_sha256 TEXT NOT NULL,
    example_sha256 TEXT,
    more_examples_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('review', 'failed', 'submitted', 'archived')),
    current_version INTEGER NOT NULL CHECK (current_version >= 1),
    build_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id, template_sha256) REFERENCES factsheet_design_files(tenant_id, sha256),
    FOREIGN KEY (tenant_id, example_sha256) REFERENCES factsheet_design_files(tenant_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_factsheet_design_drafts_subject
    ON factsheet_design_drafts(tenant_id, subject_id, created_at);

CREATE TABLE IF NOT EXISTS factsheet_design_versions (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    draft_id UUID NOT NULL REFERENCES factsheet_design_drafts(draft_id),
    version INTEGER NOT NULL CHECK (version >= 1),
    spec_json TEXT NOT NULL,
    spec_sha256 TEXT NOT NULL CHECK (spec_sha256 ~ '^[0-9a-f]{64}$'),
    parent_version INTEGER,
    source TEXT NOT NULL CHECK (source IN ('reader', 'builder', 'editor', 'undo', 'answer')),
    summary TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    checks_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (draft_id, version)
);

CREATE TABLE IF NOT EXISTS factsheet_design_proposals (
    proposal_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    draft_id UUID NOT NULL REFERENCES factsheet_design_drafts(draft_id),
    base_version INTEGER NOT NULL,
    request TEXT NOT NULL CHECK (length(request) BETWEEN 1 AND 2000),
    proposal_json TEXT NOT NULL,
    spec_json TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'discarded')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_factsheet_design_proposals_draft
    ON factsheet_design_proposals(draft_id, created_at);

ALTER TABLE factsheet_design_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE factsheet_design_files FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON factsheet_design_files;
CREATE POLICY tenant_isolation ON factsheet_design_files
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE factsheet_design_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE factsheet_design_drafts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON factsheet_design_drafts;
CREATE POLICY tenant_isolation ON factsheet_design_drafts
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE factsheet_design_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE factsheet_design_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON factsheet_design_versions;
CREATE POLICY tenant_isolation ON factsheet_design_versions
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

ALTER TABLE factsheet_design_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE factsheet_design_proposals FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON factsheet_design_proposals;
CREATE POLICY tenant_isolation ON factsheet_design_proposals
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
