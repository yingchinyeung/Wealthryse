CREATE TABLE IF NOT EXISTS tenants (
    tenant_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'closed')),
    created_at TEXT NOT NULL
);

CREATE OR REPLACE FUNCTION app_tenant_id() RETURNS UUID
LANGUAGE SQL STABLE AS $$
    SELECT COALESCE(
        NULLIF(current_setting('app.tenant_id', true), '')::UUID,
        '00000000-0000-0000-0000-000000000001'::UUID
    )
$$;

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id BIGSERIAL PRIMARY KEY,
    run_type TEXT NOT NULL,
    requested_from TEXT,
    requested_to TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    reference_code TEXT,
    raw_sha256 TEXT,
    raw_path TEXT,
    row_count BIGINT DEFAULT 0,
    reconciliation_json TEXT,
    error_message TEXT,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_started_at ON ingestion_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS flex_request_state (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    token_fingerprint TEXT NOT NULL,
    query_id TEXT NOT NULL,
    last_send_at TEXT,
    last_error_at TEXT,
    last_error_phase TEXT,
    last_error_code TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    blocked_until TEXT,
    blocked_reason TEXT,
    requires_manual_reset BOOLEAN NOT NULL DEFAULT false,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, token_fingerprint, query_id)
);

CREATE TABLE IF NOT EXISTS raw_reports (
    raw_report_id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    received_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    content_type TEXT,
    byte_count BIGINT NOT NULL,
    file_path TEXT NOT NULL,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id)
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    alias TEXT,
    base_currency TEXT,
    account_type TEXT,
    ib_entity TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source_json TEXT NOT NULL,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_accounts_tenant ON accounts(tenant_id, account_id);

CREATE TABLE IF NOT EXISTS instruments (
    conid TEXT PRIMARY KEY,
    symbol TEXT,
    description TEXT,
    asset_category TEXT,
    currency TEXT,
    listing_exchange TEXT,
    underlying_conid TEXT,
    expiry TEXT,
    strike_text TEXT,
    put_call TEXT,
    multiplier_text TEXT,
    isin TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flex_rows (
    flex_row_id BIGSERIAL PRIMARY KEY,
    account_id TEXT NOT NULL,
    section TEXT NOT NULL,
    record_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    report_date TEXT,
    conid TEXT,
    currency TEXT,
    row_sha256 TEXT NOT NULL,
    data_json TEXT NOT NULL,
    first_seen_run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    last_seen_run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    UNIQUE(account_id, section, record_type, source_key)
);
CREATE INDEX IF NOT EXISTS idx_flex_rows_section_date ON flex_rows(section, report_date);

CREATE TABLE IF NOT EXISTS trades (
    account_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    execution_id TEXT,
    trade_id TEXT,
    conid TEXT,
    trade_date TEXT,
    trade_time TEXT,
    settle_date TEXT,
    buy_sell TEXT,
    quantity_text TEXT,
    trade_price_text TEXT,
    proceeds_text TEXT,
    commission_text TEXT,
    commission_currency TEXT,
    net_cash_text TEXT,
    realized_pnl_text TEXT,
    currency TEXT,
    order_reference TEXT,
    model_code TEXT,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, source_key)
);
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_conid ON trades(conid);

CREATE TABLE IF NOT EXISTS cash_transactions (
    account_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    transaction_id TEXT,
    event_datetime TEXT,
    model_code TEXT,
    effective_date TEXT,
    settle_date TEXT,
    transaction_type TEXT,
    description TEXT,
    amount_text TEXT,
    currency TEXT,
    conid TEXT,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, source_key)
);
CREATE INDEX IF NOT EXISTS idx_cash_transactions_date ON cash_transactions(effective_date);

CREATE TABLE IF NOT EXISTS corporate_actions (
    account_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    action_id TEXT,
    effective_date TEXT,
    action_type TEXT,
    description TEXT,
    quantity_text TEXT,
    amount_text TEXT,
    currency TEXT,
    conid TEXT,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, source_key)
);

CREATE TABLE IF NOT EXISTS position_snapshots (
    account_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    conid TEXT NOT NULL,
    model_code TEXT NOT NULL DEFAULT '',
    currency TEXT,
    quantity_text TEXT,
    cost_basis_text TEXT,
    mark_price_text TEXT,
    market_value_text TEXT,
    unrealized_pnl_text TEXT,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, snapshot_date, conid, model_code)
);
CREATE INDEX IF NOT EXISTS idx_positions_date ON position_snapshots(snapshot_date);

CREATE TABLE IF NOT EXISTS cash_snapshots (
    account_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    ending_cash_text TEXT,
    settled_cash_text TEXT,
    fx_rate_to_base_text TEXT,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, snapshot_date, currency)
);

CREATE TABLE IF NOT EXISTS nav_snapshots (
    account_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    asset_class TEXT NOT NULL DEFAULT 'TOTAL',
    total_text TEXT,
    cash_text TEXT,
    stock_text TEXT,
    options_text TEXT,
    futures_text TEXT,
    bonds_text TEXT,
    accruals_text TEXT,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, snapshot_date, currency, asset_class)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    account_id TEXT NOT NULL,
    rate_date TEXT NOT NULL,
    from_currency TEXT NOT NULL,
    to_currency TEXT NOT NULL,
    rate_text TEXT NOT NULL,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, rate_date, from_currency, to_currency)
);

CREATE TABLE IF NOT EXISTS fx_transactions (
    account_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    event_datetime TEXT,
    activity_description TEXT,
    model_code TEXT,
    report_date TEXT,
    functional_currency TEXT,
    fx_currency TEXT,
    quantity_text TEXT,
    proceeds_text TEXT,
    cost_text TEXT,
    realized_pnl_text TEXT,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, source_key)
);
CREATE INDEX IF NOT EXISTS idx_fx_transactions_date ON fx_transactions(report_date);

CREATE TABLE IF NOT EXISTS derived_portfolios (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    portfolio_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source_account_id TEXT NOT NULL REFERENCES accounts(account_id),
    base_currency TEXT NOT NULL,
    exclusion_rule_json TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, portfolio_id),
    UNIQUE(tenant_id, name)
);

CREATE TABLE IF NOT EXISTS derived_portfolio_nav (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    portfolio_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    total_nav DOUBLE PRECISION,
    cash DOUBLE PRECISION,
    stock DOUBLE PRECISION,
    options DOUBLE PRECISION,
    futures DOUBLE PRECISION,
    bonds DOUBLE PRECISION,
    accruals DOUBLE PRECISION,
    source_total_nav DOUBLE PRECISION,
    excluded_market_value_base DOUBLE PRECISION NOT NULL,
    source_position_count INTEGER NOT NULL,
    included_position_count INTEGER NOT NULL,
    excluded_position_count INTEGER NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, portfolio_id, snapshot_date, currency),
    FOREIGN KEY(tenant_id, portfolio_id)
        REFERENCES derived_portfolios(tenant_id, portfolio_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS derived_portfolio_positions (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    portfolio_id TEXT NOT NULL,
    source_account_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    conid TEXT NOT NULL,
    model_code TEXT NOT NULL DEFAULT '',
    currency TEXT,
    quantity_text TEXT,
    cost_basis_text TEXT,
    mark_price_text TEXT,
    market_value_text TEXT,
    unrealized_pnl_text TEXT,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, portfolio_id, snapshot_date, conid, model_code),
    FOREIGN KEY(tenant_id, portfolio_id)
        REFERENCES derived_portfolios(tenant_id, portfolio_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS derived_portfolio_exclusions (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    portfolio_id TEXT NOT NULL,
    source_account_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    conid TEXT NOT NULL,
    model_code TEXT NOT NULL DEFAULT '',
    symbol TEXT,
    description TEXT,
    currency TEXT,
    listing_exchange TEXT,
    exclusion_reason TEXT NOT NULL,
    quantity_text TEXT,
    market_value_text TEXT,
    market_value_base DOUBLE PRECISION NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, portfolio_id, snapshot_date, conid, model_code),
    FOREIGN KEY(tenant_id, portfolio_id)
        REFERENCES derived_portfolios(tenant_id, portfolio_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS derived_portfolio_cash_ledger (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    portfolio_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    source_account_id TEXT NOT NULL,
    event_date TEXT NOT NULL,
    settle_date TEXT,
    source_type TEXT NOT NULL,
    source_key TEXT,
    conid TEXT,
    currency TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    fx_rate_to_base DOUBLE PRECISION,
    amount_base DOUBLE PRECISION,
    included INTEGER NOT NULL CHECK (included IN (0, 1)),
    external_flow INTEGER NOT NULL CHECK (external_flow IN (0, 1)),
    classification TEXT NOT NULL,
    description TEXT,
    exclusion_reason TEXT,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, portfolio_id, event_key),
    FOREIGN KEY(tenant_id, portfolio_id)
        REFERENCES derived_portfolios(tenant_id, portfolio_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS derived_portfolio_cash_balances (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    portfolio_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    opening_balance DOUBLE PRECISION NOT NULL,
    activity DOUBLE PRECISION NOT NULL,
    closing_balance DOUBLE PRECISION NOT NULL,
    fx_rate_to_base DOUBLE PRECISION NOT NULL,
    base_value DOUBLE PRECISION NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, portfolio_id, snapshot_date, currency),
    FOREIGN KEY(tenant_id, portfolio_id)
        REFERENCES derived_portfolios(tenant_id, portfolio_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_derived_portfolio_nav_date
    ON derived_portfolio_nav(tenant_id, portfolio_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_derived_portfolio_positions_date
    ON derived_portfolio_positions(tenant_id, portfolio_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_derived_portfolio_exclusions_date
    ON derived_portfolio_exclusions(tenant_id, portfolio_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_derived_portfolio_cash_ledger_date
    ON derived_portfolio_cash_ledger(tenant_id, portfolio_id, event_date, currency);
CREATE INDEX IF NOT EXISTS idx_derived_portfolio_cash_balances_date
    ON derived_portfolio_cash_balances(tenant_id, portfolio_id, snapshot_date, currency);

CREATE TABLE IF NOT EXISTS market_data_prices (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    instrument_id TEXT NOT NULL,
    price_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    instrument_name TEXT NOT NULL,
    price_type TEXT NOT NULL,
    currency TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, instrument_id, price_date, price_type)
);

CREATE INDEX IF NOT EXISTS idx_market_data_prices_date
    ON market_data_prices(tenant_id, instrument_id, price_date);

CREATE TABLE IF NOT EXISTS synthetic_portfolio_components (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    portfolio_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    component_id TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_type TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short', 'cash')),
    coefficient DOUBLE PRECISION NOT NULL,
    source_currency TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    source_value DOUBLE PRECISION NOT NULL,
    fx_rate_to_base DOUBLE PRECISION NOT NULL,
    base_value DOUBLE PRECISION NOT NULL,
    normalized_value DOUBLE PRECISION NOT NULL,
    contribution DOUBLE PRECISION NOT NULL,
    observation_date TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, portfolio_id, snapshot_date, component_id),
    FOREIGN KEY(tenant_id, portfolio_id)
        REFERENCES derived_portfolios(tenant_id, portfolio_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_synthetic_components_date
    ON synthetic_portfolio_components(tenant_id, portfolio_id, snapshot_date);

CREATE TABLE IF NOT EXISTS analytics_daily (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    account_id TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, account_id, as_of_date, calculation_version)
);

CREATE TABLE IF NOT EXISTS report_runs (
    report_run_id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    ingestion_run_id BIGINT REFERENCES ingestion_runs(run_id),
    generated_at TEXT NOT NULL,
    report_path TEXT NOT NULL,
    report_sha256 TEXT,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    error_message TEXT
);

CREATE OR REPLACE VIEW v_latest_positions WITH (security_invoker=true) AS
SELECT p.*
FROM position_snapshots p
JOIN (
    SELECT account_id, MAX(snapshot_date) AS snapshot_date
    FROM position_snapshots
    GROUP BY account_id
) latest ON latest.account_id = p.account_id AND latest.snapshot_date = p.snapshot_date;

CREATE OR REPLACE VIEW v_daily_nav WITH (security_invoker=true) AS
SELECT account_id, snapshot_date, currency,
       CAST(total_text AS DOUBLE PRECISION) AS total_nav,
       CAST(cash_text AS DOUBLE PRECISION) AS cash,
       CAST(stock_text AS DOUBLE PRECISION) AS stock,
       CAST(options_text AS DOUBLE PRECISION) AS options,
       CAST(futures_text AS DOUBLE PRECISION) AS futures,
       CAST(bonds_text AS DOUBLE PRECISION) AS bonds,
       CAST(accruals_text AS DOUBLE PRECISION) AS accruals
FROM nav_snapshots
WHERE UPPER(asset_class) IN ('TOTAL', 'ALL', '');

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE ingestion_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE flex_request_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE flex_request_state FORCE ROW LEVEL SECURITY;
ALTER TABLE raw_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_reports FORCE ROW LEVEL SECURITY;
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts FORCE ROW LEVEL SECURITY;
ALTER TABLE flex_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE flex_rows FORCE ROW LEVEL SECURITY;
ALTER TABLE analytics_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics_daily FORCE ROW LEVEL SECURITY;
ALTER TABLE report_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolios ENABLE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolios FORCE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolio_nav ENABLE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolio_nav FORCE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolio_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolio_positions FORCE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolio_exclusions ENABLE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolio_exclusions FORCE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolio_cash_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolio_cash_ledger FORCE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolio_cash_balances ENABLE ROW LEVEL SECURITY;
ALTER TABLE derived_portfolio_cash_balances FORCE ROW LEVEL SECURITY;
ALTER TABLE market_data_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_data_prices FORCE ROW LEVEL SECURITY;
ALTER TABLE synthetic_portfolio_components ENABLE ROW LEVEL SECURITY;
ALTER TABLE synthetic_portfolio_components FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON tenants;
CREATE POLICY tenant_isolation ON tenants USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON ingestion_runs;
CREATE POLICY tenant_isolation ON ingestion_runs USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON flex_request_state;
CREATE POLICY tenant_isolation ON flex_request_state USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON raw_reports;
CREATE POLICY tenant_isolation ON raw_reports USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON accounts;
CREATE POLICY tenant_isolation ON accounts USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON flex_rows;
CREATE POLICY tenant_isolation ON flex_rows USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON analytics_daily;
CREATE POLICY tenant_isolation ON analytics_daily USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON report_runs;
CREATE POLICY tenant_isolation ON report_runs USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON derived_portfolios;
CREATE POLICY tenant_isolation ON derived_portfolios USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON derived_portfolio_nav;
CREATE POLICY tenant_isolation ON derived_portfolio_nav USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON derived_portfolio_positions;
CREATE POLICY tenant_isolation ON derived_portfolio_positions USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON derived_portfolio_exclusions;
CREATE POLICY tenant_isolation ON derived_portfolio_exclusions USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON derived_portfolio_cash_ledger;
CREATE POLICY tenant_isolation ON derived_portfolio_cash_ledger USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON derived_portfolio_cash_balances;
CREATE POLICY tenant_isolation ON derived_portfolio_cash_balances USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON market_data_prices;
CREATE POLICY tenant_isolation ON market_data_prices USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON synthetic_portfolio_components;
CREATE POLICY tenant_isolation ON synthetic_portfolio_components USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

CREATE OR REPLACE FUNCTION account_belongs_to_current_tenant(candidate TEXT) RETURNS BOOLEAN
LANGUAGE SQL STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM accounts
        WHERE account_id = candidate AND tenant_id = app_tenant_id()
    )
$$;

ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades FORCE ROW LEVEL SECURITY;
ALTER TABLE cash_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cash_transactions FORCE ROW LEVEL SECURITY;
ALTER TABLE corporate_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE corporate_actions FORCE ROW LEVEL SECURITY;
ALTER TABLE position_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE position_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE cash_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE cash_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE nav_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE nav_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE fx_rates ENABLE ROW LEVEL SECURITY;
ALTER TABLE fx_rates FORCE ROW LEVEL SECURITY;
ALTER TABLE fx_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE fx_transactions FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS account_isolation ON trades;
CREATE POLICY account_isolation ON trades USING (account_belongs_to_current_tenant(account_id)) WITH CHECK (account_belongs_to_current_tenant(account_id));
DROP POLICY IF EXISTS account_isolation ON cash_transactions;
CREATE POLICY account_isolation ON cash_transactions USING (account_belongs_to_current_tenant(account_id)) WITH CHECK (account_belongs_to_current_tenant(account_id));
DROP POLICY IF EXISTS account_isolation ON corporate_actions;
CREATE POLICY account_isolation ON corporate_actions USING (account_belongs_to_current_tenant(account_id)) WITH CHECK (account_belongs_to_current_tenant(account_id));
DROP POLICY IF EXISTS account_isolation ON position_snapshots;
CREATE POLICY account_isolation ON position_snapshots USING (account_belongs_to_current_tenant(account_id)) WITH CHECK (account_belongs_to_current_tenant(account_id));
DROP POLICY IF EXISTS account_isolation ON cash_snapshots;
CREATE POLICY account_isolation ON cash_snapshots USING (account_belongs_to_current_tenant(account_id)) WITH CHECK (account_belongs_to_current_tenant(account_id));
DROP POLICY IF EXISTS account_isolation ON nav_snapshots;
CREATE POLICY account_isolation ON nav_snapshots USING (account_belongs_to_current_tenant(account_id)) WITH CHECK (account_belongs_to_current_tenant(account_id));
DROP POLICY IF EXISTS account_isolation ON fx_rates;
CREATE POLICY account_isolation ON fx_rates USING (account_belongs_to_current_tenant(account_id)) WITH CHECK (account_belongs_to_current_tenant(account_id));
DROP POLICY IF EXISTS account_isolation ON fx_transactions;
CREATE POLICY account_isolation ON fx_transactions USING (account_belongs_to_current_tenant(account_id)) WITH CHECK (account_belongs_to_current_tenant(account_id));
