ALTER TABLE reference_fx_rates
    DROP CONSTRAINT IF EXISTS reference_fx_rates_observation_status_check;

ALTER TABLE reference_fx_rates
    ADD CONSTRAINT reference_fx_rates_observation_status_check
    CHECK (observation_status IN ('official_close', 'provisional_close', 'corrected'));

INSERT INTO account_onboarding_checks(
    tenant_id, case_id, check_id, status, message, evidence_json, checked_at
)
SELECT
    app_tenant_id(), case_id, 'benchmark_fx_reconciled', 'pending',
    'Waiting for native-currency benchmark backfill, validated USD FX conversion and comparison reconciliation.',
    '{}'::jsonb, now()
FROM account_onboarding_cases
WHERE tenant_id = app_tenant_id()
  AND source_system = 'existing_v11'
  AND source_account_id = 'U16088606'
ON CONFLICT (tenant_id, case_id, check_id) DO NOTHING;
