-- Additive shadow ledger only. Does not dispatch, approve, or modify schedules.
CREATE TABLE workflow_stage_evidence (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    evidence_id UUID NOT NULL,
    cycle_key TEXT NOT NULL CHECK (length(cycle_key) BETWEEN 1 AND 80),
    subject_id TEXT NOT NULL CHECK (length(subject_id) BETWEEN 1 AND 80),
    stage TEXT NOT NULL CHECK (stage IN ('ingestion','reconciliation','benchmarks_fx','derived','risk','reports','assurance')),
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    status TEXT NOT NULL CHECK (status IN ('succeeded','unchanged','failed')),
    validated BOOLEAN NOT NULL,
    through_date DATE NOT NULL,
    input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    output_sha256 TEXT NOT NULL CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
    dependency_ids_json JSONB NOT NULL CHECK (jsonb_typeof(dependency_ids_json)='object'),
    producer TEXT NOT NULL CHECK (length(producer) BETWEEN 1 AND 100),
    policy_version TEXT NOT NULL,
    record_sha256 TEXT NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,evidence_id),
    UNIQUE (tenant_id,cycle_key,subject_id,stage,sequence_no),
    CHECK (status <> 'failed' OR NOT validated)
);

CREATE TABLE workflow_shadow_decisions (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    decision_id UUID NOT NULL,
    cycle_key TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    expected_date DATE NOT NULL,
    ready BOOLEAN NOT NULL,
    reasons_json JSONB NOT NULL CHECK (jsonb_typeof(reasons_json)='array'),
    evidence_ids_json JSONB NOT NULL CHECK (jsonb_typeof(evidence_ids_json)='object'),
    decision_sha256 TEXT NOT NULL CHECK (decision_sha256 ~ '^[0-9a-f]{64}$'),
    policy_version TEXT NOT NULL,
    execution_authorized BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_authorized),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,decision_id),
    UNIQUE (tenant_id,decision_sha256)
);

ALTER TABLE workflow_stage_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_stage_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workflow_stage_evidence
    USING (tenant_id=app_tenant_id()) WITH CHECK (tenant_id=app_tenant_id());
ALTER TABLE workflow_shadow_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_shadow_decisions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workflow_shadow_decisions
    USING (tenant_id=app_tenant_id()) WITH CHECK (tenant_id=app_tenant_id());
REVOKE ALL ON workflow_stage_evidence,workflow_shadow_decisions FROM PUBLIC;

CREATE FUNCTION reject_shadow_workflow_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Workflow evidence is append-only; append a correction instead';
END;
$$;
REVOKE ALL ON FUNCTION reject_shadow_workflow_mutation() FROM PUBLIC;
CREATE TRIGGER workflow_evidence_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON workflow_stage_evidence
    FOR EACH STATEMENT EXECUTE FUNCTION reject_shadow_workflow_mutation();
CREATE TRIGGER workflow_decisions_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON workflow_shadow_decisions
    FOR EACH STATEMENT EXECUTE FUNCTION reject_shadow_workflow_mutation();

-- Named default ACL grants survive REVOKE FROM PUBLIC; remove them as well.
DO $$
DECLARE target RECORD; recipient RECORD;
BEGIN
    FOR target IN SELECT c.oid,c.relname,c.relowner FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema()
        AND c.relname IN ('workflow_stage_evidence','workflow_shadow_decisions')
    LOOP
        FOR recipient IN SELECT DISTINCT a.grantee FROM pg_class c,
            LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
            WHERE c.oid=target.oid AND a.grantee<>target.relowner AND a.grantee<>0
        LOOP
            EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM %I CASCADE',current_schema(),target.relname,
                           pg_get_userbyid(recipient.grantee));
        END LOOP;
    END LOOP;
    FOR recipient IN SELECT DISTINCT a.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
        LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        WHERE n.nspname=current_schema() AND p.proname='reject_shadow_workflow_mutation' AND p.pronargs=0
        AND a.grantee<>p.proowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %I.reject_shadow_workflow_mutation() FROM %I CASCADE',
                       current_schema(),pg_get_userbyid(recipient.grantee));
    END LOOP;
END;
$$;
