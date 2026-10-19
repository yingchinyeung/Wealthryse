-- The market news watch (supervisor design, data and operations team; docs/AGENTS.md).
--
-- Applied after 018_supervisor_tasks.sql by scripts/install_agent_schema.py, on purpose and
-- with approval. Idempotent and additive: three new tables, one view, one inbox kind. No
-- existing table, view or grant changes otherwise.
--
-- Public news only, never client data:
--   market_news_items     every news item the watch read, once (its link, title and date; no text)
--   market_events         the market changes code confirmed from an item: market, kind, the day
--                         it takes effect, the source sentence and link
--   market_news_requests  a dated search another agent asked for, when a number didn't add up
--                         and no stored event explained it; it holds a market and dates only
-- The tool server reads the events through agent.market_events_feed (get_market_events).

CREATE TABLE IF NOT EXISTS agent.market_news_items (
    item_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    digest text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    source_id text NOT NULL CHECK (source_id ~ '^[a-z0-9-]{1,40}$'),
    markets text[] NOT NULL CHECK (cardinality(markets) BETWEEN 1 AND 10),
    url text CHECK (char_length(url) <= 1000),
    title text NOT NULL CHECK (char_length(title) <= 400),
    published_at timestamptz,
    status text NOT NULL CHECK (status IN ('skipped', 'not_event', 'event', 'failed')),
    fetched_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, digest)
);
CREATE INDEX IF NOT EXISTS market_news_items_recent ON agent.market_news_items (tenant_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS agent.market_events (
    event_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    item_id bigint REFERENCES agent.market_news_items(item_id) ON DELETE SET NULL,
    market text NOT NULL CHECK (market ~ '^X[A-Z]{3}$'),
    kind text NOT NULL CHECK (kind IN ('trading_hours', 'closing_price', 'new_venue', 'closure', 'halt',
                                       'price_limit', 'tax_or_fee', 'settlement', 'index_change', 'currency',
                                       'other_rule')),
    effective_date date NOT NULL,
    end_date date CHECK (end_date IS NULL OR end_date >= effective_date),
    summary text NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 300),
    quote text NOT NULL CHECK (char_length(quote) BETWEEN 1 AND 600),
    source_url text NOT NULL CHECK (char_length(source_url) <= 1000),
    source_id text NOT NULL CHECK (source_id ~ '^[a-z0-9-]{1,40}$'),
    official boolean NOT NULL DEFAULT false,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, market, kind, effective_date, source_url)
);
CREATE INDEX IF NOT EXISTS market_events_by_day ON agent.market_events (tenant_id, market, effective_date);

CREATE TABLE IF NOT EXISTS agent.market_news_requests (
    request_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    market text NOT NULL CHECK (market ~ '^X[A-Z]{3}$'),
    window_start date NOT NULL,
    window_end date NOT NULL CHECK (window_end >= window_start AND window_end - window_start <= 45),
    ref text CHECK (char_length(ref) <= 120),
    created_at timestamptz NOT NULL DEFAULT now(),
    searched_at timestamptz,
    items_found integer,
    UNIQUE (tenant_id, market, window_start, window_end)
);

CREATE OR REPLACE FUNCTION agent.scoped_market_events()
RETURNS TABLE (
    event_id uuid, market text, kind text, effective_date date, end_date date, summary text, quote text,
    source_url text, source_id text, official boolean, published_at timestamptz
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT e.event_id, e.market, e.kind, e.effective_date, e.end_date, e.summary, e.quote, e.source_url,
           e.source_id, e.official, e.published_at
      FROM agent.market_events e
     WHERE e.tenant_id = agent.tenant()
$$;
CREATE OR REPLACE VIEW agent.market_events_feed AS SELECT * FROM agent.scoped_market_events();

ALTER TABLE agent.inbox DROP CONSTRAINT IF EXISTS inbox_kind_check;
ALTER TABLE agent.inbox ADD CONSTRAINT inbox_kind_check CHECK (kind IN (
    'holdings_explainer', 'month_end_draft', 'data_quality',
    'month_end_report', 'month_end_hold', 'month_end_dispute',
    'onboarding_watch', 'weekly_brief', 'live_alert', 'risk_alert', 'model_check', 'export_pack',
    'guide_check', 'guide_keeper', 'market_event'));

REVOKE ALL ON agent.market_news_items, agent.market_events, agent.market_news_requests,
    agent.market_events_feed FROM PUBLIC;
REVOKE ALL ON FUNCTION agent.scoped_market_events() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION agent.scoped_market_events() TO agent_reader;
GRANT SELECT ON agent.market_events_feed TO agent_reader;

GRANT SELECT, INSERT ON agent.market_news_items, agent.market_events TO agent_writer;
GRANT SELECT, INSERT, UPDATE ON agent.market_news_requests TO agent_writer;
GRANT USAGE ON SEQUENCE agent.market_news_items_item_id_seq, agent.market_news_requests_request_id_seq
    TO agent_writer;
