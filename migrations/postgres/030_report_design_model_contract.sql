UPDATE onboarding_historical_report_batches
SET status = 'superseded',
    error_code = 'report_design_contract_superseded',
    error_message = (
        'Superseded before rendering: factsheets now model the subject completed '
        'example and presentations model the approved U16088571 v24 presentation.'
    ),
    completed_at = now()
WHERE status IN (
    'queued', 'freezing_snapshots', 'rendering', 'validating',
    'awaiting_owner_approval'
);

UPDATE account_onboarding_checks AS checks
SET status = 'pending',
    message = (
        'All completed months require a validated factsheet modelled on the '
        'subject completed example and a presentation modelled on the approved '
        'U16088571 v24 presentation.'
    ),
    evidence_json = '{}'::jsonb,
    checked_at = now()
FROM account_onboarding_cases AS cases
WHERE cases.tenant_id = checks.tenant_id
  AND cases.case_id = checks.case_id
  AND checks.check_id = 'historical_reports_backfilled'
  AND cases.status NOT IN ('active', 'cancelled');
