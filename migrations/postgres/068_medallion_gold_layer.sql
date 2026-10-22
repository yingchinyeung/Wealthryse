-- Medallion step S3a: move the gold tables into the gold schema.
--
-- SET SCHEMA copies no data. Each table keeps its rows, indexes, constraints,
-- row-level security policies, triggers, owned sequences and grants. Views and
-- functions bound to it (the agent views among them) keep working, because they
-- refer to it by id.
--
-- A view with the old name stays in public so that SQL naming public.<name>
-- keeps working. It is security_invoker, so grants and row-level security are
-- checked for whoever reads it, exactly as on the table. The table's grants,
-- column grants included, are copied onto the view, with USAGE on the schema.
--
-- A view fixes its column list when it is created. A later migration that
-- changes one of these tables names it as gold.<table> and re-creates its view
-- in public; tests/test_medallion_layers_db.py fails otherwise. Objects already
-- moved are skipped, so the file is safe to run again. Undo:
-- scripts/medallion_layers.py undo-sql gold. infra/medallion/catalog.json lists
-- the layer of every table.

-- Any other lock the batch needs (owned sequences, the new views) waits at
-- most 5 seconds.
SET LOCAL lock_timeout = '5s';

CREATE SCHEMA IF NOT EXISTS gold;
REVOKE ALL ON SCHEMA gold FROM PUBLIC;

DO $$
DECLARE
    layer CONSTANT text := 'gold';
    moved CONSTANT text[] := ARRAY[
        'crisis_replay_components',
        'crisis_replay_daily',
        'crisis_scenario_results',
        'performance_comparison_daily',
        'reporting_snapshots',
        'risk_analytics_snapshots'
    ];
    rel text;
    kind "char";
    owner_name text;
    grant_row record;
    targets text;
    deadline CONSTANT timestamptz := clock_timestamp() + interval '5 seconds';
BEGIN
    -- Take every lock the batch needs at once, without joining the lock queue.
    -- A try that cannot get all of them straight away holds nothing, so website
    -- queries never wait behind it. After 5 seconds of trying, the batch gives up
    -- with SQLSTATE 55P03 having changed nothing, and bootstrap tries again a
    -- minute later. Locking a view also locks the tables it reads.
    SELECT string_agg(format('public.%I', r), ', ' ORDER BY r) INTO targets
      FROM unnest(moved) AS r
     WHERE to_regclass(format('public.%I', r)) IS NOT NULL
       AND to_regclass(format('%I.%I', layer, r)) IS NULL;
    WHILE targets IS NOT NULL LOOP
        BEGIN
            EXECUTE format('LOCK TABLE %s IN ACCESS EXCLUSIVE MODE NOWAIT', targets);
            EXIT;
        EXCEPTION WHEN lock_not_available THEN
            IF clock_timestamp() >= deadline THEN
                RAISE EXCEPTION 'the % batch could not lock its tables within 5 seconds, so nothing was moved', layer
                    USING ERRCODE = 'lock_not_available',
                          HINT = 'Run the same migration again a minute later.';
            END IF;
            PERFORM pg_sleep(0.05);
        END;
    END LOOP;
    FOREACH rel IN ARRAY moved LOOP
        IF to_regclass(format('%I.%I', layer, rel)) IS NOT NULL THEN
            CONTINUE;  -- already moved
        END IF;
        SELECT c.relkind, pg_get_userbyid(c.relowner) INTO kind, owner_name
          FROM pg_class c WHERE c.oid = to_regclass(format('public.%I', rel));
        IF kind IS NULL THEN
            RAISE NOTICE 'public.% does not exist, so it is not moved', rel;
            CONTINUE;
        END IF;
        EXECUTE format('ALTER %s public.%I SET SCHEMA %I',
                       CASE WHEN kind = 'v' THEN 'VIEW' ELSE 'TABLE' END, rel, layer);
        EXECUTE format('CREATE VIEW public.%I WITH (security_invoker = true) AS SELECT * FROM %I.%I',
                       rel, layer, rel);
        IF owner_name <> current_user THEN
            EXECUTE format('ALTER VIEW public.%I OWNER TO %I', rel, owner_name);
        END IF;
        EXECUTE format('COMMENT ON VIEW public.%I IS %L', rel,
                       format('Compatibility view over %I.%I (medallion layer).', layer, rel));
        -- Whoever could use the table can use the view and reach the schema.
        FOR grant_row IN
            SELECT a.grantee, a.privilege_type, a.is_grantable, NULL::text AS column_name
              FROM pg_class c, aclexplode(c.relacl) a
             WHERE c.oid = to_regclass(format('%I.%I', layer, rel))
               AND a.grantee <> c.relowner
               AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
            UNION ALL
            SELECT a.grantee, a.privilege_type, a.is_grantable, att.attname::text
              FROM pg_attribute att, aclexplode(att.attacl) a
             WHERE att.attrelid = to_regclass(format('%I.%I', layer, rel))
               AND att.attnum > 0 AND NOT att.attisdropped
               AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE')
        LOOP
            EXECUTE format('GRANT %s%s ON public.%I TO %s%s',
                grant_row.privilege_type,
                CASE WHEN grant_row.column_name IS NULL THEN ''
                     ELSE format(' (%I)', grant_row.column_name) END,
                rel,
                CASE WHEN grant_row.grantee = 0 THEN 'PUBLIC'
                     ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END,
                CASE WHEN grant_row.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO %s', layer,
                CASE WHEN grant_row.grantee = 0 THEN 'PUBLIC'
                     ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END);
        END LOOP;
    END LOOP;
END;
$$;
