-- Additive shadow-only metadata. No dispatch, source mutation or schedule change.
CREATE TABLE workflow_cycle_date_policies (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    policy_id UUID NOT NULL,
    cycle_key TEXT NOT NULL CHECK (length(cycle_key) BETWEEN 1 AND 80),
    subject_id TEXT NOT NULL CHECK (length(subject_id) BETWEEN 1 AND 80),
    sequence_no INTEGER NOT NULL CHECK (sequence_no>0),
    cycle_cutoff DATE NOT NULL,
    calendar_version TEXT NOT NULL,
    expected_dates_json JSONB NOT NULL CHECK (jsonb_typeof(expected_dates_json)='object'),
    policy_version TEXT NOT NULL,
    record_sha256 TEXT NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    execution_authorized BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_authorized),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,policy_id),
    UNIQUE(tenant_id,cycle_key,subject_id,sequence_no)
);
ALTER TABLE workflow_cycle_date_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_cycle_date_policies FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workflow_cycle_date_policies
    USING (tenant_id=app_tenant_id()) WITH CHECK (tenant_id=app_tenant_id());
REVOKE ALL ON workflow_cycle_date_policies FROM PUBLIC;
CREATE TRIGGER workflow_dates_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON workflow_cycle_date_policies
    FOR EACH STATEMENT EXECUTE FUNCTION reject_shadow_workflow_mutation();
-- Named default ACLs are not cleared by revoking PUBLIC.
DO $$
DECLARE recipient RECORD;
BEGIN
    FOR recipient IN SELECT DISTINCT a.grantee FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname=current_schema() AND c.relname='workflow_cycle_date_policies'
          AND a.grantee<>c.relowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE %I.workflow_cycle_date_policies FROM %I CASCADE',
            current_schema(),pg_get_userbyid(recipient.grantee));
    END LOOP;
END;
$$;
ALTER TABLE workflow_stage_evidence
    ADD COLUMN date_policy_id UUID,
    ADD COLUMN date_policy_sha256 TEXT,
    ADD CONSTRAINT workflow_evidence_dates_fk FOREIGN KEY(tenant_id,date_policy_id)
        REFERENCES workflow_cycle_date_policies(tenant_id,policy_id),
    ADD CONSTRAINT workflow_evidence_dates_pair CHECK (
        (date_policy_id IS NULL AND date_policy_sha256 IS NULL) OR
        (date_policy_id IS NOT NULL AND date_policy_sha256 IS NOT NULL AND date_policy_sha256 ~ '^[0-9a-f]{64}$'));
ALTER TABLE workflow_shadow_decisions
    ADD COLUMN date_policy_id UUID,
    ADD COLUMN date_policy_sha256 TEXT,
    ADD CONSTRAINT workflow_decisions_dates_fk FOREIGN KEY(tenant_id,date_policy_id)
        REFERENCES workflow_cycle_date_policies(tenant_id,policy_id),
    ADD CONSTRAINT workflow_decisions_dates_pair CHECK (
        (date_policy_id IS NULL AND date_policy_sha256 IS NULL) OR
        (date_policy_id IS NOT NULL AND date_policy_sha256 IS NOT NULL AND date_policy_sha256 ~ '^[0-9a-f]{64}$'));
