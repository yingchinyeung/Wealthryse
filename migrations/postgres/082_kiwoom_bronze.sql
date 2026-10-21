-- Medallion: Kiwoom's raw answers in bronze (bronze.kiwoom_responses).
-- Applied alone, after the U8 history record, with bootstrap_postgres.py --only-migration
-- 082_kiwoom_bronze.sql; no job applies it (IBKR_RUN_MIGRATIONS stays false on every job).
-- The collector writes here only with KIWOOM_BRONZE=true (ibkr_etl.kiwoom_bronze), set on
-- institutional-daily after this has run. infra/medallion/undo/082_kiwoom_bronze_undo.sql
-- reverses it.
--
-- One row per page of one Kiwoom inquiry answer, exactly as received (body, as bytes).
-- Only inquiry APIs (ka*, kt*, the overseas account ust* ids and the overseas chart usa* ids)
-- are kept. Token calls (/oauth2/token, /oauth2/revoke) are never stored: their bodies carry
-- the access token and their requests the app key and secret.
-- request_key holds the account number and the query fields (qry_dt, strt_dt, end_dt, ...),
-- never a key, secret, token or header other than the two paging headers kept as columns.
-- A refused call (http_status <> 200 or return_code <> 0) is stored too, so a gap in silver
-- traces to Kiwoom's refusal; a conform step skips those rows.
--
-- Each collector run per account opens one ingestion_runs row (run_type 'kiwoom_rest_bronze')
-- that closes with the page count and the sha256 over its pages' body_sha256 values. Later a
-- conform step (ibkr_etl.kiwoom_conform) rebuilds silver from these rows with the collector's
-- own parse functions, so every Kiwoom silver row traces to stored answers.
--
-- Append-only like every bronze table: bronze.refuse_change() (069) refuses UPDATE, DELETE
-- and TRUNCATE, whoever asks. Isolated by tenant. bootstrap_postgres.py's layer grants reach
-- it as they reach every bronze table: the jobs' login inserts and reads (its UPDATE and DELETE
-- rights meet the triggers), the website's login reads its own tenant's rows.
-- Restricted: the account number is in request_key and in the bodies. Never log either.

SET LOCAL lock_timeout = '5s';

-- Needs the bronze schema and bronze.refuse_change() from 069. A database that has recorded 069
-- without them is refused. Only a test database built without the layer migrations (068 to 070)
-- has neither: there is nothing to do.
DO $$
BEGIN
    IF to_regnamespace('bronze') IS NULL THEN
        IF to_regclass('public.schema_migrations') IS NOT NULL THEN
            IF EXISTS (SELECT 1 FROM public.schema_migrations WHERE version = '069_medallion_bronze_layer.sql') THEN
                RAISE EXCEPTION '069 is recorded but the bronze schema is missing, so 082 changes nothing';
            END IF;
        END IF;
        RAISE NOTICE 'No bronze schema (the layer migrations were left out): 082 has nothing to do';
        RETURN;
    END IF;

    CREATE TABLE IF NOT EXISTS bronze.kiwoom_responses (
        response_id BIGSERIAL PRIMARY KEY,
        run_id BIGINT NOT NULL REFERENCES public.ingestion_runs(run_id),
        tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES public.tenants(tenant_id),
        connection_id UUID NOT NULL,
        api_id TEXT NOT NULL CHECK (api_id ~ '^(k[at][0-9]{5}|ust[0-9]{5}|usa[0-9]{5})$'),
        request_key JSONB NOT NULL CHECK (jsonb_typeof(request_key) = 'object'),
        page_no INTEGER NOT NULL CHECK (page_no >= 1),
        cont_yn TEXT CHECK (cont_yn IN ('Y', 'N')),
        next_key TEXT CHECK (length(next_key) <= 200),
        requested_at TIMESTAMPTZ NOT NULL,
        received_at TIMESTAMPTZ NOT NULL,
        http_status INTEGER NOT NULL CHECK (http_status BETWEEN 100 AND 599),
        return_code INTEGER,
        body BYTEA NOT NULL,
        body_sha256 TEXT NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT kiwoom_responses_received_after_requested CHECK (received_at >= requested_at),
        CONSTRAINT kiwoom_responses_body_sha256 CHECK (body_sha256 = encode(sha256(body), 'hex')),
        CONSTRAINT kiwoom_responses_page UNIQUE (run_id, api_id, request_key, page_no),
        -- No cascade: a connection is retired by its status, and bronze never loses rows.
        CONSTRAINT kiwoom_responses_connection FOREIGN KEY (tenant_id, connection_id)
            REFERENCES public.broker_connections(tenant_id, connection_id) ON DELETE RESTRICT
    );

    CREATE INDEX IF NOT EXISTS kiwoom_responses_by_request
        ON bronze.kiwoom_responses (tenant_id, connection_id, api_id, requested_at);

    COMMENT ON TABLE bronze.kiwoom_responses IS
        'Kiwoom REST inquiry answers as received, one row per page. Append-only. Silver is rebuilt from it.';
    COMMENT ON COLUMN bronze.kiwoom_responses.request_key IS
        'Account number and query fields of the request. Restricted. Never keys, secrets, tokens or headers.';
    COMMENT ON COLUMN bronze.kiwoom_responses.body IS
        'The response body exactly as received. Restricted.';
    COMMENT ON COLUMN bronze.kiwoom_responses.return_code IS
        'Kiwoom''s return_code from the body, when present. Rows with http_status <> 200 or return_code <> 0 are refusals.';

    ALTER TABLE bronze.kiwoom_responses ENABLE ROW LEVEL SECURITY;
    ALTER TABLE bronze.kiwoom_responses FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS tenant_isolation ON bronze.kiwoom_responses;
    CREATE POLICY tenant_isolation ON bronze.kiwoom_responses
        USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id = app_tenant_id());

    REVOKE ALL ON bronze.kiwoom_responses FROM PUBLIC;
    REVOKE ALL ON SEQUENCE bronze.kiwoom_responses_response_id_seq FROM PUBLIC;

    DROP TRIGGER IF EXISTS bronze_append_only ON bronze.kiwoom_responses;
    CREATE TRIGGER bronze_append_only BEFORE UPDATE OR DELETE ON bronze.kiwoom_responses
        FOR EACH ROW EXECUTE FUNCTION bronze.refuse_change();
    DROP TRIGGER IF EXISTS bronze_no_truncate ON bronze.kiwoom_responses;
    CREATE TRIGGER bronze_no_truncate BEFORE TRUNCATE ON bronze.kiwoom_responses
        FOR EACH STATEMENT EXECUTE FUNCTION bronze.refuse_change();
END
$$;
