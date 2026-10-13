"""Database rules for the new account onboarding (migration 063).

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN (CI starts one). Skipped otherwise. Creates and drops its own
database and uses synthetic data only.
"""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
OWNER_ROLE = "broker_onboarding_test_owner"
TEST_PASSWORD = "broker-onboarding-test-only"  # throwaway role in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
IDENTITY = "a" * 64


def _dsn(base: str, database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(base)
    params["dbname"] = database
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class BrokerOnboardingSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection

        cls.database = "broker_onboarding_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER_ROLE,)).fetchone() is None:
                # Not a superuser, so forced row-level security applies as on Azure.
                admin.execute(f"CREATE ROLE {OWNER_ROLE} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER_ROLE}')
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        connection = PostgresConnection(psycopg.connect(_dsn(ADMIN_DSN, cls.database, OWNER_ROLE)))
        connection.execute("SELECT set_config('app.tenant_id', ?, false)", (TENANT,))
        connection.commit()
        for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
            try:
                connection.execute(path.read_text(encoding="utf-8"))
                connection.commit()
            except Exception:
                connection.rollback()
                if path.name not in TOLERATED_MIGRATIONS:
                    raise
            if connection.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                    connection.execute("SELECT 1 FROM tenants").fetchone() is None:
                connection.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                   "VALUES (?, 'Test', 'active', '2026-01-01')", (TENANT,))
                connection.commit()
        connection.close()
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database), autocommit=True) as admin:
            admin.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                          "VALUES (%s, 'Other', 'active', '2026-01-01')", (OTHER_TENANT,))

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')

    def setUp(self):
        import psycopg

        self.db = psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE),
                                  options=f"-c app.tenant_id={TENANT}")
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)  # every test leaves the database as it found it

    def _connection(self, broker="kiwoom_rest", **overrides):
        connection_id = uuid.uuid4()
        prefix = "ibkr-flex-source-" if broker == "ibkr_flex" else "broker-kiwoom-rest-"
        values = {
            "connection_id": connection_id,
            "broker": broker,
            "environment": "live" if broker == "ibkr_flex" else "mock",
            "display_name": f"Test {connection_id.hex[:8]}",
            "key_vault_secret_name": prefix + connection_id.hex,
            "credential_fingerprint": "0" * 16,
            "credential_expires_on": "2027-10-01" if broker == "ibkr_flex" else None,
            "status": "pending_check",
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join(["%s"] * len(values))
        self.db.execute(f"INSERT INTO broker_connections ({columns}) VALUES ({placeholders})", list(values.values()))
        return connection_id

    def _account(self, connection_id, account_id):
        self.db.execute(
            "INSERT INTO broker_connection_accounts (connection_id, broker_account_id, discovery_status) "
            "VALUES (%s, %s, 'discovered')", (connection_id, account_id))

    def _case(self, connection_id, account_id, *, broker="kiwoom_rest", stage="request", status="platform_working",
              subject_id=None, went_live=False):
        case_id = uuid.uuid4()
        self.db.execute(
            "INSERT INTO broker_onboarding_cases (case_id, connection_id, broker, broker_account_id, display_name, "
            "reporting_currency, history_start_date, subject_id, stage, status, went_live_at) "
            "VALUES (%s, %s, %s, %s, 'Synthetic', 'KRW', '2025-01-02', %s, %s, %s, "
            "CASE WHEN %s THEN now() END)",
            (case_id, connection_id, broker, account_id, subject_id, stage, status, went_live))
        return case_id

    def _subject(self, subject_id):
        self.db.execute(
            "INSERT INTO platform_subjects (subject_id, subject_type, display_name, lifecycle_status) "
            "VALUES (%s, 'physical_account', 'Synthetic', 'onboarding')", (subject_id,))

    def assertRejected(self, statement, params=()):
        import psycopg

        with self.assertRaises(psycopg.Error):
            with self.db.transaction():
                self.db.execute(statement, params)

    def test_only_one_account_is_onboarded_at_a_time(self):
        import psycopg

        connection_id = self._connection()
        for account in ("50000001", "50000002", "50000003"):
            self._account(connection_id, account)
        first = self._case(connection_id, "50000001")
        with self.assertRaises(psycopg.errors.UniqueViolation), self.db.transaction():
            self._case(connection_id, "50000002")
        self._subject("KIWOOM-50000001")
        self.db.execute("UPDATE broker_onboarding_cases SET stage = 'first_month_end', status = 'live', "
                        "subject_id = 'KIWOOM-50000001', went_live_at = now() WHERE case_id = %s", (first,))
        self._case(connection_id, "50000002")
        with self.assertRaises(psycopg.errors.UniqueViolation), self.db.transaction():
            self._case(connection_id, "50000003")

    def test_an_account_is_never_onboarded_twice(self):
        import psycopg

        first_connection, second_connection = self._connection(), self._connection()
        self._account(first_connection, "50000009")
        self._account(second_connection, "50000009")
        case_id = self._case(first_connection, "50000009")
        self.db.execute("UPDATE broker_onboarding_cases SET status = 'cancelled' WHERE case_id = %s", (case_id,))
        self._case(second_connection, "50000009")
        self._subject("KIWOOM-50000009")
        with self.assertRaises(psycopg.errors.UniqueViolation), self.db.transaction():
            self._case(first_connection, "50000009", stage="first_month_end", status="live",
                       subject_id="KIWOOM-50000009", went_live=True)

    def test_stage_rules_hold_in_the_database(self):
        connection_id = self._connection()
        self._account(connection_id, "50000010")
        base = ("INSERT INTO broker_onboarding_cases (case_id, connection_id, broker, broker_account_id, "
                "display_name, reporting_currency, history_start_date, stage, status) "
                "VALUES (gen_random_uuid(), %s, 'kiwoom_rest', '50000010', 'Synthetic', 'KRW', '2025-01-02', %s, %s)")
        self.assertRejected(base, (connection_id, "build_history", "platform_working"))  # no subject yet
        self.assertRejected(base, (connection_id, "closed", "platform_working"))
        self.assertRejected(base, (connection_id, "request", "closed"))
        self.assertRejected(base, (connection_id, "nowhere", "platform_working"))

    def test_connections_hold_no_secret_and_ibkr_needs_expiry(self):
        self.assertRejected(
            "INSERT INTO broker_connections (connection_id, broker, environment, display_name, "
            "key_vault_secret_name, credential_fingerprint, status) VALUES "
            "(gen_random_uuid(), 'ibkr_flex', 'live', 'No expiry', 'ibkr-flex-source-' || md5('x'), %s, "
            "'pending_check')", ("0" * 16,))
        with self.assertRaises(Exception), self.db.transaction():
            self._connection(broker="kiwoom_rest", key_vault_secret_name="ibkr-flex-source-" + "0" * 32)
        with self.assertRaises(Exception), self.db.transaction():
            self._connection(broker="ibkr_flex", environment="mock")
        with self.assertRaises(Exception), self.db.transaction():
            self._connection(credential_fingerprint="not-a-fingerprint")
        self._connection(broker="ibkr_flex")

    def test_approvals_and_events_cannot_be_rewritten(self):
        connection_id = self._connection()
        self._account(connection_id, "50000020")
        case_id = self._case(connection_id, "50000020", stage="load_and_reconcile", status="waiting_on_owner")
        self.db.execute(
            "INSERT INTO broker_onboarding_approvals (case_id, approval_kind, terms_json, terms_sha256, "
            "approved_by_identity_sha256) VALUES (%s, 'account_terms', '{}', %s, %s)",
            (case_id, "b" * 64, IDENTITY))
        self.db.execute(
            "INSERT INTO broker_onboarding_events (event_id, case_id, event_type, from_stage, to_stage) "
            "VALUES (gen_random_uuid(), %s, 'approved', 'load_and_reconcile', 'load_and_reconcile')", (case_id,))
        self.assertRejected("UPDATE broker_onboarding_approvals SET terms_sha256 = %s", ("c" * 64,))
        self.assertRejected("DELETE FROM broker_onboarding_events")
        self.assertRejected("DELETE FROM broker_onboarding_cases WHERE case_id = %s", (case_id,))

    def test_rows_stay_inside_their_tenant(self):
        connection_id = self._connection()
        self.db.execute("SELECT set_config('app.tenant_id', %s, true)", (OTHER_TENANT,))
        visible = self.db.execute("SELECT count(*) FROM broker_connections WHERE connection_id = %s",
                                  (connection_id,)).fetchone()[0]
        self.assertEqual(visible, 0)

    def test_security_identifiers_are_well_formed(self):
        self.db.execute("INSERT INTO security_master (security_key, display_name, asset_class, currency, status) "
                        "VALUES ('KRX:005930', 'Synthetic Korean share', 'STK', 'KRW', 'active')")
        self.db.execute("INSERT INTO security_identifiers (scheme, identifier, security_key, source) "
                        "VALUES ('krx_code', '005930', 'KRX:005930', 'kiwoom_rest')")
        self.assertRejected("INSERT INTO security_identifiers (scheme, identifier, security_key, source) "
                            "VALUES ('krx_code', '005930', 'KRX:005930', 'owner')")
        self.assertRejected("INSERT INTO security_identifiers (scheme, identifier, security_key, source) "
                            "VALUES ('krx_code', 'SAMSUNG', 'KRX:005930', 'owner')")
        self.assertRejected("INSERT INTO security_master (security_key, display_name, asset_class, currency, "
                            "status) VALUES ('NASDAQ:AAPL', 'Bad key', 'STK', 'USD', 'active')")


if __name__ == "__main__":
    unittest.main()
