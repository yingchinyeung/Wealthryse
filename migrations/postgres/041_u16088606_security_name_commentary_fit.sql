-- Full-size visual inspection confirmed the requested security name but showed
-- that the longer paragraph extended below the mint commentary box.  Supersede
-- only the unfinished batch and require the concise, readable v7 commentary.

UPDATE onboarding_historical_report_batches
SET status = 'superseded',
    error_code = 'u16088606_security_name_commentary_fit',
    error_message = (
        'Superseded during private visual QA: page 6 security-name commentary '
        'must fit completely inside the mint commentary box.'
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
        'Every completed-month presentation must identify the page 6 largest '
        'holding by security name without commentary overflow or clipping.'
    ),
    evidence_json = '{}'::jsonb,
    checked_at = now()
FROM account_onboarding_cases AS cases
WHERE cases.tenant_id = checks.tenant_id
  AND cases.case_id = checks.case_id
  AND cases.target_subject_id = 'U16088606'
  AND checks.check_id = 'historical_reports_backfilled'
  AND cases.status NOT IN ('active', 'cancelled');
