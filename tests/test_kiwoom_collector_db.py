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

from ibkr_etl.kiwoom_client import AccountValuation, CashMovement, Holding, KiwoomError, kiwoom_selling_cost
from ibkr_etl.security_master import SecurityIdentifier

SAMSUNG = SecurityIdentifier("krx_code", "005930")
NOW = datetime(2026, 10, 19, 8, 30, tzinfo=timezone.utc)
TODAY = date(2026, 10, 19)
CLOSED = date(2025, 3, 3)  # a KRX holiday in this synthetic history


class FakeKiwoom:
    def __init__(self, *, total="8000000", fail=False, off_day=None, refuse_history=False, basis_day=None):
        self.total, self.fail, self.revoked = Decimal(total), fail, False
        self.off_day, self.refuse_history, self.asked = off_day, refuse_history, []
        self.basis_day = basis_day

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
        # As the client reads ka01690: the deposit is the day's cash, and Kiwoom's total is holdings
        # plus cash less its per-stock selling cost (11,550 KRW on 7,000,000 in 2025, 15,050 from 2026).
        price = Decimal("71000") if day == self.basis_day else Decimal("70000")
        value = price * 100
        total = Decimal("1000000") + value - kiwoom_selling_cost(value, day)
        if day == self.off_day:
            total = Decimal("8005000")
        # On the price-basis day ka01690 lists 70,000 (after-market) and the client prices the
        # holding at the official close, 71,000, which adds up to Kiwoom's 7,100,000.
        return AccountValuation("", Decimal("1000000"), total, (
            Holding(SAMSUNG, "삼성전자", Decimal("100"), Decimal("60000"), price, value),),
            securities_value=value, repriced_at_close=day == self.basis_day)

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

    def _kiwoom_account(self, account_id, *, case=True, start=date(2025, 1, 2), currency="KRW"):
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
                                 display_name="Korea stocks" if currency == "KRW" else "U.S. stocks",
                                 reporting_currency=currency,
                                 history_start_date=start, actor=None, today=TODAY)["case_id"]
        self.db.commit()
        return str(connection_id), case_id

    def _run(self, clients, now=NOW):
        from ibkr_etl.kiwoom_collector import run

        return run(self.db, TENANT, client_for=lambda target: clients[target.broker_account_id], today=TODAY,
                   now=now, include_us=False)

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
        # A closed case stays closed; any other is cancelled so the next test can start one.
        self.db.execute("UPDATE broker_onboarding_cases SET status = 'cancelled' WHERE case_id = ? "
                        "AND stage <> 'closed'", (case_id,))
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
                         "7988450")

        # A finished history is not recorded again on later runs.
        result = self._run({"5234567890": FakeKiwoom()})[0]
        self.assertEqual(result["history"], {"check": "pass", "recorded": False})

        # The deposit is the cash, and the selling cost is kept apart so the day adds up to Kiwoom's total.
        nav = self.db.execute("SELECT total_text, cash_text, stock_text, accruals_text FROM nav_snapshots "
                              "WHERE account_id = ? AND snapshot_date = '2025-01-07'", (account,)).fetchone()
        self.assertEqual((nav["total_text"], nav["cash_text"], nav["stock_text"], nav["accruals_text"]),
                         ("7988450", "1000000", "7000000", "-11550"))
        cash = self.db.execute("SELECT ending_cash_text, settled_cash_text FROM cash_snapshots "
                               "WHERE account_id = ? AND snapshot_date = '2025-01-07'", (account,)).fetchone()
        self.assertEqual((cash["ending_cash_text"], cash["settled_cash_text"]), ("1000000", "1000000"))

        # A day stored on the earlier basis (cash as the total less the holdings) is collected again.
        self.db.execute("UPDATE nav_snapshots SET cash_text = '988450', accruals_text = '0' WHERE account_id = ? "
                        "AND snapshot_date = '2025-01-07'", (account,))
        self.db.execute("UPDATE cash_snapshots SET ending_cash_text = '988450' WHERE account_id = ? "
                        "AND snapshot_date = '2025-01-07'", (account,))
        self.db.commit()
        again = FakeKiwoom()
        result = self._run({"5234567890": again})[0]
        self.assertEqual(again.asked, [date(2025, 1, 7), CLOSED])
        self.assertEqual(result["history"]["check"], "pass")
        self.assertEqual(self.db.execute("SELECT cash_text FROM nav_snapshots WHERE account_id = ? AND "
                                         "snapshot_date = '2025-01-07'", (account,)).fetchone()["cash_text"],
                         "1000000")

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
        # The day is filled before approval; the run that records the check loads nothing new.
        self._run({"6234567890": FakeKiwoom(off_day=date(2026, 10, 5))})
        self._approve_terms(case_id)
        later = FakeKiwoom(off_day=date(2026, 10, 5))
        result = self._run({"6234567890": later})[0]
        # A day that does not reconcile is asked for again on each run, in case Kiwoom has corrected it.
        self.assertEqual(later.asked, [date(2026, 10, 5)])
        self.assertEqual(result["history"]["check"], "warn")
        check = self._history_check(case_id)
        # Kiwoom's total is 5,000 KRW above holdings plus cash, which no selling cost explains.
        self.assertEqual(check["evidence_json"]["gaps"], [
            {"date": "2026-10-05", "check": "selling_cost", "gap": "-5000", "share_of_holdings": "-0.0714%"}])
        self.assertEqual(check["evidence_json"]["gap_days"], 1)

    def test_a_day_priced_at_the_official_close_adds_up_to_kiwooms_total(self):
        _, case_id = self._kiwoom_account("6334567890", start=date(2026, 9, 1))
        self.addCleanup(self._cancel, case_id)
        account = "KIWOOM-6334567890"
        self._run({"6334567890": FakeKiwoom(basis_day=date(2026, 10, 5))})
        self._approve_terms(case_id)
        result = self._run({"6334567890": FakeKiwoom()})[0]
        self.assertEqual(result["history"]["check"], "pass")
        nav = self.db.execute("SELECT total_text, cash_text, stock_text, accruals_text FROM nav_snapshots "
                              "WHERE account_id = ? AND snapshot_date = '2026-10-05'", (account,)).fetchone()
        # Holdings at the close, cash, and Kiwoom's per-stock selling cost add up to its total to the won.
        self.assertEqual((nav["total_text"], nav["cash_text"], nav["stock_text"], nav["accruals_text"]),
                         ("8084740", "1000000", "7100000", "-15260"))
        position = self.db.execute("SELECT mark_price_text, market_value_text FROM position_snapshots "
                                   "WHERE account_id = ? AND snapshot_date = '2026-10-05'", (account,)).fetchone()
        self.assertEqual((position["mark_price_text"], position["market_value_text"]), ("71000", "7100000"))

    def test_a_day_stored_at_after_market_prices_is_collected_again(self):
        _, case_id = self._kiwoom_account("6434567890", start=date(2026, 9, 1))
        self.addCleanup(self._cancel, case_id)
        account = "KIWOOM-6434567890"
        self._run({"6434567890": FakeKiwoom(basis_day=date(2026, 10, 5))})
        # As an earlier build stored it: the position at ka01690's after-market price, below
        # Kiwoom's value of the holdings, and a selling cost Kiwoom's rule does not give.
        self.db.execute("UPDATE position_snapshots SET mark_price_text = '70000', market_value_text = '7000000' "
                        "WHERE account_id = ? AND snapshot_date = '2026-10-05'", (account,))
        self.db.execute("UPDATE nav_snapshots SET stock_text = '7000000', accruals_text = '84740' "
                        "WHERE account_id = ? AND snapshot_date = '2026-10-05'", (account,))
        self.db.commit()
        from ibkr_etl.kiwoom_collector import history_issues

        self.assertEqual([gap["check"] for gap in history_issues(self.db, account, date(2026, 10, 5),
                                                                 date(2026, 10, 5))], ["selling_cost"])
        self.db.execute("UPDATE nav_snapshots SET accruals_text = '-15260' "
                        "WHERE account_id = ? AND snapshot_date = '2026-10-05'", (account,))
        self.db.execute("UPDATE nav_snapshots SET total_text = '7984740' "
                        "WHERE account_id = ? AND snapshot_date = '2026-10-05'", (account,))
        self.db.commit()
        # Adds up and the cost is in band, but the position does not match Kiwoom's holdings value
        # once stock_text is Kiwoom's: here the cost rule catches it (15,050 is due on 7,000,000).
        self.assertEqual([gap["check"] for gap in history_issues(self.db, account, date(2026, 10, 5),
                                                                 date(2026, 10, 5))], ["selling_cost_rule"])
        self.db.execute("UPDATE nav_snapshots SET stock_text = '7100000', total_text = '8084740' "
                        "WHERE account_id = ? AND snapshot_date = '2026-10-05'", (account,))
        self.db.commit()
        self.assertEqual([gap["check"] for gap in history_issues(self.db, account, date(2026, 10, 5),
                                                                 date(2026, 10, 5))], ["holdings", "selling_cost_rule"])
        again = FakeKiwoom(basis_day=date(2026, 10, 5))
        self._run({"6434567890": again})
        self.assertEqual(again.asked, [date(2026, 10, 5)])
        position = self.db.execute("SELECT market_value_text FROM position_snapshots "
                                   "WHERE account_id = ? AND snapshot_date = '2026-10-05'", (account,)).fetchone()
        self.assertEqual(position["market_value_text"], "7100000")

    def test_a_day_the_daily_run_stored_is_collected_again_by_the_history_pass(self):
        _, case_id = self._kiwoom_account("6534567890", start=date(2026, 9, 1))
        self.addCleanup(self._cancel, case_id)
        account = "KIWOOM-6534567890"
        self._run({"6534567890": FakeKiwoom()})
        # As if the daily run, during KRX's after-market, had written the day last.
        daily = self.db.execute("SELECT max(run_id) AS run_id FROM ingestion_runs WHERE run_type = 'kiwoom_rest'"
                                ).fetchone()["run_id"]
        self.db.execute("UPDATE nav_snapshots SET last_seen_run_id = ? WHERE account_id = ? "
                        "AND snapshot_date = '2026-10-05'", (daily, account))
        self.db.commit()
        again = FakeKiwoom()
        self._run({"6534567890": again})
        self.assertEqual(again.asked, [date(2026, 10, 5)])
        run_type = self.db.execute("SELECT r.run_type FROM nav_snapshots n JOIN ingestion_runs r "
                                   "ON r.run_id = n.last_seen_run_id WHERE n.account_id = ? "
                                   "AND n.snapshot_date = '2026-10-05'", (account,)).fetchone()["run_type"]
        self.assertEqual(run_type, "kiwoom_rest_history")

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

    def test_releasing_the_first_month_end_closes_a_live_case(self):
        from ibkr_etl.onboarding_v2_web import record_first_month_end

        _, case_id = self._kiwoom_account("1034567890", start=date(2026, 10, 12))
        self.addCleanup(self._cancel, case_id)
        subject = "KIWOOM-1034567890"
        self._run({"1034567890": FakeKiwoom()})
        self._approve_terms(case_id)
        self.assertIsNone(record_first_month_end(self.db, TENANT, subject, "2026-10-31"))  # not live yet
        # Approval 2 and go-live are the website's; the case is moved straight to its live stage here.
        self.db.execute("UPDATE broker_onboarding_cases SET stage = 'first_month_end', status = 'live', "
                        "went_live_at = now() WHERE case_id = ?", (case_id,))
        outcome = record_first_month_end(self.db, TENANT, subject, "2026-10-31")
        self.db.commit()
        self.assertEqual(outcome["stage"], "closed")
        check = self.db.execute("SELECT status, evidence_json FROM broker_onboarding_checks WHERE case_id = ? "
                                "AND check_id = 'first_month_end_released'", (case_id,)).fetchone()
        self.assertEqual((check["status"], check["evidence_json"]), ("pass", {"period_end": "2026-10-31"}))
        # A later month's release finds no case waiting.
        self.assertIsNone(record_first_month_end(self.db, TENANT, subject, "2026-11-30"))

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


AMGN = SecurityIdentifier("us_ticker", "AMGN")
US_NOW = datetime(2026, 10, 20, 8, 30, tzinfo=timezone.utc)  # shows Monday 19 Oct's New York close


class FakeUsKiwoom:
    """A U.S.-only account: one stock bought on the first day, USD cash, Kiwoom's daily totals."""

    def __init__(self, *, extra_shares="0", listed=True):
        self.revoked, self.asked, self.extra, self.listed = False, [], Decimal(extra_shares), listed

    def valuation(self):
        raise AssertionError("A USD account is never read through the domestic calls")

    def us_valuation(self):
        from ibkr_etl.kiwoom_client import CurrencyBalance, UsValuation

        shares = Decimal("2") + self.extra
        return UsValuation(
            holdings=(Holding(AMGN, "암젠", shares, Decimal("306"), Decimal("400"), shares * 400, "USD", "미국"),),
            balances=(CurrencyBalance("USD", Decimal("100"), shares * 400, Decimal("1350")),))

    def us_transactions(self, start, end):
        if start <= date(2026, 9, 2) <= end:
            return [CashMovement("20260902", "매수", "매매", AMGN, Decimal("2"), Decimal("612"), "USD"),
                    CashMovement("20260915", "배당금(외화)입금", "입출금", AMGN, Decimal("0"), Decimal("4.25"), "USD")]
        return []

    def us_balances_on(self, day):
        from ibkr_etl.kiwoom_client import CurrencyBalance

        self.asked.append(day)
        return (CurrencyBalance("USD", Decimal("100"), Decimal("800"), Decimal("1400")),)

    def us_daily_closes(self, ticker, exchange, since, through):
        from ibkr_etl.kiwoom_client import KiwoomUnknownStock

        if not self.listed or exchange != "ND":
            raise KiwoomUnknownStock("Kiwoom refused the call usa06012 (HTTP 200, code 7): [1903:no such stock]")
        day, closes = since, {}
        while day <= through:
            closes[day] = Decimal("400")
            day += timedelta(days=1)
        return closes

    def us_stock_quantities(self, ticker, exchange, start, end):
        # Kiwoom counts the buy posted on 2 Sep from the U.S. trade day, 1 Sep.
        day, held = start, {}
        while day <= end:
            held[day] = Decimal("2") if day >= date(2026, 9, 1) else Decimal("0")
            day += timedelta(days=1)
        return held

    def revoke(self):
        self.revoked = True


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class KiwoomUsAccountDatabaseTests(KiwoomCollectorDatabaseTests):
    """A USD case is loaded in USD from the U.S. calls; only these tests run here (the Korea ones run above)."""

    def setUp(self):
        if not self._testMethodName.startswith("test_us_"):
            self.skipTest("Korea test, run in KiwoomCollectorDatabaseTests")
        super().setUp()

    def test_us_a_usd_case_loads_in_usd_and_passes_stage_two(self):
        connection_id, case_id = self._kiwoom_account("9900000001", start=date(2026, 9, 1), currency="USD")
        self.addCleanup(self._cancel, case_id)
        client = FakeUsKiwoom()
        result = self._run({"9900000001": client}, now=US_NOW)[0]
        self.assertEqual((result["status"], result["market"]), ("loaded", "US"))
        self.assertTrue(client.revoked)
        self.assertEqual(self._checks(case_id), {
            "connection_sees_account": "pass", "account_not_already_onboarded": "pass",
            "holdings_and_cash_loaded": "pass", "transactions_loaded": "pass", "securities_mapped": "pass",
            "nav_reconciled": "pass"})
        account = "KIWOOM-9900000001"
        nav = self.db.execute("SELECT currency, total_text, stock_text, cash_text FROM nav_snapshots "
                              "WHERE account_id = ? AND snapshot_date = '2026-10-19'", (account,)).fetchone()
        self.assertEqual((nav["currency"], nav["total_text"], nav["stock_text"], nav["cash_text"]),
                         ("USD", "900", "800", "100"))
        base = self.db.execute("SELECT base_currency FROM accounts WHERE account_id = ?", (account,)).fetchone()
        self.assertEqual(base["base_currency"], "USD")
        kinds = {row["transaction_type"] for row in self.db.execute(
            "SELECT transaction_type FROM cash_transactions WHERE account_id = ?", (account,)).fetchall()}
        self.assertEqual(kinds, {"DIVIDEND"})
        self.assertEqual(self.db.execute("SELECT base_currency FROM broker_connection_accounts "
                                         "WHERE connection_id = ?", (connection_id,)).fetchone()["base_currency"],
                         "USD")

    def test_us_shares_that_do_not_add_up_fail_transactions(self):
        _, case_id = self._kiwoom_account("9900000002", start=date(2026, 9, 1), currency="USD")
        self.addCleanup(self._cancel, case_id)
        self._run({"9900000002": FakeUsKiwoom(extra_shares="1")}, now=US_NOW)
        self.assertEqual(self._checks(case_id)["transactions_loaded"], "fail")

    def test_us_history_rebuilds_each_day_from_kiwoom(self):
        _, case_id = self._kiwoom_account("9900000003", start=date(2026, 9, 1), currency="USD")
        self.addCleanup(self._cancel, case_id)
        client = FakeUsKiwoom()
        result = self._run({"9900000003": client}, now=US_NOW)[0]
        # Load and reconcile: past New York sessions are filled, no check recorded yet.
        self.assertEqual(result["history"]["recorded"], False)
        self.assertNotIn(date(2026, 9, 7), client.asked)  # Labor Day
        self.assertEqual(client.asked[0], date(2026, 9, 1))
        self.assertEqual(client.asked[-1], date(2026, 10, 16))
        self.assertEqual(result["history"]["check"], "pass")
        account = "KIWOOM-9900000003"
        stored = self.db.execute("SELECT count(*) AS n FROM nav_snapshots WHERE account_id = ? AND currency = 'USD'",
                                 (account,)).fetchone()["n"]
        self.assertEqual(stored, len(client.asked) + 1)
        first = self.db.execute("SELECT quantity_text, mark_price_text, market_value_text FROM position_snapshots "
                                "WHERE account_id = ? AND snapshot_date = '2026-09-01'", (account,)).fetchone()
        self.assertEqual((first["quantity_text"], first["mark_price_text"], first["market_value_text"]),
                         ("2", "400", "800"))

    def test_us_a_stock_kiwoom_does_not_list_takes_the_rest_of_its_total(self):
        _, case_id = self._kiwoom_account("9900000005", start=date(2026, 9, 1), currency="USD")
        self.addCleanup(self._cancel, case_id)
        client = FakeUsKiwoom(listed=False)
        result = self._run({"9900000005": client}, now=US_NOW)[0]
        # Still held today, so the stock must have Kiwoom's close: the past days wait.
        self.assertEqual(result["history"]["check"], "pending")

    def test_us_the_history_pass_keeps_the_daily_positions_that_add_up(self):
        _, case_id = self._kiwoom_account("9900000004", start=date(2026, 10, 1), currency="USD")
        self.addCleanup(self._cancel, case_id)
        account = "KIWOOM-9900000004"
        self._run({"9900000004": FakeUsKiwoom()}, now=US_NOW)
        client = FakeUsKiwoom()
        self._run({"9900000004": client}, now=US_NOW + timedelta(days=1))
        self.assertIn(date(2026, 10, 19), client.asked)
        row = self.db.execute(
            "SELECT r.run_type, (SELECT count(*) FROM position_snapshots p WHERE p.account_id = n.account_id "
            "AND p.snapshot_date = n.snapshot_date) AS positions FROM nav_snapshots n "
            "JOIN ingestion_runs r ON r.run_id = n.last_seen_run_id WHERE n.account_id = ? "
            "AND n.snapshot_date = '2026-10-19'", (account,)).fetchone()
        self.assertEqual((row["run_type"], row["positions"]), ("kiwoom_rest_us_history", 1))
