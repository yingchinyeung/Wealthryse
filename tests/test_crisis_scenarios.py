"""Crisis scenarios on made-up prices and holdings only."""
from __future__ import annotations

import os
import sqlite3
import unittest
from datetime import date, timedelta
from unittest import mock

from ibkr_etl import crisis_scenarios as cs


ORIGIN = date(2000, 1, 3)


def weekdays(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def in_window(day: date) -> bool:
    return any(s.start <= day <= s.end for s in cs.SCENARIOS)


def daily_return(day: date) -> float:
    if in_window(day):
        return -0.005
    index = (day - ORIGIN).days // 7
    return 0.01 if index % 2 == 0 else -0.008


def make_levels(multiplier: float, first: date, last: date = date(2026, 10, 1)) -> dict[str, float]:
    levels, level = {}, 100.0
    for day in weekdays(first, last):
        level *= 1 + multiplier * daily_return(day)
        levels[day.isoformat()] = level
    return levels


SERIES = {
    "SPY": make_levels(1.0, ORIGIN),
    "OLD": make_levels(1.0, ORIGIN),        # trades through every window
    "NEW": make_levels(2.0, date(2024, 1, 2)),  # listed recently; twice as sensitive
    "^HSI": make_levels(1.0, ORIGIN),
}


class FakePrices(cs.Prices):
    def history(self, symbol, start, end):
        return {d: v for d, v in SERIES.get(symbol, {}).items() if start.isoformat() <= d <= end.isoformat()}

    def fx(self, native, reporting, start, end):
        rate = 7.8 if (native, reporting) == ("USD", "HKD") else 1 / 7.8
        return [(d.isoformat(), rate) for d in weekdays(start, end)]


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE position_snapshots(account_id TEXT, snapshot_date TEXT, conid TEXT, currency TEXT,
                                        market_value_text TEXT);
        CREATE TABLE nav_snapshots(account_id TEXT, snapshot_date TEXT, asset_class TEXT, total_text TEXT);
        CREATE TABLE instruments(conid TEXT, symbol TEXT, listing_exchange TEXT);
        CREATE TABLE reference_fx_rates(from_currency TEXT, to_currency TEXT, rate_date TEXT, rate REAL,
                                        quality_status TEXT);
        CREATE TABLE platform_subjects(subject_id TEXT, subject_type TEXT, reporting_currency TEXT);
        CREATE TABLE synthetic_portfolio_components(portfolio_id TEXT, snapshot_date TEXT, component_id TEXT,
                                                    component_type TEXT, contribution TEXT);
        CREATE TABLE subject_nav_series(subject_id TEXT, snapshot_date TEXT, currency TEXT, total_text TEXT);
        CREATE TABLE crisis_scenario_results(tenant_id TEXT DEFAULT 'tenant-a', subject_id TEXT,
            holdings_as_of TEXT, scenario_id TEXT, calculation_version TEXT, reporting_currency TEXT,
            crisis_start TEXT, crisis_end TEXT, available BOOLEAN, maximum_drawdown REAL, result_json TEXT,
            generated_at TEXT);
        INSERT INTO instruments VALUES ('1', 'OLD', 'NYSE'), ('2', 'NEW', 'NYSE');
        INSERT INTO nav_snapshots VALUES ('U0000001', '2026-09-30', 'TOTAL', '1000');
        INSERT INTO position_snapshots VALUES
            ('U0000001', '2026-09-30', '1', 'USD', '400'), ('U0000001', '2026-09-30', '2', 'USD', '200');
        INSERT INTO platform_subjects VALUES ('demo-composite', 'composite_portfolio', 'HKD');
        INSERT INTO subject_nav_series VALUES ('demo-composite', '2026-09-30', 'HKD', '10000');
        INSERT INTO synthetic_portfolio_components VALUES
            ('demo-composite', '2026-09-30', 'U0000001', 'account', '6000'),
            ('demo-composite', '2026-09-30', 'hang-seng-price-index', 'index_hedge', '-2000'),
            ('demo-composite', '2026-09-30', 'cash-hkd', 'cash', '6000');
    """)
    return connection


def spy_end(scenario) -> float:
    days = [d.isoformat() for d in weekdays(scenario.start, scenario.end)]
    return SERIES["SPY"][days[-1]] / SERIES["SPY"][days[0]]


class ArithmeticTests(unittest.TestCase):
    def test_align_carries_the_prior_close_and_refuses_a_late_start(self):
        self.assertEqual(cs.align({"2020-01-01": 1.0, "2020-01-03": 2.0}, ["2020-01-02", "2020-01-03"]),
                         {"2020-01-02": 1.0, "2020-01-03": 2.0})
        self.assertIsNone(cs.align({"2020-01-03": 2.0}, ["2020-01-02", "2020-01-03"]))

    def test_own_history_needs_the_whole_window(self):
        calendar = [f"2020-01-{d:02d}" for d in range(1, 21)]
        self.assertTrue(cs.has_own_history({d: 1.0 for d in calendar}, calendar))
        self.assertFalse(cs.has_own_history({d: 1.0 for d in calendar[1:]}, calendar))
        self.assertFalse(cs.has_own_history({d: 1.0 for d in calendar[::2]}, calendar))

    def test_beta_is_estimated_bounded_and_unscaled_when_short(self):
        recent = (date(2024, 10, 1).isoformat(), date(2026, 9, 30).isoformat())
        spy = {d: v for d, v in SERIES["SPY"].items() if recent[0] <= d <= recent[1]}
        new = {d: v for d, v in SERIES["NEW"].items() if recent[0] <= d <= recent[1]}
        estimate = cs.estimate_beta(new, spy)
        self.assertAlmostEqual(estimate["beta"], 2.0, delta=0.1)
        self.assertEqual(cs.estimate_beta({d: v ** 3 for d, v in new.items()}, spy)["beta"], cs.BETA_BOUNDS[1])
        short = dict(list(spy.items())[:20])
        self.assertEqual(cs.estimate_beta(short, short)["basis"], "unscaled-short-history")

    def test_scaled_path_is_exact_at_one_and_never_negative(self):
        calendar = ["a", "b", "c"]
        prices = {"a": 100.0, "b": 50.0, "c": 75.0}
        self.assertEqual(cs.scaled_path(prices, calendar, 1.0), [1.0, 0.5, 0.75])
        self.assertEqual(cs.scaled_path(prices, calendar, 3.0), [1.0, 0.0, 0.0])
        self.assertAlmostEqual(cs.scaled_path(prices, calendar, 0.5)[-1], 0.75 * 1.25)

    def test_combine_holds_cash_flat(self):
        replay = cs.combine({"x": 0.5}, {"x": [1.0, 0.6, 0.8]}, ["a", "b", "c"])
        self.assertAlmostEqual(replay["cash_weight"], 0.5)
        self.assertAlmostEqual(replay["maximum_drawdown"], -0.2)


class AccountTests(unittest.TestCase):
    def test_direct_holdings_and_scaled_stand_ins_for_every_scenario(self):
        result = cs.account_scenarios(database(), "U0000001", FakePrices())
        self.assertEqual(result["holdings_as_of"], "2026-09-30")
        self.assertEqual([s["scenario_id"] for s in result["scenarios"]], [s.scenario_id for s in cs.SCENARIOS])
        lehman = result["scenarios"][0]
        self.assertEqual((lehman["crisis_start"], lehman["crisis_end"]), ("2008-09-15", "2009-03-09"))
        for scenario, item in zip(cs.SCENARIOS, result["scenarios"]):
            self.assertTrue(item["available"], item)
            self.assertEqual((item["direct_components"], item["proxy_components"], item["scaled_components"]),
                             (1, 1, 1))
            stand_in = next(c for c in item["components"] if c["method"] == "market_proxy")
            self.assertEqual(stand_in["source_symbol"], "SPY")
            self.assertAlmostEqual(stand_in["beta"], 2.0, delta=0.1)
            # OLD follows SPY exactly; NEW falls about twice as fast; cash 40% stays flat.
            unscaled = 0.4 + 0.4 * spy_end(scenario) + 0.2 * spy_end(scenario)
            self.assertLess(item["maximum_drawdown"], unscaled - 1)

    def test_a_window_spy_does_not_cover_is_unavailable_not_an_error(self):
        class Gappy(FakePrices):
            def history(self, symbol, start, end):
                series = super().history(symbol, start, end)
                return {} if symbol == "SPY" and start.year == 2020 else series

        result = cs.account_scenarios(database(), "U0000001", Gappy())
        covid = next(s for s in result["scenarios"] if s["scenario_id"] == "covid_2020")
        self.assertFalse(covid["available"])
        self.assertIn("SPY does not cover", covid["reason"])
        self.assertEqual(sum(s["available"] for s in result["scenarios"]), 3)


class CompositeAndStorageTests(unittest.TestCase):
    def test_composite_combines_sleeves_hedges_and_fx(self):
        connection, prices = database(), FakePrices()
        sleeve = cs.account_scenarios(connection, "U0000001", prices)
        result = cs.composite_scenarios(connection, "demo-composite", prices, lambda key, _: sleeve)
        self.assertEqual(result["reporting_currency"], "HKD")
        for own, item in zip(sleeve["scenarios"], result["scenarios"]):
            self.assertTrue(item["available"], item)
            sleeve_end = own["daily"][-1]["value"]
            hedge_end = item["components"][1]
            self.assertEqual(hedge_end["source_symbol"], "^HSI")
            expected = 1 + 0.6 * (sleeve_end - 1) - 0.2 * (spy_end(cs.SCENARIOS[result["scenarios"].index(item)]) - 1)
            self.assertAlmostEqual(item["daily"][-1]["value"], expected)

    def test_a_failed_sleeve_makes_each_scenario_say_why(self):
        def broken(key, _):
            raise RuntimeError("no holdings")

        result = cs.composite_scenarios(database(), "demo-composite", FakePrices(), broken)
        self.assertTrue(all(not s["available"] and "no holdings" in s["reason"] for s in result["scenarios"]))

    def test_store_and_load_drop_paths_and_keep_order(self):
        connection = database()
        cs.store(connection, cs.account_scenarios(connection, "U0000001", FakePrices()))
        loaded = cs.load(connection, "U0000001", as_of="2026-10-01")
        self.assertEqual([s["scenario_id"] for s in loaded["scenarios"]], [s.scenario_id for s in cs.SCENARIOS])
        self.assertNotIn("daily", loaded["scenarios"][0])
        self.assertEqual(loaded["limitations"], cs.LIMITATIONS)
        self.assertIsNone(cs.load(connection, "U0000001", as_of="2026-09-01"))
        self.assertIn("daily", cs.load(connection, "U0000001", daily=True)["scenarios"][0])

    def test_refresh_isolates_failures(self):
        connection = database()
        with mock.patch.object(cs, "Prices", lambda *_: FakePrices()):
            summary = cs.refresh(connection, accounts=["U0000001", "U9999999"], composites=["demo-composite"])
        self.assertEqual(set(summary["subjects"]), {"U0000001", "demo-composite"})
        self.assertEqual(summary["subjects"]["demo-composite"]["available"], len(cs.SCENARIOS))
        self.assertIn("U9999999", summary["failures"])
        self.assertIsNotNone(cs.load(connection, "demo-composite"))


KRW_SERIES = {
    "005930.KS": make_levels(1.0, ORIGIN),           # listed throughout: its own won prices
    "123456.KS": make_levels(2.0, date(2024, 1, 2)),  # listed recently, twice as sensitive as KOSPI
    "^KS11": make_levels(1.0, ORIGIN),
}


class WonPrices(FakePrices):
    """KRX closes in won; USD to KRW at 1,400, and KRW to HKD drifting so a path shows the FX."""

    def history(self, symbol, start, end):
        if symbol in KRW_SERIES:
            return {d: v for d, v in KRW_SERIES[symbol].items() if start.isoformat() <= d <= end.isoformat()}
        return super().history(symbol, start, end)

    def fx(self, native, reporting, start, end):
        if (native, reporting) == ("USD", "KRW"):
            return [(d.isoformat(), 1400.0) for d in weekdays(start, end)]
        if (native, reporting) == ("KRW", "HKD"):
            return [(d.isoformat(), 0.0056 * (1 - 0.001 * i)) for i, d in enumerate(weekdays(start, end))]
        return super().fx(native, reporting, start, end)


def won_database() -> sqlite3.Connection:
    connection = database()
    connection.executescript("""
        INSERT INTO platform_subjects VALUES ('KIWOOM-0001', 'physical_account', 'KRW');
        INSERT INTO instruments VALUES ('k1', '005930', 'KRX'), ('k2', '123456', 'KRX');
        INSERT INTO nav_snapshots VALUES ('KIWOOM-0001', '2026-09-30', 'TOTAL', '1000000');
        INSERT INTO position_snapshots VALUES
            ('KIWOOM-0001', '2026-09-30', 'k1', 'KRW', '400000'),
            ('KIWOOM-0001', '2026-09-30', 'k2', 'KRW', '200000'),
            ('KIWOOM-0001', '2026-09-30', '1', 'USD', '100');
        INSERT INTO reference_fx_rates VALUES ('USD', 'KRW', '2026-09-30', 1400, 'pass');
    """)
    return connection


class WonAccountTests(unittest.TestCase):
    def test_a_won_account_is_replayed_in_won_on_kospi_stand_ins(self):
        result = cs.account_scenarios(won_database(), "KIWOOM-0001", WonPrices())
        self.assertEqual(result["reporting_currency"], "KRW")
        for item in result["scenarios"]:
            self.assertTrue(item["available"], item)
            self.assertEqual(item["reporting_currency"], "KRW")
            by_symbol = {c["symbol"]: c for c in item["components"]}
            # Weights are shares of the won NAV: 40% and 20% in won, and $100 at 1,400 = 14%.
            self.assertAlmostEqual(by_symbol["005930"]["weight"], 0.4)
            self.assertAlmostEqual(by_symbol["123456"]["weight"], 0.2)
            self.assertAlmostEqual(by_symbol["OLD"]["weight"], 0.14)
            self.assertAlmostEqual(item["cash_weight"], 0.26)
            self.assertEqual((by_symbol["005930"]["method"], by_symbol["005930"]["source_symbol"]),
                             ("direct", "005930.KS"))
            self.assertEqual((by_symbol["123456"]["method"], by_symbol["123456"]["source_symbol"]),
                             ("market_proxy", "^KS11"))
            self.assertAlmostEqual(by_symbol["123456"]["beta"], 2.0, delta=0.1)
            self.assertLess(item["maximum_drawdown"], -0.01)

    def test_usd_accounts_are_unchanged(self):
        result = cs.account_scenarios(database(), "U0000001", WonPrices())
        self.assertEqual(result["reporting_currency"], "USD")
        stand_in = next(c for c in result["scenarios"][0]["components"] if c["method"] == "market_proxy")
        self.assertEqual(stand_in["source_symbol"], "SPY")

    def test_a_composite_converts_a_won_sleeve_at_its_own_fx(self):
        connection, prices = database(), WonPrices()
        sleeve = cs.account_scenarios(won_database(), "KIWOOM-0001", prices)
        result = cs.composite_scenarios(connection, "demo-composite", prices, lambda key, _: sleeve)
        for scenario, own, item in zip(cs.SCENARIOS, sleeve["scenarios"], result["scenarios"]):
            self.assertTrue(item["available"], item)
            fx = cs._fx_path(prices, "KRW", "HKD", scenario, [r["date"] for r in own["daily"]])
            hedge = spy_end(scenario) - 1
            expected = 1 + 0.6 * (own["daily"][-1]["value"] * fx[-1] - 1) - 0.2 * hedge
            self.assertAlmostEqual(item["daily"][-1]["value"], expected)


class WebsiteSwitchTests(unittest.TestCase):
    def test_switch_is_off_unless_true(self):
        self.assertFalse(cs.website_enabled({}))
        self.assertFalse(cs.website_enabled({cs.SWITCH_NAME: "yes"}))
        self.assertTrue(cs.website_enabled({cs.SWITCH_NAME: " TRUE "}))

    def test_website_reads_nothing_while_switched_off(self):
        try:
            from ibkr_etl import web
        except Exception as error:  # the website's optional dependencies are not installed
            self.skipTest(str(error))
        with mock.patch.dict(os.environ, {cs.SWITCH_NAME: "false"}), \
                mock.patch.object(web, "_database", side_effect=AssertionError("read while off")):
            self.assertIsNone(web._crisis_scenarios("U0000001", "2026-09-30"))
        with mock.patch.dict(os.environ, {cs.SWITCH_NAME: "true"}), \
                mock.patch.object(web, "_database", side_effect=RuntimeError("table missing")):
            self.assertIsNone(web._crisis_scenarios("U0000001", "2026-09-30"))


if __name__ == "__main__":
    unittest.main()
