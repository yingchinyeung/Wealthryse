-- Reconcile document-intake gates created before template registration updated
-- the stored onboarding check atomically. The current three-document package is
-- authoritative; superseded and rejected assets cannot satisfy this gate.

WITH complete_packages AS (
    SELECT
        tenant_id,
        subject_id,
        COUNT(*) AS asset_count,
        jsonb_agg(
            jsonb_build_object(
                'report_type', report_type,
                'asset_role', asset_role,
                'version_label', version_label,
                'blob_name', blob_name,
                'sha256', sha256,
                'byte_count', byte_count
            )
            ORDER BY report_type, asset_role
        ) AS assets
    FROM subject_report_template_assets
    WHERE is_current
      AND approval_status IN ('validated', 'active')
    GROUP BY tenant_id, subject_id
    HAVING BOOL_OR(report_type = 'factsheet' AND asset_role = 'template')
       AND BOOL_OR(report_type = 'presentation' AND asset_role = 'template')
       AND BOOL_OR(report_type = 'factsheet' AND asset_role = 'completed_example')
), eligible_cases AS (
    SELECT
        onboarding.tenant_id,
        onboarding.case_id,
        onboarding.stage,
        package.asset_count,
        package.assets
    FROM account_onboarding_cases onboarding
    JOIN complete_packages package
      ON package.tenant_id = onboarding.tenant_id
     AND package.subject_id = onboarding.target_subject_id
)
UPDATE account_onboarding_checks check_result
SET status = 'pass',
    message = 'All three mandatory report documents were validated and stored.',
    evidence_json = jsonb_build_object(
        'status', 'validated',
        'asset_count', eligible.asset_count,
        'assets', eligible.assets,
        'reconciled_by', 'migration-048'
    ),
    checked_at = now()
FROM eligible_cases eligible
WHERE check_result.tenant_id = eligible.tenant_id
  AND check_result.case_id = eligible.case_id
  AND check_result.check_id = 'report_documents_validated';

WITH complete_packages AS (
    SELECT tenant_id, subject_id
    FROM subject_report_template_assets
    WHERE is_current
      AND approval_status IN ('validated', 'active')
    GROUP BY tenant_id, subject_id
    HAVING BOOL_OR(report_type = 'factsheet' AND asset_role = 'template')
       AND BOOL_OR(report_type = 'presentation' AND asset_role = 'template')
       AND BOOL_OR(report_type = 'factsheet' AND asset_role = 'completed_example')
)
UPDATE account_onboarding_cases onboarding
SET stage = 'reconciliation',
    status = 'in_progress',
    updated_at = now()
FROM complete_packages package
WHERE onboarding.tenant_id = package.tenant_id
  AND onboarding.target_subject_id = package.subject_id
  AND onboarding.stage = 'template_intake';
