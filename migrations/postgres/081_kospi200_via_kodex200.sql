-- KOSPI 200 through the KODEX 200 ETF.
--
-- 079 put KOSPI 200 in the catalog as the index itself (KS200, Yahoo ^KS200, in KRW). When
-- Korea iStrategy's comparison was first built (20 Oct 2026, 22:03 UTC), Yahoo Finance returned
-- no closing prices for ^KS200, so nothing could be built. The owner then chose the KODEX 200
-- ETF (KRX 069500, Yahoo 069500.KS), which tracks KOSPI 200 and trades in KRW on the Korea
-- Exchange (decision card, 20 Oct 2026 22:08 UTC).
--
-- KS200 keeps its id, so an account mapped to it keeps its mapping; only the ticker and the
-- name change. Like the other ETFs it uses Yahoo's unadjusted close (the job fetches with
-- auto_adjust off), a price return without distributions.
--
-- It refuses, changing nothing, unless KS200 is still exactly as 079 left it, another catalog
-- row already uses 069500.KS, or a price or comparison row is already stored for KS200.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM benchmark_master
        WHERE tenant_id = app_tenant_id() AND benchmark_id = 'KS200' AND symbol = '^KS200'
          AND name = 'KOSPI 200 Price Index' AND currency = 'KRW' AND return_convention = 'price'
          AND active
    ) THEN
        RAISE EXCEPTION 'benchmark_master does not hold KS200 as 079 left it; review it before applying 081';
    END IF;
    IF EXISTS (
        SELECT 1 FROM benchmark_master
        WHERE tenant_id = app_tenant_id() AND symbol = '069500.KS'
    ) THEN
        RAISE EXCEPTION 'benchmark_master already holds 069500.KS; review it before applying 081';
    END IF;
    IF EXISTS (
        SELECT 1 FROM benchmark_prices WHERE tenant_id = app_tenant_id() AND benchmark_id = 'KS200'
    ) OR EXISTS (
        SELECT 1 FROM performance_comparison_daily WHERE tenant_id = app_tenant_id() AND benchmark_id = 'KS200'
    ) THEN
        RAISE EXCEPTION 'prices or comparison rows are already stored for KS200; review them before applying 081';
    END IF;
END;
$$;

UPDATE benchmark_master
SET symbol = '069500.KS',
    name = 'KOSPI 200 via KODEX 200 ETF (price)',
    updated_at = '2026-10-21T00:00:00+00:00'
WHERE tenant_id = app_tenant_id() AND benchmark_id = 'KS200' AND symbol = '^KS200';
