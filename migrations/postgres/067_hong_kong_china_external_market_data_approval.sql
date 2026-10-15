-- The owner approved sending China & Hong Kong's ticker symbols to Yahoo
-- Finance on 15 October 2026, in the project thread, so that its crisis
-- scenarios (and Alpha World MS's, which hold it as a sleeve) can be
-- calculated. The website wizard no longer offers the approval once a
-- subject's onboarding is finished, so it is recorded here the way 027
-- recorded the owner's earlier instruction for U16088571: same disclosure
-- version, same scope and the same owner identity digest. A database without
-- the subject (a fresh environment) skips it.
INSERT INTO account_onboarding_approvals(
    approval_id, tenant_id, case_id, subject_id, approval_type,
    approval_status, disclosure_version, destination, scope_json,
    scope_sha256, approved_by_identity_sha256, approved_at, metadata_json
)
SELECT
    '0b7d3f0e-5c1a-5b8e-9f61-6c2a4d8e7f15'::uuid,
    app_tenant_id(),
    (SELECT c.case_id FROM account_onboarding_cases c
     WHERE c.tenant_id = app_tenant_id() AND c.target_subject_id = 'hong-kong-china'
     ORDER BY c.created_at DESC LIMIT 1),
    'hong-kong-china', 'external_market_data',
    'approved', 'yahoo-ticker-disclosure-v1',
    'Yahoo Finance via yfinance',
    jsonb_build_object(
        'data', jsonb_build_array(
            'current_ticker_symbols', 'historical_ticker_symbols'
        ),
        'destination', 'Yahoo Finance via yfinance',
        'excluded', jsonb_build_array(
            'raw_ibkr_files', 'ibkr_account_identifiers',
            'flex_credentials', 'database_credentials', 'personal_information'
        ),
        'purposes', jsonb_build_array(
            'provisional_sector_classification',
            'crisis_period_price_retrieval'
        )
    ),
    '39c16c280d292272e749b7c8afbb3f746b74346e9369c947073de9a0fcaf9daf',
    '560af6bba1129ead40ac6271bc37c471f6ff0322998b3536a5c3b5d92c2210bc',
    '2026-10-15T21:20:47+00:00'::timestamptz,
    jsonb_build_object(
        'source', 'explicit_owner_instruction_after_wizard',
        'instruction', 'Approved in the project thread on 2026-10-15'
    )
WHERE EXISTS (SELECT 1 FROM platform_subjects s
              WHERE s.tenant_id = app_tenant_id() AND s.subject_id = 'hong-kong-china')
ON CONFLICT DO NOTHING;

UPDATE account_onboarding_checks k
SET status = 'pass',
    message = 'The owner approved the bounded Yahoo Finance ticker-symbol transmission.',
    checked_at = now()
FROM account_onboarding_cases c
WHERE k.tenant_id = app_tenant_id() AND c.tenant_id = k.tenant_id AND c.case_id = k.case_id
  AND c.target_subject_id = 'hong-kong-china'
  AND k.check_id = 'external_market_data_approved' AND k.status <> 'pass'
  AND EXISTS (SELECT 1 FROM account_onboarding_approvals a
              WHERE a.tenant_id = k.tenant_id AND a.subject_id = 'hong-kong-china'
                AND a.approval_type = 'external_market_data' AND a.approval_status = 'approved');
