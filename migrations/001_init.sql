PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    requested_from TEXT,
    requested_to TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    reference_code TEXT,
    raw_sha256 TEXT,
    raw_path TEXT,
    row_count INTEGER DEFAULT 0,
    reconciliation_json TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_started_at
    ON ingestion_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS flex_request_state (
    token_fingerprint TEXT NOT NULL,
    query_id TEXT NOT NULL,
    last_send_at TEXT,
    last_error_at TEXT,
    last_error_phase TEXT,
    last_error_code TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    blocked_until TEXT,
    blocked_reason TEXT,
    requires_manual_reset INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(token_fingerprint, query_id)
);

CREATE TABLE IF NOT EXISTS raw_reports (
    raw_report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    received_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    content_type TEXT,
    byte_count INTEGER NOT NULL,
    file_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    alias TEXT,
    base_currency TEXT,
    account_type TEXT,
    ib_entity TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source_json TEXT NOT NULL
);

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
    flex_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    section TEXT NOT NULL,
    record_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    report_date TEXT,
    conid TEXT,
    currency TEXT,
    row_sha256 TEXT NOT NULL,
    data_json TEXT NOT NULL,
    first_seen_run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    last_seen_run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE(account_id, section, record_type, source_key)
);

CREATE INDEX IF NOT EXISTS idx_flex_rows_section_date
    ON flex_rows(section, report_date);

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
    last_seen_run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
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
    last_seen_run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, source_key)
);

CREATE INDEX IF NOT EXISTS idx_cash_transactions_date
    ON cash_transactions(effective_date);

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
    last_seen_run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
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
    last_seen_run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, snapshot_date, conid, model_code)
);

CREATE INDEX IF NOT EXISTS idx_positions_date
    ON position_snapshots(snapshot_date);

CREATE TABLE IF NOT EXISTS cash_snapshots (
    account_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    ending_cash_text TEXT,
    settled_cash_text TEXT,
    fx_rate_to_base_text TEXT,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
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
    last_seen_run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, snapshot_date, currency, asset_class)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    account_id TEXT NOT NULL,
    rate_date TEXT NOT NULL,
    from_currency TEXT NOT NULL,
    to_currency TEXT NOT NULL,
    rate_text TEXT NOT NULL,
    row_sha256 TEXT NOT NULL,
    last_seen_run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
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
    last_seen_run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    PRIMARY KEY(account_id, source_key)
);

CREATE INDEX IF NOT EXISTS idx_fx_transactions_date
    ON fx_transactions(report_date);

CREATE TABLE IF NOT EXISTS derived_portfolios (
    portfolio_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source_account_id TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    exclusion_rule_json TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS derived_portfolio_nav (
    portfolio_id TEXT NOT NULL REFERENCES derived_portfolios(portfolio_id) ON DELETE CASCADE,
    snapshot_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    total_nav REAL,
    cash REAL,
    stock REAL,
    options REAL,
    futures REAL,
    bonds REAL,
    accruals REAL,
    source_total_nav REAL,
    excluded_market_value_base REAL NOT NULL,
    source_position_count INTEGER NOT NULL,
    included_position_count INTEGER NOT NULL,
    excluded_position_count INTEGER NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(portfolio_id, snapshot_date, currency)
);

CREATE TABLE IF NOT EXISTS derived_portfolio_positions (
    portfolio_id TEXT NOT NULL REFERENCES derived_portfolios(portfolio_id) ON DELETE CASCADE,
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
    PRIMARY KEY(portfolio_id, snapshot_date, conid, model_code)
);

CREATE TABLE IF NOT EXISTS derived_portfolio_exclusions (
    portfolio_id TEXT NOT NULL REFERENCES derived_portfolios(portfolio_id) ON DELETE CASCADE,
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
    market_value_base REAL NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(portfolio_id, snapshot_date, conid, model_code)
);

CREATE TABLE IF NOT EXISTS derived_portfolio_cash_ledger (
    portfolio_id TEXT NOT NULL REFERENCES derived_portfolios(portfolio_id) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    source_account_id TEXT NOT NULL,
    event_date TEXT NOT NULL,
    settle_date TEXT,
    source_type TEXT NOT NULL,
    source_key TEXT,
    conid TEXT,
    currency TEXT NOT NULL,
    amount REAL NOT NULL,
    fx_rate_to_base REAL,
    amount_base REAL,
    included INTEGER NOT NULL CHECK (included IN (0, 1)),
    external_flow INTEGER NOT NULL CHECK (external_flow IN (0, 1)),
    classification TEXT NOT NULL,
    description TEXT,
    exclusion_reason TEXT,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(portfolio_id, event_key)
);

CREATE TABLE IF NOT EXISTS derived_portfolio_cash_balances (
    portfolio_id TEXT NOT NULL REFERENCES derived_portfolios(portfolio_id) ON DELETE CASCADE,
    snapshot_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    opening_balance REAL NOT NULL,
    activity REAL NOT NULL,
    closing_balance REAL NOT NULL,
    fx_rate_to_base REAL NOT NULL,
    base_value REAL NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(portfolio_id, snapshot_date, currency)
);

CREATE INDEX IF NOT EXISTS idx_derived_portfolio_nav_date
    ON derived_portfolio_nav(portfolio_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_derived_portfolio_positions_date
    ON derived_portfolio_positions(portfolio_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_derived_portfolio_exclusions_date
    ON derived_portfolio_exclusions(portfolio_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_derived_portfolio_cash_ledger_date
    ON derived_portfolio_cash_ledger(portfolio_id, event_date, currency);
CREATE INDEX IF NOT EXISTS idx_derived_portfolio_cash_balances_date
    ON derived_portfolio_cash_balances(portfolio_id, snapshot_date, currency);

CREATE TABLE IF NOT EXISTS market_data_prices (
    instrument_id TEXT NOT NULL,
    price_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    instrument_name TEXT NOT NULL,
    price_type TEXT NOT NULL,
    currency TEXT NOT NULL,
    price REAL NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(instrument_id, price_date, price_type)
);

CREATE INDEX IF NOT EXISTS idx_market_data_prices_date
    ON market_data_prices(instrument_id, price_date);

CREATE TABLE IF NOT EXISTS synthetic_portfolio_components (
    portfolio_id TEXT NOT NULL REFERENCES derived_portfolios(portfolio_id) ON DELETE CASCADE,
    snapshot_date TEXT NOT NULL,
    component_id TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_type TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short', 'cash')),
    coefficient REAL NOT NULL,
    source_currency TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    source_value REAL NOT NULL,
    fx_rate_to_base REAL NOT NULL,
    base_value REAL NOT NULL,
    normalized_value REAL NOT NULL,
    contribution REAL NOT NULL,
    observation_date TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(portfolio_id, snapshot_date, component_id)
);

CREATE INDEX IF NOT EXISTS idx_synthetic_components_date
    ON synthetic_portfolio_components(portfolio_id, snapshot_date);

CREATE VIEW IF NOT EXISTS v_latest_positions AS
SELECT p.*
FROM position_snapshots p
JOIN (
    SELECT account_id, MAX(snapshot_date) AS snapshot_date
    FROM position_snapshots
    GROUP BY account_id
) latest
ON latest.account_id = p.account_id
AND latest.snapshot_date = p.snapshot_date;

CREATE VIEW IF NOT EXISTS v_daily_nav AS
SELECT account_id,
       snapshot_date,
       currency,
       CAST(total_text AS REAL) AS total_nav,
       CAST(cash_text AS REAL) AS cash,
       CAST(stock_text AS REAL) AS stock,
       CAST(options_text AS REAL) AS options,
       CAST(futures_text AS REAL) AS futures,
       CAST(bonds_text AS REAL) AS bonds,
       CAST(accruals_text AS REAL) AS accruals
FROM nav_snapshots
WHERE UPPER(asset_class) IN ('TOTAL', 'ALL', '');
