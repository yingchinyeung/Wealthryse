"""Institutional analytics: calendar, drawdowns, statistics, rolling risk and concentration."""
from __future__ import annotations

import ast
import math
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import portfolio_analytics as analytics
from ibkr_etl.performance_periods import performance_periods

ROOT = Path(__file__).resolve().parents[1]
LIVE_WEB = ROOT / "deploy/live-merged/src/ibkr_etl/web.py"
SRC_WEB = ROOT / "src/ibkr_etl/web.py"


def _weekday_nav(start: date, end: date, daily_return, flows=None):
    nav, value, day = {}, 100.0, start
    flows = flows or {}
    while day <= end:
        if day.weekday() < 5:
            if nav:
                value = value * (1 + daily_return(day)) + flows.get(day.isoformat(), 0.0)
            nav[day.isoformat()] = value
        day += timedelta(days=1)
    return nav


class DailyReturnTests(unittest.TestCase):
    def test_flows_are_removed_with_modified_dietz(self):
        nav = {"2026-01-02": 100.0, "2026-01-05": 160.0}
        returns, failures = analytics.daily_return_series(nav, {"2026-01-05": 50.0})
        self.assertEqual(failures, [])
        self.assertAlmostEqual(returns[0][1], (160 - 100 - 50) / (100 + 25))

    def test_invalid_interval_fails_the_whole_analysis(self):
        result = analytics.portfolio_analytics({"2026-01-02": 100.0, "2026-01-05": 0.0,
                                                "2026-01-06": 50.0}, {})
        self.assertFalse(result["available"])
        self.assertIn("2026-01-06", " ".join(result["issues"]))

    def test_one_observation_is_unavailable(self):
        result = analytics.portfolio_analytics({"2026-01-02": 100.0}, {})
        self.assertFalse(result["available"])


class CalendarTests(unittest.TestCase):
    def test_months_and_years_match_the_period_returns_table(self):
        nav = _weekday_nav(date(2025, 1, 20), date(2026, 3, 13),
                           lambda d: 0.002 if d.day % 3 else -0.003, {"2025-06-02": 25.0})
        flows = {"2025-06-02": 25.0}
        result = analytics.portfolio_analytics(nav, flows)
        periods = {p["key"]: p for p in performance_periods(nav, flows)["periods"]}
        rows = {row["year"]: row for row in result["monthly_calendar"]}
        self.assertEqual(list(rows), [2026, 2025])
        self.assertAlmostEqual(rows[2025]["year_return"]["twr"], periods["year_2025"]["twr"])
        self.assertAlmostEqual(rows[2026]["year_return"]["twr"], periods["ytd"]["twr"])
        self.assertAlmostEqual(rows[2026]["months"][2]["twr"], periods["mtd"]["twr"])
        self.assertTrue(rows[2026]["months"][2]["to_date"])
        self.assertTrue(rows[2025]["months"][0]["partial"])
        self.assertIsNone(rows[2026]["months"][3])
        growth = math.prod(1 + cell["twr"] for cell in rows[2025]["months"])
        self.assertAlmostEqual(growth - 1, rows[2025]["year_return"]["twr"])


class DrawdownTests(unittest.TestCase):
    def test_episodes_record_peak_trough_and_recovery(self):
        returns = [("d1", 0.10), ("d2", -0.20), ("d3", 0.05), ("d4", 0.25), ("d5", -0.10)]
        returns = [(f"2026-01-0{i + 2}", value) for i, (_, value) in enumerate(returns)]
        result = analytics.drawdown_analysis(returns, "2026-01-01")
        first, second = sorted(result["episodes"], key=lambda e: e["peak_date"])
        self.assertEqual((first["peak_date"], first["trough_date"], first["recovery_date"]),
                         ("2026-01-02", "2026-01-03", "2026-01-05"))
        self.assertAlmostEqual(first["depth"], -0.20)
        self.assertTrue(first["recovered"])
        self.assertEqual(first["days_to_recover"], 2)
        self.assertFalse(second["recovered"])
        self.assertAlmostEqual(second["depth"], -0.10)
        self.assertAlmostEqual(result["maximum_drawdown"], -0.20)
        self.assertAlmostEqual(result["current_drawdown"], -0.10)
        self.assertEqual(result["episodes"][0]["depth"], first["depth"])

    def test_no_loss_means_no_episode(self):
        result = analytics.drawdown_analysis([("2026-01-02", 0.01)], "2026-01-01")
        self.assertEqual(result["episodes"], [])
        self.assertEqual(result["maximum_drawdown"], 0.0)


class StatisticsTests(unittest.TestCase):
    def test_ratios_follow_their_definitions(self):
        values = [0.01, -0.02, 0.015, -0.005, 0.02, 0.0, -0.01, 0.012]
        returns = [(f"2026-02-{i + 2:02d}", v) for i, v in enumerate(values)]
        months = [{"month": "2026-01", "twr": 0.03}, {"month": "2026-02", "twr": -0.01},
                  {"month": "2026-03", "twr": 0.02}]
        stats = analytics.return_statistics(returns, "2026-02-01", months, -0.04)
        downside = math.sqrt(sum(min(v, 0) ** 2 for v in values) / len(values))
        mean = sum(values) / len(values)
        self.assertAlmostEqual(stats["downside_deviation"], downside * math.sqrt(252))
        self.assertAlmostEqual(stats["sortino_ratio"], mean / downside * math.sqrt(252))
        self.assertIsNone(stats["annualized_return"])
        self.assertIsNone(stats["calmar_ratio"])
        self.assertEqual(stats["best_day"], {"date": "2026-02-06", "return": 0.02})
        self.assertEqual(stats["worst_day"], {"date": "2026-02-03", "return": -0.02})
        self.assertEqual(stats["best_month"], {"month": "2026-01", "return": 0.03})
        self.assertEqual(stats["positive_months"], 2)
        self.assertAlmostEqual(stats["positive_month_ratio"], 2 / 3)
        self.assertAlmostEqual(stats["gain_loss_ratio"], 0.025 / 0.01)

    def test_symmetric_returns_have_no_skew(self):
        values = [-0.02, -0.01, 0.0, 0.01, 0.02]
        self.assertAlmostEqual(analytics._skewness(values), 0.0)
        self.assertLess(analytics._excess_kurtosis(values), 0)

    def test_calmar_uses_annualised_return_over_a_year(self):
        nav = _weekday_nav(date(2025, 1, 2), date(2026, 3, 31),
                           lambda d: -0.01 if d.month == 3 and d.year == 2025 else 0.001)
        result = analytics.portfolio_analytics(nav, {})
        stats = result["statistics"]
        self.assertIsNotNone(stats["annualized_return"])
        self.assertAlmostEqual(stats["calmar_ratio"],
                               stats["annualized_return"] / abs(result["drawdown"]["maximum_drawdown"]))


class RollingTests(unittest.TestCase):
    def test_windows_start_once_enough_history_exists(self):
        nav = _weekday_nav(date(2025, 1, 2), date(2026, 3, 31), lambda d: 0.001)
        result = analytics.portfolio_analytics(nav, {})
        rolling = result["rolling"]
        first_vol = next(row for row in rolling if row["volatility"] is not None)
        first_return = next(row for row in rolling if row["trailing_return"] is not None)
        self.assertEqual(list(nav).index(first_vol["date"]), 63)
        self.assertGreaterEqual(date.fromisoformat(first_return["date"]), date(2026, 1, 2))
        self.assertAlmostEqual(first_vol["volatility"], 0.0)
        last = rolling[-1]
        days = sum(1 for d in nav if "2025-03-31" < d <= "2026-03-31")
        self.assertAlmostEqual(last["trailing_return"], 1.001 ** days - 1)


class ConcentrationTests(unittest.TestCase):
    def test_top_weights_effective_number_and_currencies(self):
        view = {"weight_basis": "nav", "holdings": [
            {"symbol": "AAA", "currency": "USD", "weight": 0.40},
            {"symbol": "BBB", "currency": "HKD", "weight": 0.30},
            {"symbol": "CCC", "currency": "usd", "weight": 0.20},
            {"symbol": "DDD", "currency": "EUR", "weight": None},
        ]}
        result = analytics.holdings_concentration(view)
        self.assertTrue(result["available"])
        self.assertEqual(result["largest"]["symbol"], "AAA")
        self.assertAlmostEqual(result["top_5_weight"], 0.90)
        self.assertEqual((result["holdings_count"], result["weighted_count"]), (4, 3))
        self.assertAlmostEqual(result["effective_holdings"],
                               1 / sum((w / 0.9) ** 2 for w in (0.4, 0.3, 0.2)))
        self.assertAlmostEqual(result["residual_weight"], 0.10)
        self.assertEqual([(x["currency"], round(x["weight"], 6)) for x in result["currency_exposure"]],
                         [("USD", 0.6), ("HKD", 0.3)])

    def test_unweighted_holdings_are_unavailable(self):
        result = analytics.holdings_concentration({"weight_basis": None, "holdings": [{"weight": None}]})
        self.assertFalse(result["available"])


class RollingBenchmarkTests(unittest.TestCase):
    def test_beta_of_a_levered_copy_is_its_leverage(self):
        from types import SimpleNamespace
        rows = [SimpleNamespace(observation_date=f"d{i:03d}", portfolio_return=2 * r, benchmark_return=r)
                for i, r in enumerate((0.01 * math.sin(i) for i in range(140)))]
        rows.append(SimpleNamespace(observation_date="gap", portfolio_return=None, benchmark_return=0.01))
        series = analytics.rolling_benchmark_statistics(rows, window=126)
        self.assertEqual(len(series), 140 - 126 + 1)
        self.assertEqual(series[0]["date"], "d125")
        self.assertAlmostEqual(series[-1]["beta"], 2.0)
        self.assertAlmostEqual(series[-1]["correlation"], 1.0)


class TradingActivityTests(unittest.TestCase):
    def test_monthly_turnover_commissions_and_fx(self):
        trades = [
            {"trade_date": "2026-01-05", "buy_sell": "BUY", "currency": "USD", "proceeds_text": "-1000",
             "commission_text": "-1", "commission_currency": "USD", "realized_pnl_text": "0"},
            {"trade_date": "2026-01-20", "buy_sell": "SELL", "currency": "HKD", "proceeds_text": "3900",
             "commission_text": "-7.8", "commission_currency": "HKD", "realized_pnl_text": "780"},
            {"trade_date": "2026-01-21", "buy_sell": "SELL", "currency": "JPY", "proceeds_text": "100",
             "commission_text": "0", "commission_currency": "JPY", "realized_pnl_text": "0"},
            {"trade_date": "2026-03-01", "buy_sell": "BUY", "currency": "USD", "proceeds_text": "-1"},
        ]
        nav = {"2026-01-02": 9000.0, "2026-01-30": 11000.0, "2026-02-27": 10000.0}
        rates = {"USD": 1.0, "HKD": 0.128}
        result = analytics.trading_activity(trades, nav, lambda c, d: rates.get(c), as_of="2026-02-28")
        self.assertEqual(result["missing_fx_currencies"], ["JPY"])
        months = {row["month"]: row for row in result["monthly"]}
        self.assertEqual(list(months), ["2026-02", "2026-01"])
        jan = months["2026-01"]
        self.assertEqual(jan["trades"], 2)
        self.assertAlmostEqual(jan["purchases"], 1000)
        self.assertAlmostEqual(jan["sales"], 3900 * 0.128)
        self.assertAlmostEqual(jan["commissions"], 1 + 7.8 * 0.128)
        self.assertAlmostEqual(jan["realized_pnl"], 780 * 0.128)
        self.assertAlmostEqual(jan["turnover"], 3900 * 0.128 / 10000)
        self.assertEqual(months["2026-02"]["trades"], 0)
        totals = result["trailing_12_months"]
        self.assertEqual(totals["months"], 2)
        self.assertAlmostEqual(totals["annualized_turnover"], totals["turnover"] * 6)


class CorrelationTests(unittest.TestCase):
    def test_pairs_use_shared_days_and_skip_price_breaks(self):
        days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(80)]
        moves = [0.01 * math.sin(i * 1.7) for i in range(80)]
        a, b, c, price_a, price_b, price_c = {}, {}, {}, 100.0, 50.0, 20.0
        for i, day in enumerate(days):
            price_a *= 1 + moves[i]
            price_b *= 1 - moves[i]
            price_c *= 1 + (0.02 if i % 2 else -0.02)
            a[day], b[day], c[day] = price_a, price_b, price_c
        b[days[40]] = b[days[40]] * 3  # a bad mark: two 50%+ jumps
        result = analytics.holdings_correlation({"1": a, "2": b, "3": c},
                                                {"1": "AAA", "2": "BBB", "3": "CCC", "9": "ZZZ"})
        self.assertTrue(result["available"])
        self.assertEqual([h["symbol"] for h in result["holdings"]], ["AAA", "BBB", "CCC"])
        self.assertAlmostEqual(result["matrix"][0][1]["correlation"], -1.0, places=3)
        self.assertEqual(result["excluded_price_breaks"], 2)
        self.assertEqual(result["matrix"][0][0]["correlation"], 1.0)

    def test_too_few_shared_days_is_unavailable(self):
        prices = {"1": {"2026-01-01": 1.0, "2026-01-02": 1.1}, "2": {"2026-01-01": 2.0, "2026-01-02": 2.1}}
        result = analytics.holdings_correlation(prices, {"1": "A", "2": "B"})
        self.assertFalse(result["available"])
        self.assertIsNone(result["matrix"][0][1]["correlation"])


@contextmanager
def _no_database():
    yield object()


class EndpointAndPageTests(unittest.TestCase):
    VIEWER = {"email": "viewer@example.com", "object_id": "viewer-1", "role": "viewer"}
    OWNER = {"email": "owner@example.com", "role": "owner"}
    ACCESS = {"subject_id": "U1", "reporting_currency": "USD", "history_start_date": "2025-01-01",
              "subject_type": "physical_account", "base_currency": "USD"}

    def test_endpoint_uses_the_period_return_inputs(self):
        from ibkr_etl import web
        nav = [("2026-01-02", "100"), ("2026-01-05", "110"), ("2026-01-06", "99")]
        with patch.object(web, "_database", _no_database), \
                patch.object(web, "_portfolio_access_context", return_value=self.ACCESS) as access, \
                patch.object(web, "_subject_nav_values", return_value=nav) as nav_reader, \
                patch.object(web, "_subject_external_flows", return_value={"2026-01-05": 5.0}):
            result = web.portfolio_analytics_view("U1", as_of="2026-01-06", user=self.VIEWER)
        access.assert_called_once_with("U1", self.VIEWER)
        self.assertEqual(nav_reader.call_args.args[3], "2026-01-06")
        self.assertTrue(result["available"])
        self.assertEqual(result["currency"], "USD")
        self.assertAlmostEqual(result["statistics"]["best_day"]["return"], 5 / 102.5)

    def test_performance_and_holdings_tabs_carry_the_cards(self):
        from ibkr_etl import web
        performance = web._render_account_page(self.OWNER, account_id="U16088571", section="performance")
        for marker in ('id="monthly-returns"', 'id="drawdown-chart"', 'id="drawdown-episodes"',
                       'id="rolling-volatility-chart"', 'id="analytics-statistics"',
                       "void renderAnalytics(asOf)", "/analytics?as_of=", 'id="attribution-month"',
                       'id="rolling-beta-chart"', "renderRollingBenchmark(bc);"):
            self.assertIn(marker, performance)
        trades = web._render_account_page(self.OWNER, account_id="U16088571", section="trades")
        self.assertIn('id="trading-monthly"', trades)
        self.assertIn("void renderTradingActivity(asOf)", trades)
        risk = web._render_account_page(self.OWNER, account_id="U16088571", section="risk")
        self.assertIn('id="correlation-body"', risk)
        holdings = web._render_account_page(self.OWNER, account_id="U16088571", section="holdings")
        self.assertIn('id="concentration-metrics"', holdings)
        self.assertIn("renderConcentration(h)", holdings)

    def test_attribution_endpoint_uses_the_factsheet_engine(self):
        from ibkr_etl import web
        payload = {"quality_status": "pass", "message": "ok",
                   "data": {"available": True, "portfolio_monthly_return": 0.01, "security_contributions": []}}
        with patch.object(web, "_database", _no_database), \
                patch.object(web, "_portable_database_connection", lambda c: c), \
                patch.object(web, "_portfolio_access_context", return_value=self.ACCESS), \
                patch("ibkr_etl.monthly_reporting_analytics.build_monthly_reporting_analytics",
                      return_value=payload) as engine:
            result = web.monthly_attribution_view("U1", month="2026-02", as_of="2026-03-05", user=self.VIEWER)
        self.assertEqual(engine.call_args.kwargs["period_end"], "2026-02-28")
        self.assertEqual(engine.call_args.kwargs["reporting_currency"], "USD")
        self.assertTrue(result["available"])
        self.assertEqual(result["month"], "2026-02")
        from fastapi import HTTPException
        with patch.object(web, "_portfolio_access_context", return_value=self.ACCESS):
            for bad in ("2026-13", "26-01", "2026-04"):
                with self.assertRaises(HTTPException):
                    web.monthly_attribution_view("U1", month=bad, as_of="2026-03-05", user=self.VIEWER)

    def test_composites_have_no_trading_or_correlation(self):
        from ibkr_etl import web
        access = {**self.ACCESS, "subject_type": "composite_portfolio"}
        with patch.object(web, "_portfolio_access_context", return_value=access):
            self.assertTrue(web.trading_activity_view("U1", user=self.VIEWER)["not_applicable"])
            self.assertTrue(web.holdings_correlation_view("U1", user=self.VIEWER)["not_applicable"])

    def test_new_metrics_have_guide_entries(self):
        from ibkr_etl.metrics_guide import CLIENT_CALCULATIONS, METRIC_ANCHORS
        for name in ("Contribution to return", "Portfolio turnover", "Commissions / NAV", "Trading activity",
                     "Sortino ratio", "Calmar ratio", "Maximum drawdown (since inception)",
                     "Effective number of holdings", "Best and worst month", "Annualized volatility"):
            self.assertIn(name, METRIC_ANCHORS)
            self.assertIn(name, CLIENT_CALCULATIONS)

    def test_live_merged_copy_ships_the_same_endpoint_and_markup(self):
        def function(path, name):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.FunctionDef) and node.name == name:
                    return ast.dump(node, include_attributes=False)
            raise AssertionError(f"{name} not found in {path}")

        for name in ("portfolio_analytics_view", "monthly_attribution_view", "trading_activity_view",
                     "holdings_correlation_view", "benchmark_comparison"):
            self.assertEqual(function(LIVE_WEB, name), function(SRC_WEB, name), name)
        live = LIVE_WEB.read_text(encoding="utf-8")
        for marker in ("from .web_analytics_ui import (\n    ANALYTICS_SCRIPT,", "{PERFORMANCE_ANALYTICS_CARDS}</div>",
                       "{HOLDINGS_CONCENTRATION_CARD}", "{ACCOUNT_UI_SCRIPT}\n{ANALYTICS_SCRIPT}",
                       '"concentration": holdings_concentration(view)', "void renderAnalytics(asOf)",
                       "{CORRELATION_CARD}</div>", 'data-section="trades">{TRADING_ACTIVITY_CARD}',
                       "renderRollingBenchmark(null);", "void renderCorrelation(asOf)"):
            self.assertIn(marker, live)


if __name__ == "__main__":
    unittest.main()
