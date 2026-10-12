CREATE UNIQUE INDEX IF NOT EXISTS idx_report_generation_runs_template_version
    ON report_generation_runs(
        tenant_id, snapshot_id, report_type, template_id, template_version
    );
