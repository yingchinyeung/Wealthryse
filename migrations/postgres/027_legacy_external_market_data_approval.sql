-- Active subjects created before the onboarding workflow do not have an
-- account_onboarding_cases row.  Their explicit approvals still need the same
-- integrity-bound registry, so case_id is optional only for that legacy path.
ALTER TABLE account_onboarding_approvals
    ALTER COLUMN case_id DROP NOT NULL;

INSERT INTO account_onboarding_approvals(
    approval_id, tenant_id, case_id, subject_id, approval_type,
    approval_status, disclosure_version, destination, scope_json,
    scope_sha256, approved_by_identity_sha256, approved_at, metadata_json
) VALUES (
    'd5873909-8684-5c01-93be-cefd43997e94'::uuid,
    app_tenant_id(), NULL, 'U16088571', 'external_market_data',
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
    '2026-09-18T00:00:00+00:00'::timestamptz,
    jsonb_build_object(
        'source', 'explicit_owner_instruction_before_wizard',
        'legacy_active_subject', true
    )
)
ON CONFLICT DO NOTHING;
