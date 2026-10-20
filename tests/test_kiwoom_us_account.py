"""The USD Kiwoom account path (U.S. stocks only), with synthetic Kiwoom answers shaped like the live ones."""
from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from ibkr_etl.kiwoom_client import CashMovement, CurrencyBalance, Holding, KiwoomError, UsValuation
from ibkr_etl.kiwoom_collector import Securities
from ibkr_etl.kiwoom_us_account import (
    UsDay,
    day_from_balances,
    is_usd_account,
    position_gap,
    share_counts,
    us_checks,
    us_classify,
    us_movement_rows,
    us_rows,
    us_snapshot_day,
    us_trading_days,
)
from ibkr_etl.security_master import SecurityIdentifier

AMGN = SecurityIdentifier("us_ticker", "AMGN")
DFS = SecurityIdentifier("us_ticker", "DFS")
COF = SecurityIdentifier("us_ticker", "COF")
ACCOUNT = "KIWOOM-SYNTHETIC"


def _move(day, label, kind, security, quantity, amount):
    return CashMovement(day, label, kind, security, Decimal(quantity), Decimal(amount), "USD")


# The live account's labels (ust21100 rmrk_nm / deal_kind_nm).
MOVES = [
    _move("20250328", "매수", "매매", AMGN, "2", "612.10"),
    _move("20250328", "매수", "매매", DFS, "4", "677.80"),
    _move("20250418", "배당금(외화)입금", "입출금", AMGN, "0", "4.25"),
    _move("20250522", "감자액면병합출고", "입출고", DFS, "4", "0.00"),
    _move("20250522", "감자액면병합입고", "입출고", COF, "4", "0.00"),
    _move("20250522", "합병단주대금입금(외화)", "입출금", DFS, "0", "15.14"),
    _move("20250730", "매도", "매매", AMGN, "1", "300.00"),
]


def _valuation():
    return UsValuation(
        holdings=(Holding(AMGN, "암젠", Decimal("1"), Decimal("306.05"), Decimal("426.69"), Decimal("426.69"), "USD",
                          "미국", Decimal("569000")),
                  Holding(COF, "캐피탈 원", Decimal("4"), Decimal("169.45"), Decimal("200.00"), Decimal("800.00"), "USD",
                          "미국", Decimal("1066000"))),
        balances=(CurrencyBalance("USD", Decimal("2642.89"), Decimal("1226.69"), Decimal("1332.10")),),
    )


class ChoiceTests(unittest.TestCase):
    def test_only_a_usd_case_takes_this_path(self):
        self.assertTrue(is_usd_account("USD"))
        self.assertTrue(is_usd_account(" usd "))
        for other in ("KRW", "", None, "HKD"):
            self.assertFalse(is_usd_account(other))

    def test_snapshot_day_is_the_last_new_york_session(self):
        # 08:20 UTC Tuesday 20 Oct 2026 shows Monday's close; Monday 7 Sep 2026 (Labor Day) shows Friday.
        self.assertEqual(us_snapshot_day(datetime(2026, 10, 20, 8, 20, tzinfo=timezone.utc)), date(2026, 10, 19))
        self.assertEqual(us_snapshot_day(datetime(2026, 9, 8, 8, 20, tzinfo=timezone.utc)), date(2026, 9, 4))
        self.assertEqual(us_snapshot_day(datetime(2026, 10, 20, 21, 30, tzinfo=timezone.utc)), date(2026, 10, 20))

    def test_history_asks_for_nyse_sessions_only(self):
        days = us_trading_days(date(2025, 4, 16), date(2025, 4, 22))
        self.assertNotIn(date(2025, 4, 18), days)  # Good Friday
        self.assertEqual(days, [date(2025, 4, 16), date(2025, 4, 17), date(2025, 4, 21), date(2025, 4, 22)])


class LabelTests(unittest.TestCase):
    def test_every_live_label_is_read(self):
        for movement in MOVES:
            self.assertIsNotNone(us_classify(movement), movement.description)

    def test_corporate_action_cash_is_never_a_deposit(self):
        kinds = {m.description: us_classify(m)[0] for m in MOVES}
        self.assertEqual(kinds["합병단주대금입금(외화)"], "CORPORATE_ACTION_CASH")
        self.assertEqual(us_classify(_move("20260122", "유상소각(해외)입금", "입출금", None, "0", "770"))[0],
                         "CORPORATE_ACTION_CASH")
        self.assertNotIn("DEPOSIT", kinds.values())

    def test_share_counts_replay_buys_sells_and_a_merger(self):
        self.assertEqual(share_counts(MOVES), {"AMGN": Decimal("1"), "COF": Decimal("4")})

    def test_rows_are_signed_and_unknown_labels_listed(self):
        securities = Securities(known={})
        moved, unknown = us_movement_rows(
            ACCOUNT, [*MOVES, _move("20250801", "환전", "환전", None, "0", "100")], securities)
        self.assertEqual(unknown, ["20250801 환전"])
        trades = {(row["buy_sell"], row["quantity_text"], row["net_cash_text"]) for row in moved["trades"]}
        self.assertIn(("BUY", "2", "-612.10"), trades)
        self.assertIn(("SELL", "-1", "300"), trades)
        self.assertIn(("CORPORATE_ACTION_OUT", "-4", "0"), trades)
        self.assertIn(("CORPORATE_ACTION_IN", "4", "0"), trades)
        cash = {(row["transaction_type"], row["amount_text"]) for row in moved["cash_transactions"]}
        self.assertEqual(cash, {("DIVIDEND", "4.25"), ("CORPORATE_ACTION_CASH", "15.14")})


class RowAndCheckTests(unittest.TestCase):
    def test_rows_are_in_usd_and_add_up_to_kiwoom_total(self):
        valuation = _valuation()
        figures = day_from_balances(date(2026, 10, 19), valuation.balances)
        rows = us_rows(ACCOUNT, figures, valuation.holdings, Securities(known={}))
        nav = rows["nav_snapshots"][0]
        self.assertEqual(nav["currency"], "USD")
        self.assertEqual(Decimal(nav["total_text"]), Decimal("3869.58"))
        self.assertEqual(Decimal(nav["stock_text"]) + Decimal(nav["cash_text"]), Decimal(nav["total_text"]))
        self.assertEqual(rows["fx_rates"][0]["from_currency"], "KRW")
        self.assertAlmostEqual(float(rows["fx_rates"][0]["rate_text"]) * 1332.10, 1.0, places=9)
        self.assertEqual(position_gap(figures, valuation.holdings), Decimal("0"))

    def test_other_currencies_are_refused(self):
        with self.assertRaisesRegex(KiwoomError, "other than USD"):
            day_from_balances(date(2026, 10, 19), (CurrencyBalance("USD", Decimal(1), Decimal(1), Decimal(1300)),
                                                   CurrencyBalance("HKD", Decimal(5), Decimal(0), Decimal(170))))

    def test_checks_pass_when_movements_add_up_to_todays_shares(self):
        valuation = _valuation()
        figures = day_from_balances(date(2026, 10, 19), valuation.balances)
        securities = Securities(known={})
        rows = us_rows(ACCOUNT, figures, valuation.holdings, securities)
        moved, unknown = us_movement_rows(ACCOUNT, MOVES, securities)
        checks = us_checks(day=figures.day, history_start=date(2025, 3, 27), valuation=valuation, figures=figures,
                           rows={**rows, **moved}, unknown=unknown, new_securities=[], movements=MOVES)
        self.assertEqual({k: v["status"] for k, v in checks.items()},
                         {"holdings_and_cash_loaded": "pass", "transactions_loaded": "pass",
                          "securities_mapped": "pass", "nav_reconciled": "pass"})

    def test_a_missing_movement_fails_the_transactions_check(self):
        valuation = _valuation()
        figures = UsDay(date(2026, 10, 19), Decimal("2642.89"), Decimal("1226.69"), Decimal("1332.10"))
        securities = Securities(known={})
        rows = us_rows(ACCOUNT, figures, valuation.holdings, securities)
        moved, _ = us_movement_rows(ACCOUNT, MOVES[:-1], securities)
        checks = us_checks(day=figures.day, history_start=date(2025, 3, 27), valuation=valuation, figures=figures,
                           rows={**rows, **moved}, unknown=[], new_securities=[], movements=MOVES[:-1])
        self.assertEqual(checks["transactions_loaded"]["status"], "fail")
        self.assertIn("AMGN", checks["transactions_loaded"]["message"])


if __name__ == "__main__":
    unittest.main()
