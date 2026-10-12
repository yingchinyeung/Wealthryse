CREATE TABLE account_onboarding_approvals (
    approval_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    case_id UUID NOT NULL REFERENCES account_onboarding_cases(case_id) ON DELETE CASCADE,
    subject_id TEXT NOT NULL,
    approval_type TEXT NOT NULL CHECK (approval_type IN (
        'external_market_data', 'financial_contract',
        'website_preview', 'owner_activation'
    )),
    approval_status TEXT NOT NULL CHECK (approval_status IN (
        'approved', 'revoked'
    )),
    disclosure_version TEXT NOT NULL,
    destination TEXT,
    scope_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    scope_sha256 TEXT NOT NULL CHECK (scope_sha256 ~ '^[0-9a-f]{64}$'),
    approved_by_identity_sha256 TEXT NOT NULL CHECK (
        approved_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (tenant_id, subject_id)
        REFERENCES platform_subjects(tenant_id, subject_id) ON DELETE CASCADE,
    CHECK (
        (approval_status = 'approved' AND revoked_at IS NULL)
        OR (approval_status = 'revoked' AND revoked_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX account_onboarding_approvals_active_idx
    ON account_onboarding_approvals(tenant_id, subject_id, approval_type)
    WHERE approval_status = 'approved';

CREATE INDEX account_onboarding_approvals_case_idx
    ON account_onboarding_approvals(tenant_id, case_id, approved_at DESC);

ALTER TABLE account_onboarding_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_onboarding_approvals FORCE ROW LEVEL SECURITY;
CREATE POLICY account_onboarding_approvals_tenant_policy
    ON account_onboarding_approvals
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());

INSERT INTO account_onboarding_checks(
    tenant_id, case_id, check_id, status, message, evidence_json, checked_at
)
SELECT
    app_tenant_id(), case_id, 'external_market_data_approved', 'pending',
    'Owner approval is required before ticker symbols are sent to an external market-data provider.',
    '{}'::jsonb, now()
FROM account_onboarding_cases
WHERE tenant_id = app_tenant_id()
ON CONFLICT (tenant_id, case_id, check_id) DO NOTHING;

INSERT INTO account_onboarding_checks(
    tenant_id, case_id, check_id, status, message, evidence_json, checked_at
)
SELECT
    app_tenant_id(), case_id, 'risk_analytics_validated', 'pending',
    'Waiting for sector coverage, holding-level tail-loss attribution and crisis replay validation.',
    '{}'::jsonb, now()
FROM account_onboarding_cases
WHERE tenant_id = app_tenant_id()
ON CONFLICT (tenant_id, case_id, check_id) DO NOTHING;

-- The owner explicitly approved the same bounded Yahoo Finance transmission
-- for the original pilot and for U16088606.  Only ticker symbols are in scope;
-- raw IBKR files, account identifiers, credentials and personal information are
-- excluded.  Future accounts must record approval through the website wizard.
WITH approved(subject_id, approval_id, actor_sha256, approved_at) AS (
    VALUES
        ('U16088571', 'd5873909-8684-5c01-93be-cefd43997e94'::uuid,
         '560af6bba1129ead40ac6271bc37c471f6ff0322998b3536a5c3b5d92c2210bc',
         '2026-09-18T00:00:00+00:00'::timestamptz),
        ('U16088606', 'b0fc4550-0bde-53ac-8ae0-f9725c8b53f0'::uuid,
         '560af6bba1129ead40ac6271bc37c471f6ff0322998b3536a5c3b5d92c2210bc',
         '2026-09-25T14:30:00+00:00'::timestamptz)
), scoped AS (
    SELECT
        c.case_id, c.target_subject_id AS subject_id, approved.approval_id,
        approved.actor_sha256, approved.approved_at,
        jsonb_build_object(
            'data', jsonb_build_array('current_ticker_symbols', 'historical_ticker_symbols'),
            'purposes', jsonb_build_array(
                'provisional_sector_classification',
                'crisis_period_price_retrieval'
            ),
            'destination', 'Yahoo Finance via yfinance',
            'excluded', jsonb_build_array(
                'raw_ibkr_files', 'ibkr_account_identifiers',
                'flex_credentials', 'database_credentials', 'personal_information'
            )
        ) AS scope_json
    FROM approved
    JOIN account_onboarding_cases c
      ON c.tenant_id = app_tenant_id()
     AND c.target_subject_id = approved.subject_id
)
INSERT INTO account_onboarding_approvals(
    approval_id, tenant_id, case_id, subject_id, approval_type,
    approval_status, disclosure_version, destination, scope_json,
    scope_sha256, approved_by_identity_sha256, approved_at, metadata_json
)
SELECT
    approval_id, app_tenant_id(), case_id, subject_id,
    'external_market_data', 'approved', 'yahoo-ticker-disclosure-v1',
    'Yahoo Finance via yfinance', scope_json,
    '39c16c280d292272e749b7c8afbb3f746b74346e9369c947073de9a0fcaf9daf',
    actor_sha256, approved_at,
    jsonb_build_object('source', 'explicit_owner_instruction_before_wizard')
FROM scoped
ON CONFLICT DO NOTHING;

UPDATE account_onboarding_checks AS checks
SET status = 'pass',
    message = 'The owner approved the bounded transmission of current and historical ticker symbols to Yahoo Finance for sector classification and crisis-period prices.',
    evidence_json = jsonb_build_object(
        'approval_type', approvals.approval_type,
        'disclosure_version', approvals.disclosure_version,
        'destination', approvals.destination,
        'scope_sha256', approvals.scope_sha256,
        'approved_at', approvals.approved_at
    ),
    checked_at = approvals.approved_at
FROM account_onboarding_approvals AS approvals
WHERE checks.tenant_id = approvals.tenant_id
  AND checks.case_id = approvals.case_id
  AND checks.check_id = 'external_market_data_approved'
  AND approvals.approval_status = 'approved'
  AND approvals.approval_type = 'external_market_data';

