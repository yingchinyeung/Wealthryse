-- Lineage records: what each job run read and wrote, shaped after OpenLineage run events.
-- Written only by src/ibkr_etl/lineage.py, and only when LINEAGE_RECORDING=true; nothing
-- reads them yet. Both tables are append-only control-plane (ops) tables in public.
--
-- lineage_events: one START row when a run begins, then one COMPLETE, FAIL or ABORT row
-- when it ends. The lineage_runs view joins them into one row per run.
-- lineage_io: one row per dataset a run read (input) or wrote (output), written with the
-- run's last event. Tables are namespace 'postgres' and name '<schema>.<table>'; files are
-- namespace 'blob' and name '<container>/<blob>' with their sha256.
--
-- Three gold tables gain lineage_run_id, the run that last inserted or updated the row. A
-- trigger copies it from the writing session's lineage.run_id setting, which lineage.py
-- sets on a job's own connection; a session without that setting writes NULL. The column
-- is nullable, keys are unchanged, and no row is rewritten here. The compatibility views in
-- public are re-created so they show the new column (tests/test_medallion_layers_db.py).
-- reporting_snapshots (frozen, with its own source manifest) and the crisis replay tables
-- (read with SELECT * into the MS and T crisis evidence) are left unchanged.

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS lineage_events (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    run_id UUID NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('START', 'COMPLETE', 'FAIL', 'ABORT')),
    event_time TIMESTAMPTZ NOT NULL,
    job_namespace TEXT NOT NULL CHECK (length(job_namespace) BETWEEN 1 AND 200),
    job_name TEXT NOT NULL CHECK (length(job_name) BETWEEN 1 AND 200),
    code_version TEXT CHECK (code_version IS NULL OR length(code_version) <= 200),
    execution TEXT CHECK (execution IS NULL OR length(execution) <= 200),
    producer TEXT NOT NULL CHECK (length(producer) BETWEEN 1 AND 200),
    facets JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(facets) = 'object'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id, event_type)
);

-- At most one ending per run.
CREATE UNIQUE INDEX IF NOT EXISTS lineage_events_one_end
    ON lineage_events(tenant_id, run_id) WHERE event_type <> 'START';
CREATE INDEX IF NOT EXISTS lineage_events_job_time
    ON lineage_events(tenant_id, job_name, event_time DESC);

CREATE TABLE IF NOT EXISTS lineage_io (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    run_id UUID NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('input', 'output')),
    namespace TEXT NOT NULL CHECK (namespace IN ('postgres', 'blob')),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 1100),
    row_count BIGINT CHECK (row_count IS NULL OR row_count >= 0),
    sha256 TEXT CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    facets JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(facets) = 'object'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id, direction, namespace, name)
);

-- trace() finds the last run that wrote a table, and the run that read a raw file.
CREATE INDEX IF NOT EXISTS lineage_io_dataset
    ON lineage_io(tenant_id, namespace, name, direction);
CREATE INDEX IF NOT EXISTS lineage_io_sha256
    ON lineage_io(sha256) WHERE sha256 IS NOT NULL;

ALTER TABLE lineage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE lineage_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON lineage_events;
CREATE POLICY tenant_isolation ON lineage_events
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE lineage_io ENABLE ROW LEVEL SECURITY;
ALTER TABLE lineage_io FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON lineage_io;
CREATE POLICY tenant_isolation ON lineage_io
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

CREATE OR REPLACE FUNCTION reject_lineage_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Lineage records are append-only';
END;
$$;
REVOKE ALL ON FUNCTION reject_lineage_change() FROM PUBLIC;
DROP TRIGGER IF EXISTS lineage_events_append_only ON lineage_events;
CREATE TRIGGER lineage_events_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON lineage_events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_lineage_change();
DROP TRIGGER IF EXISTS lineage_io_append_only ON lineage_io;
CREATE TRIGGER lineage_io_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON lineage_io
    FOR EACH STATEMENT EXECUTE FUNCTION reject_lineage_change();

CREATE OR REPLACE VIEW lineage_runs WITH (security_invoker = true) AS
SELECT s.tenant_id,
       s.run_id,
       s.job_namespace,
       s.job_name,
       s.event_time AS started_at,
       e.event_time AS ended_at,
       COALESCE(e.event_type, 'STARTED') AS status,
       COALESCE(e.code_version, s.code_version) AS code_version,
       s.execution,
       s.producer,
       s.facets || COALESCE(e.facets, '{}'::jsonb) AS facets
FROM lineage_events s
LEFT JOIN lineage_events e
       ON e.tenant_id = s.tenant_id AND e.run_id = s.run_id AND e.event_type <> 'START'
WHERE s.event_type = 'START';
COMMENT ON VIEW lineage_runs IS
    'One row per recorded job run. status STARTED means the run has not recorded an end.';

-- Copies the writing session's lineage.run_id onto the row; NULL when it is not a run id.
-- Never raises: a missing or malformed setting gives NULL.
CREATE OR REPLACE FUNCTION lineage_stamp_run() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    setting text := current_setting('lineage.run_id', true);
BEGIN
    IF setting ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        NEW.lineage_run_id := setting::uuid;
    ELSE
        NEW.lineage_run_id := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DO $$
DECLARE
    stamped CONSTANT text[] := ARRAY[
        'crisis_scenario_results',
        'performance_comparison_daily',
        'risk_analytics_snapshots'
    ];
    rel text;
    place text;
    targets text;
    deadline CONSTANT timestamptz := clock_timestamp() + interval '5 seconds';
BEGIN
    -- As in 068: take every lock at once without joining the lock queue, so website
    -- queries never wait behind this. After 5 seconds of trying it gives up with SQLSTATE
    -- 55P03 having changed nothing. Locking the public view also locks the table behind it.
    SELECT string_agg(format('public.%I', r), ', ' ORDER BY r) INTO targets
      FROM unnest(stamped) AS r
     WHERE to_regclass(format('public.%I', r)) IS NOT NULL;
    WHILE targets IS NOT NULL LOOP
        BEGIN
            EXECUTE format('LOCK TABLE %s IN ACCESS EXCLUSIVE MODE NOWAIT', targets);
            EXIT;
        EXCEPTION WHEN lock_not_available THEN
            IF clock_timestamp() >= deadline THEN
                RAISE EXCEPTION 'the lineage migration could not lock its tables within 5 seconds, so nothing was changed'
                    USING ERRCODE = 'lock_not_available',
                          HINT = 'Run the same migration again a minute later.';
            END IF;
            PERFORM pg_sleep(0.05);
        END;
    END LOOP;
    FOREACH rel IN ARRAY stamped LOOP
        place := CASE WHEN to_regclass(format('gold.%I', rel)) IS NOT NULL THEN 'gold' ELSE 'public' END;
        IF to_regclass(format('%I.%I', place, rel)) IS NULL THEN
            RAISE NOTICE '%.% does not exist, so it gets no lineage column', place, rel;
            CONTINUE;
        END IF;
        EXECUTE format('ALTER TABLE %I.%I ADD COLUMN IF NOT EXISTS lineage_run_id UUID', place, rel);
        EXECUTE format('COMMENT ON COLUMN %I.%I.lineage_run_id IS %L', place, rel,
                       'The recorded job run that last inserted or updated this row (lineage_runs); '
                       'NULL when that run was not recorded.');
        EXECUTE format('DROP TRIGGER IF EXISTS lineage_stamp_run ON %I.%I', place, rel);
        EXECUTE format('CREATE TRIGGER lineage_stamp_run BEFORE INSERT OR UPDATE ON %I.%I '
                       'FOR EACH ROW EXECUTE FUNCTION public.lineage_stamp_run()', place, rel);
        IF place = 'gold' THEN
            -- Same view, so its owner, grants and comment stay; it gains the new last column.
            EXECUTE format('CREATE OR REPLACE VIEW public.%I WITH (security_invoker = true) AS '
                           'SELECT * FROM gold.%I', rel, rel);
        END IF;
    END LOOP;
END;
$$;
