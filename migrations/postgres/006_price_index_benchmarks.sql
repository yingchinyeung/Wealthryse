UPDATE benchmark_master
SET active = false,
    updated_at = '2026-09-22T00:00:00+00:00'
WHERE tenant_id = app_tenant_id()
  AND benchmark_id = 'SP500TR';

DELETE FROM portfolio_benchmark_mapping
WHERE tenant_id = app_tenant_id()
  AND subject_id = 'U16088571'
  AND benchmark_id = 'SP500TR';

INSERT INTO benchmark_master(
    tenant_id, benchmark_id, symbol, name, provider, currency,
    return_convention, frequency, license_status, active, created_at, updated_at
)
VALUES
    (
        app_tenant_id(), 'GSPC', '^GSPC', 'S&P 500 Price Index',
        'Yahoo Finance via yfinance', 'USD', 'price', 'daily', 'restricted', true,
        '2026-09-22T00:00:00+00:00', '2026-09-22T00:00:00+00:00'
    ),
    (
        app_tenant_id(), 'IXIC', '^IXIC', 'NASDAQ Composite Price Index',
        'Yahoo Finance via yfinance', 'USD', 'price', 'daily', 'restricted', true,
        '2026-09-22T00:00:00+00:00', '2026-09-22T00:00:00+00:00'
    )
ON CONFLICT (tenant_id, benchmark_id) DO UPDATE
SET symbol = excluded.symbol,
    name = excluded.name,
    provider = excluded.provider,
    currency = excluded.currency,
    return_convention = excluded.return_convention,
    license_status = excluded.license_status,
    active = true,
    updated_at = excluded.updated_at;

INSERT INTO portfolio_benchmark_mapping(
    tenant_id, subject_id, benchmark_id, mapping_type, effective_from,
    effective_to, reporting_currency, created_at
)
VALUES
    (
        app_tenant_id(), 'U16088571', 'GSPC', 'primary', '2025-01-20',
        NULL, 'USD', '2026-09-22T00:00:00+00:00'
    ),
    (
        app_tenant_id(), 'U16088571', 'IXIC', 'secondary', '2025-01-20',
        NULL, 'USD', '2026-09-22T00:00:00+00:00'
    )
ON CONFLICT (tenant_id, subject_id, benchmark_id, effective_from) DO UPDATE
SET mapping_type = excluded.mapping_type,
    effective_to = NULL,
    reporting_currency = excluded.reporting_currency;
