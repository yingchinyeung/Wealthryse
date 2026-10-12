-- Source-only until isolated acceptance and controlled deployment.
-- Report contents are immutable; channel status is independent of content.
CREATE TABLE specialist_daily_reports (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    report_date DATE NOT NULL,
    report_sha256 TEXT NOT NULL CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
    report_json JSONB NOT NULL CHECK (jsonb_typeof(report_json)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,report_date)
);
CREATE FUNCTION guard_specialist_daily_report() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Daily report contents are immutable'; END;
$$;
CREATE TRIGGER specialist_daily_report_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
    ON specialist_daily_reports FOR EACH STATEMENT EXECUTE FUNCTION guard_specialist_daily_report();

CREATE TABLE specialist_report_deliveries (
    tenant_id UUID NOT NULL,
    report_date DATE NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('website','email')),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','leased','sending','accepted','delivered','uncertain','failed')),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts>=0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    provider_reference TEXT,
    last_error_code TEXT CHECK (last_error_code IS NULL OR last_error_code ~ '^[a-z][a-z0-9_]{0,79}$'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,report_date,channel),
    FOREIGN KEY (tenant_id,report_date) REFERENCES specialist_daily_reports(tenant_id,report_date),
    CHECK ((state IN ('leased','sending') AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (state NOT IN ('leased','sending') AND lease_token IS NULL AND lease_expires_at IS NULL))
);

ALTER TABLE specialist_daily_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE specialist_daily_reports FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON specialist_daily_reports
    USING (tenant_id=app_tenant_id()) WITH CHECK (tenant_id=app_tenant_id());
ALTER TABLE specialist_report_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE specialist_report_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON specialist_report_deliveries
    USING (tenant_id=app_tenant_id()) WITH CHECK (tenant_id=app_tenant_id());
REVOKE ALL ON specialist_daily_reports,specialist_report_deliveries FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_specialist_daily_report() FROM PUBLIC;
-- Strip inherited named default grants, just as the audit ledger does.
DO $$
DECLARE recipient RECORD;
BEGIN
    FOR recipient IN SELECT c.relname,a.grantee FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname=current_schema() AND c.relname IN ('specialist_daily_reports','specialist_report_deliveries')
        AND a.grantee<>c.relowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM %I CASCADE',current_schema(),recipient.relname,pg_get_userbyid(recipient.grantee));
    END LOOP;
    FOR recipient IN SELECT a.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
        LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        WHERE n.nspname=current_schema() AND p.proname='guard_specialist_daily_report' AND p.pronargs=0
        AND a.grantee<>p.proowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %I.guard_specialist_daily_report() FROM %I CASCADE',current_schema(),pg_get_userbyid(recipient.grantee));
    END LOOP;
END;
$$;
