"""The Kiwoom collector against a migrated database: what it writes and which case checks it records.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN (CI starts one). Skipped otherwise. Synthetic data only; no
Kiwoom call is made.
"""
from __future__ import annotations

import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

try:
    from test_broker_onboarding_db import ADMIN_DSN, IDENTITY, OWNER_ROLE, TENANT, _dsn, _MigratedDatabase
except ImportError:  # run as tests.test_kiwoom_collector_db
    from tests.test_broker_onboarding_db import ADMIN_DSN, IDENTITY, OWNER_ROLE, TENANT, _dsn, _MigratedDatabase

from ibkr_etl.kiwoom_client import AccountValuation, CashMovement, Holding, KiwoomError
from ibkr_etl.security_master import SecurityIdentifier

SAMSUNG = SecurityIdentifier("krx_code", "005930")
NOW = datetime(2026, 10, 19, 8, 30, tzinfo=timezone.utc)
TODAY = date(2026, 10, 19)
CLOSED = date(2025, 3, 3)  # a KRX holiday in this synthetic history


class FakeKiwoom:
    def __init__(self, *, total="8000000", fail=False, off_day=None, refuse_history=False):
        self.total, self.fail, self.revoked = Decimal(total), fail, False
        self.off_day, self.refuse_history, self.asked = off_day, refuse_history, []

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

    def balance_on(self, day):
        self.asked.append(day)
        if self.refuse_history:
            raise KiwoomError("Kiwoom refused the call (HTTP 200, code 2): no data before the account opened")
        if day == CLOSED:
            return None
        total = Decimal("8005000") if day == self.off_day else Decimal("8000000")
        return AccountValuation("", Decimal("1000000"), total, (
            Holding(SAMSUNG, "삼성전자", Decimal("100"), Decimal("60000"), Decimal("70000"), Decimal("7000000")),))

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

    def _kiwoom_account(self, account_id, *, case=True, start=date(2025, 1, 2)):
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
                                 history_start_date=start, actor=None, today=TODAY)["case_id"]
        self.db.commit()
        return str(connection_id), case_id

    def _run(self, clients):
        from ibkr_etl.kiwoom_collector import run

        return run(self.db, TENANT, client_for=lambda target: clients[target.broker_account_id], today=TODAY,
                   now=NOW, include_us=False)

    def _approve_terms(self, case_id):
        from ibkr_etl.onboarding_v2_web import approve_case, case_detail

        detail = case_detail(self.db, TENANT, case_id)
        approved = approve_case(self.db, TENANT, case_id, approval="account_terms",
                                terms_sha256=detail["account_terms_sha256"],
                                acknowledge_warnings=["transactions_loaded"],
                                confirmation=None, actor=IDENTITY)
        self.db.commit()
        self.assertEqual(approved["stage"], "build_history")

    def _history_check(self, case_id):
        return self.db.execute("SELECT status, message, evidence_json FROM broker_onboarding_checks "
                               "WHERE case_id = ? AND check_id = 'history_built'", (case_id,)).fetchone()

    def _checks(self, case_id):
        rows = self.db.execute("SELECT check_id, status FROM broker_onboarding_checks WHERE case_id = ?",
                               (case_id,)).fetchall()
        return {row["check_id"]: row["status"] for row in rows}

    def _count(self, table, account):
        return self.db.execute(f"SELECT count(*) AS n FROM {table} WHERE account_id = ?", (account,)).fetchone()["n"]

    def _today(self, table, account):
        return self.db.execute(f"SELECT count(*) AS n FROM {table} WHERE account_id = ? AND snapshot_date = ?",
                               (account, TODAY.isoformat())).fetchone()["n"]

    def _retire(self, connection_id):
        self.db.rollback()
        self.db.execute("UPDATE broker_connections SET status = 'retired' WHERE connection_id = ?", (connection_id,))
        self.db.commit()

    def _cancel(self, case_id):
        self.db.rollback()
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
        self.assertEqual(self._today("position_snapshots", account), 1)
        self.assertEqual(self._count("trades", account), 1)
        self.assertEqual(self._count("cash_transactions", account), 1)
        nav = self.db.execute("SELECT total_text FROM nav_snapshots WHERE account_id = ? AND snapshot_date = ?",
                              (account, TODAY.isoformat())).fetchone()
        self.assertEqual(nav["total_text"], "8000000")
        self.assertEqual(self.db.execute(
            "SELECT security_key FROM security_identifiers WHERE scheme = 'krx_code' AND identifier = '005930'"
        ).fetchone()["security_key"], "KRX:005930")
        health = self.db.execute("SELECT status FROM broker_connections WHERE connection_id = ?",
                                 (connection_id,)).fetchone()["status"]
        self.assertEqual(health, "healthy")

        # The same day again replaces rather than adds.
        self._run({"1234567890": FakeKiwoom()})
        self.assertEqual(self._today("position_snapshots", account), 1)
        self.assertEqual(self._count("trades", account), 1)

    def test_history_fills_before_approval_and_is_recorded_after(self):
        from ibkr_etl.kiwoom_collector import HISTORY_DAYS_PER_RUN, trading_weekdays

        _, case_id = self._kiwoom_account("5234567890")
        self.addCleanup(self._cancel, case_id)
        account = "KIWOOM-5234567890"
        weekdays = trading_weekdays(date(2025, 1, 2), TODAY - timedelta(days=1))
        self.assertGreater(len(weekdays), HISTORY_DAYS_PER_RUN)

        # Load and reconcile: the first run fills the first batch of days but records no history check.
        first = FakeKiwoom()
        result = self._run({"5234567890": first})[0]
        self.assertEqual(result["history"], {"check": "pending", "recorded": False, "loaded": HISTORY_DAYS_PER_RUN - 1})
        self.assertEqual(first.asked, weekdays[:HISTORY_DAYS_PER_RUN])
        self.assertIsNone(self._history_check(case_id))

        # Build history: the rest is filled and the check recorded.
        self._approve_terms(case_id)
        second = FakeKiwoom()
        result = self._run({"5234567890": second})[0]
        self.assertEqual(result["history"]["check"], "pass")
        # Only the days still missing are asked for, plus the closed day, which has no snapshot.
        self.assertEqual(second.asked, [CLOSED] + weekdays[HISTORY_DAYS_PER_RUN:])
        check = self._history_check(case_id)
        self.assertEqual(check["status"], "pass", check["message"])
        days = self.db.execute("SELECT count(DISTINCT snapshot_date) AS n FROM nav_snapshots WHERE account_id = ?",
                               (account,)).fetchone()["n"]
        self.assertEqual(days, len(weekdays))  # every weekday but the closed one, plus today
        self.assertEqual(self.db.execute("SELECT total_text FROM nav_snapshots WHERE account_id = ? AND "
                                         "snapshot_date = '2025-06-02'", (account,)).fetchone()["total_text"],
                         "8000000")

        # A finished history is not recorded again on later runs.
        result = self._run({"5234567890": FakeKiwoom()})[0]
        self.assertEqual(result["history"], {"check": "pass", "recorded": False})

    def test_a_history_filled_before_approval_is_recorded_on_reaching_build_history(self):
        _, case_id = self._kiwoom_account("8234567890", start=date(2026, 9, 1))
        self.addCleanup(self._cancel, case_id)
        self._run({"8234567890": FakeKiwoom()})
        self._approve_terms(case_id)
        later = FakeKiwoom()
        result = self._run({"8234567890": later})[0]
        self.assertEqual(later.asked, [])
        self.assertEqual(result["history"]["check"], "pass")
        self.assertEqual(self._history_check(case_id)["status"], "pass")

    def test_a_past_day_off_kiwooms_total_warns(self):
        _, case_id = self._kiwoom_account("6234567890", start=date(2026, 9, 1))
        self.addCleanup(self._cancel, case_id)
        self._run({"6234567890": FakeKiwoom(off_day=date(2026, 10, 5))})
        self._approve_terms(case_id)
        self.db.execute("DELETE FROM nav_snapshots WHERE account_id = 'KIWOOM-6234567890' AND snapshot_date = '2026-10-05'")
        self.db.commit()
        result = self._run({"6234567890": FakeKiwoom(off_day=date(2026, 10, 5))})[0]
        self.assertEqual(result["history"]["check"], "warn")
        check = self._history_check(case_id)
        self.assertEqual(check["evidence_json"]["gaps"], [{"date": "2026-10-05", "gap": "5000"}])

    def test_a_history_refusal_fails_the_check_but_keeps_the_day(self):
        _, case_id = self._kiwoom_account("7234567890")
        self.addCleanup(self._cancel, case_id)
        self._run({"7234567890": FakeKiwoom()})
        self._approve_terms(case_id)
        result = self._run({"7234567890": FakeKiwoom(refuse_history=True)})[0]
        self.assertEqual((result["status"], result["history"]["check"]), ("loaded", "fail"))
        self.assertIn("no data before the account opened", self._history_check(case_id)["message"])
        self.assertEqual(self._today("position_snapshots", "KIWOOM-7234567890"), 1)

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

    def test_any_other_error_is_recorded_on_the_connection_not_raised(self):
        connection_id, case_id = self._kiwoom_account("9234567890")
        self.addCleanup(self._cancel, case_id)

        def unreachable(target):
            raise TimeoutError("timed out reading the key")

        from ibkr_etl.kiwoom_collector import run

        results = run(self.db, TENANT, client_for=unreachable, today=TODAY, now=NOW, include_us=False)
        self.assertEqual((results[0]["status"], results[0]["error"]), ("failed", "TimeoutError: timed out reading the key"))
        row = self.db.execute("SELECT status FROM broker_connections WHERE connection_id = ?",
                              (connection_id,)).fetchone()
        self.assertEqual(row["status"], "failing")

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
