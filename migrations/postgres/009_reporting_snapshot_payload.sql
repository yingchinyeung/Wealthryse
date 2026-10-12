ALTER TABLE reporting_snapshots
    ADD COLUMN IF NOT EXISTS snapshot_payload_json TEXT NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS publication_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS publication_blockers_json TEXT NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS benchmark_id TEXT,
    ADD COLUMN IF NOT EXISTS benchmark_convention TEXT,
    ADD COLUMN IF NOT EXISTS benchmark_provider TEXT,
    ADD COLUMN IF NOT EXISTS data_as_of TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_reporting_snapshot_content
    ON reporting_snapshots(tenant_id, subject_id, period_end, data_sha256);

CREATE OR REPLACE FUNCTION reject_frozen_reporting_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status IN ('frozen', 'approved') AND (
        NEW.subject_id IS DISTINCT FROM OLD.subject_id OR
        NEW.period_start IS DISTINCT FROM OLD.period_start OR
        NEW.period_end IS DISTINCT FROM OLD.period_end OR
        NEW.as_of_at IS DISTINCT FROM OLD.as_of_at OR
        NEW.reporting_currency IS DISTINCT FROM OLD.reporting_currency OR
        NEW.formula_version IS DISTINCT FROM OLD.formula_version OR
        NEW.source_manifest_json IS DISTINCT FROM OLD.source_manifest_json OR
        NEW.snapshot_payload_json IS DISTINCT FROM OLD.snapshot_payload_json OR
        NEW.data_sha256 IS DISTINCT FROM OLD.data_sha256 OR
        NEW.quality_status IS DISTINCT FROM OLD.quality_status OR
        NEW.publication_allowed IS DISTINCT FROM OLD.publication_allowed OR
        NEW.publication_blockers_json IS DISTINCT FROM OLD.publication_blockers_json OR
        NEW.benchmark_id IS DISTINCT FROM OLD.benchmark_id OR
        NEW.benchmark_convention IS DISTINCT FROM OLD.benchmark_convention OR
        NEW.benchmark_provider IS DISTINCT FROM OLD.benchmark_provider OR
        NEW.data_as_of IS DISTINCT FROM OLD.data_as_of OR
        NEW.created_at IS DISTINCT FROM OLD.created_at
    ) THEN
        RAISE EXCEPTION 'Frozen reporting snapshot content is immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS reporting_snapshot_immutable ON reporting_snapshots;
CREATE TRIGGER reporting_snapshot_immutable
BEFORE UPDATE ON reporting_snapshots
FOR EACH ROW EXECUTE FUNCTION reject_frozen_reporting_snapshot_mutation();
