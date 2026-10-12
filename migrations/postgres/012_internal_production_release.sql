ALTER TABLE report_generation_runs
    DROP CONSTRAINT IF EXISTS report_generation_runs_status_check;

ALTER TABLE report_generation_runs
    ADD CONSTRAINT report_generation_runs_status_check
    CHECK (status IN (
        'queued', 'running', 'success', 'failed', 'awaiting_approval',
        'released_internal', 'published'
    ));

CREATE TABLE IF NOT EXISTS report_release_decisions (
    release_decision_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    snapshot_id UUID NOT NULL REFERENCES reporting_snapshots(snapshot_id),
    release_scope TEXT NOT NULL CHECK (release_scope IN ('internal_shareholder', 'client')),
    status TEXT NOT NULL CHECK (status IN ('approved', 'revoked')),
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    UNIQUE (tenant_id, snapshot_id, release_scope, status)
);

CREATE TABLE IF NOT EXISTS report_artifact_releases (
    artifact_release_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    release_decision_id BIGINT NOT NULL REFERENCES report_release_decisions(release_decision_id),
    generation_run_id BIGINT NOT NULL REFERENCES report_generation_runs(generation_run_id),
    release_scope TEXT NOT NULL CHECK (release_scope IN ('internal_shareholder', 'client')),
    container_name TEXT NOT NULL,
    blob_name TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    released_at TEXT NOT NULL,
    UNIQUE (tenant_id, generation_run_id, release_scope)
);

CREATE OR REPLACE FUNCTION enforce_report_release_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    snapshot_publication_allowed BOOLEAN;
    snapshot_quality TEXT;
    snapshot_status TEXT;
BEGIN
    SELECT publication_allowed, quality_status, status
      INTO snapshot_publication_allowed, snapshot_quality, snapshot_status
      FROM reporting_snapshots
     WHERE tenant_id = NEW.tenant_id AND snapshot_id = NEW.snapshot_id;

    IF snapshot_status NOT IN ('frozen', 'approved') OR snapshot_quality = 'fail' THEN
        RAISE EXCEPTION 'Only a frozen, non-failing snapshot may be released';
    END IF;

    IF NEW.release_scope = 'client'
       AND NEW.status = 'approved'
       AND NOT snapshot_publication_allowed THEN
        RAISE EXCEPTION 'Client release is blocked until publication_allowed is true';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS report_release_scope_guard ON report_release_decisions;
CREATE TRIGGER report_release_scope_guard
BEFORE INSERT OR UPDATE ON report_release_decisions
FOR EACH ROW EXECUTE FUNCTION enforce_report_release_scope();

CREATE INDEX IF NOT EXISTS idx_report_release_snapshot
    ON report_release_decisions(tenant_id, snapshot_id, release_scope, status);

CREATE INDEX IF NOT EXISTS idx_artifact_release_run
    ON report_artifact_releases(tenant_id, generation_run_id, release_scope);

ALTER TABLE report_release_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_release_decisions FORCE ROW LEVEL SECURITY;
ALTER TABLE report_artifact_releases ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_artifact_releases FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON report_release_decisions;
CREATE POLICY tenant_isolation ON report_release_decisions
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON report_artifact_releases;
CREATE POLICY tenant_isolation ON report_artifact_releases
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());
