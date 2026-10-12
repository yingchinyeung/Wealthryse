ALTER TABLE benchmark_prices
    DROP CONSTRAINT IF EXISTS benchmark_prices_observation_status_check;

ALTER TABLE benchmark_prices
    ADD CONSTRAINT benchmark_prices_observation_status_check
    CHECK (observation_status IN (
        'official_close', 'intraday', 'corrected', 'provisional_close'
    ));

UPDATE benchmark_master
SET symbol = 'QQQ',
    name = 'Nasdaq-100 total-return development proxy (QQQ adjusted close)',
    provider = 'Yahoo Finance via yfinance',
    license_status = 'restricted',
    updated_at = '2026-09-22T00:00:00+00:00'
WHERE tenant_id = app_tenant_id()
  AND benchmark_id = 'XNDX'
  AND license_status = 'pending';
