"""derived-daily's database-built reference data against a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI
starts one). Skipped otherwise. Creates and drops its own database. v2's real
sector and Treasury writers fill the tables from the same fake provider answers
that v1.1's real refresh turns into reference_data.json, and the two must agree.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

from test_reference_data_source import (
    ACCOUNT, DATES, TENANT, TREASURY_XML, definition, holdings, load_source_account,
    v11_reference_file, yahoo_search,
)

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = str(ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
OWNER = "refdata_test_owner"
TEST_PASSWORD = "refdata-test-only-password"  # throwaway role in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
COUNTED = ("instrument_classifications", "risk_free_rates", "subject_calculation_runs",
           "derived_portfolio_nav", "ingestion_runs")


def _url(database: str, user: str | None = None) -> str:
    """A postgresql:// URL, the form DATABASE_URL takes. No user means the superuser."""
    from urllib.parse import quote

    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(ADMIN_DSN)
    host, port = params.get("host", "127.0.0.1"), params.get("port", "5432")
    login = user or params.get("user", "postgres")
    password = TEST_PASSWORD if user else params.get("password", "")
    return f"postgresql://{quote(login)}:{quote(password)}@{host}:{port}/{database}"


def _dsn(database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(ADMIN_DSN)
    params["dbname"] = database
    params["options"] = f"-c app.tenant_id={TENANT}"
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class DatabaseReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import connect
        from ibkr_etl.risk_free import refresh_treasury_3m_rates
        from ibkr_etl.sector_classification import _classify_and_store

        cls.database = "refdata_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
                # Like Azure: the schema owner is not a superuser, so forced RLS applies to it.
                admin.execute(f"CREATE ROLE {OWNER} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER}')
        with psycopg.connect(_dsn(cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        with psycopg.connect(_dsn(cls.database, OWNER)) as owner:
            for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
                try:
                    owner.execute(path.read_text(encoding="utf-8"))
                    owner.commit()
                except Exception:
                    owner.rollback()
                    if path.name not in TOLERATED_MIGRATIONS:
                        raise
                if owner.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                        owner.execute("SELECT 1 FROM tenants").fetchone() is None:
                    owner.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                  "VALUES (%s, 'Test', 'active', '2026-01-01')", (TENANT,))
                    owner.commit()
        cls.url = _url(cls.database, OWNER)
        connection = connect(Path("unused"), cls.url, TENANT)
        try:
            load_source_account(connection)
            # v2's own writers, fed the same provider answers as the v1.1 file.
            fake_yfinance = types.SimpleNamespace(Ticker=lambda symbol: types.SimpleNamespace(get_info=dict))
            with patch.dict(sys.modules, {"yfinance": fake_yfinance}):
                _classify_and_store(connection, holdings(), workers=1, timeout=1, fetcher=yahoo_search)
            with patch("ibkr_etl.risk_free._get", return_value=TREASURY_XML):
                refresh_treasury_3m_rates(connection, start_date=date(2026, 10, 1), end_date=date(2026, 10, 16))
            # Rows the build must ignore: HIBOR, a failed check, another tenant.
            connection.execute(
                "INSERT INTO risk_free_rates(rate_date, series_id, currency, tenor, annual_rate, "
                "day_count_convention, provider, fetched_at, quality_status) VALUES "
                "('2026-10-15', 'HKAB-HIBOR-1M', 'HKD', '1-month', 0.035, 'act/365', 'HKAB', '2026-10-15', 'pass'), "
                "('2026-10-15', 'UST-PAR-3M', 'USD', '3-month', 0.09, 'annualized-par-yield', 'x', '2026-10-15', 'fail')")
            connection.commit()
        finally:
            connection.close()
        with psycopg.connect(_dsn(cls.database), autocommit=True) as admin:
            admin.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                          "VALUES (%s, 'Other', 'active', '2026-01-01')", (OTHER_TENANT,))
            admin.execute(
                "INSERT INTO instrument_classifications(tenant_id, conid, provider, symbol, sector, status, "
                "quality_status, license_status, source_url, checked_at) VALUES "
                "(%s, '9100001', 'Yahoo Finance search profile', '700', 'Other tenant sector', 'classified', "
                "'warn', 'restricted', 'x', '2026-10-16')", (OTHER_TENANT,))
            admin.execute(
                "INSERT INTO risk_free_rates(tenant_id, rate_date, series_id, currency, tenor, annual_rate, "
                "day_count_convention, provider, fetched_at, quality_status) VALUES "
                "(%s, '2026-10-16', 'UST-PAR-3M', 'USD', '3-month', 0.07, 'annualized-par-yield', 'x', "
                "'2026-10-16', 'pass')", (OTHER_TENANT,))
            definition_json = json.dumps(definition(), sort_keys=True)
            admin.execute(
                "INSERT INTO platform_subjects(tenant_id, subject_id, subject_type, display_name, "
                "source_account_id, lifecycle_status) VALUES (%s, 'synthetic-non-taiwan', "
                "'filtered_portfolio', 'Synthetic Non-Taiwan', %s, 'active')", (TENANT, ACCOUNT))
            admin.execute(
                "INSERT INTO subject_calculation_definitions(definition_id, tenant_id, subject_id, subject_type, "
                "calculation_kind, definition_version, configuration_json, configuration_sha256, "
                "approval_status, effective_from, approved_by_identity_sha256, approved_at) VALUES "
                "(%s, %s, 'synthetic-non-taiwan', 'filtered_portfolio', "
                "'filtered_account_with_reconstructed_cash', 1, %s::jsonb, %s, 'approved', %s, %s, now())",
                (str(uuid.uuid4()), TENANT, definition_json,
                 hashlib.sha256(definition_json.encode()).hexdigest(), DATES[0], "c" * 64))
        cls.folder = tempfile.TemporaryDirectory()
        cls.file_bytes = v11_reference_file(Path(cls.folder.name))

    @classmethod
    def tearDownClass(cls):
        import psycopg

        cls.folder.cleanup()
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')

    def _connect(self, url: str | None = None):
        from ibkr_etl.db import connect

        url = url or self.url
        self.assertTrue(url.startswith("postgresql://"))
        connection = connect(Path("unused"), url, TENANT)
        self.addCleanup(connection.close)
        return connection

    def _counts(self) -> dict:
        connection = self._connect(_url(self.database))
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in COUNTED}

    def test_tables_written_by_v2_jobs_build_the_file_v11_publishes(self):
        from ibkr_etl.reference_data_source import (
            build_reference_data_from_database, compare_risk_free, compare_sectors, usd_risk_free_row,
        )
        from ibkr_etl.reference_snapshot import inspect_reference_bytes

        file_data, _ = inspect_reference_bytes(self.file_bytes)
        # The explicit tenant filter holds even for a login that bypasses row-level security.
        for url in (self.url, _url(self.database)):
            connection = self._connect(url)
            database_data = build_reference_data_from_database(connection, TENANT)
            sectors = compare_sectors(file_data["sectors"], database_data["sectors"])
            self.assertEqual((sectors["common_rows"], sectors["only_in_file"]["count"],
                              sectors["only_in_database"]["count"]), (4, 0, 0))
            self.assertEqual(sectors["field_differences"], {})
            usd = database_data["risk_free"]["USD"]
            self.assertEqual((usd["observation_date"], usd["quality_status"]), ("2026-10-14", "pass"))
            self.assertEqual(set(database_data["risk_free"]), {"USD"})
            on_date = usd_risk_free_row(connection, TENANT, "2026-10-14")
            risk_free = compare_risk_free(file_data["risk_free"], database_data["risk_free"],
                                          database_rate_on_file_date=on_date["annual_rate"])
            self.assertEqual(risk_free["differences"], [])
            self.assertEqual(risk_free["rate_difference_on_file_date"], 0.0)

    def test_derived_worker_switch_writes_a_stable_validated_file(self):
        import run_self_service_derived_refresh as worker

        settings = types.SimpleNamespace(tenant_id=TENANT)
        connection = self._connect()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reference_data.json"
            first = worker._load_reference_input(settings, connection, path, "database")
            content = path.read_bytes()
            second = worker._load_reference_input(settings, connection, path, "database")
        self.assertEqual(first, second)
        self.assertEqual(first["source"], "database")
        self.assertEqual(first["content_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual((first["classification_rows"], first["risk_free_rows"]), (4, 1))
        self.assertNotIn(b"Other tenant sector", content)

    def test_comparison_reads_only_and_finds_no_change_for_equivalent_data(self):
        import compare_reference_data_sources as compare

        before = self._counts()
        connection = self._connect()
        report = compare.run_comparison(connection, TENANT, self.file_bytes, {"source": "test"})
        self.assertTrue(report["switch_safe_for_derived_daily"])
        [subject] = report["derived_daily_impact"]
        self.assertEqual(subject["subject_id"], "synthetic-non-taiwan")
        self.assertEqual((subject["position_conids_checked"], subject["activity_conids_checked"]), (4, 3))
        self.assertEqual(report["sectors"]["field_differences"], {})
        self.assertEqual(report["risk_free"]["differences"], [])
        self.assertEqual(connection.execute("SHOW transaction_read_only").fetchone()[0], "on")
        from psycopg.errors import ReadOnlySqlTransaction

        with self.assertRaises(ReadOnlySqlTransaction):
            connection.execute("DELETE FROM risk_free_rates")
        connection.rollback()
        self.assertEqual(self._counts(), before)

    def test_command_exits_one_when_a_file_country_would_change_an_exclusion(self):
        import compare_reference_data_sources as compare

        data = json.loads(self.file_bytes)
        data["sectors"]["9100002"]["country"] = "Taiwan"
        before = self._counts()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reference_data.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            output = io.StringIO()
            environment = {"DATABASE_URL": self.url, "PORTFOLIO_TENANT_ID": TENANT}
            with patch.dict(os.environ, environment), contextlib.redirect_stdout(output):
                exit_code = compare.main(["--file", str(path)])
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["reference_data.json"])
        self.assertEqual(exit_code, 1)
        line = output.getvalue().strip()
        self.assertTrue(line.startswith("REFERENCE_DATA_COMPARISON "))
        report = json.loads(line.split(" ", 1)[1])
        self.assertFalse(report["switch_safe_for_derived_daily"])
        self.assertEqual(report["sectors"]["file_countries"]["count"], 1)
        changes = report["derived_daily_impact"][0]["excluded_status_changes"]
        self.assertEqual({row["conid"] for row in changes["examples"]}, {"9100002"})
        self.assertEqual(self._counts(), before)


if __name__ == "__main__":
    unittest.main()
