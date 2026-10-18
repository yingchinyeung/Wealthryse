-- The supervisor's task record (supervisor design, release 1: watch-only).
--
-- Applied after 017_certified_figures.sql by scripts/install_agent_schema.py, on purpose and
-- with approval. Idempotent and additive: one new table, granted to agent_writer only. No
-- existing table, view, constraint or grant changes.
--
-- agent.tasks holds one tree per request: the supervisor at the root, then the team lead, then
-- each specialist and checker, each row pointing at its parent. In watch-only mode
-- (AGENT_SUPERVISOR=watch) the supervisor records, next to every website chat answer, which team
-- and specialists it would have used, the data it would have pinned, and what today's analyst
-- actually used; planned rows have status 'shadow'. Nothing reads it to answer anyone.
--
-- It keeps ids, names, dates, counts and usage, never the question, the answer or any figure:
-- correlation_id joins a chat task to its messages, which keep their own retention. Chat tasks
-- go with their conversation (ON DELETE CASCADE), so agent.purge_conversations removes them too.

CREATE TABLE IF NOT EXISTS agent.tasks (
    task_id uuid PRIMARY KEY,
    root_id uuid NOT NULL,
    parent_id uuid REFERENCES agent.tasks(task_id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL,
    mode text NOT NULL CHECK (mode IN ('watch', 'live')),
    source text NOT NULL CHECK (source IN ('chat', 'explain', 'guide', 'case', 'export', 'design',
                                           'job', 'event', 'inbox', 'admin')),
    level text NOT NULL CHECK (level IN ('supervisor', 'lead', 'specialist', 'checker')),
    team text CHECK (team IN ('client_analysis', 'reporting', 'onboarding', 'operations')),
    role text NOT NULL CHECK (role ~ '^[a-z_]{1,40}$'),
    autonomy text NOT NULL CHECK (autonomy IN ('answer', 'draft', 'propose')),
    status text NOT NULL CHECK (status IN ('shadow', 'done', 'needs_input', 'blocked', 'rejected', 'failed')),
    subject_id text CHECK (char_length(subject_id) BETWEEN 1 AND 64),
    correlation_id text CHECK (char_length(correlation_id) BETWEEN 1 AND 64),
    conversation_id uuid REFERENCES agent.conversations(conversation_id) ON DELETE CASCADE,
    -- The data the task is pinned to: the as-of date, and the certified builds its figures came from.
    pinned_as_of date,
    build_ids text[] NOT NULL DEFAULT '{}' CHECK (cardinality(build_ids) <= 20),
    tools_allowed text[] NOT NULL DEFAULT '{}' CHECK (cardinality(tools_allowed) <= 60),
    tools_used text[] NOT NULL DEFAULT '{}' CHECK (cardinality(tools_used) <= 60),
    usage jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(usage) = 'object' AND pg_column_size(usage) <= 2000),
    detail jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object' AND pg_column_size(detail) <= 8000),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((parent_id IS NULL) = (level = 'supervisor')),
    CHECK (parent_id IS NOT NULL OR root_id = task_id)
);
CREATE INDEX IF NOT EXISTS tasks_recent ON agent.tasks (tenant_id, created_at DESC) WHERE parent_id IS NULL;
CREATE INDEX IF NOT EXISTS tasks_tree ON agent.tasks (root_id);
CREATE INDEX IF NOT EXISTS tasks_parent ON agent.tasks (parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS tasks_conversation ON agent.tasks (conversation_id) WHERE conversation_id IS NOT NULL;

REVOKE ALL ON agent.tasks FROM PUBLIC;
GRANT SELECT, INSERT ON agent.tasks TO agent_writer;
