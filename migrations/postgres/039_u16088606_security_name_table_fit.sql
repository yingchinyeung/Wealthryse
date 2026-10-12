-- A private visual-QA render showed that long Greater China security names
-- can expand the narrow summary-table rows and overlap the commentary below.
-- Supersede that unfinished batch and require the wider value column plus
-- compact, readable v5 table typography before owner review can begin.

UPDATE onboarding_historical_report_batches
SET status = 'superseded',
    error_code = 'u16088606_security_name_table_fit',
    error_message = (
        'Superseded during private visual QA: long security names must fit '
        'inside the page 6 and page 9 summary tables without expanding rows '
        'or overlapping adjacent commentary.'
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
        'Every completed-month presentation must show exact USD NAV and '
        'readable security names in the requested summary rows without '
        'overlap or clipping.'
    ),
    evidence_json = '{}'::jsonb,
    checked_at = now()
FROM account_onboarding_cases AS cases
WHERE cases.tenant_id = checks.tenant_id
  AND cases.case_id = checks.case_id
  AND cases.target_subject_id = 'U16088606'
  AND checks.check_id = 'historical_reports_backfilled'
  AND cases.status NOT IN ('active', 'cancelled');
