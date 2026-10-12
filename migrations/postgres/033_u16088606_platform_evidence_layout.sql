-- Preserve the owner-approved Greater China identity while replacing the
-- generated data-slide evidence layout with the approved U16088571 v24 native
-- chart and table system. Unapproved artifacts remain private and recoverable.

UPDATE onboarding_historical_report_batches
SET status = 'superseded',
    error_code = 'u16088606_platform_evidence_layout_upgrade',
    error_message = (
        'Superseded before owner approval: U16088606 charts and tables must '
        'use the approved U16088571 v24 evidence layout while retaining the '
        'locked iStrategy: Greater China identity.'
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
        'All completed months require U16088606 data rendered with the '
        'approved U16088571 v24 chart and table layout and the locked '
        'iStrategy: Greater China identity.'
    ),
    evidence_json = '{}'::jsonb,
    checked_at = now()
FROM account_onboarding_cases AS cases
WHERE cases.tenant_id = checks.tenant_id
  AND cases.case_id = checks.case_id
  AND cases.target_subject_id = 'U16088606'
  AND checks.check_id = 'historical_reports_backfilled'
  AND cases.status NOT IN ('active', 'cancelled');
