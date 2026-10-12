-- Additive evidence storage only: no legacy updates or active-reader cutover.
-- Importers must verify archive bytes, canonical keys and JSON content digests.
CREATE TABLE fx_evidence_runs (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    source_namespace TEXT NOT NULL CHECK (length(source_namespace) BETWEEN 1 AND 80),
    source_run_id BIGINT NOT NULL CHECK (source_run_id > 0),
    archive_sha256 TEXT NOT NULL CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
    run_status TEXT NOT NULL CHECK (run_status='success'),
    source_completed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id,account_id,source_namespace,source_run_id)
);

CREATE TABLE fx_evidence_versions (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    canonical_key TEXT NOT NULL CHECK (canonical_key ~ '^fx-rate:v2:[0-9a-f]{64}$'),
    row_sha256 TEXT NOT NULL CHECK (row_sha256 ~ '^[0-9a-f]{64}$'),
    section TEXT NOT NULL CHECK (length(section) BETWEEN 1 AND 100),
    record_type TEXT NOT NULL CHECK (lower(record_type) IN ('conversionrate','fxrate','currencyconversionrate')),
    report_date DATE NOT NULL,
    data_json JSONB NOT NULL CHECK (jsonb_typeof(data_json)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,account_id,canonical_key,row_sha256)
);

CREATE TABLE fx_evidence_observations (
    tenant_id UUID NOT NULL,
    account_id TEXT NOT NULL,
    source_namespace TEXT NOT NULL,
    source_run_id BIGINT NOT NULL,
    canonical_key TEXT NOT NULL,
    row_sha256 TEXT NOT NULL,
    PRIMARY KEY (tenant_id,account_id,source_namespace,source_run_id,canonical_key),
    UNIQUE (tenant_id,account_id,source_namespace,source_run_id,canonical_key,row_sha256),
    FOREIGN KEY (tenant_id,account_id,source_namespace,source_run_id)
        REFERENCES fx_evidence_runs(tenant_id,account_id,source_namespace,source_run_id),
    FOREIGN KEY (tenant_id,account_id,canonical_key,row_sha256)
        REFERENCES fx_evidence_versions(tenant_id,account_id,canonical_key,row_sha256)
);

CREATE TABLE fx_evidence_legacy_aliases (
    tenant_id UUID NOT NULL,
    account_id TEXT NOT NULL,
    source_namespace TEXT NOT NULL,
    legacy_section TEXT NOT NULL,
    legacy_record_type TEXT NOT NULL,
    legacy_key TEXT NOT NULL,
    row_sha256 TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    source_run_id BIGINT NOT NULL,
    legacy_first_seen_run_id BIGINT NOT NULL CHECK (legacy_first_seen_run_id > 0),
    source_snapshot_sha256 TEXT NOT NULL CHECK (source_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (tenant_id,account_id,source_namespace,legacy_section,legacy_record_type,legacy_key,row_sha256,source_snapshot_sha256),
    FOREIGN KEY (tenant_id,account_id,source_namespace,source_run_id,canonical_key,row_sha256)
        REFERENCES fx_evidence_observations(tenant_id,account_id,source_namespace,source_run_id,canonical_key,row_sha256)
);

CREATE TABLE fx_evidence_batches (
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    bundle_sha256 TEXT NOT NULL CHECK (bundle_sha256 ~ '^[0-9a-f]{64}$'),
    source_snapshot_sha256 TEXT NOT NULL CHECK (source_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    receipt_json JSONB NOT NULL CHECK (jsonb_typeof(receipt_json)='object'),
    execution_authorized BOOLEAN NOT NULL DEFAULT false CHECK (NOT execution_authorized),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,account_id,bundle_sha256)
);

CREATE FUNCTION reject_fx_evidence_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'FX evidence is append-only; retain prior versions';
END;
$$;

-- Default privileges may have granted named application roles access at CREATE.
-- Keep these unactivated evidence objects owner-only, not merely non-PUBLIC.
DO $$
DECLARE target RECORD; recipient RECORD;
BEGIN
    FOR target IN SELECT c.oid,c.relname,c.relowner FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema()
        AND c.relname IN ('fx_evidence_runs','fx_evidence_versions','fx_evidence_observations',
                         'fx_evidence_legacy_aliases','fx_evidence_batches')
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
        WHERE n.nspname=current_schema() AND p.proname='reject_fx_evidence_mutation' AND p.pronargs=0
        AND a.grantee<>p.proowner AND a.grantee<>0
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %I.reject_fx_evidence_mutation() FROM %I CASCADE',
                       current_schema(),pg_get_userbyid(recipient.grantee));
    END LOOP;
END;
$$;
REVOKE ALL ON FUNCTION reject_fx_evidence_mutation() FROM PUBLIC;

DO $$
DECLARE evidence_table TEXT;
BEGIN
    FOREACH evidence_table IN ARRAY ARRAY['fx_evidence_runs','fx_evidence_versions',
        'fx_evidence_observations','fx_evidence_legacy_aliases','fx_evidence_batches']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',evidence_table);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',evidence_table);
        EXECUTE format('CREATE POLICY tenant_account_isolation ON %I USING
            (tenant_id=app_tenant_id() AND EXISTS (SELECT 1 FROM accounts a
            WHERE a.account_id=%I.account_id AND a.tenant_id=%I.tenant_id))
            WITH CHECK (tenant_id=app_tenant_id() AND EXISTS (SELECT 1 FROM accounts a
            WHERE a.account_id=%I.account_id AND a.tenant_id=%I.tenant_id))',
            evidence_table,evidence_table,evidence_table,evidence_table,evidence_table);
        EXECUTE format('REVOKE ALL ON %I FROM PUBLIC',evidence_table);
        EXECUTE format('CREATE TRIGGER fx_evidence_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
            ON %I FOR EACH STATEMENT EXECUTE FUNCTION reject_fx_evidence_mutation()',evidence_table);
    END LOOP;
END;
$$;
