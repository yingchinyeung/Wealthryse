-- IBKR Portfolio Database query collection
-- Run one SELECT statement at a time in DB Browser for SQLite.
-- Do not use Write Changes.

-- 1. Latest import health
SELECT
    run_id,
    run_type,
    requested_from,
    requested_to,
    completed_at,
    status,
    json_extract(reconciliation_json, '$.status') AS reconciliation_status,
    json_extract(reconciliation_json, '$.warnings') AS warnings,
    row_count,
    error_message
FROM ingestion_runs
ORDER BY run_id DESC
LIMIT 10;

-- 2. Current positions
SELECT
    p.snapshot_date,
    p.account_id,
    i.symbol,
    i.description,
    i.asset_category,
    p.currency,
    CAST(p.quantity_text AS REAL) AS quantity,
    CAST(p.mark_price_text AS REAL) AS market_price,
    CAST(p.market_value_text AS REAL) AS market_value,
    CAST(p.cost_basis_text AS REAL) AS cost_basis,
    CAST(p.unrealized_pnl_text AS REAL) AS unrealized_pnl
FROM v_latest_positions AS p
LEFT JOIN instruments AS i ON i.conid = p.conid
ORDER BY p.account_id, ABS(CAST(p.market_value_text AS REAL)) DESC;

-- 3. Account summary by currency
SELECT
    p.snapshot_date,
    p.account_id,
    p.currency,
    COUNT(*) AS position_count,
    ROUND(SUM(CAST(p.market_value_text AS REAL)), 2) AS net_market_value,
    ROUND(SUM(ABS(CAST(p.market_value_text AS REAL))), 2) AS gross_market_value,
    ROUND(SUM(CAST(p.unrealized_pnl_text AS REAL)), 2) AS unrealized_pnl
FROM v_latest_positions AS p
GROUP BY p.snapshot_date, p.account_id, p.currency
ORDER BY p.account_id, p.currency;

-- 4. Exposure by asset class
SELECT
    p.account_id,
    p.currency,
    COALESCE(i.asset_category, 'UNKNOWN') AS asset_category,
    COUNT(*) AS position_count,
    ROUND(SUM(CAST(p.market_value_text AS REAL)), 2) AS net_market_value,
    ROUND(SUM(ABS(CAST(p.market_value_text AS REAL))), 2) AS gross_market_value,
    ROUND(SUM(CAST(p.unrealized_pnl_text AS REAL)), 2) AS unrealized_pnl
FROM v_latest_positions AS p
LEFT JOIN instruments AS i ON i.conid = p.conid
GROUP BY p.account_id, p.currency, i.asset_category
ORDER BY p.account_id, p.currency, gross_market_value DESC;

-- 5. Position concentration
WITH positions AS (
    SELECT
        p.account_id,
        p.currency,
        i.symbol,
        i.description,
        CAST(p.market_value_text AS REAL) AS market_value
    FROM v_latest_positions AS p
    LEFT JOIN instruments AS i ON i.conid = p.conid
),
totals AS (
    SELECT account_id, currency, SUM(ABS(market_value)) AS gross_value
    FROM positions
    GROUP BY account_id, currency
)
SELECT
    p.account_id,
    p.currency,
    p.symbol,
    p.description,
    ROUND(p.market_value, 2) AS market_value,
    ROUND(100.0 * ABS(p.market_value) / NULLIF(t.gross_value, 0), 2)
        AS percent_of_gross_portfolio
FROM positions AS p
JOIN totals AS t
  ON t.account_id = p.account_id
 AND t.currency = p.currency
ORDER BY percent_of_gross_portfolio DESC;

-- 6. Historical NAV
SELECT
    snapshot_date,
    account_id,
    currency,
    ROUND(total_nav, 2) AS total_nav,
    ROUND(cash, 2) AS cash,
    ROUND(stock, 2) AS stock,
    ROUND(options, 2) AS options,
    ROUND(futures, 2) AS futures,
    ROUND(bonds, 2) AS bonds,
    ROUND(accruals, 2) AS accruals
FROM v_daily_nav
ORDER BY snapshot_date, account_id, currency;

-- 7. Unadjusted NAV changes (not cash-flow-adjusted performance)
WITH history AS (
    SELECT
        snapshot_date,
        account_id,
        currency,
        total_nav,
        LAG(total_nav) OVER (
            PARTITION BY account_id, currency ORDER BY snapshot_date
        ) AS previous_nav
    FROM v_daily_nav
)
SELECT
    snapshot_date,
    account_id,
    currency,
    ROUND(total_nav, 2) AS total_nav,
    ROUND(total_nav - previous_nav, 2) AS nav_change,
    ROUND(100.0 * (total_nav / NULLIF(previous_nav, 0) - 1), 3)
        AS unadjusted_change_percent
FROM history
ORDER BY snapshot_date DESC, account_id, currency;

-- 8. Recent trades
SELECT
    t.trade_date,
    t.trade_time,
    t.account_id,
    i.symbol,
    i.asset_category,
    t.buy_sell,
    CAST(t.quantity_text AS REAL) AS quantity,
    CAST(t.trade_price_text AS REAL) AS trade_price,
    CAST(t.proceeds_text AS REAL) AS proceeds,
    CAST(t.commission_text AS REAL) AS commission,
    CAST(t.realized_pnl_text AS REAL) AS realized_pnl,
    t.currency
FROM trades AS t
LEFT JOIN instruments AS i ON i.conid = t.conid
ORDER BY t.trade_date DESC, t.trade_time DESC
LIMIT 200;

-- 9. Monthly commissions
SELECT
    SUBSTR(trade_date, 1, 7) AS month,
    account_id,
    commission_currency,
    ROUND(-SUM(CAST(commission_text AS REAL)), 2) AS commission_cost
FROM trades
WHERE commission_text IS NOT NULL
GROUP BY month, account_id, commission_currency
ORDER BY month DESC, account_id, commission_currency;

-- 10. Monthly realized trading P&L
SELECT
    SUBSTR(trade_date, 1, 7) AS month,
    account_id,
    currency,
    ROUND(SUM(CAST(realized_pnl_text AS REAL)), 2) AS realized_pnl
FROM trades
WHERE realized_pnl_text IS NOT NULL
GROUP BY month, account_id, currency
ORDER BY month DESC, account_id, currency;

-- 11. Cash transactions
SELECT
    effective_date,
    event_datetime,
    account_id,
    currency,
    description,
    CAST(amount_text AS REAL) AS amount,
    model_code
FROM cash_transactions
ORDER BY effective_date DESC, event_datetime DESC;

-- 12. Cash transaction summary by description
SELECT
    description,
    currency,
    COUNT(*) AS transaction_count,
    ROUND(SUM(CAST(amount_text AS REAL)), 2) AS total_amount
FROM cash_transactions
GROUP BY description, currency
ORDER BY transaction_count DESC;

-- 13. Latest cash balances
WITH latest AS (
    SELECT account_id, currency, MAX(snapshot_date) AS snapshot_date
    FROM cash_snapshots
    GROUP BY account_id, currency
)
SELECT
    c.snapshot_date,
    c.account_id,
    c.currency,
    CAST(c.ending_cash_text AS REAL) AS ending_cash,
    CAST(c.settled_cash_text AS REAL) AS settled_cash,
    CAST(c.fx_rate_to_base_text AS REAL) AS fx_rate_to_base
FROM cash_snapshots AS c
JOIN latest AS l
  ON l.account_id = c.account_id
 AND l.currency = c.currency
 AND l.snapshot_date = c.snapshot_date
ORDER BY c.account_id, c.currency;

-- 14. FX transactions
SELECT
    report_date,
    event_datetime,
    account_id,
    functional_currency,
    fx_currency,
    activity_description,
    CAST(quantity_text AS REAL) AS quantity,
    CAST(proceeds_text AS REAL) AS proceeds,
    CAST(cost_text AS REAL) AS cost,
    CAST(realized_pnl_text AS REAL) AS realized_pnl
FROM fx_transactions
ORDER BY report_date DESC, event_datetime DESC;

-- 15. Corporate actions
SELECT
    c.effective_date,
    c.account_id,
    i.symbol,
    c.action_type,
    c.description,
    CAST(c.quantity_text AS REAL) AS quantity,
    CAST(c.amount_text AS REAL) AS amount,
    c.currency
FROM corporate_actions AS c
LEFT JOIN instruments AS i ON i.conid = c.conid
ORDER BY c.effective_date DESC;

-- 16. Current options positions
SELECT
    p.snapshot_date,
    p.account_id,
    i.symbol,
    i.description,
    i.expiry,
    CAST(i.strike_text AS REAL) AS strike,
    i.put_call,
    p.currency,
    CAST(p.quantity_text AS REAL) AS quantity,
    CAST(p.market_value_text AS REAL) AS market_value,
    CAST(p.unrealized_pnl_text AS REAL) AS unrealized_pnl
FROM v_latest_positions AS p
JOIN instruments AS i ON i.conid = p.conid
WHERE i.asset_category IN ('OPT', 'FOP')
ORDER BY i.expiry, i.symbol, strike;

-- 17. Alpha World MS normalized NAV history
SELECT
    snapshot_date,
    currency AS base_currency,
    ROUND(total_nav, 6) AS normalized_nav,
    ROUND(cash, 6) AS constant_non_interest_bearing_cash,
    generated_at
FROM derived_portfolio_nav
WHERE portfolio_id = 'alpha-world-ms'
ORDER BY snapshot_date;

-- 18. Alpha World MS component values and contributions for a chosen period
-- Change the two dates as needed.
SELECT
    snapshot_date,
    component_name,
    component_type,
    direction,
    coefficient,
    source_currency,
    base_currency,
    ROUND(source_value, 6) AS source_value,
    ROUND(fx_rate_to_base, 8) AS fx_rate_to_base,
    ROUND(normalized_value, 6) AS normalized_value,
    ROUND(contribution, 6) AS nav_contribution,
    observation_date
FROM synthetic_portfolio_components
WHERE portfolio_id = 'alpha-world-ms'
  AND snapshot_date BETWEEN '2025-01-20' AND '2025-12-31'
ORDER BY snapshot_date, component_id;

-- 19. Stored Hang Seng price-index and SPY settlement-proxy observations
SELECT
    instrument_id,
    price_date,
    symbol,
    instrument_name,
    price_type,
    currency,
    ROUND(price, 6) AS price,
    source,
    fetched_at
FROM market_data_prices
WHERE price_date BETWEEN '2025-01-20' AND '2025-12-31'
ORDER BY instrument_id, price_date;

-- 20. Latest Alpha World MS allocation/exposure by sleeve
WITH latest AS (
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM synthetic_portfolio_components
    WHERE portfolio_id = 'alpha-world-ms'
)
SELECT
    c.snapshot_date,
    c.component_name,
    c.direction,
    ROUND(
        CASE WHEN c.component_type = 'cash' THEN c.coefficient
             ELSE c.coefficient * 100.0 END,
        2
    ) AS initial_formula_weight_percent,
    ROUND(c.contribution, 6) AS current_nav_contribution,
    c.observation_date
FROM synthetic_portfolio_components AS c
JOIN latest AS l ON l.snapshot_date = c.snapshot_date
WHERE c.portfolio_id = 'alpha-world-ms'
ORDER BY ABS(c.contribution) DESC;

-- 21. Alpha World T normalized NAV history
SELECT
    snapshot_date,
    currency AS base_currency,
    ROUND(total_nav, 6) AS normalized_nav,
    ROUND(cash, 6) AS constant_non_interest_bearing_cash,
    generated_at
FROM derived_portfolio_nav
WHERE portfolio_id = 'alpha-world-t'
ORDER BY snapshot_date;

-- 22. Alpha World T component values and contributions for a chosen period
-- Change the two dates as needed.
SELECT
    snapshot_date,
    component_name,
    component_type,
    direction,
    coefficient,
    source_currency,
    base_currency,
    ROUND(source_value, 6) AS source_value,
    ROUND(fx_rate_to_base, 8) AS fx_rate_to_base,
    ROUND(normalized_value, 6) AS normalized_value,
    ROUND(contribution, 6) AS nav_contribution,
    observation_date
FROM synthetic_portfolio_components
WHERE portfolio_id = 'alpha-world-t'
  AND snapshot_date BETWEEN '2025-01-20' AND '2025-12-31'
ORDER BY snapshot_date, component_id;

-- 23. Latest Alpha World T allocation/exposure by sleeve
WITH latest AS (
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM synthetic_portfolio_components
    WHERE portfolio_id = 'alpha-world-t'
)
SELECT
    c.snapshot_date,
    c.component_name,
    c.direction,
    ROUND(
        CASE WHEN c.component_type = 'cash' THEN c.coefficient
             ELSE c.coefficient * 100.0 END,
        2
    ) AS initial_formula_weight_percent,
    ROUND(c.contribution, 6) AS current_nav_contribution,
    c.observation_date
FROM synthetic_portfolio_components AS c
JOIN latest AS l ON l.snapshot_date = c.snapshot_date
WHERE c.portfolio_id = 'alpha-world-t'
ORDER BY ABS(c.contribution) DESC;
