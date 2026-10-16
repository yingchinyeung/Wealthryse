-- The export agent: plain-language exports, a checker before download, and
-- scheduled export packs.
--
-- Applied after 014_risk_watch.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent.
--
-- The website builds every export file itself, from the same reads as the
-- Exports tab's CSV buttons, and runs fixed checks on it. The agent app only
-- turns the person's request into a plan and writes a cover note from the
-- check results, so it still never reads portfolio data. It stores:
--
--   1. agent.export_checks: one row per export the checker saw, with the
--      plan, a fingerprint of the files, the checks and the cover note. The
--      website rebuilds the files at download and refuses them unless they
--      match the fingerprint, so what is downloaded is what was checked. A
--      failed export can't be released; one that needs review is released
--      only by the person, and the row records it. Kept as the audit record.
--   2. agent.export_packs: exports a person saved to run every month,
--      quarter or year. The export_packs job puts one inbox card per pack and
--      period when the period's data is in (kind export_pack), shown only to
--      the person who saved it. Building and releasing it is the same flow as
--      any other export. Nothing is emailed or sent.
--
-- This file sets the full lists of jobs and inbox kinds.

CREATE TABLE IF NOT EXISTS agent.export_packs (
    pack_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    user_object_id text NOT NULL CHECK (user_object_id ~ '^[0-9a-f-]{36}$'),
    subject_id text NOT NULL CHECK (char_length(subject_id) BETWEEN 1 AND 64),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    schedule text NOT NULL CHECK (schedule IN ('monthly', 'quarterly', 'yearly')),
    plan jsonb NOT NULL CHECK (jsonb_typeof(plan) = 'object' AND pg_column_size(plan) <= 20000),
    created_at timestamptz NOT NULL DEFAULT now(),
    archived_at timestamptz
);
CREATE INDEX IF NOT EXISTS export_packs_owner ON agent.export_packs (tenant_id, user_object_id, subject_id)
    WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS agent.export_checks (
    check_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    user_object_id text NOT NULL CHECK (user_object_id ~ '^[0-9a-f-]{36}$'),
    subject_id text NOT NULL CHECK (char_length(subject_id) BETWEEN 1 AND 64),
    plan jsonb NOT NULL CHECK (jsonb_typeof(plan) = 'object' AND pg_column_size(plan) <= 20000),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('pass', 'review', 'fail')),
    note text NOT NULL DEFAULT '' CHECK (char_length(note) <= 6000),
    note_checked boolean NOT NULL DEFAULT false,
    concerns jsonb NOT NULL DEFAULT '[]'::jsonb,
    checks jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (pg_column_size(checks) <= 200000),
    pack_item_id bigint REFERENCES agent.inbox(item_id) ON DELETE SET NULL,
    correlation_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    released_at timestamptz,
    released_after_review boolean NOT NULL DEFAULT false,
    CHECK (status <> 'fail' OR released_at IS NULL)
);
CREATE INDEX IF NOT EXISTS export_checks_owner ON agent.export_checks (tenant_id, user_object_id, created_at DESC);

REVOKE ALL ON agent.export_packs, agent.export_checks FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON agent.export_packs, agent.export_checks TO agent_writer;

ALTER TABLE agent.service_grants DROP CONSTRAINT IF EXISTS service_grants_job_name_check;
ALTER TABLE agent.service_grants ADD CONSTRAINT service_grants_job_name_check
    CHECK (job_name IN ('holdings_explainer', 'month_end_drafter', 'data_quality', 'month_team', 'onboarding',
                        'operations', 'weekly_brief', 'live_market', 'risk_watch', 'export_packs'));

ALTER TABLE agent.inbox DROP CONSTRAINT IF EXISTS inbox_kind_check;
ALTER TABLE agent.inbox ADD CONSTRAINT inbox_kind_check CHECK (kind IN (
    'holdings_explainer', 'month_end_draft', 'data_quality',
    'month_end_report', 'month_end_hold', 'month_end_dispute',
    'onboarding_watch', 'weekly_brief', 'live_alert', 'risk_alert', 'model_check', 'export_pack'));
