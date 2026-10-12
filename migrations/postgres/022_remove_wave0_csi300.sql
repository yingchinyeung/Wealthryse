DELETE FROM performance_comparison_daily
WHERE tenant_id = app_tenant_id()
  AND subject_id = 'U16088606'
  AND benchmark_id = 'CSI300';

DELETE FROM portfolio_benchmark_mapping
WHERE tenant_id = app_tenant_id()
  AND subject_id = 'U16088606'
  AND benchmark_id = 'CSI300';

UPDATE platform_subjects
SET metadata_json = metadata_json || jsonb_build_object(
        'secondary_benchmarks', jsonb_build_array('SHCOMP', 'HSI'),
        'csi300_removed_by_owner', true,
        'benchmark_contract_version', 2
    ),
    updated_at = now()
WHERE tenant_id = app_tenant_id()
  AND subject_id = 'U16088606';

UPDATE account_onboarding_cases
SET configuration_json = configuration_json || jsonb_build_object(
        'secondary_benchmarks', jsonb_build_array('SHCOMP', 'HSI'),
        'csi300_removed_by_owner', true,
        'benchmark_contract_version', 2
    ),
    updated_at = now()
WHERE tenant_id = app_tenant_id()
  AND source_system = 'existing_v11'
  AND source_account_id = 'U16088606';

UPDATE account_onboarding_checks
SET status = 'pending',
    message = 'Waiting for reconciliation of the owner-revised TAIEX, Shanghai Composite and Hang Seng benchmark set.',
    evidence_json = jsonb_build_object(
        'primary_benchmark', '^TWII',
        'secondary_benchmarks', jsonb_build_array('000001.SS', '^HSI'),
        'removed_benchmark', '000300.SS',
        'return_convention', 'price',
        'reporting_currency', 'USD'
    ),
    checked_at = now()
WHERE tenant_id = app_tenant_id()
  AND case_id = (
      SELECT case_id FROM account_onboarding_cases
      WHERE tenant_id = app_tenant_id()
        AND source_system = 'existing_v11'
        AND source_account_id = 'U16088606'
  )
  AND check_id = 'benchmark_fx_reconciled';
