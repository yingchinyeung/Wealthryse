-- The risk watch: daily risk alerts and a monthly check of the risk models.
--
-- Applied after 013_replay_rounds.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent.
--
-- The job reads only the tools every analyst already has (get_risk_outlook,
-- get_market_regimes and the rest), for the accounts granted to it. It writes
-- two inbox kinds and nothing else: a risk alert, at most once per rule and
-- account, with the analyst's explanation; and one model check per account a
-- month, saying whether the VaR backtest and the outlook's ranges still match
-- what happened.
--
-- This file now sets the full lists of jobs and inbox kinds and validates
-- them; 010_live_market.sql adds its shorter lists NOT VALID.

ALTER TABLE agent.service_grants DROP CONSTRAINT IF EXISTS service_grants_job_name_check;
ALTER TABLE agent.service_grants ADD CONSTRAINT service_grants_job_name_check
    CHECK (job_name IN ('holdings_explainer', 'month_end_drafter', 'data_quality', 'month_team', 'onboarding',
                        'operations', 'weekly_brief', 'live_market', 'risk_watch'));

ALTER TABLE agent.inbox DROP CONSTRAINT IF EXISTS inbox_kind_check;
ALTER TABLE agent.inbox ADD CONSTRAINT inbox_kind_check CHECK (kind IN (
    'holdings_explainer', 'month_end_draft', 'data_quality',
    'month_end_report', 'month_end_hold', 'month_end_dispute',
    'onboarding_watch', 'weekly_brief', 'live_alert', 'risk_alert', 'model_check'));
