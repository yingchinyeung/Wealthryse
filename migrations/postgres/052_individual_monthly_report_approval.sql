-- Preserve the original owner decision and every file while allowing a file's
-- website entitlement to be withdrawn and restored independently.
ALTER TABLE report_artifact_releases
    ADD COLUMN IF NOT EXISTS revoked_at TEXT,
    ADD COLUMN IF NOT EXISTS revoked_by TEXT,
    ADD COLUMN IF NOT EXISTS state_revision BIGINT NOT NULL DEFAULT 1;

ALTER TABLE report_artifact_releases
    DROP CONSTRAINT IF EXISTS report_artifact_releases_revocation_pair_check;

ALTER TABLE report_artifact_releases
    ADD CONSTRAINT report_artifact_releases_revocation_pair_check
    CHECK ((revoked_at IS NULL) = (revoked_by IS NULL));

CREATE INDEX IF NOT EXISTS idx_artifact_release_active
    ON report_artifact_releases(tenant_id, generation_run_id, release_scope)
    WHERE revoked_at IS NULL;
