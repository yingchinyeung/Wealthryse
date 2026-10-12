-- Security-invoker views give the website one read contract for physical and
-- derived subjects while preserving row-level security on the source tables.

CREATE OR REPLACE VIEW subject_nav_series
WITH (security_invoker = true) AS
SELECT
    a.tenant_id,
    n.account_id AS subject_id,
    'physical_account'::text AS source_kind,
    n.snapshot_date,
    n.currency,
    n.total_text
FROM nav_snapshots n
JOIN accounts a ON a.account_id = n.account_id
WHERE n.asset_class = 'TOTAL'
UNION ALL
SELECT
    n.tenant_id,
    n.portfolio_id AS subject_id,
    'derived_portfolio'::text AS source_kind,
    n.snapshot_date,
    n.currency,
    n.total_nav::text AS total_text
FROM derived_portfolio_nav n;

CREATE OR REPLACE VIEW subject_position_series
WITH (security_invoker = true) AS
SELECT
    a.tenant_id,
    p.account_id AS subject_id,
    'physical_account'::text AS source_kind,
    p.snapshot_date,
    p.conid,
    p.model_code,
    p.currency,
    p.quantity_text,
    p.cost_basis_text,
    p.mark_price_text,
    p.market_value_text,
    p.unrealized_pnl_text
FROM position_snapshots p
JOIN accounts a ON a.account_id = p.account_id
UNION ALL
SELECT
    p.tenant_id,
    p.portfolio_id AS subject_id,
    'derived_portfolio'::text AS source_kind,
    p.snapshot_date,
    p.conid,
    p.model_code,
    p.currency,
    p.quantity_text,
    p.cost_basis_text,
    p.mark_price_text,
    p.market_value_text,
    p.unrealized_pnl_text
FROM derived_portfolio_positions p;

CREATE OR REPLACE VIEW subject_cash_balance_series
WITH (security_invoker = true) AS
SELECT
    a.tenant_id,
    c.account_id AS subject_id,
    'physical_account'::text AS source_kind,
    c.snapshot_date,
    c.currency,
    c.ending_cash_text,
    c.settled_cash_text,
    c.fx_rate_to_base_text
FROM cash_snapshots c
JOIN accounts a ON a.account_id = c.account_id
UNION ALL
SELECT
    c.tenant_id,
    c.portfolio_id AS subject_id,
    'derived_portfolio'::text AS source_kind,
    c.snapshot_date,
    c.currency,
    c.closing_balance::text AS ending_cash_text,
    c.closing_balance::text AS settled_cash_text,
    c.fx_rate_to_base::text AS fx_rate_to_base_text
FROM derived_portfolio_cash_balances c;

CREATE OR REPLACE VIEW subject_cash_activity_series
WITH (security_invoker = true) AS
SELECT
    a.tenant_id,
    c.account_id AS subject_id,
    'physical_account'::text AS source_kind,
    c.source_key,
    c.effective_date,
    c.event_datetime,
    c.transaction_type,
    c.description,
    c.amount_text,
    c.currency,
    c.conid,
    true AS included,
    false AS external_flow
FROM cash_transactions c
JOIN accounts a ON a.account_id = c.account_id
UNION ALL
SELECT
    c.tenant_id,
    c.portfolio_id AS subject_id,
    'derived_portfolio'::text AS source_kind,
    c.event_key AS source_key,
    c.event_date AS effective_date,
    c.event_date AS event_datetime,
    c.classification AS transaction_type,
    c.description,
    c.amount::text AS amount_text,
    c.currency,
    c.conid,
    c.included = 1 AS included,
    c.external_flow = 1 AS external_flow
FROM derived_portfolio_cash_ledger c;
