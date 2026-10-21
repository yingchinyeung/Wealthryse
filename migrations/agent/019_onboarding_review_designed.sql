-- The history reviewer also reads the reports of cases in an owner-made design
-- (a broker account such as Kiwoom, a filtered portfolio, or a composite with no
-- built-in design), not only composites in the Alpha World MS or T design.
--
-- Applied after 018_supervisor_tasks.sql by scripts/install_agent_schema.py, on
-- purpose and with approval. Idempotent. It replaces one function with the same
-- row type; no table, view, constraint or grant changes. Those cases' reports are
-- rendered by ibkr_etl/designed_reports.py and recorded in the same
-- reports_rendered check, and their review is recorded the same way
-- (ibkr_etl/onboarding_agent_review.py), so approval 2 can unlock for them.

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
     WHERE c.tenant_id = agent.tenant()
       AND (c.report_style IS NULL OR c.source_kind = 'composite_portfolio')
       AND c.stage = 'build_history' AND c.status <> 'cancelled'
       AND k.status = 'pass'
$$;

REVOKE ALL ON FUNCTION agent.scoped_onboarding_report_cases() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION agent.scoped_onboarding_report_cases() TO agent_reader;
