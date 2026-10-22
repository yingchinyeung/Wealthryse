-- The connection agent names what a new login's Flex query is missing.
--
-- Applied after 010_live_market.sql by scripts/install_agent_schema.py, on
-- purpose and with approval. Idempotent.
--
-- The Flex discovery job now checks each statement it downloads against the
-- platform's Flex recipe (ibkr_etl.flex_recipe) and keeps, beside the check's
-- result, the sections and fields the query lacks: section names, statuses
-- and messages, never statement content. This adds that summary to the
-- connection agent's view of each login, so its report can say exactly which
-- sections to tick.

DROP VIEW IF EXISTS agent.broker_logins;
DROP FUNCTION IF EXISTS agent.scoped_broker_logins();

CREATE OR REPLACE FUNCTION agent.scoped_broker_logins()
RETURNS TABLE (
    connection_id uuid, display_name text, connection_status text, credential_expires_on date,
    flex_source_id uuid, source_status text, credential_status text,
    blocked_until timestamptz, requires_manual_reset boolean,
    checks_requested bigint, latest_request_id uuid, latest_status text, latest_error_phase text,
    latest_error_code text, latest_created_at timestamptz, latest_completed_at timestamptz,
    latest_account_count integer, accounts_found bigint, latest_recipe jsonb
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT b.connection_id, b.display_name, b.status, b.credential_expires_on,
           q.flex_source_id, q.status, f.status,
           s.blocked_until, COALESCE(s.requires_manual_reset, false),
           (SELECT count(*) FROM public.ibkr_flex_source_validation_requests r
             WHERE r.tenant_id = q.tenant_id AND r.flex_source_id = q.flex_source_id),
           l.validation_request_id, l.status, l.error_phase, l.error_code, l.created_at, l.completed_at,
           NULLIF(l.result_json ->> 'account_count', '')::integer,
           (SELECT count(*) FROM public.ibkr_flex_source_accounts a
             WHERE a.tenant_id = q.tenant_id AND a.flex_source_id = q.flex_source_id
               AND a.discovery_status NOT IN ('not_portfolio', 'ignored')),
           l.result_json -> 'recipe'
      FROM public.broker_connections b
      JOIN public.ibkr_flex_queries q ON q.tenant_id = b.tenant_id AND q.credential_id = b.connection_id
      JOIN public.ibkr_flex_credentials f ON f.tenant_id = q.tenant_id AND f.credential_id = q.credential_id
      LEFT JOIN public.ibkr_credential_circuit_state s
        ON s.tenant_id = q.tenant_id AND s.credential_id = q.credential_id
      LEFT JOIN LATERAL (
          SELECT r.validation_request_id, r.status, r.error_phase, r.error_code, r.created_at, r.completed_at,
                 r.result_json
            FROM public.ibkr_flex_source_validation_requests r
           WHERE r.tenant_id = q.tenant_id AND r.flex_source_id = q.flex_source_id
           ORDER BY r.created_at DESC LIMIT 1
      ) l ON true
     WHERE b.tenant_id = agent.tenant() AND b.broker = 'ibkr_flex' AND b.status <> 'retired'
$$;

CREATE OR REPLACE VIEW agent.broker_logins AS SELECT * FROM agent.scoped_broker_logins();

REVOKE ALL ON agent.broker_logins FROM PUBLIC;
REVOKE ALL ON FUNCTION agent.scoped_broker_logins() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION agent.scoped_broker_logins() TO agent_reader;
GRANT SELECT ON agent.broker_logins TO agent_reader;
