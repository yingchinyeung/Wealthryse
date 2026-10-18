-- The Desk page's two silver views (wiring design section 1, docs/QUANT_ANALYTICS.md). HELD
-- MIGRATION. Not in migrations/postgres, so no bootstrap or job applies it. On its approved day
-- it moves to migrations/postgres/077_desk_live_views.sql and runs alone
-- (bootstrap_postgres.py --only-migration 077_desk_live_views.sql), apart from 076 (the gold
-- quant table, quant_analytics.sql). desk_live_views_undo.sql reverses it and only it.
--
-- subject_live_executions: the fills the live collector records (bronze live_executions), one row
-- per execution, under the physical account's subject id. The Desk page picks today's.
-- benchmark_live_quotes: the latest live quote of each benchmark contract (live_contracts with
-- is_benchmark), for the Desk page's market factor.
--
-- Both are security invoker, as subject_live_positions is, so the reader's grants and the live
-- tables' row security apply. They are created in public, then moved into silver with a
-- security_invoker view of the same name left in public, exactly as 070 moved
-- subject_live_positions. Grants: none here. bootstrap_postgres.py, which runs this file, then
-- gives the app and website logins SELECT on both, as on every silver view. Nothing existing
-- changes: agent.live_executions and the live views keep reading what they read today.

SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
    IF to_regnamespace('bronze') IS NULL OR to_regnamespace('silver') IS NULL THEN
        RAISE EXCEPTION 'desk_live_views.sql needs the bronze and silver schemas (migrations 069 and 070)';
    END IF;
END;
$$;

CREATE VIEW public.subject_live_executions
WITH (security_invoker = true) AS
SELECT
    e.tenant_id,
    e.account_id AS subject_id,
    e.exec_id,
    e.executed_at,
    e.conid,
    e.symbol,
    e.sec_type,
    e.currency,
    e.side,
    e.quantity,
    e.price,
    e.commission,
    e.commission_currency,
    e.realized_pnl
FROM bronze.live_executions e;
COMMENT ON VIEW public.subject_live_executions IS
    'Live fills per physical account (subject), from the live collector. The Desk page reads today''s.';

CREATE VIEW public.benchmark_live_quotes
WITH (security_invoker = true) AS
SELECT
    c.tenant_id,
    c.conid,
    c.symbol,
    q.currency,
    q.last_price,
    q.bid_price,
    q.ask_price,
    q.prior_close,
    q.market_data_type,
    q.observed_at
FROM bronze.live_contracts c
CROSS JOIN LATERAL (
    SELECT quote.currency, quote.last_price, quote.bid_price, quote.ask_price, quote.prior_close,
           quote.market_data_type, quote.observed_at
    FROM bronze.live_quotes quote
    WHERE quote.tenant_id = c.tenant_id AND quote.conid = c.conid
    ORDER BY quote.observed_at DESC
    LIMIT 1
) q
WHERE c.is_benchmark;
COMMENT ON VIEW public.benchmark_live_quotes IS
    'The latest live quote of each benchmark contract (live_contracts.is_benchmark).';

-- Into silver, as 070 moved the other silver views.
DO $$
DECLARE
    layer CONSTANT text := 'silver';
    moved CONSTANT text[] := ARRAY['subject_live_executions', 'benchmark_live_quotes'];
    rel text;
    owner_name text;
    grant_row record;
BEGIN
    FOREACH rel IN ARRAY moved LOOP
        IF to_regclass(format('%I.%I', layer, rel)) IS NOT NULL THEN
            CONTINUE;  -- already moved
        END IF;
        SELECT pg_get_userbyid(c.relowner) INTO owner_name
          FROM pg_class c WHERE c.oid = to_regclass(format('public.%I', rel));
        EXECUTE format('ALTER VIEW public.%I SET SCHEMA %I', rel, layer);
        EXECUTE format('CREATE VIEW public.%I WITH (security_invoker = true) AS SELECT * FROM %I.%I',
                       rel, layer, rel);
        IF owner_name <> current_user THEN
            EXECUTE format('ALTER VIEW public.%I OWNER TO %I', rel, owner_name);
        END IF;
        EXECUTE format('COMMENT ON VIEW public.%I IS %L', rel,
                       format('Compatibility view over %I.%I (medallion layer).', layer, rel));
        -- Whoever could use the view can use its compatibility view and reach the schema.
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
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO %s', layer,
                CASE WHEN grant_row.grantee = 0 THEN 'PUBLIC'
                     ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END);
        END LOOP;
    END LOOP;
END;
$$;
