-- WealthRyse agent schema.
--
-- Kept out of migrations/postgres on purpose: jobs apply that folder
-- automatically at start-up, and this schema must only be installed on
-- purpose, with approval, by scripts/install_agent_schema.py.
--
-- Two kinds of object live here:
--   1. Read-only views with the SAME NAMES as the platform tables they cover.
--      The tool server connects with search_path = agent, so the platform's
--      own calculation code (performance periods, external flows, reporting
--      FX) runs unchanged but can only see the accounts named in the
--      transaction setting agent.subjects. With nothing set, every
--      account-scoped view is empty.
--   2. Tables the agents own: conversations, audit, runs, inbox, feedback
--      and evaluation cases.
--
-- Roles: agent_reader (tool server) may read the views and append audit rows.
-- agent_writer (agent runtime and jobs) may write the agent tables and cannot
-- read any portfolio view. Neither role has any access to the public schema.

CREATE SCHEMA IF NOT EXISTS agent;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        CREATE ROLE agent_reader NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_writer') THEN
        CREATE ROLE agent_writer NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;

REVOKE ALL ON SCHEMA agent FROM PUBLIC;

-- Scope helpers -------------------------------------------------------------

CREATE OR REPLACE FUNCTION agent.tenant() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
$$;

-- Accounts the current transaction may read. Empty unless set.
CREATE OR REPLACE FUNCTION agent.scope() RETURNS text[]
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(string_to_array(NULLIF(current_setting('agent.subjects', true), ''), ','), ARRAY[]::text[])
$$;

-- Scoped accounts plus the broker accounts whose FX history they report with.
CREATE OR REPLACE FUNCTION agent.fx_scope() RETURNS text[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT ARRAY(
        SELECT unnest(agent.scope())
        UNION
        SELECT s.source_account_id FROM public.platform_subjects s
         WHERE s.tenant_id = agent.tenant() AND s.subject_id = ANY(agent.scope())
           AND s.source_account_id IS NOT NULL
        UNION
        SELECT d.configuration_json ->> 'source_account_id'
          FROM public.subject_calculation_definitions d
         WHERE d.tenant_id = agent.tenant() AND d.subject_id = ANY(agent.scope())
           AND d.configuration_json ->> 'source_account_id' IS NOT NULL
    )
$$;

-- Accounts a signed-in person may use, mirroring the website's rule: owners
-- and platform admins see every active account; everyone else needs an
-- explicit read-level entitlement. Unknown or inactive people get nothing.
CREATE OR REPLACE FUNCTION agent.allowed_subjects(p_object_id text) RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    WITH person AS (
        SELECT u.entra_object_id FROM public.app_users u
         WHERE u.tenant_id = agent.tenant() AND lower(u.entra_object_id) = lower(p_object_id)
           AND u.status = 'active'
    ), privileged AS (
        SELECT EXISTS (
            SELECT 1 FROM public.app_user_roles r JOIN person p ON lower(r.entra_object_id) = lower(p.entra_object_id)
             WHERE r.tenant_id = agent.tenant() AND r.role_status = 'active'
               AND r.role_name IN ('owner', 'platform_admin')
               AND r.effective_from <= now() AND (r.effective_to IS NULL OR r.effective_to >= now())
        ) AS yes
    )
    SELECT s.subject_id FROM public.platform_subjects s
     WHERE s.tenant_id = agent.tenant() AND s.lifecycle_status = 'active'
       AND EXISTS (SELECT 1 FROM person)
       AND ((SELECT yes FROM privileged) OR EXISTS (
            SELECT 1 FROM public.user_account_access a
             WHERE a.tenant_id = s.tenant_id AND a.subject_id = s.subject_id
               AND lower(a.entra_object_id) = lower(p_object_id)
               AND a.access_level IN ('read', 'report', 'analyze', 'approve', 'admin')))
     ORDER BY s.subject_id
$$;

-- Accounts a scheduled job may cover (rows are added by the installer only).
CREATE TABLE IF NOT EXISTS agent.service_grants (
    tenant_id uuid NOT NULL,
    job_name text NOT NULL CHECK (job_name IN ('holdings_explainer', 'month_end_drafter', 'data_quality')),
    subject_id text NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT now(),
    granted_by text NOT NULL,
    PRIMARY KEY (tenant_id, job_name, subject_id)
);

CREATE OR REPLACE FUNCTION agent.service_subjects(p_job_name text) RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT g.subject_id FROM agent.service_grants g
      JOIN public.platform_subjects s ON s.tenant_id = g.tenant_id AND s.subject_id = g.subject_id
     WHERE g.tenant_id = agent.tenant() AND g.job_name = p_job_name AND s.lifecycle_status = 'active'
     ORDER BY g.subject_id
$$;

-- Scoped read-only views (same names as the platform tables) ---------------
--
-- Each view reads through a SECURITY DEFINER function with a fixed
-- search_path. That keeps the platform's own row-level-security policies
-- (whose helper functions use unqualified names) resolving against public,
-- applies the account scope inside, and means the reader role needs no
-- privilege on any platform table.

CREATE OR REPLACE FUNCTION agent.scoped_platform_subjects() RETURNS SETOF public.platform_subjects
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.platform_subjects WHERE tenant_id = agent.tenant() AND subject_id = ANY(agent.scope())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_nav_series() RETURNS SETOF public.subject_nav_series
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.subject_nav_series WHERE tenant_id = agent.tenant() AND subject_id = ANY(agent.scope())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_position_series() RETURNS SETOF public.subject_position_series
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.subject_position_series WHERE tenant_id = agent.tenant() AND subject_id = ANY(agent.scope())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_synthetic_components() RETURNS SETOF public.synthetic_portfolio_components
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.synthetic_portfolio_components WHERE tenant_id = agent.tenant() AND portfolio_id = ANY(agent.scope())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_cash_ledger() RETURNS SETOF public.derived_portfolio_cash_ledger
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.derived_portfolio_cash_ledger WHERE tenant_id = agent.tenant() AND portfolio_id = ANY(agent.scope())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_calculation_definitions() RETURNS SETOF public.subject_calculation_definitions
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.subject_calculation_definitions WHERE tenant_id = agent.tenant() AND subject_id = ANY(agent.scope())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_accounts() RETURNS SETOF public.accounts
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.accounts WHERE tenant_id = agent.tenant() AND account_id = ANY(agent.fx_scope())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_fx_rates() RETURNS SETOF public.fx_rates
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.fx_rates WHERE account_id = ANY(agent.fx_scope())
$$;

-- Only the two sections the external-flow reader uses.
CREATE OR REPLACE FUNCTION agent.scoped_flex_rows() RETURNS SETOF public.flex_rows
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.flex_rows WHERE tenant_id = agent.tenant() AND account_id = ANY(agent.scope())
       AND section IN ('Transfers', 'CashTransactions')
$$;

-- Reference data, limited to instruments the scoped accounts hold.
CREATE OR REPLACE FUNCTION agent.scoped_instruments() RETURNS SETOF public.instruments
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT i.* FROM public.instruments i WHERE i.conid IN (
        SELECT p.conid FROM public.subject_position_series p
         WHERE p.tenant_id = agent.tenant() AND p.subject_id = ANY(agent.scope()))
$$;

CREATE OR REPLACE FUNCTION agent.scoped_classifications() RETURNS SETOF public.instrument_classifications
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT c.* FROM public.instrument_classifications c WHERE c.tenant_id = agent.tenant() AND c.conid IN (
        SELECT p.conid FROM public.subject_position_series p
         WHERE p.tenant_id = agent.tenant() AND p.subject_id = ANY(agent.scope()))
$$;

CREATE OR REPLACE FUNCTION agent.scoped_benchmark_mapping() RETURNS SETOF public.portfolio_benchmark_mapping
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.portfolio_benchmark_mapping WHERE tenant_id = agent.tenant() AND subject_id = ANY(agent.scope())
$$;

CREATE OR REPLACE FUNCTION agent.scoped_benchmark_master() RETURNS SETOF public.benchmark_master
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.benchmark_master WHERE tenant_id = agent.tenant() AND benchmark_id IN (
        SELECT m.benchmark_id FROM public.portfolio_benchmark_mapping m
         WHERE m.tenant_id = agent.tenant() AND m.subject_id = ANY(agent.scope()))
$$;

CREATE OR REPLACE FUNCTION agent.scoped_benchmark_prices() RETURNS SETOF public.benchmark_prices
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.benchmark_prices WHERE tenant_id = agent.tenant() AND benchmark_id IN (
        SELECT m.benchmark_id FROM public.portfolio_benchmark_mapping m
         WHERE m.tenant_id = agent.tenant() AND m.subject_id = ANY(agent.scope()))
$$;

CREATE OR REPLACE FUNCTION agent.scoped_ingestion_runs() RETURNS SETOF public.ingestion_runs
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM public.ingestion_runs WHERE tenant_id = agent.tenant()
$$;

CREATE OR REPLACE VIEW agent.platform_subjects AS
    SELECT tenant_id, subject_id, subject_type, display_name, source_account_id, account_type,
           base_currency, reporting_currency, history_start_date, inception_date, lifecycle_status,
           created_at, updated_at
      FROM agent.scoped_platform_subjects();
CREATE OR REPLACE VIEW agent.subject_nav_series AS SELECT * FROM agent.scoped_nav_series();
CREATE OR REPLACE VIEW agent.subject_position_series AS SELECT * FROM agent.scoped_position_series();
CREATE OR REPLACE VIEW agent.synthetic_portfolio_components AS SELECT * FROM agent.scoped_synthetic_components();
CREATE OR REPLACE VIEW agent.derived_portfolio_cash_ledger AS SELECT * FROM agent.scoped_cash_ledger();
CREATE OR REPLACE VIEW agent.subject_calculation_definitions AS
    SELECT definition_id, tenant_id, subject_id, subject_type, calculation_kind, definition_version,
           configuration_json, approval_status, effective_from, effective_to, created_at, approved_at
      FROM agent.scoped_calculation_definitions();
CREATE OR REPLACE VIEW agent.accounts AS
    SELECT account_id, alias, base_currency, account_type, tenant_id FROM agent.scoped_accounts();
CREATE OR REPLACE VIEW agent.fx_rates AS
    SELECT account_id, rate_date, from_currency, to_currency, rate_text FROM agent.scoped_fx_rates();
CREATE OR REPLACE VIEW agent.flex_rows AS
    SELECT flex_row_id, account_id, section, report_date, conid, currency, data_json, tenant_id
      FROM agent.scoped_flex_rows();
CREATE OR REPLACE VIEW agent.instruments AS
    SELECT conid, symbol, description, asset_category, currency, listing_exchange,
           underlying_conid, expiry, strike_text, put_call, multiplier_text, isin
      FROM agent.scoped_instruments();
CREATE OR REPLACE VIEW agent.instrument_classifications AS
    SELECT tenant_id, conid, provider, symbol, sector, industry, exchange, status,
           quality_status, license_status, checked_at
      FROM agent.scoped_classifications();
CREATE OR REPLACE VIEW agent.portfolio_benchmark_mapping AS SELECT * FROM agent.scoped_benchmark_mapping();
CREATE OR REPLACE VIEW agent.benchmark_master AS SELECT * FROM agent.scoped_benchmark_master();
CREATE OR REPLACE VIEW agent.benchmark_prices AS
    SELECT tenant_id, benchmark_id, observation_date, level, currency, observation_status, provider, quality_status
      FROM agent.scoped_benchmark_prices();
CREATE OR REPLACE VIEW agent.ingestion_runs AS
    SELECT run_id, run_type, started_at, completed_at, status, tenant_id FROM agent.scoped_ingestion_runs();

-- Agent-owned tables --------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent.conversations (
    conversation_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    user_object_id text NOT NULL,
    subject_id text NOT NULL,
    title text NOT NULL DEFAULT 'New conversation',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    archived_at timestamptz
);
CREATE INDEX IF NOT EXISTS conversations_owner_idx
    ON agent.conversations (tenant_id, user_object_id, subject_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent.messages (
    message_id bigserial PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES agent.conversations(conversation_id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    content text NOT NULL,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    status text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'rejected', 'failed')),
    correlation_id text,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_conversation_idx ON agent.messages (conversation_id, message_id);

-- Metadata only: hashes, never arguments or results.
CREATE TABLE IF NOT EXISTS agent.tool_calls (
    tool_call_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    correlation_id text NOT NULL,
    caller_kind text NOT NULL CHECK (caller_kind IN ('user', 'service')),
    caller_sha256 text NOT NULL,
    tool_name text NOT NULL,
    subject_id text,
    request_sha256 text NOT NULL,
    response_sha256 text,
    status text NOT NULL CHECK (status IN ('success', 'denied', 'failed')),
    error_code text,
    duration_ms integer,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tool_calls_correlation_idx ON agent.tool_calls (correlation_id);

CREATE TABLE IF NOT EXISTS agent.runs (
    run_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    job_name text NOT NULL,
    correlation_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    items_written integer NOT NULL DEFAULT 0,
    error_code text,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS agent.inbox (
    item_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('holdings_explainer', 'month_end_draft', 'data_quality')),
    subject_id text NOT NULL,
    period_key text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'read', 'approved', 'dismissed')),
    run_id bigint REFERENCES agent.runs(run_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    decided_by_sha256 text,
    UNIQUE (tenant_id, kind, subject_id, period_key)
);

CREATE TABLE IF NOT EXISTS agent.feedback (
    feedback_id bigserial PRIMARY KEY,
    message_id bigint NOT NULL REFERENCES agent.messages(message_id) ON DELETE CASCADE,
    user_object_id text NOT NULL,
    rating smallint NOT NULL CHECK (rating IN (-1, 1)),
    comment text CHECK (char_length(comment) <= 1000),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id, user_object_id)
);

CREATE TABLE IF NOT EXISTS agent.eval_cases (
    case_id text PRIMARY KEY,
    subject_id text NOT NULL,
    question text NOT NULL,
    expected_tools text[] NOT NULL DEFAULT ARRAY[]::text[],
    must_refuse boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Retention: delete conversations untouched for p_days (default 90).
CREATE OR REPLACE FUNCTION agent.purge_conversations(p_days integer DEFAULT 90) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE removed integer;
BEGIN
    IF p_days < 1 THEN RAISE EXCEPTION 'retention must be at least one day'; END IF;
    DELETE FROM agent.conversations
     WHERE tenant_id = agent.tenant() AND updated_at < now() - make_interval(days => p_days);
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$$;

-- Grants --------------------------------------------------------------------

REVOKE ALL ON ALL TABLES IN SCHEMA agent FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA agent FROM PUBLIC;

GRANT USAGE ON SCHEMA agent TO agent_reader, agent_writer;
GRANT EXECUTE ON FUNCTION agent.tenant(), agent.scope() TO agent_reader, agent_writer;
GRANT EXECUTE ON FUNCTION agent.fx_scope(), agent.allowed_subjects(text), agent.service_subjects(text),
    agent.scoped_platform_subjects(), agent.scoped_nav_series(), agent.scoped_position_series(),
    agent.scoped_synthetic_components(), agent.scoped_cash_ledger(), agent.scoped_calculation_definitions(),
    agent.scoped_accounts(), agent.scoped_fx_rates(), agent.scoped_flex_rows(), agent.scoped_instruments(),
    agent.scoped_classifications(), agent.scoped_benchmark_mapping(), agent.scoped_benchmark_master(),
    agent.scoped_benchmark_prices(), agent.scoped_ingestion_runs() TO agent_reader;
GRANT EXECUTE ON FUNCTION agent.purge_conversations(integer) TO agent_writer;

GRANT SELECT ON
    agent.platform_subjects, agent.subject_nav_series, agent.subject_position_series,
    agent.synthetic_portfolio_components, agent.derived_portfolio_cash_ledger,
    agent.subject_calculation_definitions, agent.accounts, agent.fx_rates, agent.flex_rows,
    agent.instruments, agent.instrument_classifications, agent.portfolio_benchmark_mapping,
    agent.benchmark_master, agent.benchmark_prices, agent.ingestion_runs
TO agent_reader;
GRANT INSERT ON agent.tool_calls TO agent_reader;
GRANT USAGE ON SEQUENCE agent.tool_calls_tool_call_id_seq TO agent_reader;

GRANT SELECT, INSERT, UPDATE ON agent.conversations, agent.messages, agent.runs, agent.inbox, agent.feedback TO agent_writer;
GRANT SELECT ON agent.eval_cases, agent.tool_calls TO agent_writer;
GRANT USAGE ON SEQUENCE agent.messages_message_id_seq, agent.runs_run_id_seq,
    agent.inbox_item_id_seq, agent.feedback_feedback_id_seq TO agent_writer;
