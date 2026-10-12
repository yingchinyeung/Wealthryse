-- Additive isolated qualification constraints; never rewrites the accepted DDL.
ALTER TABLE owner_codex_execution_fences ADD CONSTRAINT owner_fence_exact_worker_scope
    UNIQUE (tenant_id,owner_object_id,task_id,worker_id);
ALTER TABLE owner_codex_execution_fence_events ADD CONSTRAINT owner_fence_event_worker_binding
    FOREIGN KEY (tenant_id,owner_object_id,task_id,worker_id)
    REFERENCES owner_codex_execution_fences(tenant_id,owner_object_id,task_id,worker_id);
CREATE FUNCTION guard_owner_execution_event_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE matching BOOLEAN;
BEGIN
    EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.owner_codex_execution_fences WHERE tenant_id=$1 AND owner_object_id=$2 AND task_id=$3 AND worker_id=$4 AND state=$5)', TG_TABLE_SCHEMA)
        INTO matching USING NEW.tenant_id,NEW.owner_object_id,NEW.task_id,NEW.worker_id,NEW.state;
    IF NOT matching THEN RAISE EXCEPTION 'Execution event must match current worker state'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER owner_execution_event_insert_guard BEFORE INSERT ON owner_codex_execution_fence_events
    FOR EACH ROW EXECUTE FUNCTION guard_owner_execution_event_insert();
REVOKE ALL ON FUNCTION guard_owner_execution_event_insert() FROM PUBLIC;
