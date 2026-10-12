-- Source-only rollout: no application grants, no runtime enablement, no IBKR updates.
CREATE TABLE specialist_audit_events (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    sequence_no BIGINT NOT NULL CHECK (sequence_no > 0),
    event_id TEXT NOT NULL CHECK (event_id ~ '^[A-Za-z0-9-]{1,80}$'),
    event_json JSONB NOT NULL CHECK (jsonb_typeof(event_json)='object'),
    previous_sha256 TEXT NOT NULL CHECK (previous_sha256 ~ '^[0-9a-f]{64}$'),
    event_sha256 TEXT NOT NULL CHECK (event_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,sequence_no),
    UNIQUE (tenant_id,event_id),
    CHECK (event_json->>'event_id'=event_id),
    CHECK ((event_json->>'sequence')::bigint=sequence_no),
    CHECK (event_json->>'previous_sha256'=previous_sha256),
    CHECK (event_json->>'sha256'=event_sha256),
    CHECK (event_json ?& ARRAY['event_id','sequence','previous_sha256','sha256','occurred_at',
        'role','action','operation_id','phase','evidence_sha256','cost_microusd']),
    CHECK (event_json - ARRAY['event_id','sequence','previous_sha256','sha256','occurred_at',
        'role','action','operation_id','phase','evidence_sha256','cost_microusd'] = '{}'::jsonb),
    CHECK (jsonb_typeof(event_json->'role')='string' AND event_json->>'role' IN
        ('data_analyst','data_scientist','data_engineer','financial_analyst','platform_operations','software_maintenance')),
    CHECK (jsonb_typeof(event_json->'phase')='string' AND event_json->>'phase' IN
        ('started','succeeded','failed','denied','cancelled','rolled_back')),
    CHECK (jsonb_typeof(event_json->'action')='string' AND event_json->>'action' ~ '^[a-z][a-z0-9_]{0,79}$'),
    CHECK (jsonb_typeof(event_json->'operation_id')='string' AND event_json->>'operation_id' ~ '^[A-Za-z0-9-]{1,80}$'),
    CHECK (jsonb_typeof(event_json->'occurred_at')='string'),
    CHECK (jsonb_typeof(event_json->'sequence')='number'),
    CHECK (jsonb_typeof(event_json->'evidence_sha256')='string' AND event_json->>'evidence_sha256' ~ '^[0-9a-f]{64}$'),
    CHECK (jsonb_typeof(event_json->'cost_microusd')='number' AND event_json->>'cost_microusd' ~ '^[0-9]+$')
);

ALTER TABLE specialist_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE specialist_audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON specialist_audit_events
    USING (tenant_id=app_tenant_id()) WITH CHECK (tenant_id=app_tenant_id());
REVOKE ALL ON specialist_audit_events FROM PUBLIC;

CREATE FUNCTION guard_specialist_audit() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE prior RECORD;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Specialist audit is append-only';
    END IF;
    IF NEW.tenant_id IS DISTINCT FROM app_tenant_id() THEN
        RAISE EXCEPTION 'Specialist audit tenant mismatch';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('specialist-audit:' || NEW.tenant_id::text, 0));
    EXECUTE format('SELECT sequence_no,event_sha256 FROM %I.specialist_audit_events
        WHERE tenant_id=$1 ORDER BY sequence_no DESC LIMIT 1', TG_TABLE_SCHEMA)
        INTO prior USING NEW.tenant_id;
    IF NEW.sequence_no <> coalesce(prior.sequence_no,0)+1
       OR NEW.previous_sha256 <> coalesce(prior.event_sha256,repeat('0',64)) THEN
        RAISE EXCEPTION 'Specialist audit sequence or previous hash mismatch';
    END IF;
    NEW.recorded_at := clock_timestamp();
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION guard_specialist_audit() FROM PUBLIC;
CREATE TRIGGER specialist_audit_insert_guard BEFORE INSERT ON specialist_audit_events
    FOR EACH ROW EXECUTE FUNCTION guard_specialist_audit();
CREATE TRIGGER specialist_audit_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON specialist_audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION guard_specialist_audit();

-- Remove named grants inherited from existing ALTER DEFAULT PRIVILEGES too.
DO $$
DECLARE recipient RECORD;
BEGIN
    FOR recipient IN SELECT DISTINCT a.grantee FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname=current_schema() AND c.relname='specialist_audit_events'
        AND a.grantee<>c.relowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE %I.specialist_audit_events FROM %I CASCADE',
            current_schema(),pg_get_userbyid(recipient.grantee));
    END LOOP;
    FOR recipient IN SELECT DISTINCT a.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
        LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        WHERE n.nspname=current_schema() AND p.proname='guard_specialist_audit' AND p.pronargs=0
        AND a.grantee<>p.proowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %I.guard_specialist_audit() FROM %I CASCADE',
            current_schema(),pg_get_userbyid(recipient.grantee));
    END LOOP;
END;
$$;
