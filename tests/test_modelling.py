from __future__ import annotations

import contextlib
import math
import random
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ibkr_etl import fama_french, modelling  # noqa: E402
from ibkr_etl.web_modelling import install  # noqa: E402


def _weekdays(start: date, count: int) -> list[str]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _prices(seed: int = 7, holdings: int = 6, sessions: int = 300):
    generator = random.Random(seed)
    days = _weekdays(date(2025, 6, 2), sessions)
    market = [generator.gauss(0.0004, 0.01) for _ in days]
    prices = {}
    for index in range(holdings):
        beta, level, series = 0.6 + 0.2 * index, 100.0, {}
        for day, move in zip(days, market):
            level *= 1.0 + beta * move + generator.gauss(0.0001, 0.008 + 0.002 * index)
            series[day] = level
        prices[str(1000 + index)] = series
    return days, market, prices


class ProjectionTests(unittest.TestCase):
    def test_constant_returns_project_exact_compounding(self) -> None:
        daily = [0.001] * 120
        result = modelling.project_wealth(daily, start_value=1000.0, start_date="2026-01-31", years=1)
        month = 1.001 ** 21
        self.assertAlmostEqual(result["summary"]["median_end"], 1000.0 * month ** 12, places=6)
        self.assertAlmostEqual(result["summary"]["p5_end"], result["summary"]["p95_end"], places=6)
        self.assertEqual(result["series"][1]["date"], "2026-02-28")  # month ends clamp
        self.assertEqual(len(result["series"]), 13)

    def test_withdrawals_can_deplete_and_target_probability_is_reported(self) -> None:
        daily = [0.0] * 100
        result = modelling.project_wealth(
            daily, start_value=2000.0, start_date="2026-01-01", years=1, monthly_flow=-100.0, target=500.0
        )
        self.assertEqual(result["summary"]["probability_depleted"], 0.0)
        self.assertAlmostEqual(result["summary"]["median_end"], 800.0)
        self.assertEqual(result["summary"]["probability_target"], 1.0)
        drained = modelling.project_wealth(daily, start_value=500.0, start_date="2026-01-01", years=1, monthly_flow=-100.0)
        self.assertEqual(drained["summary"]["probability_depleted"], 1.0)
        self.assertEqual(drained["summary"]["median_end"], 0.0)

    def test_return_assumption_shifts_the_median_and_keeps_spread(self) -> None:
        generator = random.Random(3)
        daily = [generator.gauss(0.001, 0.01) for _ in range(400)]
        base = modelling.project_wealth(daily, start_value=1.0, start_date="2026-01-01", years=5)
        assumed = modelling.project_wealth(daily, start_value=1.0, start_date="2026-01-01", years=5, annual_return=-0.02)
        self.assertAlmostEqual(assumed["assumptions"]["annual_return"], -0.02, places=9)
        self.assertLess(assumed["summary"]["median_end"], base["summary"]["median_end"])
        self.assertAlmostEqual(assumed["summary"]["median_end"], 0.98 ** 5, delta=0.1)
        self.assertEqual(assumed["assumptions"]["return_source"], "user")

    def test_projection_is_reproducible_and_validates_inputs(self) -> None:
        daily = [0.002, -0.001, 0.0005] * 40
        first = modelling.project_wealth(daily, start_value=10.0, start_date="2026-01-01", years=2)
        second = modelling.project_wealth(daily, start_value=10.0, start_date="2026-01-01", years=2)
        self.assertEqual(first["series"], second["series"])
        with self.assertRaises(modelling.ModellingInputError):
            modelling.project_wealth(daily[:10], start_value=10.0, start_date="2026-01-01")
        with self.assertRaises(modelling.ModellingInputError):
            modelling.project_wealth(daily, start_value=10.0, start_date="2026-01-01", years=31)
        with self.assertRaises(modelling.ModellingInputError):
            modelling.project_wealth(daily, start_value=0.0, start_date="2026-01-01")


class WhatIfTests(unittest.TestCase):
    def test_matrix_skips_splits_and_drops_sparse_holdings(self) -> None:
        days = _weekdays(date(2026, 1, 5), 100)
        prices = {"a": {d: 100.0 + i for i, d in enumerate(days)}, "b": {d: 50.0 for d in days[-20:]}}
        prices["a"][days[50]] = 400.0  # a split-like jump out and back
        matrix = modelling.holding_return_matrix(prices)
        self.assertIn("a", matrix["returns"])
        self.assertEqual(matrix["excluded"], ["b"])
        self.assertEqual(matrix["excluded_price_breaks"], 2)

    def test_identical_weights_give_zero_differences_and_cash_dilutes_risk(self) -> None:
        days, market, prices = _prices()
        matrix = modelling.holding_return_matrix(prices)
        current = {key: 0.15 for key in prices}
        benchmark = dict(zip(days, market))
        same = modelling.what_if(current, current, matrix, risk_free=0.03, benchmark=benchmark)
        self.assertTrue(all(value == 0 for value in same["difference"].values() if value is not None))
        self.assertAlmostEqual(same["current"]["cash_weight"], 0.1)
        half = modelling.what_if(current, {k: v / 2 for k, v in current.items()}, matrix, benchmark=benchmark)
        self.assertLess(half["difference"]["annual_volatility"], 0)
        self.assertAlmostEqual(half["proposed"]["beta"], half["current"]["beta"] / 2, places=6)

    def test_rejects_short_positions_and_excess_leverage(self) -> None:
        _, _, prices = _prices()
        matrix = modelling.holding_return_matrix(prices)
        with self.assertRaises(modelling.ModellingInputError):
            modelling.what_if({"1000": 0.5}, {"1000": -0.1}, matrix)
        with self.assertRaises(modelling.ModellingInputError):
            modelling.what_if({"1000": 0.5}, {"1000": 2.5}, matrix)


class OptimisationTests(unittest.TestCase):
    def test_capped_simplex_projection(self) -> None:
        weights = modelling.project_capped_simplex([0.9, 0.5, -0.2, 0.1], 0.4)
        self.assertAlmostEqual(sum(weights), 1.0)
        self.assertTrue(all(-1e-12 <= w <= 0.4 + 1e-12 for w in weights))

    def test_risk_parity_equalises_risk_contributions(self) -> None:
        cov = [[0.04, 0.006, 0.0], [0.006, 0.09, 0.01], [0.0, 0.01, 0.01]]
        weights = modelling.risk_parity_weights(cov)
        for share in modelling.risk_contributions(weights, cov):
            self.assertAlmostEqual(share, 1 / 3, places=6)

    def test_minimum_variance_matches_closed_form_for_uncorrelated_assets(self) -> None:
        cov = [[0.04, 0.0], [0.0, 0.01]]
        weights = modelling.mean_variance_weights(cov, [0.0, 0.0], risk_aversion_inverse=0.0, cap=1.0, iterations=3000)
        self.assertAlmostEqual(weights[0], 0.2, places=5)  # inverse-variance: (1/0.04)/(1/0.04+1/0.01)

    def test_optimise_reports_frontier_and_respects_cap(self) -> None:
        _, _, prices = _prices(holdings=6)
        matrix = modelling.holding_return_matrix(prices)
        current = {key: 0.15 for key in prices}
        result = modelling.optimise(current, {"1000": {"symbol": "AAA"}}, matrix, risk_free=0.02, cap=0.3)
        self.assertEqual(len(result["holdings"]), 6)
        self.assertEqual(result["holdings"][0]["symbol"], "AAA")
        for name in ("minimum_variance", "maximum_sharpe"):
            weights = [h[f"{name}_weight"] for h in result["holdings"]]
            self.assertAlmostEqual(sum(weights), 1.0, places=6)
            self.assertLessEqual(max(weights), 0.3 + 1e-6)
        portfolios = result["portfolios"]
        self.assertLessEqual(portfolios["minimum_variance"]["volatility"], portfolios["current"]["volatility"] + 1e-9)
        self.assertGreaterEqual(portfolios["maximum_sharpe"]["sharpe_ratio"], portfolios["current"]["sharpe_ratio"] - 1e-6)
        vols = [p["volatility"] for p in result["frontier"]]
        self.assertEqual(vols, sorted(vols))
        self.assertAlmostEqual(sum(h["current_risk"] for h in result["holdings"]), 1.0, places=9)

    def test_optimise_needs_two_holdings(self) -> None:
        _, _, prices = _prices(holdings=1)
        with self.assertRaises(modelling.ModellingInputError):
            modelling.optimise({"1000": 1.0}, {}, modelling.holding_return_matrix(prices))


class FactorModelTests(unittest.TestCase):
    def _factors(self, days, generator):
        return {
            day: {"Mkt-RF": generator.gauss(0.0004, 0.01), "SMB": generator.gauss(0, 0.005),
                  "HML": generator.gauss(0, 0.005), "RMW": generator.gauss(0, 0.004),
                  "CMA": generator.gauss(0, 0.004), "MOM": generator.gauss(0, 0.006), "RF": 0.0001}
            for day in days
        }

    def test_recovers_known_exposures(self) -> None:
        generator = random.Random(11)
        days = _weekdays(date(2023, 1, 2), 900)
        factors = self._factors(days, generator)
        portfolio = [
            (day, f["RF"] + 1.1 * f["Mkt-RF"] + 0.4 * f["SMB"] - 0.3 * f["MOM"] + generator.gauss(0, 0.002))
            for day, f in factors.items()
        ]
        result = modelling.factor_model(portfolio, factors, region_label="Test")
        betas = {x["factor"]: x["beta"] for x in result["exposures"]}
        self.assertAlmostEqual(betas["Mkt-RF"], 1.1, delta=0.08)
        self.assertAlmostEqual(betas["SMB"], 0.4, delta=0.15)
        self.assertAlmostEqual(betas["MOM"], -0.3, delta=0.12)
        self.assertGreater(result["r_squared"], 0.8)
        self.assertEqual(result["weeks"], modelling.FACTOR_MAX_WEEKS)
        self.assertTrue(result["momentum_included"])

    def test_short_overlap_is_refused(self) -> None:
        generator = random.Random(2)
        days = _weekdays(date(2026, 1, 5), 60)
        factors = self._factors(days, generator)
        with self.assertRaises(modelling.ModellingInputError):
            modelling.factor_model([(d, 0.001) for d in days], factors, region_label="Test")

    def test_weekly_returns_compound_within_iso_week(self) -> None:
        weeks = modelling.weekly_returns([("2026-10-12", 0.1), ("2026-10-16", 0.1), ("2026-10-19", 0.05)])
        self.assertAlmostEqual(weeks["2026-10-12"], 0.21)
        self.assertAlmostEqual(weeks["2026-10-19"], 0.05)


FIVE_FACTOR_CSV = """This file was created using the 202608 Bloomberg database.
Missing data are indicated by -99.99.

,Mkt-RF,SMB,HML,RMW,CMA,RF
20260803,    0.52,   -0.10,    0.20,   -0.01,    0.03,    0.02
20260804,   -1.00,    0.30,  -99.99,    0.05,    0.00,    0.02

 Annual Factors: January-December
,Mkt-RF,SMB,HML,RMW,CMA,RF
  2025,   12.00,   1.00,   2.00,   3.00,   4.00,   4.50

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""


class FrenchParserTests(unittest.TestCase):
    def test_parses_daily_rows_as_decimals(self) -> None:
        rows = fama_french.parse_french_csv(FIVE_FACTOR_CSV)
        self.assertEqual(sorted(rows), ["2026-08-03", "2026-08-04"])
        self.assertAlmostEqual(rows["2026-08-03"]["Mkt-RF"], 0.0052)
        self.assertNotIn("HML", rows["2026-08-04"])  # missing marker dropped

    def test_momentum_column_is_renamed(self) -> None:
        rows = fama_french.parse_french_csv(",WML\n20260803,  0.40\n", single_column="MOM")
        self.assertAlmostEqual(rows["2026-08-03"]["MOM"], 0.004)

    def test_empty_file_is_an_error(self) -> None:
        with self.assertRaises(fama_french.FactorDataError):
            fama_french.parse_french_csv("nothing here")


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, prices, benchmark):
        self.prices, self.benchmark = prices, benchmark

    def execute(self, sql, params=()):
        if "risk_analytics_snapshots" in sql:
            return _Result([{"payload_json": {"metrics": {"risk_free_annual_rate": 0.04}}}])
        if "portfolio_benchmark_mapping" in sql:
            return _Result([{"benchmark_id": "spy", "name": "S&P 500", "reporting_currency": "USD"}])
        if "performance_comparison_daily" in sql:
            return _Result([{"observation_date": d, "benchmark_return": r} for d, r in self.benchmark.items()])
        if "subject_position_series" in sql:
            return _Result([
                {"snapshot_date": day, "conid": conid, "mark_price_text": str(value)}
                for conid, series in self.prices.items() for day, value in series.items()
            ])
        raise AssertionError(sql)


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        days, market, prices = _prices(holdings=4)
        self.days = days
        connection = _Connection(prices, dict(zip(days, market)))
        nav, level = [], 1_000_000.0
        for day, move in zip(days, market):
            level *= 1.0 + move
            nav.append((day, f"{level:.2f}"))
        snapshot = days[-1]
        holdings = [{"snapshot_date": snapshot, "conid": conid} for conid in prices]
        app = FastAPI()
        self.subject_type = "physical_account"
        factor_days = _weekdays(date(2025, 1, 6), 400)
        generator = random.Random(5)
        factors = {d: {"Mkt-RF": generator.gauss(0, 0.01), "SMB": 0.0001 * generator.random(),
                       "HML": generator.gauss(0, 0.004), "RMW": generator.gauss(0, 0.003),
                       "CMA": generator.gauss(0, 0.003), "RF": 0.0001} for d in factor_days}
        install(
            app,
            authorized_user=lambda: {"email": "owner@example.com", "role": "owner"},
            access_context=lambda account, user: {"reporting_currency": "USD", "subject_type": self.subject_type},
            parse_as_of=lambda value: value,
            read_database=lambda: contextlib.nullcontext(connection),
            nav_values=lambda connection, account, access, through: nav,
            external_flows=lambda connection, account, access: {},
            latest_holdings=lambda connection, account, as_of: holdings,
            enrich_holdings=lambda connection, account, rows, **kwargs: {"holdings": [
                {"conid": row["conid"], "symbol": f"S{row['conid']}", "description": "", "weight": 0.2}
                for row in rows
            ]},
            comparison_version=lambda account, currency: "v1",
            tenant_id="t",
            risk_analytics_version="r1",
            fetch_closes=lambda symbol, start, end: {d: 100.0 + i * 0.1 for i, d in enumerate(days)},
            load_factors=lambda region: {"region_label": "Developed markets", "source_url": "https://example.test",
                                         "factors": factors, "latest_date": max(factors), "momentum_note": "Momentum is left out: test"},
        )
        self.client = TestClient(app)

    def test_projection_route(self) -> None:
        response = self.client.get("/api/portfolio/A/modelling/projection", params={"years": 3, "monthly_flow": "1000", "target": "2000000"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["series"]), 37)
        self.assertEqual(body["currency"], "USD")
        self.assertIsNotNone(body["summary"]["probability_target"])
        bad = self.client.get("/api/portfolio/A/modelling/projection", params={"annual_return": "abc"})
        self.assertEqual(bad.status_code, 400)

    def test_what_if_route_with_added_ticker(self) -> None:
        response = self.client.get("/api/portfolio/A/modelling/what-if", params={"weights": "1000:0.4,1001:0", "add": "VOO:0.1"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertAlmostEqual(body["proposed"]["cash_weight"], 1 - 0.4 - 0.2 - 0.2 - 0.1)
        self.assertEqual(body["added"][0]["symbol"], "VOO")
        self.assertTrue(body["added"][0]["available"])
        self.assertIsNotNone(body["current"]["beta"])
        self.assertTrue(body["risk_free_available"])
        unknown = self.client.get("/api/portfolio/A/modelling/what-if", params={"weights": "9999:0.1"})
        self.assertEqual(unknown.status_code, 400)
        bad_ticker = self.client.get("/api/portfolio/A/modelling/what-if", params={"add": "DROP TABLE:0.1"})
        self.assertEqual(bad_ticker.status_code, 400)

    def test_optimise_route_and_composites_are_refused(self) -> None:
        response = self.client.get("/api/portfolio/A/modelling/optimise", params={"max_weight": "40"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["maximum_weight"], 0.4)
        self.subject_type = "composite_portfolio"
        refused = self.client.get("/api/portfolio/A/modelling/optimise")
        self.assertEqual(refused.status_code, 409)

    def test_factor_route(self) -> None:
        response = self.client.get("/api/portfolio/A/modelling/factors", params={"region": "developed"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["momentum_included"])
        self.assertTrue(math.isfinite(body["r_squared"]))
        self.assertEqual(self.client.get("/api/portfolio/A/modelling/factors", params={"region": "mars"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
