-- Five more comparison benchmarks in the catalog: iShares MSCI Hong Kong (EWH), iShares
-- MSCI Taiwan (EWT), SPDR S&P 500 (SPY), Technology Select Sector SPDR (XLK) and the
-- KOSPI 200 index (Yahoo ^KS200, in KRW).
--
-- Catalog only. No account is mapped to them here: an owner picks them for an account in
-- the onboarding financial contract, as with the existing benchmarks. Alpha World MS and T
-- keep their fixed two-benchmark contracts.
--
-- The ETFs use Yahoo's unadjusted close (the job fetches with auto_adjust off), so like the
-- other benchmarks they are price returns without dividends. KOSPI 200 is the index itself,
-- not a KRW-listed ETF, to match how HSI, TAIEX and the S&P 500 are carried.
-- It refuses to run if any of the five ids or symbols is already in the catalog, rather
-- than overwrite or re-activate an existing row.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM benchmark_master
        WHERE tenant_id = app_tenant_id()
          AND (benchmark_id IN ('EWH', 'EWT', 'SPY', 'XLK', 'KS200')
               OR symbol IN ('EWH', 'EWT', 'SPY', 'XLK', '^KS200'))
    ) THEN
        RAISE EXCEPTION 'benchmark_master already holds one of EWH, EWT, SPY, XLK or KS200; review it before applying 079';
    END IF;
END;
$$;

INSERT INTO benchmark_master(
    tenant_id, benchmark_id, symbol, name, provider, currency,
    return_convention, frequency, license_status, active, created_at, updated_at
)
VALUES
    (
        app_tenant_id(), 'EWH', 'EWH', 'iShares MSCI Hong Kong ETF (price)',
        'Yahoo Finance via yfinance', 'USD', 'price', 'daily', 'restricted', true,
        '2026-10-19T00:00:00+00:00', '2026-10-19T00:00:00+00:00'
    ),
    (
        app_tenant_id(), 'EWT', 'EWT', 'iShares MSCI Taiwan ETF (price)',
        'Yahoo Finance via yfinance', 'USD', 'price', 'daily', 'restricted', true,
        '2026-10-19T00:00:00+00:00', '2026-10-19T00:00:00+00:00'
    ),
    (
        app_tenant_id(), 'SPY', 'SPY', 'SPDR S&P 500 ETF Trust (price)',
        'Yahoo Finance via yfinance', 'USD', 'price', 'daily', 'restricted', true,
        '2026-10-19T00:00:00+00:00', '2026-10-19T00:00:00+00:00'
    ),
    (
        app_tenant_id(), 'XLK', 'XLK', 'Technology Select Sector SPDR Fund (price)',
        'Yahoo Finance via yfinance', 'USD', 'price', 'daily', 'restricted', true,
        '2026-10-19T00:00:00+00:00', '2026-10-19T00:00:00+00:00'
    ),
    (
        app_tenant_id(), 'KS200', '^KS200', 'KOSPI 200 Price Index',
        'Yahoo Finance via yfinance', 'KRW', 'price', 'daily', 'restricted', true,
        '2026-10-19T00:00:00+00:00', '2026-10-19T00:00:00+00:00'
    );
