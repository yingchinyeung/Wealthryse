-- Onboarding agents, part 1: the history reviewer (Agentic AI integration map,
-- "Frontier onboarding").
--
-- Applied after 002_month_team.sql by scripts/install_agent_schema.py, on
-- purpose and with approval, like 001 and 002. Idempotent. Needs migrations
-- 064 and 065 (the onboarding case tables) to be in place first.
--
-- Adds:
--   1. the onboarding job;
--   2. one scoped read-only view of composite cases waiting for their stage 3
--      review, with the reports their reports_rendered check lists, for the
--      tool server only;
--   3. the reviewer's own table, for the agent writer.
-- Nothing here can change a case, a check, a report or any platform table:
-- the platform's month-end report job records the agent_review_complete check
-- from these rows (ibkr_etl/onboarding_agent_review.py).

ALTER TABLE agent.service_grants DROP CONSTRAINT IF EXISTS service_grants_job_name_check;
ALTER TABLE agent.service_grants ADD CONSTRAINT service_grants_job_name_check
    CHECK (job_name IN ('holdings_explainer', 'month_end_drafter', 'data_quality', 'month_team', 'onboarding')) NOT VALID;

-- Scoped case view (tool server) ----------------------------------------------

-- A case is not an account the agent may see yet (its subject goes live only
-- at GO LIVE), so this view is not limited by agent.scope(). It shows only
-- composite cases in stage 3 whose reports have been rendered, and only what
-- the reviewer needs: the case's own name, description and design, and the
-- reports_rendered evidence (file names, months, hashes and storage paths).
CREATE OR REPLACE FUNCTION agent.scoped_onboarding_report_cases()
RETURNS TABLE (
    case_id uuid, subject_id text, display_name text, description text, report_style text,
    reporting_currency text, history_start_date text, reports_evidence_json text, reports_checked_at text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT c.case_id, c.target_subject_id, c.display_name, c.description, c.report_style,
           c.reporting_currency, c.history_start_date::text, k.evidence_json::text, k.checked_at::text
      FROM public.broker_onboarding_cases c
      JOIN public.broker_onboarding_checks k
        ON k.tenant_id = c.tenant_id AND k.case_id = c.case_id AND k.check_id = 'reports_rendered'
     WHERE c.tenant_id = agent.tenant() AND c.source_kind = 'composite_portfolio'
       AND c.report_style IS NOT NULL AND c.stage = 'build_history' AND c.status <> 'cancelled'
       AND k.status = 'pass'
$$;

CREATE OR REPLACE VIEW agent.onboarding_report_cases AS SELECT * FROM agent.scoped_onboarding_report_cases();

-- Reviewer table (agent writer) -------------------------------------------------

-- One review per case and set of rendered reports. ``reports_sha256`` is the
-- digest of the reports_rendered evidence the review read
-- (ibkr_etl.onboarding_agent_review.reports_digest), so a re-render needs a new
-- review, and the platform records only a review of the reports the owner
-- will see. ``findings`` are the problems found, each with its month, file and
-- page; ``evidence`` says what was checked. Never the files themselves.
CREATE TABLE IF NOT EXISTS agent.onboarding_reviews (
    review_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    case_id uuid NOT NULL,
    subject_id text NOT NULL,
    reports_sha256 text NOT NULL CHECK (reports_sha256 ~ '^[0-9a-f]{64}$'),
    outcome text NOT NULL CHECK (outcome IN ('pass', 'warn', 'fail')),
    summary text NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 1000),
    findings jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    correlation_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, case_id, reports_sha256)
);

-- Grants ----------------------------------------------------------------------

REVOKE ALL ON agent.onboarding_reviews, agent.onboarding_report_cases FROM PUBLIC;
REVOKE ALL ON FUNCTION agent.scoped_onboarding_report_cases() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION agent.scoped_onboarding_report_cases() TO agent_reader;
GRANT SELECT ON agent.onboarding_report_cases TO agent_reader;

GRANT SELECT, INSERT ON agent.onboarding_reviews TO agent_writer;
GRANT USAGE ON SEQUENCE agent.onboarding_reviews_review_id_seq TO agent_writer;
