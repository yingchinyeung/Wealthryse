-- The first private v2 render proved that full security names belong on the
-- requested chart axes but not in the adjacent narrow summary tables.  Stop
-- the unapproved draft from being approved and require a clean v3 batch that
-- preserves ticker labels in those compact summary fields.

UPDATE onboarding_historical_report_batches
SET status = 'superseded',
    error_code = 'u16088606_security_name_axis_layout_fix',
    error_message = (
        'Superseded during private visual QA: security names remain on chart '
        'axes, while adjacent narrow summary fields retain compact tickers.'
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
        'Every completed-month presentation must show security names only on '
        'the page 6 and page 9 chart axes without overflowing adjacent content.'
    ),
    evidence_json = '{}'::jsonb,
    checked_at = now()
FROM account_onboarding_cases AS cases
WHERE cases.tenant_id = checks.tenant_id
  AND cases.case_id = checks.case_id
  AND cases.target_subject_id = 'U16088606'
  AND checks.check_id = 'historical_reports_backfilled'
  AND cases.status NOT IN ('active', 'cancelled');
