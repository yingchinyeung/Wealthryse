-- SOURCE ONLY. No login, role, grant, runtime activation or financial/source writes.
-- app.owner_object_id is set LOCAL by the trusted, freshly authorizing service.
-- A future restricted login/grant and real PostgreSQL isolation acceptance are
-- required; a caller-selectable session setting is not an authentication system.
CREATE TABLE owner_codex_sessions (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    owner_object_id UUID NOT NULL,
    session_id UUID NOT NULL,
    idempotency_key UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,owner_object_id,session_id),
    UNIQUE (tenant_id,owner_object_id,idempotency_key)
);

CREATE TABLE owner_codex_tasks (
    tenant_id UUID NOT NULL,
    owner_object_id UUID NOT NULL,
    task_id UUID NOT NULL,
    session_id UUID NOT NULL,
    idempotency_key UUID NOT NULL,
    task_kind TEXT NOT NULL CHECK (task_kind='performance_series_review'),
    subject_ids JSONB NOT NULL CHECK (jsonb_typeof(subject_ids)='array'
        AND jsonb_array_length(subject_ids) BETWEEN 1 AND 50),
    input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    authority_sha256 TEXT NOT NULL CHECK (authority_sha256 ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN
        ('queued','running','cancel_requested','needs_reconciliation','succeeded','failed','cancelled')),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    worker_id UUID,
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    intent_receipt_sha256 TEXT CHECK (intent_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    outcome_receipt_sha256 TEXT CHECK (outcome_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    result_sha256 TEXT CHECK (result_sha256 ~ '^[0-9a-f]{64}$'),
    error_code TEXT CHECK (error_code IS NULL OR error_code='worker_failed'),
    delivery_available BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,owner_object_id,task_id),
    UNIQUE (tenant_id,owner_object_id,idempotency_key),
    FOREIGN KEY (tenant_id,owner_object_id,session_id)
        REFERENCES owner_codex_sessions(tenant_id,owner_object_id,session_id),
    CHECK ((state IN ('running','cancel_requested') AND worker_id IS NOT NULL
        AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND intent_receipt_sha256 IS NOT NULL)
        OR (state NOT IN ('running','cancel_requested') AND worker_id IS NULL
        AND lease_token IS NULL AND lease_expires_at IS NULL)),
    CHECK (NOT delivery_available OR (state IN ('succeeded','failed','cancelled')
        AND intent_receipt_sha256 IS NOT NULL AND outcome_receipt_sha256 IS NOT NULL)),
    CHECK ((state='succeeded' AND result_sha256 IS NOT NULL AND delivery_available)
        OR (state<>'succeeded' AND result_sha256 IS NULL)),
    CHECK ((state='failed' AND error_code IS NOT NULL AND error_code='worker_failed')
        OR (state<>'failed' AND error_code IS NULL)),
    CHECK (state<>'cancel_requested' OR cancel_requested_at IS NOT NULL),
    CHECK (state<>'succeeded' OR cancel_requested_at IS NULL),
    CHECK (state NOT IN ('failed','succeeded') OR delivery_available)
);
CREATE INDEX owner_codex_tasks_owner_history ON owner_codex_tasks
    (tenant_id,owner_object_id,session_id,created_at DESC,task_id DESC);
CREATE INDEX owner_codex_tasks_leases ON owner_codex_tasks
    (tenant_id,owner_object_id,lease_expires_at) WHERE state IN ('running','cancel_requested');

CREATE TABLE owner_codex_task_events (
    tenant_id UUID NOT NULL,
    owner_object_id UUID NOT NULL,
    task_id UUID NOT NULL,
    event_no BIGINT NOT NULL CHECK (event_no>=1),
    state TEXT NOT NULL CHECK (state IN
        ('queued','running','cancel_requested','needs_reconciliation','succeeded','failed','cancelled')),
    code TEXT NOT NULL CHECK (code IN ('queued','audit_intent_verified','running','heartbeat',
        'cancel_requested','needs_reconciliation','succeeded','failed','cancelled','delivery_ready')),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,owner_object_id,task_id,event_no),
    FOREIGN KEY (tenant_id,owner_object_id,task_id)
        REFERENCES owner_codex_tasks(tenant_id,owner_object_id,task_id)
);

ALTER TABLE owner_codex_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_codex_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_isolation ON owner_codex_sessions
    USING (tenant_id=app_tenant_id() AND owner_object_id::text=current_setting('app.owner_object_id',true)
        AND tenant_id::text=current_setting('app.tenant_id',true))
    WITH CHECK (tenant_id=app_tenant_id() AND owner_object_id::text=current_setting('app.owner_object_id',true)
        AND tenant_id::text=current_setting('app.tenant_id',true));
ALTER TABLE owner_codex_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_codex_tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_isolation ON owner_codex_tasks
    USING (tenant_id=app_tenant_id() AND owner_object_id::text=current_setting('app.owner_object_id',true)
        AND tenant_id::text=current_setting('app.tenant_id',true))
    WITH CHECK (tenant_id=app_tenant_id() AND owner_object_id::text=current_setting('app.owner_object_id',true)
        AND tenant_id::text=current_setting('app.tenant_id',true));
ALTER TABLE owner_codex_task_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_codex_task_events FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_isolation ON owner_codex_task_events
    USING (tenant_id=app_tenant_id() AND owner_object_id::text=current_setting('app.owner_object_id',true)
        AND tenant_id::text=current_setting('app.tenant_id',true))
    WITH CHECK (tenant_id=app_tenant_id() AND owner_object_id::text=current_setting('app.owner_object_id',true)
        AND tenant_id::text=current_setting('app.tenant_id',true));
REVOKE ALL ON owner_codex_sessions,owner_codex_tasks,owner_codex_task_events FROM PUBLIC;

CREATE FUNCTION guard_owner_codex_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Owner task history cannot be rewritten'; END;
$$;
CREATE TRIGGER owner_codex_sessions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
    ON owner_codex_sessions FOR EACH STATEMENT EXECUTE FUNCTION guard_owner_codex_append_only();
CREATE TRIGGER owner_codex_events_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
    ON owner_codex_task_events FOR EACH STATEMENT EXECUTE FUNCTION guard_owner_codex_append_only();
CREATE TRIGGER owner_codex_tasks_no_delete BEFORE DELETE OR TRUNCATE
    ON owner_codex_tasks FOR EACH STATEMENT EXECUTE FUNCTION guard_owner_codex_append_only();

CREATE FUNCTION guard_owner_codex_task() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item JSONB; previous_value TEXT := ''; current_tenant UUID;
BEGIN
    EXECUTE format('SELECT %I.app_tenant_id()', TG_TABLE_SCHEMA) INTO current_tenant;
    IF NEW.tenant_id IS DISTINCT FROM current_tenant
       OR NEW.tenant_id::text IS DISTINCT FROM current_setting('app.tenant_id',true)
       OR NEW.owner_object_id::text IS DISTINCT FROM current_setting('app.owner_object_id',true) THEN
        RAISE EXCEPTION 'Owner task scope mismatch';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(NEW.subject_ids) LOOP
        IF jsonb_typeof(item)<>'string' OR (item #>> '{}') !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$'
           OR (item #>> '{}') COLLATE "C" <= previous_value COLLATE "C" THEN
            RAISE EXCEPTION 'Canonical registered subject identifiers required';
        END IF;
        previous_value := item #>> '{}';
    END LOOP;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'queued' OR NEW.revision<>1 OR NEW.delivery_available
           OR NEW.intent_receipt_sha256 IS NOT NULL OR NEW.outcome_receipt_sha256 IS NOT NULL
           OR NEW.cancel_requested_at IS NOT NULL THEN
            RAISE EXCEPTION 'Owner tasks must begin queued without execution evidence';
        END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.tenant_id,NEW.owner_object_id,NEW.task_id,NEW.session_id,NEW.idempotency_key,
           NEW.task_kind,NEW.subject_ids,NEW.input_sha256,NEW.request_sha256,NEW.authority_sha256,NEW.created_at)
       IS DISTINCT FROM ROW(OLD.tenant_id,OLD.owner_object_id,OLD.task_id,OLD.session_id,OLD.idempotency_key,
           OLD.task_kind,OLD.subject_ids,OLD.input_sha256,OLD.request_sha256,OLD.authority_sha256,OLD.created_at)
       OR NEW.revision<>OLD.revision+1 OR NEW.updated_at<OLD.updated_at THEN
        RAISE EXCEPTION 'Immutable owner request or revision mismatch';
    END IF;
    IF OLD.intent_receipt_sha256 IS NOT NULL
       AND NEW.intent_receipt_sha256 IS DISTINCT FROM OLD.intent_receipt_sha256 THEN
        RAISE EXCEPTION 'Protected intent binding cannot be replaced';
    END IF;
    IF OLD.cancel_requested_at IS NOT NULL
       AND NEW.cancel_requested_at IS DISTINCT FROM OLD.cancel_requested_at THEN
        RAISE EXCEPTION 'Owner cancellation intent cannot be cleared or replaced';
    END IF;
    IF NEW.state='succeeded' AND NEW.cancel_requested_at IS NOT NULL THEN
        RAISE EXCEPTION 'A cancelled request cannot deliver a successful result';
    END IF;
    IF NOT ((OLD.state='queued' AND NEW.state IN ('queued','running','cancelled'))
        OR (OLD.state='running' AND NEW.state IN ('running','cancel_requested','needs_reconciliation','succeeded','failed'))
        OR (OLD.state='cancel_requested' AND NEW.state IN ('needs_reconciliation','failed','cancelled'))
        OR (OLD.state='needs_reconciliation' AND NEW.state IN ('succeeded','failed','cancelled'))
        OR (OLD.state='needs_reconciliation' AND NEW.state='needs_reconciliation'
            AND OLD.cancel_requested_at IS NULL AND NEW.cancel_requested_at IS NOT NULL)
        OR (OLD.state='cancelled' AND NEW.state='cancelled' AND NOT OLD.delivery_available AND NEW.delivery_available)) THEN
        RAISE EXCEPTION 'Owner task transition rejected';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER owner_codex_task_guard BEFORE INSERT OR UPDATE ON owner_codex_tasks
    FOR EACH ROW EXECUTE FUNCTION guard_owner_codex_task();

CREATE FUNCTION guard_owner_codex_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_task RECORD;
BEGIN
    EXECUTE format('SELECT revision,state FROM %I.owner_codex_tasks
        WHERE tenant_id=$1 AND owner_object_id=$2 AND task_id=$3', TG_TABLE_SCHEMA)
        INTO current_task USING NEW.tenant_id,NEW.owner_object_id,NEW.task_id;
    IF current_task.revision IS NULL OR NEW.event_no<>current_task.revision OR NEW.state<>current_task.state
       OR NOT ((NEW.state='queued' AND NEW.code IN ('queued','audit_intent_verified'))
          OR (NEW.state='running' AND NEW.code IN ('running','heartbeat'))
          OR (NEW.state=NEW.code AND NEW.state IN ('cancel_requested','needs_reconciliation','succeeded','failed','cancelled'))
          OR (NEW.state='needs_reconciliation' AND NEW.code='cancel_requested')
          OR (NEW.state='cancelled' AND NEW.code='delivery_ready')) THEN
        RAISE EXCEPTION 'Owner status event does not match the task revision';
    END IF;
    NEW.occurred_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER owner_codex_event_guard BEFORE INSERT ON owner_codex_task_events
    FOR EACH ROW EXECUTE FUNCTION guard_owner_codex_event();

CREATE FUNCTION guard_owner_codex_event_presence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE present BOOLEAN;
BEGIN
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.owner_codex_task_events WHERE tenant_id=$1
        AND owner_object_id=$2 AND task_id=$3 AND event_no=$4 AND state=$5)', TG_TABLE_SCHEMA)
        INTO present USING NEW.tenant_id,NEW.owner_object_id,NEW.task_id,NEW.revision,NEW.state;
    IF NOT present THEN
        RAISE EXCEPTION 'Every owner task revision requires an atomic status event';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER owner_codex_revision_event AFTER INSERT OR UPDATE ON owner_codex_tasks
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_owner_codex_event_presence();
REVOKE ALL ON FUNCTION guard_owner_codex_append_only(),guard_owner_codex_task(),
    guard_owner_codex_event(),guard_owner_codex_event_presence() FROM PUBLIC;

-- Also strip named grants inherited from ALTER DEFAULT PRIVILEGES.
DO $$
DECLARE recipient RECORD;
BEGIN
    FOR recipient IN SELECT c.relname,a.grantee FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname=current_schema() AND c.relname IN
        ('owner_codex_sessions','owner_codex_tasks','owner_codex_task_events')
        AND a.grantee<>c.relowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM %I CASCADE',current_schema(),recipient.relname,pg_get_userbyid(recipient.grantee));
    END LOOP;
    FOR recipient IN SELECT p.proname,a.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
        LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        WHERE n.nspname=current_schema() AND p.proname IN ('guard_owner_codex_append_only',
        'guard_owner_codex_task','guard_owner_codex_event','guard_owner_codex_event_presence') AND p.pronargs=0
        AND a.grantee<>p.proowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %I.%I() FROM %I CASCADE',current_schema(),recipient.proname,pg_get_userbyid(recipient.grantee));
    END LOOP;
END;
$$;
