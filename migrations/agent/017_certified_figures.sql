-- Certified figures for the agents (medallion upgrade U5, build plan part 7).
--
-- Applied after 016_guide_check.sql by scripts/install_agent_schema.py, on purpose and with
-- approval. Idempotent and additive: it creates three functions and two views, grants them to
-- agent_reader only, and changes no existing grant. On a database without the certified
-- figures tables (migration 073 not applied yet) it does nothing; a later run installs it.
--
-- The tools that read it (get_certified_figures, trace_figure) stay hidden until
-- AGENT_CERTIFIED_FIGURES=on, and serve only figures whose website page is on.
--
--   agent.certified_figures       the published certified figures of the accounts in scope,
--                                 as every website reader sees them (public.v_certified_figures)
--   agent.certified_builds        the tenant's latest builds: status and times only, so a
--                                 figure can say it is behind and why; no checks or reasons,
--                                 which can name other accounts
--   agent.certified_figure_trace  where one figure in scope came from: the build, then each
--                                 job run back to the raw files, with times, code versions,
--                                 row counts and sha256; table names, never storage paths

DO $certified$
BEGIN
    IF to_regclass('public.v_certified_figures') IS NULL OR to_regclass('public.certified_builds') IS NULL THEN
        RAISE NOTICE 'public.v_certified_figures is not installed yet; agent.certified_figures is skipped';
        RETURN;
    END IF;

    EXECUTE $sql$
    CREATE OR REPLACE FUNCTION agent.scoped_certified_figures()
    RETURNS TABLE (tenant_id uuid, subject_id text, figure_id text, period text, item text, benchmark_id text,
                   as_of_date date, value double precision, value_text text, unit text, currency text,
                   status text, reason text, detail jsonb, period_start date, period_end date,
                   observations integer, definition_id text, definition_version text,
                   calculation_version text, source_table text, source_key jsonb, build_id uuid,
                   lineage_run_id uuid, build_status text, published_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $body$
        SELECT f.tenant_id, f.subject_id, f.figure_id, f.period, f.item, f.benchmark_id, f.as_of_date,
               f.value, f.value_text, f.unit, f.currency, f.status, f.reason, f.detail, f.period_start,
               f.period_end, f.observations, f.definition_id, f.definition_version, f.calculation_version,
               f.source_table, f.source_key, f.build_id, f.lineage_run_id, f.build_status, f.published_at
          FROM public.v_certified_figures f
         WHERE f.tenant_id = agent.tenant() AND f.subject_id = ANY(agent.scope())
    $body$
    $sql$;

    EXECUTE $sql$
    CREATE OR REPLACE FUNCTION agent.scoped_certified_builds()
    RETURNS TABLE (build_id uuid, status text, started_at timestamptz, published_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $body$
        SELECT b.build_id, b.status, b.started_at, b.published_at
          FROM public.certified_builds b
         WHERE b.tenant_id = agent.tenant() AND cardinality(agent.scope()) > 0
         ORDER BY b.started_at DESC
         LIMIT 50
    $body$
    $sql$;

    -- As lineage.trace() walks back from a gold row: the figure's own run first, then for each
    -- table a run read, the latest run that wrote it and ended before (basis 'dataset-time', an
    -- inference). Raw files are named only by their sha256: their paths can hold account numbers.
    EXECUTE $sql$
    CREATE OR REPLACE FUNCTION agent.certified_figure_trace(p_subject_id text, p_figure_id text, p_period text,
                                                            p_item text, p_benchmark_id text, p_as_of_date date)
    RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $body$
    DECLARE
        figure_row record;
        build record;
        run record;
        upstream record;
        pending uuid[] := ARRAY[]::uuid[];
        depths integer[] := ARRAY[]::integer[];
        bases text[] := ARRAY[]::text[];
        seen uuid[] := ARRAY[]::uuid[];
        statements bigint[] := ARRAY[]::bigint[];
        steps jsonb := '[]'::jsonb;
        inputs jsonb;
        outputs jsonb;
        current_run uuid;
        level integer;
        basis text;
    BEGIN
        IF p_subject_id IS NULL OR NOT p_subject_id = ANY(agent.scope()) THEN
            RETURN NULL;
        END IF;
        SELECT f.build_id, f.lineage_run_id, f.source_table, f.source_key, f.calculation_version, f.written_at
          INTO figure_row
          FROM public.v_certified_figures f
         WHERE f.tenant_id = agent.tenant() AND f.subject_id = p_subject_id AND f.figure_id = p_figure_id
           AND f.period = COALESCE(p_period, '') AND f.item = COALESCE(p_item, '')
           AND f.benchmark_id = COALESCE(p_benchmark_id, '') AND f.as_of_date = p_as_of_date;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        SELECT b.status, b.started_at, b.published_at INTO build
          FROM public.certified_builds b WHERE b.tenant_id = agent.tenant() AND b.build_id = figure_row.build_id;
        IF figure_row.lineage_run_id IS NOT NULL THEN
            pending := ARRAY[figure_row.lineage_run_id];
            depths := ARRAY[0];
            bases := ARRAY['row'];
        END IF;
        WHILE cardinality(pending) > 0 AND jsonb_array_length(steps) < 40 LOOP
            current_run := pending[1];
            level := depths[1];
            basis := bases[1];
            pending := pending[2:];
            depths := depths[2:];
            bases := bases[2:];
            CONTINUE WHEN current_run = ANY(seen) OR level > 6;
            seen := seen || current_run;
            SELECT r.job_name, r.status, r.started_at, r.ended_at, r.code_version, r.facets INTO run
              FROM public.lineage_runs r WHERE r.tenant_id = agent.tenant() AND r.run_id = current_run;
            IF NOT FOUND THEN
                steps := steps || jsonb_build_object('depth', level, 'basis', basis, 'recorded', false);
                CONTINUE;
            END IF;
            SELECT COALESCE(jsonb_agg(jsonb_build_object(
                       'dataset', CASE WHEN io.namespace = 'postgres' THEN io.name ELSE 'raw file' END,
                       'row_count', io.row_count, 'sha256', io.sha256) ORDER BY io.namespace, io.name)
                       FILTER (WHERE io.direction = 'input'), '[]'::jsonb),
                   COALESCE(jsonb_agg(jsonb_build_object(
                       'dataset', CASE WHEN io.namespace = 'postgres' THEN io.name ELSE 'raw file' END,
                       'row_count', io.row_count, 'sha256', io.sha256) ORDER BY io.namespace, io.name)
                       FILTER (WHERE io.direction = 'output'), '[]'::jsonb)
              INTO inputs, outputs
              FROM public.lineage_io io WHERE io.tenant_id = agent.tenant() AND io.run_id = current_run;
            steps := steps || jsonb_build_object(
                'depth', level, 'basis', basis, 'recorded', true, 'job', run.job_name, 'status', run.status,
                'started_at', run.started_at, 'ended_at', run.ended_at, 'code_version', run.code_version,
                'inputs', inputs, 'outputs', outputs);
            IF jsonb_typeof(run.facets -> 'ingestion_run_ids') = 'array' THEN
                statements := statements || ARRAY(
                    SELECT value::bigint FROM jsonb_array_elements_text(run.facets -> 'ingestion_run_ids') AS ids(value)
                     WHERE value ~ '^[0-9]{1,18}$');
            END IF;
            FOR upstream IN
                SELECT DISTINCT ON (io.name) r.run_id
                  FROM public.lineage_io io
                  JOIN public.lineage_io o ON o.tenant_id = io.tenant_id AND o.direction = 'output'
                       AND o.namespace = io.namespace AND o.name = io.name
                  JOIN public.lineage_runs r ON r.tenant_id = o.tenant_id AND r.run_id = o.run_id
                 WHERE io.tenant_id = agent.tenant() AND io.run_id = current_run AND io.direction = 'input'
                   AND io.namespace = 'postgres' AND r.ended_at IS NOT NULL AND r.ended_at <= run.started_at
                   AND r.run_id <> current_run
                 ORDER BY io.name, r.ended_at DESC
            LOOP
                pending := pending || upstream.run_id;
                depths := depths || (level + 1);
                bases := bases || 'dataset-time'::text;
            END LOOP;
        END LOOP;
        RETURN jsonb_build_object(
            'build', jsonb_build_object('build_id', figure_row.build_id, 'status', build.status,
                                        'started_at', build.started_at, 'published_at', build.published_at),
            'source', jsonb_build_object('table', figure_row.source_table, 'key', figure_row.source_key),
            'calculation_version', figure_row.calculation_version,
            'written_at', figure_row.written_at,
            'recorded', figure_row.lineage_run_id IS NOT NULL,
            'runs', steps,
            'ibkr_statements', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('run_id', i.run_id, 'run_type', i.run_type, 'status', i.status,
                                                    'requested_from', i.requested_from,
                                                    'requested_to', i.requested_to, 'sha256', i.raw_sha256)
                                 ORDER BY i.run_id)
                  FROM public.ingestion_runs i
                 WHERE i.tenant_id = agent.tenant() AND i.run_id = ANY(statements)), '[]'::jsonb));
    END
    $body$
    $sql$;

    EXECUTE 'CREATE OR REPLACE VIEW agent.certified_figures AS SELECT * FROM agent.scoped_certified_figures()';
    EXECUTE 'CREATE OR REPLACE VIEW agent.certified_builds AS SELECT * FROM agent.scoped_certified_builds()';

    -- New objects only: functions are executable by PUBLIC when created, so that goes first.
    EXECUTE 'REVOKE ALL ON FUNCTION agent.scoped_certified_figures(), agent.scoped_certified_builds(), '
            'agent.certified_figure_trace(text, text, text, text, text, date) FROM PUBLIC';
    EXECUTE 'REVOKE ALL ON agent.certified_figures, agent.certified_builds FROM PUBLIC';
    EXECUTE 'GRANT EXECUTE ON FUNCTION agent.scoped_certified_figures(), agent.scoped_certified_builds(), '
            'agent.certified_figure_trace(text, text, text, text, text, date) TO agent_reader';
    EXECUTE 'GRANT SELECT ON agent.certified_figures, agent.certified_builds TO agent_reader';
END
$certified$;
