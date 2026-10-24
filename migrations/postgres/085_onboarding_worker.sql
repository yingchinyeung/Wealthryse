-- Self-service onboarding: the onboarding worker's run records, up to three open cases, and the
-- account terms the website now asks for (the benchmark and, for Kiwoom, whether to read the U.S.
-- account). See docs/SELF_SERVICE_ONBOARDING.md.
--
-- Ops only (public): no bronze, silver or gold object changes. Additive except one index: the
-- one-case-at-a-time index is replaced by a limit of three open cases, which the website checks
-- under an advisory lock (onboarding_v2_web.MAX_OPEN_CASES). Applied alone with
-- bootstrap_postgres.py --only-migration 085_onboarding_worker.sql, outside the 07:20-09:45 UTC
-- daily chain. infra/medallion/undo/085_onboarding_worker_undo.sql reverses it.

SET LOCAL lock_timeout = '5s';

-- What the onboarding worker was asked to do and what it did. The website writes a 'requested' row
-- for Run now or Check now; the worker claims it, runs the case's steps and writes the outcome.
-- A scheduled run writes its own row. Holds counts and messages only: never a key, a figure or an
-- account number beyond the case it names.
CREATE TABLE IF NOT EXISTS onboarding_worker_runs (
    run_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    case_id UUID,
    connection_id UUID,
    trigger TEXT NOT NULL CHECK (trigger IN ('schedule', 'run_now', 'check_connection')),
    status TEXT NOT NULL CHECK (status IN ('requested', 'running', 'done', 'failed', 'skipped')),
    requested_by_identity_sha256 TEXT CHECK (
        requested_by_identity_sha256 IS NULL OR requested_by_identity_sha256 ~ '^[0-9a-f]{64}$'
    ),
    execution_reference TEXT CHECK (execution_reference IS NULL OR length(execution_reference) <= 400),
    message TEXT CHECK (message IS NULL OR length(message) <= 1000),
    steps_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    UNIQUE (tenant_id, run_id),
    FOREIGN KEY (tenant_id, case_id) REFERENCES broker_onboarding_cases(tenant_id, case_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, connection_id) REFERENCES broker_connections(tenant_id, connection_id) ON DELETE CASCADE,
    CHECK (trigger <> 'run_now' OR case_id IS NOT NULL),
    CHECK (trigger <> 'check_connection' OR connection_id IS NOT NULL),
    CHECK (status IN ('requested', 'running') OR finished_at IS NOT NULL)
);

-- One request waiting per case or connection: a second Run now joins the first.
CREATE UNIQUE INDEX IF NOT EXISTS onboarding_worker_one_waiting_case_idx
    ON onboarding_worker_runs(tenant_id, case_id) WHERE status IN ('requested', 'running') AND case_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS onboarding_worker_one_waiting_connection_idx
    ON onboarding_worker_runs(tenant_id, connection_id)
    WHERE status IN ('requested', 'running') AND connection_id IS NOT NULL AND case_id IS NULL;
CREATE INDEX IF NOT EXISTS onboarding_worker_runs_case_idx
    ON onboarding_worker_runs(tenant_id, case_id, requested_at DESC);

COMMENT ON TABLE onboarding_worker_runs IS
    'Each onboarding worker run (scheduled, Run now or Check now): what it did for a case or connection.';

ALTER TABLE onboarding_worker_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding_worker_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS onboarding_worker_runs_tenant_policy ON onboarding_worker_runs;
CREATE POLICY onboarding_worker_runs_tenant_policy ON onboarding_worker_runs
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
REVOKE ALL ON onboarding_worker_runs FROM PUBLIC;

-- Account terms the start form asks for. Both stay NULL on earlier cases, whose approved terms
-- (and their digests) are unchanged: NULL benchmarks means none was picked here, NULL
-- kiwoom_include_us means the collector's own KIWOOM_INCLUDE_US setting.
ALTER TABLE broker_onboarding_cases ADD COLUMN IF NOT EXISTS benchmarks_json JSONB CHECK (
    benchmarks_json IS NULL OR (jsonb_typeof(benchmarks_json) = 'object'
                                AND jsonb_typeof(benchmarks_json -> 'primary') = 'string')
);
ALTER TABLE broker_onboarding_cases ADD COLUMN IF NOT EXISTS kiwoom_include_us BOOLEAN CHECK (
    kiwoom_include_us IS NULL OR broker = 'kiwoom_rest'
);

-- Up to three accounts at a time instead of one. Never the same account or portfolio twice still
-- holds (broker_onboarding_one_case_per_account_idx and _one_case_per_subject_idx stay).
DROP INDEX IF EXISTS broker_onboarding_one_pre_live_case_idx;
