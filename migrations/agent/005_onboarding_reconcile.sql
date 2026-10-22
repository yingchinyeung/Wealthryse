-- Onboarding agents, part 3: the reconciliation agent for broker accounts
-- (Agentic AI integration map, "Frontier onboarding").
--
-- Applied after 004_onboarding_intake.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent. Needs migration 064 (the
-- onboarding case and security master tables) to be in place first.
--
-- A broker account in stage 2 (load and reconcile) is not a platform subject
-- yet, so agent.scope() never covers it. The scheduled Flex worker already
-- loads every account in its statement, so the account's holdings, cash,
-- transactions and the broker's own NAV are in the platform tables. These
-- read-only views show the reconciliation agent that data for the IBKR
-- accounts of open stage 2 cases only, and nothing for any other account.
--
-- Adds:
--   1. the open broker cases and their accounts;
--   2. scoped views of those accounts' NAV, positions, cash, FX rates,
--      transactions and corporate actions, the instruments they hold, and the
--      security master's identifiers (reference data, no client data);
--   3. the agent's own table of results, for the agent writer.
-- Nothing here can change a case, a check or any platform table: the
-- platform records the four stage 2 checks from these rows
-- (ibkr_etl/onboarding_agent_reconcile.py).

-- Cases and accounts -------------------------------------------------------------

CREATE OR REPLACE FUNCTION agent.scoped_onboarding_broker_cases()
RETURNS TABLE (
    case_id uuid, subject_id text, broker text, broker_account_id text, display_name text,
    reporting_currency text, history_start_date text, base_currency text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT c.case_id, c.target_subject_id, c.broker, c.broker_account_id, c.display_name,
           c.reporting_currency, c.history_start_date::text, a.base_currency
      FROM public.broker_onboarding_cases c
      LEFT JOIN public.broker_connection_accounts a
        ON a.tenant_id = c.tenant_id AND a.connection_id = c.connection_id
       AND a.broker_account_id = c.broker_account_id
     WHERE c.tenant_id = agent.tenant() AND c.source_kind = 'broker_account' AND c.broker = 'ibkr_flex'
       AND c.stage = 'load_and_reconcile' AND c.status <> 'cancelled'
$$;

CREATE OR REPLACE VIEW agent.onboarding_broker_cases AS SELECT * FROM agent.scoped_onboarding_broker_cases();

-- The accounts the views below may show: those of open stage 2 IBKR cases.
CREATE OR REPLACE FUNCTION agent.onboarding_case_accounts() RETURNS text[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT COALESCE(array_agg(broker_account_id), ARRAY[]::text[]) FROM agent.scoped_onboarding_broker_cases()
$$;

-- Account data -------------------------------------------------------------------

CREATE OR REPLACE FUNCTION agent.scoped_onboarding_nav() RETURNS SETOF public.nav_snapshots
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.nav_snapshots WHERE account_id = ANY(agent.onboarding_case_accounts())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_onboarding_positions() RETURNS SETOF public.position_snapshots
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.position_snapshots WHERE account_id = ANY(agent.onboarding_case_accounts())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_onboarding_cash() RETURNS SETOF public.cash_snapshots
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.cash_snapshots WHERE account_id = ANY(agent.onboarding_case_accounts())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_onboarding_fx_rates() RETURNS SETOF public.fx_rates
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.fx_rates WHERE account_id = ANY(agent.onboarding_case_accounts())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_onboarding_corporate_actions() RETURNS SETOF public.corporate_actions
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.corporate_actions WHERE account_id = ANY(agent.onboarding_case_accounts())
$$;

-- Trades and cash transactions as dated amounts: what the checks need to see
-- that the history is loaded and to match a break against a settlement.
CREATE OR REPLACE FUNCTION agent.scoped_onboarding_activity()
RETURNS TABLE (account_id text, kind text, activity_date text, settle_date text, activity_type text,
               conid text, amount_text text, currency text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT t.account_id, 'trade', t.trade_date, t.settle_date, t.buy_sell, t.conid, t.net_cash_text, t.currency
      FROM public.trades t WHERE t.account_id = ANY(agent.onboarding_case_accounts())
    UNION ALL
    SELECT c.account_id, 'cash', c.effective_date, c.settle_date, c.transaction_type, c.conid, c.amount_text,
           c.currency
      FROM public.cash_transactions c WHERE c.account_id = ANY(agent.onboarding_case_accounts())
$$;

-- Reference data: the instruments these accounts hold, and the security
-- master's identifiers (shared by every tenant, no client data).
CREATE OR REPLACE FUNCTION agent.scoped_onboarding_instruments() RETURNS SETOF public.instruments
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT i.* FROM public.instruments i WHERE i.conid IN (
        SELECT p.conid FROM public.position_snapshots p WHERE p.account_id = ANY(agent.onboarding_case_accounts()))
$$;

CREATE OR REPLACE FUNCTION agent.scoped_security_identifiers()
RETURNS TABLE (scheme text, identifier text, security_key text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT scheme, identifier, security_key FROM public.security_identifiers
$$;

CREATE OR REPLACE VIEW agent.onboarding_nav AS SELECT * FROM agent.scoped_onboarding_nav();
CREATE OR REPLACE VIEW agent.onboarding_positions AS SELECT * FROM agent.scoped_onboarding_positions();
CREATE OR REPLACE VIEW agent.onboarding_cash AS SELECT * FROM agent.scoped_onboarding_cash();
CREATE OR REPLACE VIEW agent.onboarding_fx_rates AS SELECT * FROM agent.scoped_onboarding_fx_rates();
CREATE OR REPLACE VIEW agent.onboarding_corporate_actions AS SELECT * FROM agent.scoped_onboarding_corporate_actions();
CREATE OR REPLACE VIEW agent.onboarding_activity AS SELECT * FROM agent.scoped_onboarding_activity();
CREATE OR REPLACE VIEW agent.onboarding_instruments AS SELECT * FROM agent.scoped_onboarding_instruments();
CREATE OR REPLACE VIEW agent.security_identifiers AS SELECT * FROM agent.scoped_security_identifiers();

-- Results (agent writer) ----------------------------------------------------------

-- One result per case and state of its loaded data. ``data_sha256`` is the
-- digest of what was checked (dates, counts, holdings), so new data gets a
-- new result. ``checks`` holds the four stage 2 checks, each with its status,
-- message and evidence; ``proposals`` the security master entries proposed
-- for securities not yet in it.
CREATE TABLE IF NOT EXISTS agent.onboarding_reconciliations (
    reconciliation_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    case_id uuid NOT NULL,
    subject_id text NOT NULL,
    data_sha256 text NOT NULL CHECK (data_sha256 ~ '^[0-9a-f]{64}$'),
    checks jsonb NOT NULL,
    proposals jsonb NOT NULL DEFAULT '[]'::jsonb,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    correlation_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, case_id, data_sha256)
);

-- Grants ----------------------------------------------------------------------

REVOKE ALL ON agent.onboarding_reconciliations, agent.onboarding_broker_cases, agent.onboarding_nav,
    agent.onboarding_positions, agent.onboarding_cash, agent.onboarding_fx_rates,
    agent.onboarding_corporate_actions, agent.onboarding_activity, agent.onboarding_instruments,
    agent.security_identifiers FROM PUBLIC;
REVOKE ALL ON FUNCTION agent.scoped_onboarding_broker_cases(), agent.onboarding_case_accounts(),
    agent.scoped_onboarding_nav(), agent.scoped_onboarding_positions(), agent.scoped_onboarding_cash(),
    agent.scoped_onboarding_fx_rates(), agent.scoped_onboarding_corporate_actions(),
    agent.scoped_onboarding_activity(), agent.scoped_onboarding_instruments(),
    agent.scoped_security_identifiers() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION agent.scoped_onboarding_broker_cases(), agent.onboarding_case_accounts(),
    agent.scoped_onboarding_nav(), agent.scoped_onboarding_positions(), agent.scoped_onboarding_cash(),
    agent.scoped_onboarding_fx_rates(), agent.scoped_onboarding_corporate_actions(),
    agent.scoped_onboarding_activity(), agent.scoped_onboarding_instruments(),
    agent.scoped_security_identifiers() TO agent_reader;
GRANT SELECT ON agent.onboarding_broker_cases, agent.onboarding_nav, agent.onboarding_positions,
    agent.onboarding_cash, agent.onboarding_fx_rates, agent.onboarding_corporate_actions,
    agent.onboarding_activity, agent.onboarding_instruments, agent.security_identifiers TO agent_reader;

GRANT SELECT, INSERT ON agent.onboarding_reconciliations TO agent_writer;
GRANT USAGE ON SEQUENCE agent.onboarding_reconciliations_reconciliation_id_seq TO agent_writer;
