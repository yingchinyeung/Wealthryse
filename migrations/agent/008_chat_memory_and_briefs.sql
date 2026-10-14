-- The personal analyst's memory and weekly briefs (Agentic AI integration
-- map, "Personal analyst for each client").
--
-- Applied after 007_onboarding_watch.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent.
--
-- Chat remembers only the current conversation, and conversations are deleted
-- after the retention period. Memory adds a few preferences each person can
-- see, change and delete on the website: how long answers should be, how
-- technical, which benchmark they compare with, and what they care about.
-- They are set by the person, either on the "What I remember" view or by
-- asking the analyst to remember something. Retention does not delete them;
-- the person does.
--
-- The weekly brief is a short note per account, written by a scheduled job
-- with the same tools and figure checks as chat, and shown in the website
-- inbox only to the people who can see that account. Nothing is emailed.
--
-- Adds:
--   1. agent.memories, for the agent app (agent_writer);
--   2. the weekly_brief job and inbox kind.

CREATE TABLE IF NOT EXISTS agent.memories (
    tenant_id uuid NOT NULL,
    user_object_id text NOT NULL CHECK (user_object_id ~ '^[0-9a-f-]{36}$'),
    key text NOT NULL CHECK (key IN ('answer_length', 'tone', 'benchmark', 'focus')),
    value text NOT NULL CHECK (char_length(value) BETWEEN 1 AND 200),
    source text NOT NULL CHECK (source IN ('settings', 'chat')),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_object_id, key)
);

ALTER TABLE agent.service_grants DROP CONSTRAINT IF EXISTS service_grants_job_name_check;
ALTER TABLE agent.service_grants ADD CONSTRAINT service_grants_job_name_check
    CHECK (job_name IN ('holdings_explainer', 'month_end_drafter', 'data_quality', 'month_team', 'onboarding',
                        'operations', 'weekly_brief'));

ALTER TABLE agent.inbox DROP CONSTRAINT IF EXISTS inbox_kind_check;
ALTER TABLE agent.inbox ADD CONSTRAINT inbox_kind_check CHECK (kind IN (
    'holdings_explainer', 'month_end_draft', 'data_quality',
    'month_end_report', 'month_end_hold', 'month_end_dispute',
    'onboarding_watch', 'weekly_brief'));

REVOKE ALL ON agent.memories FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON agent.memories TO agent_writer;
