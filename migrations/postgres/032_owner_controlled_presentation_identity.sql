ALTER TABLE account_onboarding_approvals
    DROP CONSTRAINT IF EXISTS account_onboarding_approvals_approval_type_check;

ALTER TABLE account_onboarding_approvals
    ADD CONSTRAINT account_onboarding_approvals_approval_type_check
    CHECK (approval_type IN (
        'external_market_data', 'financial_contract',
        'presentation_identity', 'historical_report_batch',
        'website_preview', 'owner_activation'
    ));

INSERT INTO account_onboarding_checks(
    tenant_id, case_id, check_id, status, message, evidence_json, checked_at
)
SELECT
    tenant_id, case_id, 'presentation_identity_approved', 'pending',
    'The owner must approve the server-derived presentation identity before document upload.',
    '{}'::jsonb, now()
FROM account_onboarding_cases
WHERE status NOT IN ('active', 'cancelled')
ON CONFLICT (tenant_id, case_id, check_id) DO NOTHING;

-- The owner already approved the U16088606 Greater China identity in the
-- onboarding trial.  This migration records that instruction in the same
-- owner-only contract used for every future self-service onboarding case.
INSERT INTO account_onboarding_approvals(
    approval_id, tenant_id, case_id, subject_id, approval_type,
    approval_status, disclosure_version, destination, scope_json,
    scope_sha256, approved_by_identity_sha256, approved_at, metadata_json
)
SELECT
    'aa5e8d9a-cde9-5a97-9b2e-421f096a32cc'::uuid,
    c.tenant_id, c.case_id, c.target_subject_id,
    'presentation_identity', 'approved', 'presentation-identity-v1',
    'Internal report renderer',
    '{"derivation":"approved_subject_display_name","layout_policy":"subject_template","schema_version":1,"source_display_identity_sha256":"e84d954fd857d730498486162e71dc8347fc110cf101889c3435969733304464","source_display_name":"U16088606-Greater China","strategy_identity_sha256":"19dc0ad3aeb970926844274cc64f75a55eb6e0eb9f40d7088802f4603d68d7d7","strategy_name":"iStrategy: Greater China","subject_id":"U16088606"}'::jsonb,
    '8e8cc4272095de700627c9e894382458739c62a697e129e8673ba868ec198f63',
    '560af6bba1129ead40ac6271bc37c471f6ff0322998b3536a5c3b5d92c2210bc',
    '2026-09-28T00:00:00+00:00'::timestamptz,
    jsonb_build_object(
        'source', 'explicit_owner_instruction_before_identity_lock',
        'title_source', 'owner_approved_subject_display_name',
        'uploader_editable', false
    )
FROM account_onboarding_cases c
WHERE c.tenant_id = app_tenant_id()
  AND c.target_subject_id = 'U16088606'
ON CONFLICT DO NOTHING;

UPDATE account_onboarding_checks AS checks
SET status = 'pass',
    message = (
        'The owner-approved presentation identity is locked to '
        'iStrategy: Greater China and the subject presentation template.'
    ),
    evidence_json = jsonb_build_object(
        'approval_id', approvals.approval_id,
        'scope_sha256', approvals.scope_sha256,
        'strategy_name', approvals.scope_json ->> 'strategy_name',
        'layout_policy', approvals.scope_json ->> 'layout_policy',
        'uploader_editable', false
    ),
    checked_at = approvals.approved_at
FROM account_onboarding_approvals approvals
WHERE checks.tenant_id = approvals.tenant_id
  AND checks.case_id = approvals.case_id
  AND checks.check_id = 'presentation_identity_approved'
  AND approvals.subject_id = 'U16088606'
  AND approvals.approval_type = 'presentation_identity'
  AND approvals.approval_status = 'approved';
