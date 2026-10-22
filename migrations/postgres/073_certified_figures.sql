-- Certified figures (medallion upgrade U5, docs/MEDALLION_CERTIFIED_FIGURES.md): one stored
-- number per Metrics guide figure, subject and date, written by one daily build that checks
-- itself before it publishes. Written only by scripts/build_certified_figures.py, which does
-- nothing unless CERTIFIED_FIGURES_BUILD=on; nothing reads these yet.
--
-- certified_builds: one row per build. It starts as shadow, then becomes published or held;
-- a published or held build can later be rolled_back. Rows are never deleted.
-- certified_figures: the figures, append-only. A build writes a row only for a new date or a
-- value that changed, and only while the build is in shadow.
-- v_certified_figures: what readers see, the row from the newest published build for each
-- figure. security_invoker, so the caller's tenant applies.
--
-- New gold objects: created in public, then moved into gold with a security_invoker view of
-- the same name left in public, exactly as 068 left the existing gold tables, so bootstrap's
-- grants, scripts/medallion_layers.py and its undo treat them alike. Without a gold schema
-- (a database before 068) they stay in public. Additive: no existing table or view changes.
-- Both tables get lineage_run_id from 071's trigger, stamped when the row is inserted.

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.certified_builds (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    build_id UUID NOT NULL,
    run_kind TEXT NOT NULL CHECK (run_kind IN ('daily', 'backfill')),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('shadow', 'published', 'held', 'rolled_back')),
    published_at TIMESTAMPTZ,
    checks JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(checks) = 'object'),
    reason TEXT CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 2000),
    lineage_run_id UUID,
    PRIMARY KEY (tenant_id, build_id),
    CHECK (status <> 'published' OR published_at IS NOT NULL),
    CHECK (status NOT IN ('held', 'rolled_back') OR reason IS NOT NULL)
);
COMMENT ON TABLE public.certified_builds IS
    'One row per certified-figures build: shadow, then published or held; rolled_back undoes one.';

CREATE TABLE IF NOT EXISTS public.certified_figures (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    build_id UUID NOT NULL,
    subject_id TEXT NOT NULL CHECK (length(subject_id) BETWEEN 1 AND 200),
    figure_id TEXT NOT NULL CHECK (figure_id ~ '^[a-z]+\.[a-z0-9_]+$'),
    period TEXT NOT NULL DEFAULT '' CHECK (length(period) <= 40),
    item TEXT NOT NULL DEFAULT '' CHECK (length(item) <= 200),
    benchmark_id TEXT NOT NULL DEFAULT '' CHECK (length(benchmark_id) <= 200),
    as_of_date DATE NOT NULL,
    value DOUBLE PRECISION CHECK (value IS NULL OR value NOT IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)),
    value_text TEXT CHECK (value_text IS NULL OR length(value_text) <= 400),
    unit TEXT NOT NULL CHECK (unit IN ('ratio', 'number', 'money', 'date', 'text')),
    currency TEXT CHECK (currency IS NULL OR length(currency) BETWEEN 1 AND 12),
    status TEXT NOT NULL CHECK (status IN ('ok', 'unavailable')),
    reason TEXT CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 2000),
    detail JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
    period_start DATE,
    period_end DATE,
    observations INTEGER CHECK (observations IS NULL OR observations >= 0),
    definition_id TEXT NOT NULL CHECK (definition_id ~ '^metric-[a-z0-9-]+$'),
    definition_version TEXT NOT NULL CHECK (definition_version ~ '^[0-9a-f]{12}$'),
    calculation_version TEXT NOT NULL CHECK (length(calculation_version) BETWEEN 1 AND 200),
    input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    source_table TEXT NOT NULL CHECK (source_table ~ '^(silver|gold)\.[a-z_][a-z0-9_]*$'),
    source_key JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(source_key) = 'object'),
    lineage_run_id UUID,
    written_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subject_id, figure_id, period, item, benchmark_id, as_of_date, build_id),
    FOREIGN KEY (tenant_id, build_id) REFERENCES public.certified_builds(tenant_id, build_id),
    -- ok: a number for ratio, number and money, a text for date and text; unavailable: neither, and why.
    CHECK (status <> 'ok' OR (CASE WHEN unit IN ('date', 'text') THEN value_text IS NOT NULL
                                   ELSE value IS NOT NULL END)),
    CHECK (status <> 'unavailable' OR (value IS NULL AND value_text IS NULL AND reason IS NOT NULL)),
    CHECK (unit <> 'date' OR value_text IS NULL OR value_text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end)
);
COMMENT ON TABLE public.certified_figures IS
    'Certified figures: one row per figure, subject, period, item, benchmark and date for each build '
    'that wrote it. Append-only; read through v_certified_figures.';

CREATE INDEX IF NOT EXISTS certified_figures_subject_date
    ON public.certified_figures(tenant_id, subject_id, as_of_date);
CREATE INDEX IF NOT EXISTS certified_figures_build
    ON public.certified_figures(tenant_id, build_id);
CREATE INDEX IF NOT EXISTS certified_builds_published
    ON public.certified_builds(tenant_id, published_at DESC) WHERE status = 'published';

ALTER TABLE public.certified_builds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.certified_builds FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.certified_builds;
CREATE POLICY tenant_isolation ON public.certified_builds
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());
ALTER TABLE public.certified_figures ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.certified_figures FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.certified_figures;
CREATE POLICY tenant_isolation ON public.certified_figures
    USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

-- Figures are append-only.
CREATE OR REPLACE FUNCTION public.certified_figures_refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Certified figures are append-only; a new build writes new rows';
END;
$$;
REVOKE ALL ON FUNCTION public.certified_figures_refuse_change() FROM PUBLIC;
DROP TRIGGER IF EXISTS certified_figures_append_only ON public.certified_figures;
CREATE TRIGGER certified_figures_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON public.certified_figures
    FOR EACH STATEMENT EXECUTE FUNCTION public.certified_figures_refuse_change();
DROP TRIGGER IF EXISTS certified_builds_never_removed ON public.certified_builds;
CREATE TRIGGER certified_builds_never_removed
    BEFORE DELETE OR TRUNCATE ON public.certified_builds
    FOR EACH STATEMENT EXECUTE FUNCTION public.certified_figures_refuse_change();

-- Rows go only into a build that is still in shadow, so a published build never changes.
CREATE OR REPLACE FUNCTION public.certified_figures_into_shadow_build() RETURNS trigger
LANGUAGE plpgsql SET search_path = gold, public, pg_temp AS $$
DECLARE
    closed text;
BEGIN
    SELECT b.status INTO closed
      FROM (SELECT DISTINCT tenant_id, build_id FROM added) a
      JOIN certified_builds b ON b.tenant_id = a.tenant_id AND b.build_id = a.build_id
     WHERE b.status <> 'shadow'
     LIMIT 1;
    IF closed IS NOT NULL THEN
        RAISE EXCEPTION 'Certified figures can be added only to a build in shadow, not one that is %', closed;
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION public.certified_figures_into_shadow_build() FROM PUBLIC;
DROP TRIGGER IF EXISTS certified_figures_into_shadow_build ON public.certified_figures;
CREATE TRIGGER certified_figures_into_shadow_build
    AFTER INSERT ON public.certified_figures REFERENCING NEW TABLE AS added
    FOR EACH STATEMENT EXECUTE FUNCTION public.certified_figures_into_shadow_build();

-- A build's status moves only forward: shadow to published or held, held to published, and a
-- published or held build to rolled_back. Its id, kind, start and run never change.
CREATE OR REPLACE FUNCTION public.certified_builds_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant_id <> OLD.tenant_id OR NEW.build_id <> OLD.build_id OR NEW.run_kind <> OLD.run_kind
       OR NEW.started_at <> OLD.started_at OR NEW.lineage_run_id IS DISTINCT FROM OLD.lineage_run_id THEN
        RAISE EXCEPTION 'Only a certified build''s status, checks, reason and times can change';
    END IF;
    IF OLD.status = 'published' AND NEW.published_at IS DISTINCT FROM OLD.published_at THEN
        RAISE EXCEPTION 'A published certified build keeps its publication time';
    END IF;
    IF NEW.status <> OLD.status AND (OLD.status, NEW.status) NOT IN (
        ('shadow', 'published'), ('shadow', 'held'), ('held', 'published'),
        ('published', 'rolled_back'), ('held', 'rolled_back')) THEN
        RAISE EXCEPTION 'A certified build cannot go from % to %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.certified_builds_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS certified_builds_guard ON public.certified_builds;
CREATE TRIGGER certified_builds_guard
    BEFORE UPDATE ON public.certified_builds
    FOR EACH ROW EXECUTE FUNCTION public.certified_builds_guard();

-- The run that wrote the row (071's function); stamped once, on insert.
DROP TRIGGER IF EXISTS lineage_stamp_run ON public.certified_builds;
CREATE TRIGGER lineage_stamp_run BEFORE INSERT ON public.certified_builds
    FOR EACH ROW EXECUTE FUNCTION public.lineage_stamp_run();
DROP TRIGGER IF EXISTS lineage_stamp_run ON public.certified_figures;
CREATE TRIGGER lineage_stamp_run BEFORE INSERT ON public.certified_figures
    FOR EACH ROW EXECUTE FUNCTION public.lineage_stamp_run();
COMMENT ON COLUMN public.certified_builds.lineage_run_id IS
    'The recorded job run that wrote this build (lineage_runs); NULL when that run was not recorded.';
COMMENT ON COLUMN public.certified_figures.lineage_run_id IS
    'The recorded job run that wrote this row (lineage_runs); NULL when that run was not recorded.';

-- For each figure, the row from the newest published build that wrote it. A rolled-back
-- build drops out, so readers fall back to the build before it at once.
CREATE OR REPLACE VIEW public.v_certified_figures WITH (security_invoker = true) AS
SELECT DISTINCT ON (f.tenant_id, f.subject_id, f.figure_id, f.period, f.item, f.benchmark_id, f.as_of_date)
       f.*, b.status AS build_status, b.published_at
  FROM public.certified_figures f
  JOIN public.certified_builds b ON b.tenant_id = f.tenant_id AND b.build_id = f.build_id
 WHERE b.status = 'published'
 ORDER BY f.tenant_id, f.subject_id, f.figure_id, f.period, f.item, f.benchmark_id, f.as_of_date,
          b.published_at DESC, b.build_id DESC;
COMMENT ON VIEW public.v_certified_figures IS
    'Certified figures as readers see them: the row from the newest published build for each figure.';

-- Into gold once the layer exists, as 068 moved the other gold tables.
DO $$
DECLARE
    layer CONSTANT text := 'gold';
    moved CONSTANT text[] := ARRAY['certified_builds', 'certified_figures', 'v_certified_figures'];
    rel text;
    kind "char";
    grant_row record;
BEGIN
    IF to_regnamespace(layer) IS NULL THEN
        RAISE NOTICE 'There is no gold schema yet, so the certified figures tables stay in public';
        RETURN;
    END IF;
    FOREACH rel IN ARRAY moved LOOP
        IF to_regclass(format('%I.%I', layer, rel)) IS NOT NULL THEN
            CONTINUE;  -- already moved
        END IF;
        SELECT c.relkind INTO kind FROM pg_class c WHERE c.oid = to_regclass(format('public.%I', rel));
        EXECUTE format('ALTER %s public.%I SET SCHEMA %I',
                       CASE WHEN kind = 'v' THEN 'VIEW' ELSE 'TABLE' END, rel, layer);
        EXECUTE format('CREATE VIEW public.%I WITH (security_invoker = true) AS SELECT * FROM %I.%I',
                       rel, layer, rel);
        EXECUTE format('COMMENT ON VIEW public.%I IS %L', rel,
                       format('Compatibility view over %I.%I (medallion layer).', layer, rel));
        -- Whoever can use the table can use the view, as in 068.
        FOR grant_row IN
            SELECT a.grantee, a.privilege_type, a.is_grantable
              FROM pg_class c, aclexplode(c.relacl) a
             WHERE c.oid = to_regclass(format('%I.%I', layer, rel))
               AND a.grantee <> c.relowner
               AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
        LOOP
            EXECUTE format('GRANT %s ON public.%I TO %s%s', grant_row.privilege_type, rel,
                CASE WHEN grant_row.grantee = 0 THEN 'PUBLIC'
                     ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END,
                CASE WHEN grant_row.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
        END LOOP;
    END LOOP;
END;
$$;
