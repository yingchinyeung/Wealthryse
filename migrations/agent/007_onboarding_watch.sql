-- The go-live coordinator's first-month watch (Agentic AI integration map,
-- "Frontier onboarding").
--
-- Applied after 006_operations.sql by scripts/install_agent_schema.py, on
-- purpose and with approval. Idempotent.
--
-- After go-live a case sits in its first month-end stage until that month is
-- released. The onboarding job watches each such account's first days: that
-- daily data arrives, that the daily assurance checks pass, and that the
-- month-end team has access to it before its first close. It reports what it
-- finds as inbox cards. It reads only these summary columns: dates and
-- statuses, never positions or values.
--
-- Adds:
--   1. the inbox kind for the watch's cards;
--   2. one scoped view of live first-month cases, for the tool server only.

ALTER TABLE agent.inbox DROP CONSTRAINT IF EXISTS inbox_kind_check;
ALTER TABLE agent.inbox ADD CONSTRAINT inbox_kind_check CHECK (kind IN (
    'holdings_explainer', 'month_end_draft', 'data_quality',
    'month_end_report', 'month_end_hold', 'month_end_dispute',
    'onboarding_watch'));

CREATE OR REPLACE FUNCTION agent.scoped_onboarding_live_cases()
RETURNS TABLE (
    case_id uuid, subject_id text, display_name text, source_kind text, history_start_date text,
    went_live_at timestamptz, latest_data_date text, latest_assurance_date date, latest_assurance_status text,
    month_team_has_access boolean, first_month_end_status text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT c.case_id, c.target_subject_id, c.display_name, c.source_kind, c.history_start_date::text,
           c.went_live_at,
           (SELECT max(n.snapshot_date) FROM public.subject_nav_series n
             WHERE n.tenant_id = c.tenant_id AND n.subject_id = c.target_subject_id),
           r.assurance_date, r.status,
           EXISTS (SELECT 1 FROM agent.service_grants g
                    WHERE g.tenant_id = c.tenant_id AND g.job_name = 'month_team' AND g.subject_id = c.target_subject_id),
           (SELECT k.status FROM public.broker_onboarding_checks k
             WHERE k.tenant_id = c.tenant_id AND k.case_id = c.case_id AND k.check_id = 'first_month_end_released')
      FROM public.broker_onboarding_cases c
      LEFT JOIN LATERAL (
          SELECT p.assurance_date, p.status FROM public.portfolio_analyst_assurance_runs p
           WHERE p.tenant_id = c.tenant_id AND p.subject_id = c.target_subject_id AND p.status <> 'running'
           ORDER BY p.assurance_date DESC, p.assurance_run_id DESC LIMIT 1
      ) r ON true
     WHERE c.tenant_id = agent.tenant() AND c.stage = 'first_month_end' AND c.status <> 'cancelled'
$$;

CREATE OR REPLACE VIEW agent.onboarding_live_cases AS SELECT * FROM agent.scoped_onboarding_live_cases();

REVOKE ALL ON agent.onboarding_live_cases FROM PUBLIC;
REVOKE ALL ON FUNCTION agent.scoped_onboarding_live_cases() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION agent.scoped_onboarding_live_cases() TO agent_reader;
GRANT SELECT ON agent.onboarding_live_cases TO agent_reader;
