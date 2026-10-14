-- Month-end agent team (docs/AGENTS.md, "Month-end team").
--
-- Applied after 001_agent_schema.sql by scripts/install_agent_schema.py, on
-- purpose and with approval, like 001. Idempotent.
--
-- Adds:
--   1. the month_team job and three inbox kinds;
--   2. two scoped read-only views of the month-end package and its frozen
--      snapshot, for the tool server only;
--   3. the team's own run, step and objection tables, for the agent writer.
-- Nothing here can change a report, a release or any platform table.

ALTER TABLE agent.service_grants DROP CONSTRAINT IF EXISTS service_grants_job_name_check;
ALTER TABLE agent.service_grants ADD CONSTRAINT service_grants_job_name_check
    CHECK (job_name IN ('holdings_explainer', 'month_end_drafter', 'data_quality', 'month_team'));

-- month_end_report: one per report, links to the account's Reports tab.
-- month_end_hold: an account the data agent blocked; approved = proceed, dismissed = hold.
-- month_end_dispute: commentary the writer and reviewer could not agree on;
--   approved = keep the writer's text, dismissed = side with the reviewer.
ALTER TABLE agent.inbox DROP CONSTRAINT IF EXISTS inbox_kind_check;
ALTER TABLE agent.inbox ADD CONSTRAINT inbox_kind_check CHECK (kind IN (
    'holdings_explainer', 'month_end_draft', 'data_quality',
    'month_end_report', 'month_end_hold', 'month_end_dispute'));

-- Scoped package and snapshot views (tool server) ---------------------------

CREATE OR REPLACE FUNCTION agent.scoped_month_end_packages()
RETURNS TABLE (
    subject_id text, period_end text, snapshot_id uuid, snapshot_sha256 text, quality_status text,
    publication_allowed boolean, report_type text, generation_run_id bigint, status text,
    output_reference text, output_sha256 text, completed_at text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT s.subject_id, s.period_end, s.snapshot_id, s.data_sha256, s.quality_status,
           s.publication_allowed, r.report_type, r.generation_run_id, r.status,
           r.output_reference, r.output_sha256, r.completed_at
      FROM public.reporting_snapshots s
      JOIN public.report_generation_runs r ON r.tenant_id = s.tenant_id AND r.snapshot_id = s.snapshot_id
     WHERE s.tenant_id = agent.tenant() AND s.subject_id = ANY(agent.scope())
       AND r.report_type IN ('factsheet', 'presentation')
       AND r.status IN ('awaiting_approval', 'released_internal', 'published')
$$;

CREATE OR REPLACE FUNCTION agent.scoped_month_end_snapshots()
RETURNS TABLE (subject_id text, period_end text, snapshot_id uuid, snapshot_sha256 text, payload_json text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT s.subject_id, s.period_end, s.snapshot_id, s.data_sha256, s.snapshot_payload_json
      FROM public.reporting_snapshots s
     WHERE s.tenant_id = agent.tenant() AND s.subject_id = ANY(agent.scope())
       AND s.status IN ('frozen', 'approved')
$$;

CREATE OR REPLACE VIEW agent.month_end_packages AS SELECT * FROM agent.scoped_month_end_packages();
CREATE OR REPLACE VIEW agent.month_end_snapshots AS SELECT * FROM agent.scoped_month_end_snapshots();

-- Team tables (agent writer) -------------------------------------------------

-- One row per account, month and mode. ``mode`` is month (the month being
-- reported now) or replay (an already released month, shown on the Month-end
-- page only). ``live`` says whether the month-end package job puts the team's
-- agreed commentary into this month's decks (never the factsheets); until it
-- does, the team runs in shadow beside today's process. ``stage`` is where the coordinator is:
--   data      -> the data agent is checking, or waiting for the next sync
--   held      -> blocked; waiting for Ying to choose hold or proceed
--   words     -> analyst figures and writer commentary, with reviewer rounds
--   package   -> waiting for the month-end package job's files
--   checking  -> reviewer and visual QA are checking the package files
--   ready     -> checked; one report-ready inbox item written (month runs only)
-- A failed step keeps the stage and counts an attempt, so the next wake retries
-- it; after three failures in a row the coordinator stops and the Month-end
-- page shows the run as stopped until it is retried.
-- ``commentary_status`` is the writer and reviewer's outcome: agreed after up
-- to two rounds, disputed (Ying settles it), or Ying's settlement.
CREATE TABLE IF NOT EXISTS agent.month_runs (
    month_run_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    subject_id text NOT NULL,
    period_end date NOT NULL,
    mode text NOT NULL DEFAULT 'month' CHECK (mode IN ('month', 'replay')),
    live boolean NOT NULL DEFAULT false,
    stage text NOT NULL DEFAULT 'data'
        CHECK (stage IN ('data', 'held', 'words', 'package', 'checking', 'ready')),
    data_verdict text CHECK (data_verdict IN ('clean', 'fixable', 'blocked')),
    figures jsonb NOT NULL DEFAULT '{}'::jsonb,
    commentary jsonb,
    commentary_rounds smallint NOT NULL DEFAULT 0,
    commentary_status text CHECK (commentary_status IN ('agreed', 'disputed', 'writer_upheld', 'reviewer_upheld')),
    package jsonb,
    verdicts jsonb NOT NULL DEFAULT '{}'::jsonb,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempts smallint NOT NULL DEFAULT 0,
    last_error text CHECK (char_length(last_error) <= 200),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, subject_id, period_end, mode)
);
CREATE INDEX IF NOT EXISTS month_runs_period_idx ON agent.month_runs (tenant_id, period_end, stage);

-- Every agent step, in order. Evidence holds figures, citations and checks;
-- never raw files.
CREATE TABLE IF NOT EXISTS agent.month_steps (
    step_id bigserial PRIMARY KEY,
    month_run_id bigint NOT NULL REFERENCES agent.month_runs(month_run_id) ON DELETE CASCADE,
    agent_name text NOT NULL
        CHECK (agent_name IN ('coordinator', 'data', 'analyst', 'writer', 'reviewer', 'visual_qa')),
    round smallint NOT NULL DEFAULT 1,
    outcome text NOT NULL CHECK (outcome IN ('pass', 'objection', 'blocked', 'waiting', 'skipped', 'failed')),
    summary text NOT NULL CHECK (char_length(summary) <= 600),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    correlation_id text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS month_steps_run_idx ON agent.month_steps (month_run_id, step_id);

CREATE TABLE IF NOT EXISTS agent.month_objections (
    objection_id bigserial PRIMARY KEY,
    month_run_id bigint NOT NULL REFERENCES agent.month_runs(month_run_id) ON DELETE CASCADE,
    step_id bigint NOT NULL REFERENCES agent.month_steps(step_id) ON DELETE CASCADE,
    raised_by text NOT NULL CHECK (raised_by IN ('data', 'reviewer', 'visual_qa')),
    target text NOT NULL CHECK (target IN ('data', 'commentary', 'factsheet', 'presentation')),
    page smallint,
    region jsonb,
    message text NOT NULL CHECK (char_length(message) <= 600),
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'overruled')),
    resolution text CHECK (char_length(resolution) <= 600),
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);
CREATE INDEX IF NOT EXISTS month_objections_run_idx ON agent.month_objections (month_run_id, status);

-- Grants ----------------------------------------------------------------------

REVOKE ALL ON agent.month_runs, agent.month_steps, agent.month_objections,
    agent.month_end_packages, agent.month_end_snapshots FROM PUBLIC;
REVOKE ALL ON FUNCTION agent.scoped_month_end_packages(), agent.scoped_month_end_snapshots() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION agent.scoped_month_end_packages(), agent.scoped_month_end_snapshots() TO agent_reader;
GRANT SELECT ON agent.month_end_packages, agent.month_end_snapshots TO agent_reader;

GRANT SELECT, INSERT, UPDATE ON agent.month_runs, agent.month_steps, agent.month_objections TO agent_writer;
GRANT USAGE ON SEQUENCE agent.month_runs_month_run_id_seq, agent.month_steps_step_id_seq,
    agent.month_objections_objection_id_seq TO agent_writer;
