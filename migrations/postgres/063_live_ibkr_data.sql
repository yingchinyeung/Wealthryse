-- Live, read-only IBKR data from the TWS API (IB Gateway on the gateway VM).
-- The Flex statement stays the book of record; these tables only feed the
-- website's live view, the daily cross-check and later the market data jobs.
-- The collector role (ibkr_live_collector) is provisioned separately by
-- scripts/provision_live_collector.py with grants on these seven tables only.
-- Rollback: drop the two views, the seven tables and the role, and restore the
-- previous subject_feature_states feature_key check.

CREATE TABLE live_gateway_status (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    observed_at TIMESTAMPTZ NOT NULL,
    gateway_state TEXT NOT NULL CHECK (gateway_state IN ('connected', 'disconnected')),
    last_message_at TIMESTAMPTZ,
    market_data_lines_in_use INTEGER NOT NULL DEFAULT 0 CHECK (market_data_lines_in_use >= 0),
    market_data_type SMALLINT CHECK (market_data_type BETWEEN 1 AND 4),
    last_error_code INTEGER,
    last_error_message TEXT CHECK (last_error_message IS NULL OR length(last_error_message) <= 300),
    collector_version TEXT NOT NULL,
    PRIMARY KEY (tenant_id, observed_at)
);

CREATE TABLE live_account_values (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    observed_at TIMESTAMPTZ NOT NULL,
    base_currency TEXT NOT NULL CHECK (base_currency ~ '^[A-Z]{3}$'),
    net_liquidation NUMERIC NOT NULL,
    total_cash NUMERIC,
    daily_pnl NUMERIC,
    unrealized_pnl NUMERIC,
    realized_pnl NUMERIC,
    cash_by_currency JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(cash_by_currency) = 'object'),
    PRIMARY KEY (tenant_id, account_id, observed_at)
);

-- Written in the same transaction and with the same observed_at as the
-- account's live_account_values row, so an account with no positions is an
-- empty snapshot rather than a stale one.
CREATE TABLE live_positions (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    account_id TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    conid TEXT NOT NULL CHECK (conid ~ '^[0-9]+$'),
    symbol TEXT NOT NULL,
    sec_type TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    primary_exchange TEXT,
    quantity NUMERIC NOT NULL,
    average_cost NUMERIC,
    market_price NUMERIC,
    market_value NUMERIC,
    unrealized_pnl NUMERIC,
    daily_pnl NUMERIC,
    PRIMARY KEY (tenant_id, account_id, observed_at, conid),
    FOREIGN KEY (tenant_id, account_id, observed_at)
        REFERENCES live_account_values(tenant_id, account_id, observed_at) ON DELETE CASCADE
);

CREATE TABLE live_quotes (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    conid TEXT NOT NULL CHECK (conid ~ '^[0-9]+$'),
    observed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    last_price NUMERIC,
    bid_price NUMERIC,
    ask_price NUMERIC,
    prior_close NUMERIC,
    market_data_type SMALLINT NOT NULL CHECK (market_data_type BETWEEN 1 AND 4),
    PRIMARY KEY (tenant_id, conid, observed_at)
);

CREATE TABLE live_executions (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    exec_id TEXT NOT NULL CHECK (length(exec_id) BETWEEN 1 AND 64),
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    executed_at TIMESTAMPTZ NOT NULL,
    conid TEXT NOT NULL CHECK (conid ~ '^[0-9]+$'),
    symbol TEXT NOT NULL,
    sec_type TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    price NUMERIC NOT NULL,
    commission NUMERIC,
    commission_currency TEXT CHECK (commission_currency IS NULL OR commission_currency ~ '^[A-Z]{3}$'),
    realized_pnl NUMERIC,
    first_seen_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, exec_id)
);

CREATE TABLE live_daily_bars (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    conid TEXT NOT NULL CHECK (conid ~ '^[0-9]+$'),
    what_to_show TEXT NOT NULL CHECK (what_to_show IN ('ADJUSTED_LAST', 'TRADES')),
    bar_date DATE NOT NULL,
    open_price NUMERIC,
    high_price NUMERIC,
    low_price NUMERIC,
    close_price NUMERIC NOT NULL,
    volume NUMERIC,
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    fetched_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, conid, what_to_show, bar_date)
);

CREATE TABLE live_contracts (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    conid TEXT NOT NULL CHECK (conid ~ '^[0-9]+$'),
    symbol TEXT NOT NULL,
    local_symbol TEXT,
    sec_type TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    primary_exchange TEXT,
    long_name TEXT,
    industry TEXT,
    category TEXT,
    subcategory TEXT,
    is_benchmark BOOLEAN NOT NULL DEFAULT false,
    refreshed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, conid)
);

-- Retention deletes run by time across accounts and instruments.
CREATE INDEX live_gateway_status_time_idx ON live_gateway_status (observed_at);
CREATE INDEX live_account_values_time_idx ON live_account_values (observed_at);
CREATE INDEX live_quotes_time_idx ON live_quotes (observed_at);
CREATE INDEX live_executions_account_time_idx
    ON live_executions (tenant_id, account_id, executed_at DESC);

ALTER TABLE live_gateway_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_gateway_status FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON live_gateway_status
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE live_account_values ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_account_values FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON live_account_values
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE live_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_positions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON live_positions
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE live_quotes ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_quotes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON live_quotes
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE live_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_executions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON live_executions
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE live_daily_bars ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_daily_bars FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON live_daily_bars
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE live_contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_contracts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON live_contracts
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

-- Security-invoker views keep row-level security and per-account access as
-- they work for the subject_*_series views. Physical accounts are subjects
-- under their IBKR account ID.
CREATE VIEW subject_live_summary
WITH (security_invoker = true) AS
SELECT
    a.tenant_id,
    a.account_id AS subject_id,
    v.observed_at,
    v.base_currency,
    v.net_liquidation,
    v.total_cash,
    v.daily_pnl,
    v.unrealized_pnl,
    v.realized_pnl,
    v.cash_by_currency
FROM accounts a
CROSS JOIN LATERAL (
    SELECT *
    FROM live_account_values latest
    WHERE latest.tenant_id = a.tenant_id AND latest.account_id = a.account_id
    ORDER BY latest.observed_at DESC
    LIMIT 1
) v;

CREATE VIEW subject_live_positions
WITH (security_invoker = true) AS
SELECT
    s.tenant_id,
    s.subject_id,
    p.observed_at,
    p.conid,
    COALESCE(c.symbol, p.symbol) AS symbol,
    c.long_name AS description,
    p.sec_type,
    p.currency,
    COALESCE(c.primary_exchange, p.primary_exchange) AS primary_exchange,
    c.industry,
    c.category,
    c.subcategory,
    p.quantity,
    p.average_cost,
    p.market_price,
    p.market_value,
    p.unrealized_pnl,
    p.daily_pnl,
    q.last_price,
    q.bid_price,
    q.ask_price,
    q.prior_close,
    q.market_data_type,
    q.observed_at AS quote_observed_at
FROM subject_live_summary s
JOIN live_positions p
  ON p.tenant_id = s.tenant_id
 AND p.account_id = s.subject_id
 AND p.observed_at = s.observed_at
LEFT JOIN live_contracts c
  ON c.tenant_id = p.tenant_id AND c.conid = p.conid
LEFT JOIN LATERAL (
    SELECT quote.last_price, quote.bid_price, quote.ask_price, quote.prior_close,
           quote.market_data_type, quote.observed_at
    FROM live_quotes quote
    WHERE quote.tenant_id = p.tenant_id AND quote.conid = p.conid
    ORDER BY quote.observed_at DESC
    LIMIT 1
) q ON true;

-- The owner-only pilot and the later opening to every authorized viewer are
-- per-account switches: 'preview' shows live data to the owner only and
-- 'active' to everyone who may already view that account.
ALTER TABLE subject_feature_states
    DROP CONSTRAINT IF EXISTS subject_feature_states_feature_key_check;
ALTER TABLE subject_feature_states
    ADD CONSTRAINT subject_feature_states_feature_key_check CHECK (feature_key IN (
        'data_ingestion', 'website_analytics', 'ai_analyst',
        'factsheet', 'presentation', 'viewer_access', 'live_view'
    ));
