-- The live market agent (Agentic AI integration map, "Live market agent").
--
-- Applied after 009_connection_checks.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent.
--
-- PR 58's read-only collector stores live account values, positions, quotes
-- and executions from IB Gateway. The agent reads only those stored rows,
-- through the views below; it never connects to the gateway, so it has no
-- route to an order. Like the website, it shows live data for an account
-- only when that account's live_view switch is 'active' (the 'preview' pilot
-- stays with the owner's own website view).
--
-- Adds:
--   1. scoped views of the latest live snapshot, its positions, the last
--      day's executions and the gateway's state, for the tool server only;
--   2. the live_market job and its inbox kind (alerts with explanations);
--   3. the full, validated lists of jobs and inbox kinds.
--
-- The installer applies every file again on each run, and earlier files set
-- shorter lists. Those add their lists NOT VALID, so rows of a later job or
-- kind do not stop a re-run; the last file that changes a list sets it in
-- full and validates it. A later file that adds a job or kind takes over that
-- role and changes this file's lists to NOT VALID.

CREATE OR REPLACE FUNCTION agent.live_active(p_subject text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT EXISTS (SELECT 1 FROM public.subject_feature_states f
                    WHERE f.tenant_id = agent.tenant() AND f.subject_id = p_subject
                      AND f.feature_key = 'live_view' AND f.feature_state = 'active')
$$;

CREATE OR REPLACE FUNCTION agent.scoped_live_summary()
RETURNS TABLE (subject_id text, observed_at timestamptz, base_currency text, net_liquidation numeric,
               total_cash numeric, daily_pnl numeric, unrealized_pnl numeric, realized_pnl numeric,
               cash_by_currency jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT s.subject_id, s.observed_at, s.base_currency, s.net_liquidation, s.total_cash, s.daily_pnl,
           s.unrealized_pnl, s.realized_pnl, s.cash_by_currency
      FROM public.subject_live_summary s
     WHERE s.tenant_id = agent.tenant() AND s.subject_id = ANY(agent.scope()) AND agent.live_active(s.subject_id)
$$;

CREATE OR REPLACE FUNCTION agent.scoped_live_positions()
RETURNS TABLE (subject_id text, observed_at timestamptz, conid text, symbol text, description text, sec_type text,
               currency text, primary_exchange text, industry text, category text, quantity numeric,
               market_price numeric, market_value numeric, unrealized_pnl numeric, daily_pnl numeric,
               last_price numeric, prior_close numeric, market_data_type smallint, quote_observed_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT p.subject_id, p.observed_at, p.conid, p.symbol, p.description, p.sec_type, p.currency,
           p.primary_exchange, p.industry, p.category, p.quantity, p.market_price, p.market_value,
           p.unrealized_pnl, p.daily_pnl, p.last_price, p.prior_close, p.market_data_type, p.quote_observed_at
      FROM public.subject_live_positions p
     WHERE p.tenant_id = agent.tenant() AND p.subject_id = ANY(agent.scope()) AND agent.live_active(p.subject_id)
$$;

CREATE OR REPLACE FUNCTION agent.scoped_live_executions()
RETURNS TABLE (subject_id text, executed_at timestamptz, symbol text, sec_type text, currency text, side text,
               quantity numeric, price numeric, commission numeric, realized_pnl numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT e.account_id, e.executed_at, e.symbol, e.sec_type, e.currency, e.side, e.quantity, e.price,
           e.commission, e.realized_pnl
      FROM public.live_executions e
     WHERE e.tenant_id = agent.tenant() AND e.account_id = ANY(agent.scope()) AND agent.live_active(e.account_id)
       AND e.executed_at >= now() - interval '24 hours'
$$;

-- The gateway's latest heartbeat. No client data: a state, times and a count.
CREATE OR REPLACE FUNCTION agent.scoped_live_gateway()
RETURNS TABLE (observed_at timestamptz, gateway_state text, market_data_type smallint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT g.observed_at, g.gateway_state, g.market_data_type FROM public.live_gateway_status g
     WHERE g.tenant_id = agent.tenant() ORDER BY g.observed_at DESC LIMIT 1
$$;

CREATE OR REPLACE VIEW agent.live_summary AS SELECT * FROM agent.scoped_live_summary();
CREATE OR REPLACE VIEW agent.live_positions AS SELECT * FROM agent.scoped_live_positions();
CREATE OR REPLACE VIEW agent.live_executions AS SELECT * FROM agent.scoped_live_executions();
CREATE OR REPLACE VIEW agent.live_gateway AS SELECT * FROM agent.scoped_live_gateway();

ALTER TABLE agent.service_grants DROP CONSTRAINT IF EXISTS service_grants_job_name_check;
ALTER TABLE agent.service_grants ADD CONSTRAINT service_grants_job_name_check
    CHECK (job_name IN ('holdings_explainer', 'month_end_drafter', 'data_quality', 'month_team', 'onboarding',
                        'operations', 'weekly_brief', 'live_market'));

ALTER TABLE agent.inbox DROP CONSTRAINT IF EXISTS inbox_kind_check;
ALTER TABLE agent.inbox ADD CONSTRAINT inbox_kind_check CHECK (kind IN (
    'holdings_explainer', 'month_end_draft', 'data_quality',
    'month_end_report', 'month_end_hold', 'month_end_dispute',
    'onboarding_watch', 'weekly_brief', 'live_alert'));

REVOKE ALL ON agent.live_summary, agent.live_positions, agent.live_executions, agent.live_gateway FROM PUBLIC;
REVOKE ALL ON FUNCTION agent.live_active(text), agent.scoped_live_summary(), agent.scoped_live_positions(),
    agent.scoped_live_executions(), agent.scoped_live_gateway() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION agent.live_active(text), agent.scoped_live_summary(), agent.scoped_live_positions(),
    agent.scoped_live_executions(), agent.scoped_live_gateway() TO agent_reader;
GRANT SELECT ON agent.live_summary, agent.live_positions, agent.live_executions, agent.live_gateway TO agent_reader;
