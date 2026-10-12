-- Replace the page 6 gross-holdings ratio with exact USD NAV and require
-- security names in the page 6 and page 9 summary tables.  Supersede only
-- the private, unapproved U16088606 batch so it cannot advance with stale
-- ticker-based fields.

UPDATE onboarding_historical_report_batches
SET status = 'superseded',
    error_code = 'u16088606_security_name_summary_tables',
    error_message = (
        'Superseded for owner-requested presentation table updates: page 6 '
        'shows exact USD NAV and security names, and page 9 shows security '
        'names for the largest contributor and detractor.'
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
        'security names in the requested page 6 and page 9 summary rows.'
    ),
    evidence_json = '{}'::jsonb,
    checked_at = now()
FROM account_onboarding_cases AS cases
WHERE cases.tenant_id = checks.tenant_id
  AND cases.case_id = checks.case_id
  AND cases.target_subject_id = 'U16088606'
  AND checks.check_id = 'historical_reports_backfilled'
  AND cases.status NOT IN ('active', 'cancelled');
