-- Source-only until isolated acceptance and controlled deployment.
-- This migration enables nothing and grants no service/user access.
CREATE TABLE specialist_observation_controls (
    tenant_id UUID PRIMARY KEY REFERENCES tenants(tenant_id),
    kill_switch BOOLEAN NOT NULL DEFAULT TRUE,
    policy_version TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE specialist_observation_grants (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    principal_id UUID NOT NULL,
    specialist_role TEXT NOT NULL CHECK (specialist_role IN
        ('data_analyst','data_scientist','data_engineer','financial_analyst','platform_operations','software_maintenance')),
    subject_ids TEXT[] NOT NULL DEFAULT '{}',
    resource_ids TEXT[] NOT NULL DEFAULT '{}',
    tool_names TEXT[] NOT NULL DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    background_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMPTZ NOT NULL,
    policy_version TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,principal_id),
    CHECK (cardinality(subject_ids)<=500 AND cardinality(resource_ids)<=500 AND cardinality(tool_names)<=50),
    CHECK (array_position(subject_ids,NULL) IS NULL AND array_position(resource_ids,NULL) IS NULL
           AND array_position(tool_names,NULL) IS NULL)
);
ALTER TABLE specialist_observation_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE specialist_observation_controls FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON specialist_observation_controls
    USING (tenant_id=app_tenant_id()) WITH CHECK (tenant_id=app_tenant_id());
ALTER TABLE specialist_observation_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE specialist_observation_grants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON specialist_observation_grants
    USING (tenant_id=app_tenant_id()) WITH CHECK (tenant_id=app_tenant_id());
REVOKE ALL ON specialist_observation_controls,specialist_observation_grants FROM PUBLIC;
DO $$
DECLARE recipient RECORD;
BEGIN
    FOR recipient IN SELECT c.relname,a.grantee FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname=current_schema() AND c.relname IN ('specialist_observation_controls','specialist_observation_grants')
        AND a.grantee<>c.relowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM %I CASCADE',current_schema(),recipient.relname,pg_get_userbyid(recipient.grantee));
    END LOOP;
END;
$$;
