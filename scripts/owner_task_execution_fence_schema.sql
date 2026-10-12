-- STEP 3 SOURCE ONLY. Deliberately outside automatic production migrations.
-- Install only in a reviewed isolated schema after scoped role/RLS qualification.
-- No users, grants, inference activation or received financial-data operations.
CREATE TABLE owner_codex_execution_runtimes (
    tenant_id UUID NOT NULL,
    worker_id UUID NOT NULL,
    active_attempt_id UUID,
    quarantined BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (tenant_id,worker_id),
    CHECK (NOT quarantined OR active_attempt_id IS NOT NULL)
);
CREATE TABLE owner_codex_execution_fences (
    tenant_id UUID NOT NULL,
    owner_object_id UUID NOT NULL,
    task_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    worker_id UUID NOT NULL,
    lease_token UUID NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    intent_binding_sha256 TEXT NOT NULL CHECK (intent_binding_sha256 ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('reserved','intent_verified','launch_reserved','settled','quarantined')),
    intent_receipt_sha256 TEXT CHECK (intent_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    outcome_receipt_sha256 TEXT CHECK (outcome_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (tenant_id,owner_object_id,task_id),
    UNIQUE (tenant_id,worker_id,attempt_id),
    CHECK (state NOT IN ('intent_verified','launch_reserved') OR intent_receipt_sha256 IS NOT NULL),
    CHECK ((state='settled') = (outcome_receipt_sha256 IS NOT NULL))
);
CREATE TABLE owner_codex_execution_fence_events (
    tenant_id UUID NOT NULL,
    owner_object_id UUID NOT NULL,
    task_id UUID NOT NULL,
    worker_id UUID NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('reserved','intent_verified','launch_reserved','settled','quarantined')),
    binding_sha256 TEXT NOT NULL CHECK (binding_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (tenant_id,owner_object_id,task_id,state),
    FOREIGN KEY (tenant_id,owner_object_id,task_id) REFERENCES owner_codex_execution_fences(tenant_id,owner_object_id,task_id)
);
ALTER TABLE owner_codex_execution_runtimes ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_codex_execution_runtimes FORCE ROW LEVEL SECURITY;
ALTER TABLE owner_codex_execution_fences ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_codex_execution_fences FORCE ROW LEVEL SECURITY;
ALTER TABLE owner_codex_execution_fence_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_codex_execution_fence_events FORCE ROW LEVEL SECURITY;
CREATE POLICY worker_isolation ON owner_codex_execution_runtimes
    USING (tenant_id=app_tenant_id() AND tenant_id::text=current_setting('app.tenant_id',true)
        AND worker_id::text=current_setting('app.execution_worker_id',true))
    WITH CHECK (tenant_id=app_tenant_id() AND tenant_id::text=current_setting('app.tenant_id',true)
        AND worker_id::text=current_setting('app.execution_worker_id',true));
CREATE POLICY worker_isolation ON owner_codex_execution_fences
    USING (tenant_id=app_tenant_id() AND tenant_id::text=current_setting('app.tenant_id',true)
        AND worker_id::text=current_setting('app.execution_worker_id',true))
    WITH CHECK (tenant_id=app_tenant_id() AND tenant_id::text=current_setting('app.tenant_id',true)
        AND worker_id::text=current_setting('app.execution_worker_id',true));
CREATE POLICY worker_isolation ON owner_codex_execution_fence_events
    USING (tenant_id=app_tenant_id() AND tenant_id::text=current_setting('app.tenant_id',true)
        AND worker_id::text=current_setting('app.execution_worker_id',true))
    WITH CHECK (tenant_id=app_tenant_id() AND tenant_id::text=current_setting('app.tenant_id',true)
        AND worker_id::text=current_setting('app.execution_worker_id',true));
REVOKE ALL ON owner_codex_execution_runtimes,owner_codex_execution_fences,owner_codex_execution_fence_events FROM PUBLIC;
CREATE FUNCTION guard_owner_execution_fence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE','TRUNCATE') THEN RAISE EXCEPTION 'Execution reservations cannot be deleted'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'reserved' OR NEW.intent_receipt_sha256 IS NOT NULL OR NEW.outcome_receipt_sha256 IS NOT NULL THEN
            RAISE EXCEPTION 'Execution must begin reserved';
        END IF;
    ELSE
        IF ROW(NEW.tenant_id,NEW.owner_object_id,NEW.task_id,NEW.attempt_id,NEW.worker_id,NEW.lease_token,
               NEW.request_sha256,NEW.intent_binding_sha256)
           IS DISTINCT FROM ROW(OLD.tenant_id,OLD.owner_object_id,OLD.task_id,OLD.attempt_id,OLD.worker_id,
               OLD.lease_token,OLD.request_sha256,OLD.intent_binding_sha256)
           OR NOT ((OLD.state='reserved' AND NEW.state IN ('intent_verified','settled','quarantined'))
               OR (OLD.state='intent_verified' AND NEW.state IN ('launch_reserved','settled','quarantined'))
               OR (OLD.state='launch_reserved' AND NEW.state IN ('settled','quarantined')))
           OR (OLD.intent_receipt_sha256 IS NOT NULL AND NEW.intent_receipt_sha256 IS DISTINCT FROM OLD.intent_receipt_sha256) THEN
            RAISE EXCEPTION 'Execution cannot be rebound or rearmed';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER owner_execution_fence_guard BEFORE INSERT OR UPDATE ON owner_codex_execution_fences
    FOR EACH ROW EXECUTE FUNCTION guard_owner_execution_fence();
CREATE TRIGGER owner_execution_fence_no_delete BEFORE DELETE OR TRUNCATE ON owner_codex_execution_fences
    FOR EACH STATEMENT EXECUTE FUNCTION guard_owner_execution_fence();
CREATE FUNCTION guard_owner_execution_runtime() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE matching BOOLEAN;
BEGIN
    IF TG_OP IN ('DELETE','TRUNCATE') THEN RAISE EXCEPTION 'Runtime fence cannot be deleted'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.active_attempt_id IS NOT NULL OR NEW.quarantined THEN RAISE EXCEPTION 'Runtime must begin idle'; END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.tenant_id,NEW.worker_id) IS DISTINCT FROM ROW(OLD.tenant_id,OLD.worker_id)
       OR OLD.quarantined OR (OLD.active_attempt_id IS NOT NULL AND NEW.active_attempt_id IS NOT NULL
           AND OLD.active_attempt_id IS DISTINCT FROM NEW.active_attempt_id) THEN
        RAISE EXCEPTION 'Runtime cannot be rebound or rearmed';
    END IF;
    EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.owner_codex_execution_fences WHERE tenant_id=$1 AND worker_id=$2 AND attempt_id=$3 AND state=$4)', TG_TABLE_SCHEMA)
        INTO matching USING NEW.tenant_id,NEW.worker_id,coalesce(NEW.active_attempt_id,OLD.active_attempt_id),
        CASE WHEN NEW.quarantined THEN 'quarantined' WHEN NEW.active_attempt_id IS NULL THEN 'settled' ELSE 'reserved' END;
    IF NOT matching THEN RAISE EXCEPTION 'Matching task fence required'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER owner_execution_runtime_guard BEFORE INSERT OR UPDATE ON owner_codex_execution_runtimes
    FOR EACH ROW EXECUTE FUNCTION guard_owner_execution_runtime();
CREATE TRIGGER owner_execution_runtime_no_delete BEFORE DELETE OR TRUNCATE ON owner_codex_execution_runtimes
    FOR EACH STATEMENT EXECUTE FUNCTION guard_owner_execution_runtime();
CREATE FUNCTION guard_owner_execution_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Execution events are append only'; END;
$$;
CREATE TRIGGER owner_execution_events_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON owner_codex_execution_fence_events
    FOR EACH STATEMENT EXECUTE FUNCTION guard_owner_execution_event();
REVOKE ALL ON FUNCTION guard_owner_execution_fence(),guard_owner_execution_runtime(),guard_owner_execution_event() FROM PUBLIC;
