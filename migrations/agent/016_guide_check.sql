-- The guide check: a weekly comparison of each account's figures with the Metrics guide.
--
-- Applied after 015_export_agent.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent.
--
-- The job reads one job-only tool, get_guide_conformance, which recomputes the
-- account's statistics from the guide's formulas with a separate implementation
-- and compares them with the website's figures and the analyst's, for the
-- accounts granted to it. It writes one inbox kind and nothing else: a card the
-- week something differs, with the analyst's explanation, and otherwise one
-- all-clear a month.
--
-- The guide_keeper job needs no grant: it reads only the deployed code and the
-- guide, and posts its drafts as guide_keeper cards under the 'platform'
-- subject, which the website shows to owners only.

ALTER TABLE agent.service_grants DROP CONSTRAINT IF EXISTS service_grants_job_name_check;
ALTER TABLE agent.service_grants ADD CONSTRAINT service_grants_job_name_check
    CHECK (job_name IN ('holdings_explainer', 'month_end_drafter', 'data_quality', 'month_team', 'onboarding',
                        'operations', 'weekly_brief', 'live_market', 'risk_watch', 'export_packs', 'guide_check'));

ALTER TABLE agent.inbox DROP CONSTRAINT IF EXISTS inbox_kind_check;
ALTER TABLE agent.inbox ADD CONSTRAINT inbox_kind_check CHECK (kind IN (
    'holdings_explainer', 'month_end_draft', 'data_quality',
    'month_end_report', 'month_end_hold', 'month_end_dispute',
    'onboarding_watch', 'weekly_brief', 'live_alert', 'risk_alert', 'model_check', 'export_pack',
    'guide_check', 'guide_keeper'));
