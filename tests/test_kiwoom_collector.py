"""The Kiwoom collector's row building and stage checks, with synthetic Kiwoom answers only."""
from __future__ import annotations

import importlib.util
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from ibkr_etl.kiwoom_client import AccountValuation, CashMovement, CurrencyBalance, Holding, UsValuation
from ibkr_etl.kiwoom_collector import (
    Securities,
    classify,
    collector_enabled,
    movement_rows,
    platform_account_id,
    snapshot_rows,
    trading_weekdays,
    stage_two_checks,
    windows,
    Collected,
)
from ibkr_etl.security_master import SecurityIdentifier

ROOT = Path(__file__).resolve().parents[1]
SAMSUNG = SecurityIdentifier("krx_code", "005930")
HYNIX = SecurityIdentifier("krx_code", "000660")
APPLE = SecurityIdentifier("us_ticker", "AAPL")


def _valuation(deposit="1000000", total="8200000"):
    return AccountValuation(
        account_name="Synthetic",
        deposit=Decimal(deposit),
        estimated_assets=Decimal(total),
        holdings=(
            Holding(SAMSUNG, "삼성전자", Decimal("100"), Decimal("60000"), Decimal("70000"), Decimal("7000000")),
            Holding(HYNIX, "SK하이닉스", Decimal("1"), Decimal("150000"), Decimal("200000"), Decimal("200000")),
        ),
    )


def _us():
    return UsValuation(
        holdings=(Holding(APPLE, "Apple", Decimal("2"), Decimal("150"), Decimal("200"), Decimal("400"), "USD",
                          "NASDAQ", Decimal("560000")),),
        balances=(CurrencyBalance("USD", Decimal("100"), Decimal("400"), Decimal("1400")),),
    )


def _move(day, kind, amount, *, security=None, quantity="0", currency="KRW", description=""):
    return CashMovement(day, description, kind, security, Decimal(quantity), Decimal(amount), currency)


class SwitchAndDatesTests(unittest.TestCase):
    def test_off_unless_set_exactly(self):
        self.assertFalse(collector_enabled({}))
        self.assertFalse(collector_enabled({"KIWOOM_COLLECTOR_ENABLED": "yes"}))
        self.assertTrue(collector_enabled({"KIWOOM_COLLECTOR_ENABLED": " TRUE "}))

    def test_history_asks_for_weekdays_only(self):
        days = trading_weekdays(date(2026, 10, 16), date(2026, 10, 20))
        self.assertEqual(days, [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)])
        self.assertEqual(trading_weekdays(date(2026, 10, 20), date(2026, 10, 19)), [])

    def test_windows_cover_the_range_without_overlap(self):
        spans = windows(date(2025, 1, 1), date(2025, 7, 1), days=90)
        self.assertEqual(spans[0], (date(2025, 1, 1), date(2025, 3, 31)))
        self.assertEqual(spans[-1][1], date(2025, 7, 1))
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertEqual((start - end).days, 1)
        self.assertEqual(windows(date(2025, 1, 2), date(2025, 1, 1)), [])

    def test_snapshot_day_follows_the_krx_close(self):
        spec = importlib.util.spec_from_file_location("run_kiwoom_collector", ROOT / "scripts" / "run_kiwoom_collector.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # 17:20 KST on a Monday (the daily job's 08:20 UTC): that Monday's close.
        self.assertEqual(module.snapshot_day(datetime(2026, 10, 19, 8, 20, tzinfo=timezone.utc)), date(2026, 10, 19))
        # 08:00 KST on a Monday, before the open: Friday's close.
        self.assertEqual(module.snapshot_day(datetime(2026, 10, 18, 23, 0, tzinfo=timezone.utc)), date(2026, 10, 16))
        # Saturday: Friday's close.
        self.assertEqual(module.snapshot_day(datetime(2026, 10, 17, 8, 20, tzinfo=timezone.utc)), date(2026, 10, 16))


class RowTests(unittest.TestCase):
    def test_account_id_is_the_case_subject(self):
        self.assertEqual(platform_account_id("1234567890"), "KIWOOM-1234567890")

    def test_holdings_use_a_known_ibkr_key_or_a_new_krx_key(self):
        securities = Securities(known={SAMSUNG: "265598110"})
        rows = snapshot_rows("KIWOOM-1234567890", date(2026, 10, 19), _valuation(), None, securities)
        keys = [row["conid"] for row in rows["position_snapshots"]]
        self.assertEqual(keys, ["265598110", "KRX:000660"])
        self.assertEqual([item.key for item in securities.new.values()], ["KRX:000660"])
        self.assertEqual(securities.new["KRX:000660"].name, "SK하이닉스")
        samsung = rows["position_snapshots"][0]
        self.assertEqual((samsung["quantity_text"], samsung["mark_price_text"], samsung["market_value_text"],
                          samsung["cost_basis_text"], samsung["unrealized_pnl_text"]),
                         ("100", "70000", "7000000", "6000000", "1000000"))
        self.assertEqual(rows["nav_snapshots"][0]["total_text"], "8200000")
        self.assertEqual(rows["nav_snapshots"][0]["stock_text"], "7200000")
        self.assertEqual(rows["cash_snapshots"], [{
            "account_id": "KIWOOM-1234567890", "snapshot_date": "2026-10-19", "currency": "KRW",
            "ending_cash_text": "1000000", "settled_cash_text": "1000000", "fx_rate_to_base_text": "1"}])
        self.assertEqual(rows["fx_rates"], [])

    def test_us_account_adds_to_the_krw_total_at_kiwooms_rate(self):
        rows = snapshot_rows("KIWOOM-1", date(2026, 10, 19), _valuation(), _us(), Securities(known={}))
        nav = rows["nav_snapshots"][0]
        # 8,200,000 + (400 + 100) * 1,400
        self.assertEqual(nav["total_text"], "8900000")
        self.assertEqual(nav["cash_text"], "1140000")
        self.assertEqual(nav["stock_text"], "7760000")
        self.assertEqual(rows["fx_rates"][0]["rate_text"], "1400")
        self.assertIn("US:AAPL", [row["conid"] for row in rows["position_snapshots"]])
        self.assertEqual([row["currency"] for row in rows["cash_snapshots"]], ["KRW", "USD"])

    def test_movements_become_signed_trades_and_cash(self):
        movements = [
            _move("2025-01-03", "입금", "5000000"),
            _move("2025-01-06", "매수", "6000000", security=SAMSUNG, quantity="100"),
            _move("2025-02-10", "매도", "200000", security=HYNIX, quantity="1"),
            _move("2025-04-15", "배당금입금", "36100", security=SAMSUNG),
            _move("2025-05-01", "출금", "-100000"),
            _move("2025-05-01", "출금", "-100000"),
        ]
        rows, unknown = movement_rows("KIWOOM-1", movements, Securities(known={}))
        self.assertEqual(unknown, [])
        buy, sell = rows["trades"]
        self.assertEqual((buy["buy_sell"], buy["quantity_text"], buy["net_cash_text"], buy["trade_price_text"]),
                         ("BUY", "100", "-6000000", "60000"))
        self.assertEqual((sell["buy_sell"], sell["quantity_text"], sell["net_cash_text"]), ("SELL", "-1", "200000"))
        kinds = [(row["transaction_type"], row["amount_text"], row["conid"]) for row in rows["cash_transactions"]]
        self.assertEqual(kinds, [("DEPOSIT", "5000000", None), ("DIVIDEND", "36100", "KRX:005930"),
                                 ("WITHDRAWAL", "-100000", None), ("WITHDRAWAL", "-100000", None)])
        # Two identical withdrawals on one day are both kept.
        self.assertEqual(len({row["source_key"] for row in rows["cash_transactions"]}), 4)

    def test_source_keys_are_stable_across_runs(self):
        movements = [_move("2025-01-03", "입금", "5000000")]
        first, _ = movement_rows("KIWOOM-1", movements, Securities(known={}))
        second, _ = movement_rows("KIWOOM-1", movements, Securities(known={}))
        self.assertEqual(first["cash_transactions"][0]["source_key"], second["cash_transactions"][0]["source_key"])

    def test_unknown_labels_are_listed_not_guessed(self):
        self.assertIsNone(classify(_move("2025-01-03", "기타정산", "1")))
        rows, unknown = movement_rows("KIWOOM-1", [_move("2025-01-03", "기타정산", "1")], Securities(known={}))
        self.assertEqual(unknown, ["2025-01-03 기타정산"])
        self.assertEqual(rows, {"trades": [], "cash_transactions": []})


class CheckTests(unittest.TestCase):
    def _collected(self, valuation=None, us=None, movements=(), unknown=()):
        valuation = valuation or _valuation()
        securities = Securities(known={})
        rows = snapshot_rows("KIWOOM-1", date(2026, 10, 19), valuation, us, securities)
        moved, _ = movement_rows("KIWOOM-1", movements, securities)
        rows.update(moved)
        return Collected("KIWOOM-1", date(2026, 10, 19), valuation, us, tuple(movements), rows, tuple(unknown),
                         tuple(securities.new[key] for key in sorted(securities.new)))

    def test_a_matching_account_passes_all_four(self):
        checks = stage_two_checks(self._collected(movements=[_move("2025-01-03", "입금", "1")]),
                                  history_start=date(2025, 1, 2))
        self.assertEqual({key: value["status"] for key, value in checks.items()}, {
            "holdings_and_cash_loaded": "pass", "transactions_loaded": "pass", "securities_mapped": "pass",
            "nav_reconciled": "pass"})
        self.assertEqual(checks["securities_mapped"]["evidence"]["new_securities"], ["KRX:000660", "KRX:005930"])

    def test_a_gap_to_kiwooms_total_fails(self):
        checks = stage_two_checks(self._collected(valuation=_valuation(total="8300000")),
                                  history_start=date(2025, 1, 2))
        self.assertEqual(checks["nav_reconciled"]["status"], "fail")
        self.assertEqual(checks["nav_reconciled"]["evidence"]["gap"], "-100000")

    def test_unknown_labels_fail_and_an_empty_history_warns(self):
        failed = stage_two_checks(self._collected(unknown=["2025-01-03 기타정산"]), history_start=date(2025, 1, 2))
        self.assertEqual(failed["transactions_loaded"]["status"], "fail")
        self.assertIn("기타정산", failed["transactions_loaded"]["message"])
        empty = stage_two_checks(self._collected(), history_start=date(2025, 1, 2))
        self.assertEqual(empty["transactions_loaded"]["status"], "warn")

    def test_us_holdings_are_reconciled_too(self):
        checks = stage_two_checks(self._collected(us=_us()), history_start=date(2025, 1, 2))
        self.assertEqual(checks["nav_reconciled"]["status"], "pass")
        self.assertEqual(set(checks["nav_reconciled"]["evidence"]), {"korea", "us"})


if __name__ == "__main__":
    unittest.main()
