UPDATE platform_subjects
SET display_name = 'U16088571 (U.S. Technology and Biotechnology)',
    updated_at = now()
WHERE tenant_id = app_tenant_id()
  AND subject_id = 'U16088571';

UPDATE platform_subjects
SET display_name = 'U16088606-Greater China',
    reporting_currency = 'USD',
    history_start_date = '2025-01-20',
    inception_date = COALESCE(inception_date, '2025-01-20'),
    metadata_json = metadata_json || jsonb_build_object(
        'financial_contract_version', 1,
        'financial_contract_approved_at', '2026-09-24T17:00:00+00:00',
        'benchmark_return_convention', 'price',
        'benchmark_reporting_currency', 'USD',
        'benchmark_fx_conversion_required', true,
        'risk_free_series', 'DGS3MO'
    ),
    updated_at = now()
WHERE tenant_id = app_tenant_id()
  AND subject_id = 'U16088606';

INSERT INTO benchmark_master(
    tenant_id, benchmark_id, symbol, name, provider, currency,
    return_convention, frequency, license_status, active, created_at, updated_at
)
VALUES
    (
        app_tenant_id(), 'TAIEX', '^TWII', 'TAIEX Price Index',
        'Yahoo Finance via yfinance', 'TWD', 'price', 'daily', 'restricted', true,
        '2026-09-24T17:00:00+00:00', '2026-09-24T17:00:00+00:00'
    ),
    (
        app_tenant_id(), 'CSI300', '000300.SS', 'CSI 300 Price Index',
        'Yahoo Finance via yfinance', 'CNY', 'price', 'daily', 'restricted', true,
        '2026-09-24T17:00:00+00:00', '2026-09-24T17:00:00+00:00'
    ),
    (
        app_tenant_id(), 'SHCOMP', '000001.SS', 'Shanghai Composite Price Index',
        'Yahoo Finance via yfinance', 'CNY', 'price', 'daily', 'restricted', true,
        '2026-09-24T17:00:00+00:00', '2026-09-24T17:00:00+00:00'
    ),
    (
        app_tenant_id(), 'HSI', '^HSI', 'Hang Seng Price Index',
        'Yahoo Finance via yfinance', 'HKD', 'price', 'daily', 'restricted', true,
        '2026-09-24T17:00:00+00:00', '2026-09-24T17:00:00+00:00'
    )
ON CONFLICT (tenant_id, benchmark_id) DO UPDATE
SET symbol = excluded.symbol,
    name = excluded.name,
    provider = excluded.provider,
    currency = excluded.currency,
    return_convention = excluded.return_convention,
    frequency = excluded.frequency,
    license_status = excluded.license_status,
    active = true,
    updated_at = excluded.updated_at;

INSERT INTO portfolio_benchmark_mapping(
    tenant_id, subject_id, benchmark_id, mapping_type, effective_from,
    effective_to, reporting_currency, created_at
)
VALUES
    (
        app_tenant_id(), 'U16088606', 'TAIEX', 'primary', '2025-01-20',
        NULL, 'USD', '2026-09-24T17:00:00+00:00'
    ),
    (
        app_tenant_id(), 'U16088606', 'CSI300', 'secondary', '2025-01-20',
        NULL, 'USD', '2026-09-24T17:00:00+00:00'
    ),
    (
        app_tenant_id(), 'U16088606', 'SHCOMP', 'secondary', '2025-01-20',
        NULL, 'USD', '2026-09-24T17:00:00+00:00'
    ),
    (
        app_tenant_id(), 'U16088606', 'HSI', 'secondary', '2025-01-20',
        NULL, 'USD', '2026-09-24T17:00:00+00:00'
    )
ON CONFLICT (tenant_id, subject_id, benchmark_id, effective_from) DO UPDATE
SET mapping_type = excluded.mapping_type,
    effective_to = NULL,
    reporting_currency = excluded.reporting_currency;

UPDATE account_onboarding_cases
SET reporting_currency = 'USD',
    stage = 'analytics_configuration',
    status = 'in_progress',
    configuration_json = configuration_json || jsonb_build_object(
        'display_name', 'U16088606-Greater China',
        'reporting_start_date', '2025-01-20',
        'reporting_currency', 'USD',
        'primary_benchmark', 'TAIEX',
        'secondary_benchmarks', jsonb_build_array('CSI300', 'SHCOMP', 'HSI'),
        'benchmark_return_convention', 'price',
        'benchmark_reporting_currency', 'USD',
        'benchmark_fx_conversion_required', true,
        'reporting_contract_status', 'approved'
    ),
    updated_at = now()
WHERE tenant_id = app_tenant_id()
  AND source_system = 'existing_v11'
  AND source_account_id = 'U16088606'
  AND stage = 'financial_contract';

UPDATE account_onboarding_checks
SET status = 'pass',
    message = 'The owner-approved USD reporting contract and price-index benchmark set are registered.',
    evidence_json = jsonb_build_object(
        'display_name', 'U16088606-Greater China',
        'reporting_start_date', '2025-01-20',
        'reporting_currency', 'USD',
        'primary_benchmark', '^TWII',
        'secondary_benchmarks', jsonb_build_array('000300.SS', '000001.SS', '^HSI'),
        'return_convention', 'price',
        'benchmark_fx_conversion_required', true
    ),
    checked_at = now()
WHERE tenant_id = app_tenant_id()
  AND case_id = (
      SELECT case_id
      FROM account_onboarding_cases
      WHERE tenant_id = app_tenant_id()
        AND source_system = 'existing_v11'
        AND source_account_id = 'U16088606'
  )
  AND check_id = 'financial_contract_approved';
