-- Repair cases created by early wizard builds and make their gate names match
-- the historical-report workflow used by the final page-by-page experience.
INSERT INTO account_onboarding_checks(
    tenant_id, case_id, check_id, status, message, evidence_json, checked_at
)
SELECT
    tenant_id, case_id, 'historical_reports_backfilled', status, message,
    evidence_json, checked_at
FROM account_onboarding_checks
WHERE tenant_id = app_tenant_id()
  AND check_id = 'historical_reports_approved'
ON CONFLICT (tenant_id, case_id, check_id) DO NOTHING;

DELETE FROM account_onboarding_checks
WHERE tenant_id = app_tenant_id()
  AND check_id = 'historical_reports_approved';

INSERT INTO account_onboarding_checks(
    tenant_id, case_id, check_id, status, message, evidence_json, checked_at
)
SELECT
    tenant_id, case_id, 'presentation_identity_approved', 'pending',
    'Owner approval of the server-derived presentation identity is required.',
    '{}'::jsonb, now()
FROM account_onboarding_cases
WHERE tenant_id = app_tenant_id()
ON CONFLICT (tenant_id, case_id, check_id) DO NOTHING;

UPDATE account_onboarding_checks checks
SET status = 'pass',
    message = 'The owner-approved presentation identity is locked and cannot be edited by document uploaders.',
    evidence_json = approvals.scope_json,
    checked_at = approvals.approved_at
FROM account_onboarding_cases cases
JOIN account_onboarding_approvals approvals
  ON approvals.tenant_id = cases.tenant_id
 AND approvals.case_id = cases.case_id
 AND approvals.approval_type = 'presentation_identity'
 AND approvals.approval_status = 'approved'
WHERE checks.tenant_id = app_tenant_id()
  AND checks.tenant_id = cases.tenant_id
  AND checks.case_id = cases.case_id
  AND checks.check_id = 'presentation_identity_approved';
