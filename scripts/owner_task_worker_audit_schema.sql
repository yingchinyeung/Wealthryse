-- STEP 3 SOURCE ONLY; NOT an automatic production migration or enrollment.
-- Install in a separately reviewed isolated schema with a fixed search_path.
-- Grant SELECT/INSERT only to writer, SELECT only to separate verifier.
-- Roles, credentials, Azure grants and model/runtime activation are not created.
CREATE TABLE owner_codex_worker_audit_events (
    tenant_id UUID NOT NULL,
    owner_object_id UUID NOT NULL,
    task_id UUID NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('intent','outcome')),
    worker_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    binding_sha256 TEXT NOT NULL CHECK (binding_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 TEXT NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    envelope_json TEXT NOT NULL CHECK (octet_length(envelope_json) BETWEEN 1 AND 16384),
    PRIMARY KEY (tenant_id,owner_object_id,task_id,phase),
    UNIQUE (tenant_id,worker_id,attempt_id,phase)
);
ALTER TABLE owner_codex_worker_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_codex_worker_audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY worker_audit_isolation ON owner_codex_worker_audit_events
    USING (tenant_id=app_tenant_id() AND tenant_id::text=current_setting('app.tenant_id',true)
        AND worker_id::text=current_setting('app.execution_worker_id',true))
    WITH CHECK (tenant_id=app_tenant_id() AND tenant_id::text=current_setting('app.tenant_id',true)
        AND worker_id::text=current_setting('app.execution_worker_id',true));
REVOKE ALL ON owner_codex_worker_audit_events FROM PUBLIC;
CREATE FUNCTION guard_owner_worker_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Worker audit evidence is append only';
END;
$$;
CREATE TRIGGER owner_worker_audit_no_mutation
    BEFORE UPDATE OR DELETE OR TRUNCATE ON owner_codex_worker_audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION guard_owner_worker_audit();
REVOKE ALL ON FUNCTION guard_owner_worker_audit() FROM PUBLIC;
