from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import rebalancing as rb  # noqa: E402

DIAGONAL = [[0.04, 0.0], [0.0, 0.09]]


class BandTests(unittest.TestCase):
    def test_cube_root_rule_by_hand(self) -> None:
        cov = [[0.04, 0.01, 0.0], [0.01, 0.09, 0.0], [0.0, 0.0, 0.0225]]
        result = rb.no_trade_bands(cov, {"A": 0.4, "B": 0.35, "C": 0.25}, cost_bps=10, risk_aversion=4)
        self.assertAlmostEqual(result["bands"]["A"], (3 * 0.001 * 0.4 ** 2 * 0.6 ** 2 / 8) ** (1 / 3), places=14)
        self.assertAlmostEqual(result["bands"]["A"], 0.0278495, places=6)
        row = result["holdings"][0]
        self.assertAlmostEqual(row["lower"], 0.4 - row["half_width"], places=14)
        self.assertAlmostEqual(row["upper"], 0.4 + row["half_width"], places=14)

    def test_bands_scale_as_the_cube_root(self) -> None:
        base = rb.no_trade_bands(DIAGONAL, [0.6, 0.4], cost_bps=5, risk_aversion=3)["bands"]
        dearer = rb.no_trade_bands(DIAGONAL, [0.6, 0.4], cost_bps=40, risk_aversion=3)["bands"]
        warier = rb.no_trade_bands(DIAGONAL, [0.6, 0.4], cost_bps=5, risk_aversion=24)["bands"]
        for i in range(2):
            self.assertAlmostEqual(dearer[i], 2.0 * base[i], places=14)
            self.assertAlmostEqual(warier[i], 0.5 * base[i], places=14)

    def test_drift_speed_and_cost_from_the_covariance(self) -> None:
        result = rb.no_trade_bands(DIAGONAL, [0.5, 0.5], cost_bps=10, risk_aversion=2)
        row = result["holdings"][0]
        drift = 0.25 * 0.25 * (0.04 + 0.09)        # w²·Var(r₁ − r_p), r_p = (r₁ + r₂)/2
        half = row["half_width"]
        self.assertAlmostEqual(row["drift_volatility"], math.sqrt(drift), places=14)
        self.assertAlmostEqual(row["expected_years_to_edge"], half * half / drift, places=12)
        self.assertAlmostEqual(row["expected_turnover_per_year"], drift / (2 * half), places=12)
        self.assertAlmostEqual(result["expected_cost_per_year"], 2 * 0.001 * drift / (2 * half), places=14)

    def test_trading_rate_matches_a_simulated_band_policy(self) -> None:
        row = rb.no_trade_bands(DIAGONAL, [0.5, 0.5], cost_bps=10, risk_aversion=2)["holdings"][0]
        half, drift = row["half_width"], row["drift_volatility"] ** 2
        generator, steps_per_year, years = random.Random(11), 2520, 100
        step = math.sqrt(drift / steps_per_year)
        position, traded, squares = 0.0, 0.0, 0.0
        for _ in range(steps_per_year * years):
            position += generator.gauss(0.0, step)
            if abs(position) > half:
                traded += abs(position) - half
                position = math.copysign(half, position)
            squares += position * position
        self.assertAlmostEqual(traded / years, row["expected_turnover_per_year"], delta=0.1 * row["expected_turnover_per_year"])
        self.assertAlmostEqual(squares / (steps_per_year * years), half * half / 3, delta=0.1 * half * half / 3)

    def test_edge_cases_and_bad_inputs(self) -> None:
        result = rb.no_trade_bands([[0.04]], [1.0], cost_bps=10, risk_aversion=2)
        self.assertEqual(result["bands"][0], 0.0)
        self.assertIsNone(result["holdings"][0]["expected_years_to_edge"])
        unheld = rb.no_trade_bands(DIAGONAL, [0.0, 0.6], cost_bps=10, risk_aversion=2)["holdings"][0]
        self.assertEqual((unheld["half_width"], unheld["expected_turnover_per_year"]), (0.0, 0.0))
        free = rb.no_trade_bands(DIAGONAL, [0.5, 0.5], cost_bps=0, risk_aversion=2)
        self.assertEqual(free["expected_cost_per_year"], 0.0)
        self.assertIsNone(free["holdings"][0]["expected_turnover_per_year"])
        for weights, kwargs in (([0.7, 0.6], {}), ([0.5, -0.1], {}), ([1.0], {}), ([0.5, 0.5], {"risk_aversion": 0}),
                                ([0.5, 0.5], {"cost_bps": -1}), ([0.5, None], {})):
            with self.assertRaises(ModellingInputError):
                rb.no_trade_bands(DIAGONAL, weights, **{"cost_bps": 10, "risk_aversion": 2, **kwargs})


class PlanTests(unittest.TestCase):
    CURRENT = {"A": 0.45, "B": 0.25, "C": 0.20, "E": 0.05}
    TARGET = {"A": 0.40, "B": 0.30, "C": 0.20, "D": 0.10}
    BANDS = {"A": 0.03, "B": 0.02, "C": 0.05, "D": 0.02}
    PRICES = {"A": 100.0, "B": 50.0, "C": 20.0, "D": 10.0, "E": 5.0}

    def test_trades_go_to_the_band_edge_by_hand(self) -> None:
        plan = rb.rebalance_plan(self.CURRENT, self.TARGET, self.BANDS, nav=1_000_000, prices=self.PRICES, cost_bps=10,
                                 dividend_withholding={"A": {"rate": 0.30, "yield": 0.02}, "B": (0.15, 0.01)})
        trades = {t["holding"]: t for t in plan["trades"]}
        self.assertEqual(set(trades), {"A", "B", "D", "E"})        # C is inside its band
        self.assertAlmostEqual(trades["A"]["value"], -20_000, places=6)    # 45% down to the 43% edge
        self.assertAlmostEqual(trades["A"]["shares"], -200, places=8)
        self.assertAlmostEqual(trades["B"]["shares"], 600, places=8)       # 25% up to the 28% edge
        self.assertAlmostEqual(trades["D"]["value"], 80_000, places=6)     # new holding to its 8% lower edge
        self.assertAlmostEqual(trades["E"]["shares"], -10_000, places=8)   # no target and no band: sold out
        self.assertEqual(trades["E"]["side"], "sell")
        self.assertAlmostEqual(plan["traded_value"], 180_000, places=6)
        self.assertAlmostEqual(plan["expected_cost"], 180, places=8)
        self.assertAlmostEqual(plan["full_rebalance_cost"], 250, places=8)
        self.assertAlmostEqual(plan["cost_saved"], 70, places=8)
        self.assertAlmostEqual(plan["cash_weight_before"], 0.05, places=12)
        self.assertAlmostEqual(plan["cash_weight_after"], 0.05 - 0.04 - 180 / 1_000_000, places=12)
        self.assertAlmostEqual(plan["remaining_drift"]["total"], 0.07, places=12)
        self.assertAlmostEqual(plan["remaining_drift"]["largest"], 0.03, places=12)
        self.assertEqual(plan["warnings"], [])
        withholding = plan["withholding"]
        self.assertAlmostEqual(withholding["holdings"][0]["withheld_per_year"], 430_000 * 0.02 * 0.30, places=6)
        self.assertAlmostEqual(withholding["withheld_per_year"], 430_000 * 0.006 + 280_000 * 0.0015, places=6)
        self.assertAlmostEqual(withholding["share_of_nav"], withholding["withheld_per_year"] / 1_000_000, places=14)

    def test_inside_every_band_means_no_trades(self) -> None:
        plan = rb.rebalance_plan({"A": 0.42, "B": 0.58}, {"A": 0.4, "B": 0.6}, {"A": 0.03, "B": 0.03},
                                 nav=10_000, prices={}, cost_bps=10)
        self.assertEqual(plan["trades"], [])
        self.assertEqual(plan["expected_cost"], 0.0)
        self.assertIsNone(plan["withholding"])

    def test_bands_from_the_rule_feed_the_plan(self) -> None:
        bands = rb.no_trade_bands(DIAGONAL, {"X": 0.5, "Y": 0.5}, cost_bps=10, risk_aversion=2)["bands"]
        plan = rb.rebalance_plan({"X": 0.6, "Y": 0.4}, {"X": 0.5, "Y": 0.5}, bands, nav=50_000,
                                 prices={"X": 10.0, "Y": 20.0}, cost_bps=10)
        weights = {h["holding"]: h["weight_after"] for h in plan["holdings"]}
        self.assertAlmostEqual(weights["X"], 0.5 + bands["X"], places=14)
        self.assertAlmostEqual(weights["Y"], 0.5 - bands["Y"], places=14)

    def test_short_cash_is_flagged_and_bad_inputs_refused(self) -> None:
        plan = rb.rebalance_plan({"A": 0.5, "B": 0.5}, {"A": 0.3, "B": 0.7}, {}, nav=1000,
                                 prices={"A": 1.0, "B": 1.0}, cost_bps=10)
        self.assertLess(plan["cash_weight_after"], 0)            # the trades' cost has to come from somewhere
        self.assertTrue(plan["warnings"])
        with self.assertRaises(ModellingInputError):
            rb.rebalance_plan(self.CURRENT, self.TARGET, self.BANDS, nav=1000, prices={"A": 100.0}, cost_bps=10)
        for kwargs in ({"nav": 0}, {"cost_bps": -1}, {"dividend_withholding": {"A": {"rate": 1.5, "yield": 0.02}}},
                       {"dividend_withholding": {"A": 0.3}}):
            with self.assertRaises(ModellingInputError):
                rb.rebalance_plan(self.CURRENT, self.TARGET, self.BANDS,
                                  **{"nav": 1000, "prices": self.PRICES, "cost_bps": 10, **kwargs})
        with self.assertRaises(ModellingInputError):
            rb.rebalance_plan({"A": -0.1}, self.TARGET, self.BANDS, nav=1000, prices=self.PRICES, cost_bps=10)


if __name__ == "__main__":
    unittest.main()
