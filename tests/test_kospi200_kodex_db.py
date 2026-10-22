"""Migration 081 (KOSPI 200 through the KODEX 200 ETF) against a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts
one). Skipped otherwise. Creates and drops its own database and writes synthetic rows only.
The database has every migration before 081 applied by a login that is not a superuser,
as on Azure, so forced row-level security applies. Each test applies 081 inside a
transaction it rolls back.
"""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations" / "postgres"
NAME = "081_kospi200_via_kodex200.sql"
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER = "kodex_test_owner"
TEST_PASSWORD = "kodex-test-only-password"  # throwaway role in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
CATALOG = ("SELECT benchmark_id, symbol, name, currency, return_convention, active FROM benchmark_master "
           "WHERE symbol IN ('^KS200', '069500.KS') OR benchmark_id = 'KS200' ORDER BY benchmark_id, symbol")


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
class KodexMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = "kodex_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
                admin.execute(f"CREATE ROLE {OWNER} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER}')
        with psycopg.connect(_dsn(cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        with psycopg.connect(_dsn(cls.database, OWNER)) as owner:
            for path in sorted(MIGRATIONS.glob("*.sql")):
                if path.name >= NAME:
                    break
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

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')

    def setUp(self):
        import psycopg

        self.db = psycopg.connect(_dsn(self.database, OWNER))
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)

    def catalog(self):
        return [tuple(row) for row in self.db.execute(CATALOG).fetchall()]

    def apply(self):
        self.db.execute((MIGRATIONS / NAME).read_text(encoding="utf-8"))

    def refused(self, message):
        import psycopg

        before = self.catalog()
        self.db.execute("SAVEPOINT before_081")
        with self.assertRaisesRegex(psycopg.errors.RaiseException, message):
            self.apply()
        self.db.execute("ROLLBACK TO SAVEPOINT before_081")
        self.assertEqual(self.catalog(), before)

    def test_ks200_becomes_the_kodex_200_etf(self):
        self.assertEqual(self.catalog(), [("KS200", "^KS200", "KOSPI 200 Price Index", "KRW", "price", True)])
        self.apply()
        self.assertEqual(self.catalog(), [
            ("KS200", "069500.KS", "KOSPI 200 via KODEX 200 ETF (price)", "KRW", "price", True)])
        # Every migration after 081 still applies.
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name > NAME:
                self.db.execute(path.read_text(encoding="utf-8"))

    def test_an_account_mapped_to_ks200_keeps_its_mapping(self):
        self.db.execute(
            "INSERT INTO portfolio_benchmark_mapping(tenant_id, subject_id, benchmark_id, mapping_type, "
            "effective_from, effective_to, reporting_currency, created_at) "
            "VALUES (%s, 'KIWOOM-TEST', 'KS200', 'primary', '2025-03-21', NULL, 'KRW', '2026-10-20T22:03:00+00:00')",
            (TENANT,))
        self.apply()
        rows = self.db.execute(
            "SELECT m.subject_id, b.symbol FROM portfolio_benchmark_mapping AS m "
            "JOIN benchmark_master AS b ON b.benchmark_id = m.benchmark_id WHERE m.subject_id = 'KIWOOM-TEST'"
        ).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("KIWOOM-TEST", "069500.KS")])

    def test_refuses_when_ks200_has_changed_since_079(self):
        self.db.execute("UPDATE benchmark_master SET name = 'KOSPI 200' WHERE benchmark_id = 'KS200'")
        self.refused("does not hold KS200 as 079 left it")

    def test_refuses_when_another_row_uses_the_etf_ticker(self):
        self.db.execute(
            "INSERT INTO benchmark_master(tenant_id, benchmark_id, symbol, name, provider, currency, "
            "return_convention, frequency, license_status, active, created_at, updated_at) "
            "VALUES (%s, 'KODEX200', '069500.KS', 'KODEX 200', 'Yahoo Finance via yfinance', 'KRW', 'price', "
            "'daily', 'restricted', true, '2026-10-20T00:00:00+00:00', '2026-10-20T00:00:00+00:00')", (TENANT,))
        self.refused("already holds 069500.KS")

    def test_refuses_when_a_ks200_price_is_stored(self):
        self.db.execute(
            "INSERT INTO benchmark_prices(benchmark_id, observation_date, level, currency, observation_status, "
            "provider, source_reference, observed_at, fetched_at, quality_status, raw_sha256) "
            "VALUES ('KS200', '2026-10-19', 400.0, 'KRW', 'provisional_close', 'Yahoo Finance via yfinance', "
            "'synthetic', NULL, '2026-10-20T22:03:00+00:00', 'warn', %s)", ("c" * 64,))
        self.refused("already stored for KS200")


if __name__ == "__main__":
    unittest.main()
