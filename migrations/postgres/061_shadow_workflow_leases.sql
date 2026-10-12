-- Shadow metadata only: no production grants, dispatcher or source mutations.
CREATE TABLE workflow_lease_events (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    event_id UUID NOT NULL,
    cycle_key TEXT NOT NULL CHECK (length(cycle_key) BETWEEN 1 AND 80),
    subject_id TEXT NOT NULL CHECK (length(subject_id) BETWEEN 1 AND 80),
    stage TEXT NOT NULL CHECK (stage IN ('reconciliation','derived','risk','assurance')),
    sequence_no INTEGER NOT NULL CHECK (sequence_no>0),
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json)='object'
        AND payload_json @> '{"execution_authorized":false}'::jsonb),
    record_sha256 TEXT NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(tenant_id,event_id),
    UNIQUE(tenant_id,cycle_key,subject_id,stage,sequence_no)
);
ALTER TABLE workflow_lease_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_lease_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workflow_lease_events
    USING (tenant_id=app_tenant_id()) WITH CHECK (tenant_id=app_tenant_id());
CREATE TRIGGER workflow_leases_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON workflow_lease_events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_shadow_workflow_mutation();
REVOKE ALL ON workflow_lease_events FROM PUBLIC;
DO $$
DECLARE recipient RECORD;
BEGIN
    FOR recipient IN SELECT DISTINCT a.grantee FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname=current_schema() AND c.relname='workflow_lease_events'
          AND a.grantee<>c.relowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE %I.workflow_lease_events FROM %I CASCADE',
            current_schema(),pg_get_userbyid(recipient.grantee));
    END LOOP;
END;
$$;
