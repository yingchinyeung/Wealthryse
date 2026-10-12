CREATE TABLE IF NOT EXISTS instrument_classifications (
    tenant_id UUID NOT NULL DEFAULT app_tenant_id() REFERENCES tenants(tenant_id),
    conid TEXT NOT NULL REFERENCES instruments(conid),
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    lookup_symbol TEXT,
    sector TEXT,
    industry TEXT,
    exchange TEXT,
    status TEXT NOT NULL CHECK (status IN ('classified', 'unclassified', 'failed')),
    quality_status TEXT NOT NULL CHECK (quality_status IN ('pass', 'warn', 'fail')),
    license_status TEXT NOT NULL CHECK (license_status IN ('approved', 'restricted')),
    source_url TEXT NOT NULL,
    response_sha256 TEXT,
    error_code TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, conid, provider)
);

CREATE INDEX IF NOT EXISTS idx_instrument_classification_sector
    ON instrument_classifications(tenant_id, provider, sector);

ALTER TABLE instrument_classifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE instrument_classifications FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON instrument_classifications;
CREATE POLICY tenant_isolation ON instrument_classifications
    USING (tenant_id = app_tenant_id())
    WITH CHECK (tenant_id = app_tenant_id());
