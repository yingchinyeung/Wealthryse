-- Undo of migrations/postgres/085_onboarding_worker.sql. Stop the onboarding worker job first
-- (ibkrv2stg-onboard-worker), and make sure no more than one case is before go-live, or the
-- one-case-at-a-time index cannot be built again. Run as the migration login in one transaction
-- (psql -1 -f): it also removes 085's row from schema_migrations, so a later bootstrap applies 085
-- again. The worker's run records are dropped; the cases, checks and approvals stay as they are.
SET LOCAL lock_timeout = '5s';
DROP TABLE IF EXISTS public.onboarding_worker_runs;
ALTER TABLE public.broker_onboarding_cases DROP COLUMN IF EXISTS benchmarks_json;
ALTER TABLE public.broker_onboarding_cases DROP COLUMN IF EXISTS kiwoom_include_us;
CREATE UNIQUE INDEX IF NOT EXISTS broker_onboarding_one_pre_live_case_idx
    ON public.broker_onboarding_cases(tenant_id)
    WHERE stage IN ('request', 'load_and_reconcile', 'build_history') AND status <> 'cancelled';
DELETE FROM public.schema_migrations WHERE version = '085_onboarding_worker.sql';
