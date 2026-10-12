-- Synthetic data for the wealthryse_agent database tests. Not real accounts.
INSERT INTO ingestion_runs(run_id, run_type, started_at, completed_at, status, tenant_id)
VALUES (1, 'flex', '2026-01-01T00:00:00Z', '2026-03-31T23:00:00Z', 'success', '00000000-0000-0000-0000-000000000001');

INSERT INTO accounts(account_id, alias, base_currency, account_type, first_seen_at, last_seen_at, source_json, tenant_id) VALUES
 ('UTEST1', 'Test one', 'USD', 'Individual', '2026-01-01', '2026-03-31', '{}', '00000000-0000-0000-0000-000000000001'),
 ('UTEST2', 'Test two', 'USD', 'Individual', '2026-01-01', '2026-03-31', '{}', '00000000-0000-0000-0000-000000000001');

INSERT INTO platform_subjects(tenant_id, subject_id, subject_type, display_name, source_account_id, base_currency,
  reporting_currency, history_start_date, lifecycle_status) VALUES
 ('00000000-0000-0000-0000-000000000001', 'UTEST1', 'physical_account', 'Test one', 'UTEST1', 'USD', 'USD', '2026-01-02', 'active'),
 ('00000000-0000-0000-0000-000000000001', 'UTEST2', 'physical_account', 'Test two', 'UTEST2', 'USD', 'USD', '2026-01-02', 'active');

INSERT INTO nav_snapshots(account_id, snapshot_date, currency, asset_class, total_text, row_sha256, last_seen_run_id) VALUES
 ('UTEST1', '2026-01-02', 'USD', 'TOTAL', '10000', 'x1', 1),
 ('UTEST1', '2026-01-30', 'USD', 'TOTAL', '10500', 'x2', 1),
 ('UTEST1', '2026-02-27', 'USD', 'TOTAL', '12100', 'x3', 1),
 ('UTEST1', '2026-03-31', 'USD', 'TOTAL', '12705', 'x4', 1),
 ('UTEST2', '2026-01-02', 'USD', 'TOTAL', '50000', 'y1', 1),
 ('UTEST2', '2026-03-31', 'USD', 'TOTAL', '55000', 'y2', 1);

INSERT INTO instruments(conid, symbol, description, asset_category, currency, listing_exchange, first_seen_at, last_seen_at, source_json) VALUES
 ('265598', 'AAPL', 'APPLE INC', 'STK', 'USD', 'NASDAQ', '2026-01-01', '2026-03-31', '{}'),
 ('5212', '700', 'TENCENT HOLDINGS LTD', 'STK', 'HKD', 'SEHK', '2026-01-01', '2026-03-31', '{}'),
 ('756733', 'SPY', 'SPDR S&P 500 ETF', 'STK', 'USD', 'ARCA', '2026-01-01', '2026-03-31', '{}');

INSERT INTO instrument_classifications(tenant_id, conid, provider, symbol, sector, industry, exchange, status,
  quality_status, license_status, source_url, checked_at) VALUES
 ('00000000-0000-0000-0000-000000000001', '265598', 'Yahoo Finance search profile', 'AAPL', 'Technology', 'Consumer Electronics', 'NMS', 'classified', 'pass', 'approved', 'https://example.invalid', '2026-03-31'),
 ('00000000-0000-0000-0000-000000000001', '5212', 'Yahoo Finance search profile', '0700.HK', 'Communication Services', 'Internet Content', 'HKG', 'classified', 'pass', 'approved', 'https://example.invalid', '2026-03-31');

-- AAPL: 10 -> 15 shares; Tencent held at 100 shares; HKD moves 0.128 -> 0.129.
INSERT INTO position_snapshots(account_id, snapshot_date, conid, model_code, currency, quantity_text, cost_basis_text,
  mark_price_text, market_value_text, unrealized_pnl_text, row_sha256, last_seen_run_id) VALUES
 ('UTEST1', '2026-01-30', '265598', '', 'USD', '10', '1500', '200', '2000', '500', 'p1', 1),
 ('UTEST1', '2026-01-30', '5212', '', 'HKD', '100', '30000', '400', '40000', '10000', 'p2', 1),
 ('UTEST1', '2026-03-31', '265598', '', 'USD', '15', '2600', '220', '3300', '700', 'p3', 1),
 ('UTEST1', '2026-03-31', '5212', '', 'HKD', '100', '30000', '420', '42000', '12000', 'p4', 1),
 ('UTEST2', '2026-03-31', '756733', '', 'USD', '50', '25000', '600', '30000', '5000', 'q1', 1);

INSERT INTO fx_rates(account_id, rate_date, from_currency, to_currency, rate_text, row_sha256, last_seen_run_id) VALUES
 ('UTEST1', '2026-01-30', 'HKD', 'USD', '0.128', 'f1', 1),
 ('UTEST1', '2026-03-31', 'HKD', 'USD', '0.129', 'f2', 1);

-- One 1,000 USD deposit on 2026-02-10.
INSERT INTO flex_rows(account_id, section, record_type, source_key, report_date, currency, row_sha256, data_json,
  first_seen_run_id, last_seen_run_id, tenant_id) VALUES
 ('UTEST1', 'Transfers', 'Transfer', 't1', '2026-02-10', 'USD', 'r1',
  '{"date": "20260210", "assetCategory": "CASH", "currency": "USD", "cashTransfer": "1000", "direction": "IN"}',
  1, 1, '00000000-0000-0000-0000-000000000001');

INSERT INTO portfolio_benchmark_mapping(tenant_id, subject_id, benchmark_id, mapping_type, effective_from, reporting_currency, created_at)
VALUES ('00000000-0000-0000-0000-000000000001', 'UTEST1', 'GSPC', 'primary', '2026-01-01', 'USD', '2026-01-01');

INSERT INTO benchmark_prices(tenant_id, benchmark_id, observation_date, level, currency, observation_status, provider, fetched_at, quality_status) VALUES
 ('00000000-0000-0000-0000-000000000001', 'GSPC', '2026-01-02', 6000, 'USD', 'official_close', 'test', '2026-03-31', 'pass'),
 ('00000000-0000-0000-0000-000000000001', 'GSPC', '2026-03-31', 6300, 'USD', 'official_close', 'test', '2026-03-31', 'pass');

INSERT INTO app_users(tenant_id, entra_object_id, email, display_name, role_name, status, created_at, updated_at) VALUES
 ('00000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'owner@example.invalid', 'Owner', 'owner', 'active', '2026-01-01', '2026-01-01'),
 ('00000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'viewer@example.invalid', 'Viewer', 'viewer', 'active', '2026-01-01', '2026-01-01');
INSERT INTO app_user_roles(tenant_id, entra_object_id, role_name, role_status, effective_from, reason, created_at, updated_at) VALUES
 ('00000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'owner', 'active', '2026-01-01', 'test', now(), now()),
 ('00000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'viewer', 'active', '2026-01-01', 'test', now(), now());
INSERT INTO user_account_access(tenant_id, entra_object_id, subject_id, access_level, granted_at, granted_by)
VALUES ('00000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'UTEST1', 'read', '2026-01-01', 'test');

INSERT INTO agent.service_grants(tenant_id, job_name, subject_id, granted_by)
VALUES ('00000000-0000-0000-0000-000000000001', 'holdings_explainer', 'UTEST1', 'test');
