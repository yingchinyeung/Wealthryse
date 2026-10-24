-- The newest published certified date per account, for the supervisor's event dispatcher (gold).
--
-- Applied after 020_kiwoom_flows.sql by scripts/install_agent_schema.py, on purpose and with
-- approval. Idempotent and additive: one function and one view for agent_reader only, and no
-- existing grant changes. On a database without the certified figures tables (migration 073 not
-- applied yet) it does nothing; a later run installs it.
--
--   agent.certified_latest   one row per account in scope: subject_id, the newest as_of_date
--                            that a published certified build covers for it, and build_id, the
--                            newest published build that wrote figures for that date.
--
-- wealthryse_agent.tools.get_settled_dates reads it (to_regclass first, so the dispatcher ran on
-- silver dates until now): a data date wakes a job only once this date reaches it. A held or
-- shadow build is not published, and a rolled-back one drops out, so neither moves the date
-- forward. Only the tenant's own builds count; dates and ids only, never a figure.

DO $latest$
BEGIN
    IF to_regclass('public.certified_figures') IS NULL OR to_regclass('public.certified_builds') IS NULL THEN
        RAISE NOTICE 'public.certified_figures is not installed yet; agent.certified_latest is skipped';
        RETURN;
    END IF;

    EXECUTE $sql$
    CREATE OR REPLACE FUNCTION agent.scoped_certified_latest()
    RETURNS TABLE (subject_id text, build_id uuid, as_of_date date)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $body$
        -- Per account, newest date first along certified_figures_subject_date, so it stops at
        -- the first published row instead of reading the account's whole history.
        SELECT s.subject_id, latest.build_id, latest.as_of_date
          FROM (SELECT DISTINCT unnest(agent.scope()) AS subject_id) s
         CROSS JOIN LATERAL (
               SELECT f.build_id, f.as_of_date
                 FROM public.certified_figures f
                 JOIN public.certified_builds b ON b.tenant_id = f.tenant_id AND b.build_id = f.build_id
                WHERE f.tenant_id = agent.tenant() AND f.subject_id = s.subject_id AND b.status = 'published'
                ORDER BY f.as_of_date DESC, b.published_at DESC, b.build_id DESC
                LIMIT 1) latest
    $body$
    $sql$;

    EXECUTE 'CREATE OR REPLACE VIEW agent.certified_latest AS SELECT * FROM agent.scoped_certified_latest()';

    -- New objects only: functions are executable by PUBLIC when created, so that goes first.
    EXECUTE 'REVOKE ALL ON FUNCTION agent.scoped_certified_latest() FROM PUBLIC';
    EXECUTE 'REVOKE ALL ON agent.certified_latest FROM PUBLIC';
    EXECUTE 'GRANT EXECUTE ON FUNCTION agent.scoped_certified_latest() TO agent_reader';
    EXECUTE 'GRANT SELECT ON agent.certified_latest TO agent_reader';
END
$latest$;
