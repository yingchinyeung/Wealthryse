-- The connection agent (Agentic AI integration map, "Frontier onboarding":
-- confirm the broker sees the account).
--
-- Applied after 008_chat_memory_and_briefs.sql by scripts/install_agent_schema.py,
-- on purpose and with approval. Idempotent.
--
-- When the owner adds an IBKR login on the Account onboarding page, nothing
-- contacts IBKR until someone presses "Check now". The connection agent does
-- that first check itself: it queues the same one-attempt request the button
-- queues, which the Flex discovery job (ibkrv2stg-flex-discovery) then runs.
-- It then says, in plain words, what the check found: which accounts the
-- login sees, or exactly what went wrong and what to do about it.
--
-- It queues only the first check of a login that has never been checked, and
-- only when the button would be allowed to: never a retry, never while the
-- login is cooling down or waits for an owner-reviewed reset. IBKR locks a
-- token after repeated failures, so every later check stays the owner's.
-- It never sees the token or its Key Vault name.
--
-- Adds:
--   1. one scoped view of the tenant's IBKR logins and their latest check, for
--      the tool server only;
--   2. agent.queue_first_connection_check(), the only write the agent makes
--      outside its own schema;
--   3. agent.connection_reports, what the agent found, which the platform
--      posts to the administrator's system updates;
--   4. the final, validated lists of jobs and inbox kinds.
--
-- The installer applies every file again on each run, and the earlier files
-- each set a shorter list. Those now add their lists NOT VALID, so rows of a
-- later job or kind do not stop a re-run; the last file sets the full list and
-- validates it. A later file that adds a job or kind does the same.

CREATE OR REPLACE FUNCTION agent.scoped_broker_logins()
RETURNS TABLE (
    connection_id uuid, display_name text, connection_status text, credential_expires_on date,
    flex_source_id uuid, source_status text, credential_status text,
    blocked_until timestamptz, requires_manual_reset boolean,
    checks_requested bigint, latest_request_id uuid, latest_status text, latest_error_phase text,
    latest_error_code text, latest_created_at timestamptz, latest_completed_at timestamptz,
    latest_account_count integer, accounts_found bigint
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
               AND a.discovery_status NOT IN ('not_portfolio', 'ignored'))
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

-- The same request the "Check now" button queues, for a login never checked.
-- Returns the request id, or NULL when the login is not eligible.
CREATE OR REPLACE FUNCTION agent.queue_first_connection_check(p_flex_source_id uuid)
RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    v_tenant uuid := agent.tenant();
    v_to date := (now() AT TIME ZONE 'Asia/Hong_Kong')::date - 1;
    v_id uuid := gen_random_uuid();
BEGIN
    IF v_tenant IS NULL THEN
        RETURN NULL;
    END IF;
    PERFORM 1
       FROM public.ibkr_flex_queries q
       JOIN public.ibkr_flex_credentials f ON f.tenant_id = q.tenant_id AND f.credential_id = q.credential_id
       JOIN public.broker_connections b ON b.tenant_id = q.tenant_id AND b.connection_id = q.credential_id
       LEFT JOIN public.ibkr_credential_circuit_state s
         ON s.tenant_id = q.tenant_id AND s.credential_id = q.credential_id
      WHERE q.tenant_id = v_tenant AND q.flex_source_id = p_flex_source_id
        AND q.status = 'pending_validation'
        AND f.status NOT IN ('suspended', 'expired', 'revoked')
        AND b.status NOT IN ('suspended', 'retired')
        AND NOT COALESCE(s.requires_manual_reset, false)
        AND (s.blocked_until IS NULL OR s.blocked_until <= now())
        AND NOT EXISTS (SELECT 1 FROM public.ibkr_flex_source_validation_requests r
                         WHERE r.tenant_id = q.tenant_id AND r.flex_source_id = q.flex_source_id)
      FOR UPDATE OF q;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    INSERT INTO public.ibkr_flex_source_validation_requests(
        validation_request_id, tenant_id, flex_source_id, status, requested_from, requested_to,
        requested_by_identity_sha256, attempt_count, created_at)
    VALUES (v_id, v_tenant, p_flex_source_id, 'pending', v_to - 6, v_to, NULL, 0, now())
    ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    RETURN v_id;
END
$$;

CREATE TABLE IF NOT EXISTS agent.connection_reports (
    report_id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    flex_source_id uuid NOT NULL,
    report_key text NOT NULL CHECK (char_length(report_key) BETWEEN 1 AND 120),
    state text NOT NULL CHECK (state IN ('queued', 'found_accounts', 'failed', 'blocked', 'stuck', 'waiting')),
    title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
    body text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 2000),
    owner_steps jsonb NOT NULL DEFAULT '[]'::jsonb,
    validation_request_id uuid,
    run_id bigint REFERENCES agent.runs(run_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, flex_source_id, report_key)
);

ALTER TABLE agent.service_grants DROP CONSTRAINT IF EXISTS service_grants_job_name_check;
ALTER TABLE agent.service_grants ADD CONSTRAINT service_grants_job_name_check
    CHECK (job_name IN ('holdings_explainer', 'month_end_drafter', 'data_quality', 'month_team', 'onboarding',
                        'operations', 'weekly_brief'));

ALTER TABLE agent.inbox DROP CONSTRAINT IF EXISTS inbox_kind_check;
ALTER TABLE agent.inbox ADD CONSTRAINT inbox_kind_check CHECK (kind IN (
    'holdings_explainer', 'month_end_draft', 'data_quality',
    'month_end_report', 'month_end_hold', 'month_end_dispute',
    'onboarding_watch', 'weekly_brief'));

REVOKE ALL ON agent.broker_logins, agent.connection_reports FROM PUBLIC;
REVOKE ALL ON FUNCTION agent.scoped_broker_logins(), agent.queue_first_connection_check(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION agent.scoped_broker_logins() TO agent_reader;
GRANT SELECT ON agent.broker_logins TO agent_reader;
GRANT EXECUTE ON FUNCTION agent.queue_first_connection_check(uuid) TO agent_writer;
GRANT SELECT, INSERT ON agent.connection_reports TO agent_writer;
GRANT USAGE ON SEQUENCE agent.connection_reports_report_id_seq TO agent_writer;
