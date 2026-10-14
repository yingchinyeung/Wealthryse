-- The operations agent (Agentic AI integration map, "Operations agent").
--
-- Applied after 005_onboarding_reconcile.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent. Needs migration 013 (the
-- autonomous administrator's incident tables) to be in place first.
--
-- The autonomous administrator stays the only thing that acts. The operations
-- agent investigates the incidents it opens: it reads the open incidents, the
-- recent job runs and the administrator's own runs and actions through these
-- read-only views, reads Azure telemetry with read-only roles, and writes its
-- diagnosis to its own table. The platform posts each diagnosis into the
-- admin chat's system updates conversation and shows it on the System health
-- page (ibkr_etl/ops_agent_posts.py). Nothing here can change an incident, a
-- run or any platform table.
--
-- Adds:
--   1. the operations job;
--   2. scoped views of open incidents, recent ingestion runs, and the
--      administrator's recent runs and actions, for the tool server only;
--   3. the agent's own table of investigations, for the agent writer.

ALTER TABLE agent.service_grants DROP CONSTRAINT IF EXISTS service_grants_job_name_check;
ALTER TABLE agent.service_grants ADD CONSTRAINT service_grants_job_name_check
    CHECK (job_name IN ('holdings_explainer', 'month_end_drafter', 'data_quality', 'month_team', 'onboarding',
                        'operations')) NOT VALID;

-- Scoped views (tool server) ------------------------------------------------------

-- Open and acknowledged incidents, as the administrator recorded them. The
-- evidence is the administrator's own (dates, statuses, counts); its
-- diagnosis text is left out, as the agent forms its own.
CREATE OR REPLACE FUNCTION agent.scoped_open_incidents()
RETURNS TABLE (
    incident_id bigint, incident_key text, check_id text, severity text, status text, description text,
    evidence_json text, first_seen_at text, last_seen_at text, occurrence_count integer
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT i.incident_id, i.incident_key, i.check_id, i.severity, i.status, i.description, i.evidence_json,
           i.first_seen_at, i.last_seen_at, i.occurrence_count
      FROM public.system_incidents i
     WHERE i.tenant_id = agent.tenant() AND i.status IN ('open', 'acknowledged')
$$;

-- The last 14 days of import runs: type, times, status and error, never the
-- raw statement or its path.
CREATE OR REPLACE FUNCTION agent.scoped_recent_ingestion_runs()
RETURNS TABLE (
    run_id bigint, run_type text, started_at text, completed_at text, status text, row_count bigint,
    error_message text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT r.run_id, r.run_type, r.started_at, r.completed_at, r.status, r.row_count, left(r.error_message, 2000)
      FROM public.ingestion_runs r
     WHERE r.tenant_id = agent.tenant() AND left(r.started_at, 10) >= (current_date - 14)::text
$$;

-- The administrator's runs and actions over the last 7 days.
CREATE OR REPLACE FUNCTION agent.scoped_recent_admin_runs()
RETURNS TABLE (
    agent_run_id bigint, started_at text, completed_at text, status text, finding_count integer,
    action_count integer, summary text, error_code text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT r.agent_run_id, r.started_at, r.completed_at, r.status, r.finding_count, r.action_count,
           left(r.summary, 2000), r.error_code
      FROM public.autonomous_agent_runs r
     WHERE r.tenant_id = agent.tenant() AND left(r.started_at, 10) >= (current_date - 7)::text
$$;

CREATE OR REPLACE FUNCTION agent.scoped_recent_admin_actions()
RETURNS TABLE (
    action_id bigint, incident_id bigint, action_type text, target_job text, status text, reason text,
    created_at text, executed_at text, error_code text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT a.action_id, a.incident_id, a.action_type, a.target_job, a.status, left(a.reason, 2000), a.created_at,
           a.executed_at, a.error_code
      FROM public.autonomous_actions a
     WHERE a.tenant_id = agent.tenant() AND left(a.created_at, 10) >= (current_date - 7)::text
$$;

CREATE OR REPLACE VIEW agent.open_incidents AS SELECT * FROM agent.scoped_open_incidents();
CREATE OR REPLACE VIEW agent.recent_ingestion_runs AS SELECT * FROM agent.scoped_recent_ingestion_runs();
CREATE OR REPLACE VIEW agent.recent_admin_runs AS SELECT * FROM agent.scoped_recent_admin_runs();
CREATE OR REPLACE VIEW agent.recent_admin_actions AS SELECT * FROM agent.scoped_recent_admin_actions();

-- Investigations (agent writer) -----------------------------------------------------

-- One investigation per incident and episode: an incident that resolves and
-- comes back has a new first_seen_at, so it is investigated again.
-- ``diagnosis`` holds the summary, likely cause, confidence, the evidence it
-- rests on (each item naming the telemetry it came from), and the steps for
-- the owner; ``sources`` lists the telemetry read, with the digest of each
-- excerpt, never the logs themselves. ``pull_request`` is the draft pull
-- request the agent opened for a code fix, if any.
CREATE TABLE IF NOT EXISTS agent.ops_investigations (
    investigation_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    incident_id bigint NOT NULL,
    incident_key text NOT NULL,
    episode_started_at text NOT NULL,
    status text NOT NULL CHECK (status IN ('diagnosed', 'inconclusive')),
    diagnosis jsonb NOT NULL,
    sources jsonb NOT NULL DEFAULT '[]'::jsonb,
    pull_request jsonb,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    correlation_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, incident_id, episode_started_at)
);

-- Grants ----------------------------------------------------------------------------

REVOKE ALL ON agent.ops_investigations, agent.open_incidents, agent.recent_ingestion_runs, agent.recent_admin_runs,
    agent.recent_admin_actions FROM PUBLIC;
REVOKE ALL ON FUNCTION agent.scoped_open_incidents(), agent.scoped_recent_ingestion_runs(),
    agent.scoped_recent_admin_runs(), agent.scoped_recent_admin_actions() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION agent.scoped_open_incidents(), agent.scoped_recent_ingestion_runs(),
    agent.scoped_recent_admin_runs(), agent.scoped_recent_admin_actions() TO agent_reader;
GRANT SELECT ON agent.open_incidents, agent.recent_ingestion_runs, agent.recent_admin_runs,
    agent.recent_admin_actions TO agent_reader;

GRANT SELECT, INSERT ON agent.ops_investigations TO agent_writer;
GRANT USAGE ON SEQUENCE agent.ops_investigations_investigation_id_seq TO agent_writer;
