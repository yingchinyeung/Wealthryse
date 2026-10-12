-- Replace every unapproved U16088606 historical-report batch whose factsheet
-- may split one sector/sub-sector across separate single-region rows.  The new
-- renderer aggregates the complete holding set for each sector/sub-sector and
-- prints every represented region in the Region cell.

UPDATE onboarding_historical_report_batches
SET status = 'superseded',
    error_code = 'factsheet_multi_region_allocation_upgrade',
    error_message = (
        'Superseded before owner approval: every Sector Allocation row must '
        'list all regions represented by its sector/sub-sector holdings.'
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
        'All completed-month factsheets must aggregate Sector Allocation by '
        'sector and sub-sector and display every represented region.'
    ),
    evidence_json = '{}'::jsonb,
    checked_at = now()
FROM account_onboarding_cases AS cases
WHERE cases.tenant_id = checks.tenant_id
  AND cases.case_id = checks.case_id
  AND cases.target_subject_id = 'U16088606'
  AND checks.check_id = 'historical_reports_backfilled'
  AND cases.status NOT IN ('active', 'cancelled');
