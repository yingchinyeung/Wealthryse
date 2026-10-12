UPDATE onboarding_historical_report_batches
SET status = 'superseded',
    error_code = 'u16088606_presentation_contract_corrected',
    error_message = (
        'Superseded before owner approval: U16088606 presentations must use '
        'the owner-approved U16088606 Greater China template and must identify '
        'the strategy as iStrategy: Greater China.'
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
        'All completed months require a validated factsheet modelled on the '
        'subject completed example and a presentation rendered from the '
        'owner-approved U16088606 Greater China template.'
    ),
    evidence_json = '{}'::jsonb,
    checked_at = now()
FROM account_onboarding_cases AS cases
WHERE cases.tenant_id = checks.tenant_id
  AND cases.case_id = checks.case_id
  AND cases.target_subject_id = 'U16088606'
  AND checks.check_id = 'historical_reports_backfilled'
  AND cases.status NOT IN ('active', 'cancelled');
