"""The USD Kiwoom account path (U.S. stocks only), with synthetic Kiwoom answers shaped like the live ones."""
from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from ibkr_etl.kiwoom_client import CashMovement, CurrencyBalance, Holding, KiwoomError, KiwoomUnknownStock, UsValuation
from ibkr_etl.kiwoom_collector import Securities
from ibkr_etl.kiwoom_us_account import (
    UsDay,
    UsHistoryPositions,
    balance_gaps,
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
        # New York time decides, so the switch moves with daylight saving: 16:30 EST on 2 Nov is before it.
        self.assertEqual(us_snapshot_day(datetime(2026, 11, 2, 21, 30, tzinfo=timezone.utc)), date(2026, 10, 30))
        self.assertEqual(us_snapshot_day(datetime(2026, 11, 2, 22, 5, tzinfo=timezone.utc)), date(2026, 11, 2))

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

    def test_the_website_sees_english_labels_not_kiwoom_korean(self):
        moved, _ = us_movement_rows(ACCOUNT, [*MOVES, *LIVE], Securities(known={}))
        texts = [row["description"] for row in moved["cash_transactions"]]
        texts += [row["order_reference"] for row in moved["trades"] if row["order_reference"]]
        self.assertTrue(texts)
        self.assertTrue(all(text.isascii() for text in texts), texts)
        labels = {(row["transaction_type"], row["description"]) for row in moved["cash_transactions"]}
        self.assertIn(("DIVIDEND", "Dividend"), labels)
        self.assertIn(("CORPORATE_ACTION_CASH", "Corporate action cash"), labels)


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



class FakeHistoryKiwoom:
    """Kiwoom's closes and per-stock daily holdings for a synthetic account; GONE is no longer listed."""

    def __init__(self, closes, quantities):
        self.closes, self.quantities, self.calls = closes, quantities, []

    def us_daily_closes(self, ticker, exchange, since, through):
        self.calls.append(("usa06012", ticker, exchange))
        if ticker not in self.closes or exchange != "NY":
            raise KiwoomUnknownStock("[1903:no such stock]")
        return {day: price for day, price in self.closes[ticker].items() if since <= day <= through}

    def us_stock_quantities(self, ticker, exchange, start, end):
        self.calls.append(("usa21730", ticker, exchange))
        return {day: count for day, count in self.quantities.get(ticker, {}).items() if start <= day <= end}


def _days(*pairs):
    return {date.fromisoformat(day): Decimal(value) for day, value in pairs}


GONE = SecurityIdentifier("us_ticker", "GONE")
LATE = SecurityIdentifier("us_ticker", "LATE")


class HistoryPositionTests(unittest.TestCase):
    def setUp(self):
        # AMGN bought on Mon 2 Mar 2026 (posted Wed 4 Mar, Kiwoom counts it from 2 Mar); GONE bought with it and
        # cancelled for cash posted 6 Mar; LATE bought on 3 Mar and cancelled later, never listed either.
        self.moves = [
            _move("20260304", "매수", "매매", AMGN, "2", "600.00"),
            _move("20260304", "매수", "매매", GONE, "10", "300.00"),
            _move("20260305", "매수", "매매", LATE, "5", "100.00"),
            _move("20260306", "유상소각출고(적요)", "입출고", GONE, "10", "0"),
            _move("20260310", "유상소각출고(적요)", "입출고", LATE, "5", "0"),
        ]
        self.kiwoom = FakeHistoryKiwoom(
            {"AMGN": _days(("2026-02-27", "290"), ("2026-03-02", "300"), ("2026-03-03", "310"),
                           ("2026-03-04", "305"), ("2026-03-05", "320"), ("2026-03-06", "330"))},
            {"AMGN": _days(("2026-02-27", "0"), ("2026-03-01", "0"), ("2026-03-02", "2"), ("2026-03-03", "2"),
                           ("2026-03-04", "2"), ("2026-03-05", "2"))})
        self.positions = UsHistoryPositions(self.kiwoom, self.moves, since=date(2026, 2, 27),
                                            through=date(2026, 3, 6), held_today=["AMGN"])

    def _on(self, day, securities_value):
        held = self.positions(date.fromisoformat(day), UsDay(date.fromisoformat(day), Decimal("0"),
                                                             Decimal(securities_value), Decimal("1400")))
        return {h.code: (h.quantity, h.price, h.market_value) for h in held}

    def test_a_trade_counts_from_the_day_kiwoom_counts_it(self):
        self.assertEqual(self._on("2026-02-27", "0"), {})
        held = self._on("2026-03-02", "900")
        self.assertEqual(held["AMGN"], (Decimal("2"), Decimal("300"), Decimal("600")))
        # GONE has no per-stock holding at Kiwoom; it counts from the day the trade posted with it counts from.
        self.assertEqual(held["GONE"], (Decimal("10"), Decimal("30"), Decimal("300")))

    def test_one_unlisted_stock_takes_the_rest_of_kiwooms_total(self):
        held = self._on("2026-03-02", "912.50")
        self.assertEqual(held["GONE"], (Decimal("10"), Decimal("31.25"), Decimal("312.50")))
        self.assertEqual(sum(value for *_, value in held.values()), Decimal("912.50"))

    def test_with_two_unlisted_the_one_leaving_soonest_takes_the_rest(self):
        self._on("2026-03-02", "912.50")
        # LATE is two U.S. sessions before its posting date (no other trade posted with it): Tue 3 Mar.
        held = self._on("2026-03-03", "1050")
        self.assertEqual(held["LATE"], (Decimal("5"), Decimal("20"), Decimal("100")))  # its trade price
        self.assertEqual(held["GONE"][2], Decimal("1050") - 620 - 100)

    def test_a_corporate_action_counts_from_its_posting_date(self):
        self.assertIn("GONE", self._on("2026-03-05", "1000"))
        self.assertNotIn("GONE", self._on("2026-03-06", "760"))

    def test_a_stock_still_held_without_a_close_leaves_the_day_open(self):
        positions = UsHistoryPositions(FakeHistoryKiwoom({}, {}), self.moves, since=date(2026, 2, 27),
                                       through=date(2026, 3, 6), held_today=["AMGN"])
        self.assertIsNone(positions(date(2026, 3, 2), UsDay(date(2026, 3, 2), Decimal(0), Decimal(900),
                                                            Decimal(1400))))

    def test_kiwoom_is_asked_once_per_run(self):
        self._on("2026-03-02", "900")
        asked = len(self.kiwoom.calls)
        self._on("2026-03-03", "1050")
        self.assertEqual(len(self.kiwoom.calls), asked)
        self.assertIn(("usa06012", "AMGN", "NY"), self.kiwoom.calls)


def _row(day, order, label, kind, security, quantity, amount, balance):
    return CashMovement(day, label, kind, security, Decimal(quantity), Decimal(amount), "USD",
                        balance=Decimal(balance), order=order)


TGT = SecurityIdentifier("us_ticker", "TGT")
# Kiwoom's own rows around 26 Nov 2025 on the live account (amounts and cash after each row, ust21100):
# deal 1 of 26 Nov is missing from the list, and the cash after the VRSN dividend is 377.20 above the cash
# before it plus the dividend. A split's shares-out row shows a cash of zero; the TGT sale's first fill
# carries no cash and the second carries both.
LIVE = [
    _row("2025-11-21", "11:00:00|000000001", "배당금(외화)입금", "입출금", AMGN, "0", "2.00", "778.44"),
    _row("2025-11-26", "11:38:11|000000002", "배당금(외화)입금", "입출금", AMGN, "0", "1.96", "1157.60"),
    _row("2025-11-27", "08:09:58|000000001", "매도", "매매", DFS, "4", "993.03", "2150.63"),
    _row("2025-11-27", "08:09:58|000000002", "매수", "매매", COF, "16", "1534.78", "615.85"),
    _row("2025-12-01", "08:09:08|000000001", "매도", "매매", TGT, "3", "0.00", "615.85"),
    _row("2025-12-01", "08:09:08|000000002", "매도", "매매", TGT, "3", "517.92", "1133.77"),
    _row("2025-12-01", "09:00:00|000000003", "액면분할병합출고", "입출고", AMGN, "2", "0.00", "0.00"),
    _row("2025-12-01", "09:00:00|000000004", "액면분할병합입고", "입출고", AMGN, "4", "0.00", "0.00"),
    _row("2025-12-02", "10:35:38|000000001", "배당금(외화)입금", "입출금", COF, "0", "4.25", "1139.84"),
    _row("2026-01-08", "08:10:16|000000003", "매수", "매매", AMGN, "1", "898.16", "241.68"),
    _row("2026-01-08", "10:15:40|000000005", "배당금(외화)입금", "입출금", AMGN, "0", "2.55", "411.42"),
]


class BalanceGapTests(unittest.TestCase):
    def test_kiwooms_cash_jumps_are_read_as_deposits(self):
        gaps = balance_gaps(LIVE)
        self.assertEqual([(g.trade_date, g.kind, g.amount) for g in gaps],
                         [("2025-11-26", "DEPOSIT", Decimal("377.20")), ("2026-01-08", "DEPOSIT", Decimal("167.19"))])

    def test_won_covering_an_overdraft_is_dated_with_the_row_that_overdrew(self):
        # 28 Mar 2025: the opening buys left the cash at -14.32; Kiwoom's daily cash is 0.00 the next day,
        # and the next listed row is a dividend on 18 Apr.
        rows = [_row("2025-03-28", "08:11:41|000000049", "매수", "매매", AMGN, "1", "671.85", "697.28"),
                _row("2025-03-28", "08:11:41|000000050", "매수", "매매", COF, "1", "711.60", "-14.32"),
                _row("2025-04-18", "11:09:00|000000001", "배당금(외화)입금", "입출금", AMGN, "0", "0.89", "0.89")]
        self.assertEqual([(g.trade_date, g.kind, g.amount) for g in balance_gaps(rows)],
                         [("2025-03-28", "DEPOSIT", Decimal("14.32"))])

    def test_rows_are_replayed_in_kiwooms_order_whatever_order_they_arrive_in(self):
        self.assertEqual(balance_gaps(list(reversed(LIVE))), balance_gaps(LIVE))

    def test_a_withdrawal_and_small_adjustments(self):
        rows = [_row("2026-04-01", "10:00:00|000000001", "배당금(외화)입금", "입출금", AMGN, "0", "4.25", "2159.39"),
                _row("2026-04-14", "16:48:23|000000001", "배당금(외화)입금", "입출금", AMGN, "0", "4.25", "2165.46"),
                _row("2026-04-20", "10:00:00|000000001", "배당금(외화)입금", "입출금", AMGN, "0", "1.00", "1166.46")]
        # The 1.82 USD dividend adjustment stays income; the 1,000 USD drop is a withdrawal.
        self.assertEqual([(g.kind, g.amount) for g in balance_gaps(rows)], [("WITHDRAWAL", Decimal("-1000.00"))])

    def test_the_first_cash_only_starts_the_chain(self):
        self.assertEqual(balance_gaps(LIVE[1:2]), [])

    def test_an_unread_label_breaks_the_chain_rather_than_guessing(self):
        rows = [LIVE[0], _row("2025-11-24", "09:00:00|000000001", "환전", "환전", None, "0", "50", "828.44"), LIVE[1]]
        self.assertEqual(balance_gaps(rows), [])

    def test_deposits_are_stored_as_cash_rows_and_listed_movements_are_unchanged(self):
        moved, unknown = us_movement_rows(ACCOUNT, LIVE, Securities(known={}))
        self.assertEqual(unknown, [])
        deposits = [(row["effective_date"], row["transaction_type"], row["amount_text"], row["currency"])
                    for row in moved["cash_transactions"] if row["transaction_type"] in ("DEPOSIT", "WITHDRAWAL")]
        self.assertEqual(deposits, [("2025-11-26", "DEPOSIT", "377.20", "USD"), ("2026-01-08", "DEPOSIT", "167.19", "USD")])
        again, _ = us_movement_rows(ACCOUNT, LIVE, Securities(known={}))
        self.assertEqual([r["source_key"] for r in again["cash_transactions"]],
                         [r["source_key"] for r in moved["cash_transactions"]])
        # Movements without Kiwoom's cash (the Korea list, older fixtures) give no deposits.
        plain, _ = us_movement_rows(ACCOUNT, MOVES, Securities(known={}))
        self.assertNotIn("DEPOSIT", {row["transaction_type"] for row in plain["cash_transactions"]})


if __name__ == "__main__":
    unittest.main()
