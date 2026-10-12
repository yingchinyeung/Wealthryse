-- Re-render every private U16088606 presentation with security names, rather
-- than ticker symbols, on the category axes of the holdings-concentration and
-- monthly-security-contribution charts.  Changed artifacts require a fresh
-- integrity-bound owner review; earlier approvals remain preserved as audit
-- history but no longer satisfy the current report contract.

UPDATE onboarding_historical_report_batches
SET status = 'superseded',
    error_code = 'u16088606_security_name_chart_axes_upgrade',
    error_message = (
        'Superseded after the chart-axis contract changed: pages 6 and 9 must '
        'show security names from the frozen snapshot instead of ticker symbols.'
    ),
    completed_at = now()
WHERE subject_id = 'U16088606'
  AND status IN (
      'queued', 'freezing_snapshots', 'rendering', 'validating',
      'awaiting_owner_approval', 'approved'
  );

UPDATE account_onboarding_approvals
SET approval_status = 'revoked',
    revoked_at = now(),
    metadata_json = metadata_json || jsonb_build_object(
        'revocation_reason',
        'Superseded by the U16088606 security-name chart-axis rendering contract.'
    )
WHERE subject_id = 'U16088606'
  AND approval_type IN ('historical_report_batch', 'website_preview')
  AND approval_status = 'approved';

UPDATE account_onboarding_checks AS checks
SET status = 'pending',
    message = CASE checks.check_id
        WHEN 'historical_reports_backfilled' THEN
            'Every completed-month presentation must show security names on the page 6 and page 9 chart axes and pass exact snapshot-label validation.'
        WHEN 'website_preview_validated' THEN
            'Website preview approval must be repeated after the regenerated historical presentation batch is approved.'
        ELSE checks.message
    END,
    evidence_json = '{}'::jsonb,
    checked_at = now()
FROM account_onboarding_cases AS cases
WHERE cases.tenant_id = checks.tenant_id
  AND cases.case_id = checks.case_id
  AND cases.target_subject_id = 'U16088606'
  AND checks.check_id IN ('historical_reports_backfilled', 'website_preview_validated')
  AND cases.status NOT IN ('active', 'cancelled');

UPDATE account_onboarding_cases
SET stage = 'historical_reporting', status = 'in_progress', updated_at = now()
WHERE target_subject_id = 'U16088606'
  AND status NOT IN ('active', 'cancelled');
