-- The page 6 explanatory paragraph still used the largest holding's ticker
-- after the adjacent chart and summary table moved to security names.
-- Supersede only the unfinished U16088606 batch so every historical and
-- future presentation names the security consistently.

UPDATE onboarding_historical_report_batches
SET status = 'superseded',
    error_code = 'u16088606_security_name_commentary',
    error_message = (
        'Superseded before owner approval: page 6 commentary must identify '
        'the largest holding by security name instead of its ticker.'
    ),
    completed_at = now()
WHERE subject_id = 'U16088606'
  AND status IN (
      'queued', 'freezing_snapshots', 'rendering', 'validating',
      'awaiting_owner_approval'
  );

UPDATE account_onboarding_checks AS checks
SET status = 'pending',
    message = (
        'Every completed-month presentation must identify the page 6 '
        'largest holding by security name in the chart, table and commentary.'
    ),
    evidence_json = '{}'::jsonb,
    checked_at = now()
FROM account_onboarding_cases AS cases
WHERE cases.tenant_id = checks.tenant_id
  AND cases.case_id = checks.case_id
  AND cases.target_subject_id = 'U16088606'
  AND checks.check_id = 'historical_reports_backfilled'
  AND cases.status NOT IN ('active', 'cancelled');
