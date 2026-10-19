"""The Kiwoom collector against a migrated database: what it writes and which case checks it records.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN (CI starts one). Skipped otherwise. Synthetic data only; no
Kiwoom call is made.
"""
from __future__ import annotations

import unittest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

try:
    from test_broker_onboarding_db import ADMIN_DSN, OWNER_ROLE, TENANT, _dsn, _MigratedDatabase
except ImportError:  # run as tests.test_kiwoom_collector_db
    from tests.test_broker_onboarding_db import ADMIN_DSN, OWNER_ROLE, TENANT, _dsn, _MigratedDatabase

from ibkr_etl.kiwoom_client import AccountValuation, CashMovement, Holding, KiwoomError
from ibkr_etl.security_master import SecurityIdentifier

SAMSUNG = SecurityIdentifier("krx_code", "005930")
NOW = datetime(2026, 10, 19, 8, 30, tzinfo=timezone.utc)
TODAY = date(2026, 10, 19)


class FakeKiwoom:
    def __init__(self, *, total="8000000", fail=False):
        self.total, self.fail, self.revoked = Decimal(total), fail, False

    def valuation(self):
        if self.fail:
            raise KiwoomError("Kiwoom refused the call (HTTP 403, code 8005): IP not registered")
        return AccountValuation("Synthetic", Decimal("1000000"), self.total, (
            Holding(SAMSUNG, "삼성전자", Decimal("100"), Decimal("60000"), Decimal("70000"), Decimal("7000000")),))

    def transactions(self, start, end):
        if start <= date(2025, 1, 6) <= end:
            return [CashMovement("2025-01-03", "", "입금", None, Decimal("0"), Decimal("7000000"), "KRW"),
                    CashMovement("2025-01-06", "", "매수", SAMSUNG, Decimal("100"), Decimal("6000000"), "KRW")]
        return []

    def us_valuation(self):
        raise AssertionError("US calls are off in this test")

    def revoke(self):
        self.revoked = True


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class KiwoomCollectorDatabaseTests(_MigratedDatabase):
    def setUp(self):
        import psycopg

        from ibkr_etl.db import PostgresConnection, _hybrid_row_factory

        raw = psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE), options=f"-c app.tenant_id={TENANT}",
                              row_factory=_hybrid_row_factory)
        self.db = PostgresConnection(raw)
        self.addCleanup(raw.close)

    def _kiwoom_account(self, account_id, *, case=True):
        from ibkr_etl.onboarding_v2_web import start_case

        connection_id = uuid.uuid4()
        self.db.execute(
            "INSERT INTO broker_connections (connection_id, broker, environment, display_name, key_vault_secret_name, "
            "credential_fingerprint, status) VALUES (?, 'kiwoom_rest', 'mock', ?, ?, ?, 'pending_check')",
            (connection_id, f"Kiwoom {connection_id.hex[:6]}", "broker-kiwoom-rest-" + connection_id.hex, "0" * 16))
        self.db.execute(
            "INSERT INTO broker_connection_accounts (connection_id, broker_account_id, discovery_status) "
            "VALUES (?, ?, 'discovered')", (connection_id, account_id))
        self.addCleanup(self._retire, connection_id)
        case_id = None
        if case:
            case_id = start_case(self.db, TENANT, connection_id=str(connection_id), broker_account_id=account_id,
                                 display_name="Korea stocks", reporting_currency="KRW",
                                 history_start_date=date(2025, 1, 2), actor=None, today=TODAY)["case_id"]
        self.db.commit()
        return str(connection_id), case_id

    def _run(self, clients):
        from ibkr_etl.kiwoom_collector import run

        return run(self.db, TENANT, client_for=lambda target: clients[target.broker_account_id], today=TODAY,
                   now=NOW, include_us=False)

    def _checks(self, case_id):
        rows = self.db.execute("SELECT check_id, status FROM broker_onboarding_checks WHERE case_id = ?",
                               (case_id,)).fetchall()
        return {row["check_id"]: row["status"] for row in rows}

    def _count(self, table, account):
        return self.db.execute(f"SELECT count(*) AS n FROM {table} WHERE account_id = ?", (account,)).fetchone()["n"]

    def _retire(self, connection_id):
        self.db.rollback()
        self.db.execute("UPDATE broker_connections SET status = 'retired' WHERE connection_id = ?", (connection_id,))
        self.db.commit()

    def _cancel(self, case_id):
        self.db.execute("UPDATE broker_onboarding_cases SET status = 'cancelled' WHERE case_id = ?", (case_id,))
        self.db.commit()

    def test_a_new_case_loads_and_passes_stage_two(self):
        connection_id, case_id = self._kiwoom_account("1234567890")
        self.addCleanup(self._cancel, case_id)
        client = FakeKiwoom()
        results = self._run({"1234567890": client})
        self.assertEqual(results[0]["status"], "loaded")
        self.assertTrue(client.revoked)
        stage = self.db.execute("SELECT stage FROM broker_onboarding_cases WHERE case_id = ?",
                                (case_id,)).fetchone()["stage"]
        # The checks pass; the owner's account terms approval is still to come.
        self.assertEqual(stage, "load_and_reconcile")
        self.assertEqual(self._checks(case_id), {
            "connection_sees_account": "pass", "account_not_already_onboarded": "pass",
            "holdings_and_cash_loaded": "pass", "transactions_loaded": "pass", "securities_mapped": "pass",
            "nav_reconciled": "pass"})
        account = "KIWOOM-1234567890"
        self.assertEqual(self._count("position_snapshots", account), 1)
        self.assertEqual(self._count("trades", account), 1)
        self.assertEqual(self._count("cash_transactions", account), 1)
        nav = self.db.execute("SELECT total_text FROM nav_snapshots WHERE account_id = ?", (account,)).fetchone()
        self.assertEqual(nav["total_text"], "8000000")
        self.assertEqual(self.db.execute(
            "SELECT security_key FROM security_identifiers WHERE scheme = 'krx_code' AND identifier = '005930'"
        ).fetchone()["security_key"], "KRX:005930")
        health = self.db.execute("SELECT status FROM broker_connections WHERE connection_id = ?",
                                 (connection_id,)).fetchone()["status"]
        self.assertEqual(health, "healthy")

        # The same day again replaces rather than adds.
        self._run({"1234567890": FakeKiwoom()})
        self.assertEqual(self._count("position_snapshots", account), 1)
        self.assertEqual(self._count("trades", account), 1)

    def test_a_refusal_marks_the_connection_and_writes_nothing(self):
        connection_id, case_id = self._kiwoom_account("2234567890")
        self.addCleanup(self._cancel, case_id)
        results = self._run({"2234567890": FakeKiwoom(fail=True)})
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("IP not registered", results[0]["error"])
        row = self.db.execute("SELECT status, last_check_json FROM broker_connections WHERE connection_id = ?",
                              (connection_id,)).fetchone()
        self.assertEqual(row["status"], "failing")
        self.assertEqual(self._count("position_snapshots", "KIWOOM-2234567890"), 0)
        self.assertEqual(self._checks(case_id)["connection_sees_account"], "pending")

    def test_a_gap_fails_reconciliation_and_holds_the_case(self):
        _, case_id = self._kiwoom_account("3234567890")
        self.addCleanup(self._cancel, case_id)
        self._run({"3234567890": FakeKiwoom(total="8100000")})
        self.assertEqual(self._checks(case_id)["nav_reconciled"], "fail")

    def test_an_account_without_a_case_is_only_checked(self):
        connection_id, _ = self._kiwoom_account("44345678", case=False)
        results = self._run({"44345678": FakeKiwoom()})
        self.assertEqual(results[0]["status"], "checked")
        self.assertEqual(self._count("position_snapshots", "KIWOOM-44345678"), 0)
        self.assertEqual(self.db.execute("SELECT count(*) AS n FROM accounts WHERE account_id = 'KIWOOM-44345678'"
                                         ).fetchone()["n"], 0)


if __name__ == "__main__":
    unittest.main()
