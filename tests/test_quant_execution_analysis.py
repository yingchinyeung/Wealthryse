from __future__ import annotations

import json
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import execution_analysis as ea  # noqa: E402


def _parts(result) -> float:
    return sum(part["amount"] for part in result["components"].values())


class ImplementationShortfallTests(unittest.TestCase):
    def test_buy_known_answer(self) -> None:
        fills = [{"quantity": 400, "price": 101.0, "commission": -2.0},
                 {"quantity": 300, "price": 101.5, "commission": -1.5}]
        result = ea.implementation_shortfall({"side": "BUY", "quantity": 1000}, fills,
                                             decision_price=100.0, arrival_price=100.5, final_price=103.0)
        parts = result["components"]
        self.assertAlmostEqual(parts["delay"]["amount"], 700 * 0.5)
        self.assertAlmostEqual(parts["trading"]["amount"], 400 * 0.5 + 300 * 1.0)
        self.assertAlmostEqual(parts["commission"]["amount"], 3.5)
        self.assertAlmostEqual(parts["opportunity"]["amount"], 300 * 3.0)
        self.assertAlmostEqual(result["total"]["amount"], 1753.5)
        self.assertAlmostEqual(result["total"]["bps"], 175.35)
        self.assertAlmostEqual(_parts(result), result["total"]["amount"])
        self.assertAlmostEqual(sum(p["bps"] for p in parts.values()), result["total"]["bps"])
        self.assertAlmostEqual(result["fill_rate"], 0.7)
        self.assertAlmostEqual(result["average_fill_price"], 70850.0 / 700)
        json.dumps(result)

    def test_sale_mirrors_the_buy(self) -> None:
        fills = [{"quantity": -400, "price": 99.0, "commission": -2.0},
                 {"quantity": -300, "price": 98.5, "commission": -1.5}]
        result = ea.implementation_shortfall({"side": "SELL", "quantity": 1000}, fills,
                                             decision_price=100.0, arrival_price=99.5, final_price=97.0)
        parts = result["components"]
        self.assertAlmostEqual(parts["delay"]["amount"], 350.0)
        self.assertAlmostEqual(parts["trading"]["amount"], 500.0)
        self.assertAlmostEqual(parts["opportunity"]["amount"], 900.0)
        self.assertAlmostEqual(result["total"]["amount"], 1753.5)

    def test_parts_sum_to_the_total_for_random_orders(self) -> None:
        generator = random.Random(11)
        for _ in range(300):
            ordered = generator.randint(1, 5000)
            fills, left = [], ordered
            for _ in range(generator.randint(0, 5)):
                if left <= 0:
                    break
                quantity = generator.randint(1, left)
                left -= quantity
                fills.append({"quantity": quantity, "price": 50 * math.exp(generator.gauss(0, 0.02)),
                              "commission": -generator.random()})
            result = ea.implementation_shortfall(
                {"side": generator.choice(["BUY", "SELL"]), "quantity": ordered}, fills,
                decision_price=50 * math.exp(generator.gauss(0, 0.02)),
                arrival_price=50 * math.exp(generator.gauss(0, 0.02)),
                final_price=50 * math.exp(generator.gauss(0, 0.05)))
            self.assertAlmostEqual(_parts(result), result["total"]["amount"], places=6)

    def test_edge_cases(self) -> None:
        nothing = ea.implementation_shortfall({"side": "BUY", "quantity": 100}, [],
                                              decision_price=10.0, arrival_price=10.2, final_price=11.0)
        self.assertAlmostEqual(nothing["components"]["delay"]["amount"], 0.0)
        self.assertAlmostEqual(nothing["total"]["amount"], 100.0)   # all opportunity cost
        self.assertIsNone(nothing["average_fill_price"])
        full = ea.implementation_shortfall({"side": "BUY", "quantity": 100}, [{"quantity": 100, "price": 10.0}],
                                           decision_price=10.0, arrival_price=10.0, final_price=12.0)
        self.assertEqual(full["components"]["opportunity"]["amount"], 0.0)
        self.assertEqual(full["total"]["amount"], 0.0)
        for order, fills in (({"side": "BUY", "quantity": 10}, [{"quantity": 11, "price": 1.0}]),
                             ({"side": "HOLD", "quantity": 10}, []),
                             ({"side": "BUY", "quantity": 0}, []),
                             ({"side": "BUY", "quantity": 10}, [{"quantity": 5, "price": None}])):
            with self.assertRaises(ModellingInputError):
                ea.implementation_shortfall(order, fills, decision_price=1.0, arrival_price=1.0, final_price=1.0)
        with self.assertRaises(ModellingInputError):
            ea.implementation_shortfall({"side": "BUY", "quantity": 10}, [], decision_price=float("nan"),
                                        arrival_price=1.0, final_price=1.0)


class SlippageAndSpreadTests(unittest.TestCase):
    def test_vwap_slippage(self) -> None:
        fills = [{"side": "BUY", "quantity": 100, "price": 10.1}, {"side": "BUY", "quantity": 100, "price": 10.3}]
        result = ea.vwap_slippage(fills, 10.0)
        self.assertAlmostEqual(result["average_fill_price"], 10.2)
        self.assertAlmostEqual(result["slippage_amount"], 40.0)
        self.assertAlmostEqual(result["slippage_bps"], 200.0)
        sold = ea.vwap_slippage([{"quantity": 100, "price": 10.2}], 10.0, side="SELL")
        self.assertAlmostEqual(sold["slippage_bps"], -200.0)        # sold above VWAP: better, a negative cost
        with self.assertRaises(ModellingInputError):
            ea.vwap_slippage(fills + [{"side": "SELL", "quantity": 1, "price": 10.0}], 10.0)
        with self.assertRaises(ModellingInputError):
            ea.vwap_slippage([], 10.0, side="BUY")

    def test_effective_half_spread(self) -> None:
        buy = ea.spread_paid(100.05, 99.9, 100.1, "BUY")
        self.assertAlmostEqual(buy["effective_half_spread_bps"], 5.0)
        self.assertAlmostEqual(buy["quoted_half_spread_bps"], 10.0)
        self.assertAlmostEqual(buy["share_of_quoted"], 0.5)
        self.assertAlmostEqual(ea.spread_paid(99.95, 99.9, 100.1, "SELL")["effective_half_spread_bps"], 5.0)
        self.assertAlmostEqual(ea.spread_paid(100.0, 99.9, 100.1, "BUY")["effective_half_spread"], 0.0)
        self.assertLess(ea.spread_paid(99.98, 99.9, 100.1, "BUY")["effective_half_spread"], 0.0)
        self.assertIsNone(ea.spread_paid(100.0, 100.0, 100.0, "BUY")["share_of_quoted"])
        with self.assertRaises(ModellingInputError):
            ea.spread_paid(100.0, 100.2, 100.1, "BUY")


class ImpactTests(unittest.TestCase):
    def test_square_root_law_known_answer(self) -> None:
        result = ea.square_root_impact(10_000, 1_000_000, 0.02, price=50.0)
        self.assertAlmostEqual(result["impact"], 0.002)
        self.assertAlmostEqual(result["impact_bps"], 20.0)
        self.assertAlmostEqual(result["cost_amount"], 0.002 * 50.0 * 10_000)
        self.assertEqual(result["warnings"], [])
        quadrupled = ea.square_root_impact(-40_000, 1_000_000, 0.02, coefficient=0.5)
        self.assertAlmostEqual(quadrupled["impact"], 0.5 * 2 * 0.002)
        self.assertTrue(ea.square_root_impact(200_000, 1_000_000, 0.02)["warnings"])
        with self.assertRaises(ModellingInputError):
            ea.square_root_impact(10, 0, 0.02)

    def test_fit_recovers_a_known_coefficient(self) -> None:
        generator = random.Random(5)
        records = []
        for _ in range(400):
            quantity, volume = generator.uniform(1e3, 5e4), generator.uniform(2e5, 5e6)
            sigma = generator.uniform(0.01, 0.04)
            x = sigma * math.sqrt(quantity / volume)
            records.append({"cost": 0.7 * x + generator.gauss(0, 0.0005), "quantity": quantity,
                            "average_daily_volume": volume, "daily_volatility": sigma})
        records.append({"cost": float("nan"), "quantity": 1, "average_daily_volume": 1, "daily_volatility": 0.01})
        result = ea.fit_impact_coefficient(records)
        self.assertEqual(result["observations"], 400)
        self.assertEqual(result["skipped"], 1)
        self.assertLess(abs(result["coefficient"] - 0.7), 3 * result["standard_error"])
        self.assertLess(result["standard_error"], 0.05)

    def test_fit_exact_line_and_too_few_trades(self) -> None:
        records = [{"cost": 0.9 * 0.02 * math.sqrt(q / 1e6), "quantity": q, "average_daily_volume": 1e6,
                    "daily_volatility": 0.02} for q in range(1000, 11000, 1000)]
        result = ea.fit_impact_coefficient(records)
        self.assertAlmostEqual(result["coefficient"], 0.9)
        self.assertAlmostEqual(result["standard_error"], 0.0)
        self.assertAlmostEqual(result["r_squared_uncentred"], 1.0)
        with self.assertRaises(ModellingInputError):
            ea.fit_impact_coefficient(records[:9])


class AlmgrenChrissTests(unittest.TestCase):
    # The parameters of Almgren and Chriss's (2000) worked example: a million shares over five days.
    PARAMS = dict(volatility=0.95, temporary_impact=2.5e-6, permanent_impact=2.5e-7)

    def test_no_risk_aversion_is_twap(self) -> None:
        exact = ea.almgren_chriss_schedule(1e6, 5, risk_aversion=0.0, **self.PARAMS)
        self.assertEqual(exact["holdings"], [1e6, 8e5, 6e5, 4e5, 2e5, 0.0])
        self.assertIsNone(exact["time_scale"])
        gaps = []
        for lam in (1e-9, 1e-12, 1e-15):
            near = ea.almgren_chriss_schedule(1e6, 5, risk_aversion=lam, **self.PARAMS)
            gaps.append(max(abs(got - want) for got, want in zip(near["holdings"], exact["holdings"])) / 1e6)
        self.assertEqual(gaps, sorted(gaps, reverse=True))   # the gap closes as risk aversion goes to 0
        self.assertLess(gaps[-1], 1e-7)

    def test_twap_cost_and_variance_closed_forms(self) -> None:
        x, n, tau = 1e6, 5, 1.0
        result = ea.almgren_chriss_schedule(x, n, risk_aversion=0.0, period_length=tau, **self.PARAMS)
        eta_tilde = 2.5e-6 - 0.5 * 2.5e-7 * tau
        self.assertAlmostEqual(result["expected_cost"], 0.5 * 2.5e-7 * x * x + eta_tilde * x * x / (n * tau))
        self.assertAlmostEqual(result["variance"], 0.95 ** 2 * tau * x * x * (n - 1) * (2 * n - 1) / (6 * n))
        self.assertAlmostEqual(result["twap"]["expected_cost"], result["expected_cost"])

    def test_optimal_schedule_solves_the_euler_lagrange_equation(self) -> None:
        lam, tau = 2e-6, 0.5
        result = ea.almgren_chriss_schedule(1e6, 10, risk_aversion=lam, period_length=tau, **self.PARAMS)
        x = result["holdings"]
        eta_tilde = 2.5e-6 - 0.5 * 2.5e-7 * tau
        kappa_tilde_sq = lam * 0.95 ** 2 / eta_tilde
        self.assertAlmostEqual(2 * (math.cosh(result["kappa"] * tau) - 1) / tau ** 2, kappa_tilde_sq)
        for k in range(1, len(x) - 1):
            self.assertAlmostEqual((x[k - 1] - 2 * x[k] + x[k + 1]) / tau ** 2 / (kappa_tilde_sq * x[k]), 1.0, places=9)
        self.assertAlmostEqual(x[0], 1e6)
        self.assertEqual(x[-1], 0.0)
        self.assertAlmostEqual(sum(result["trades"]), 1e6)
        self.assertLess(result["objective"], result["twap"]["objective"])
        generator = random.Random(1)
        for _ in range(50):
            moved = [x[0]] + [v + generator.gauss(0, 5e3) for v in x[1:-1]] + [0.0]
            cost, variance = ea._schedule_cost(moved, tau, 0.95, eta_tilde, 2.5e-7)
            self.assertGreater(cost + lam * variance, result["objective"])

    def test_more_risk_aversion_trades_sooner(self) -> None:
        slow = ea.almgren_chriss_schedule(1e6, 5, risk_aversion=1e-7, **self.PARAMS)
        fast = ea.almgren_chriss_schedule(1e6, 5, risk_aversion=1e-5, **self.PARAMS)
        self.assertGreater(fast["trades"][0], slow["trades"][0])
        self.assertLess(fast["variance"], slow["variance"])
        self.assertGreater(fast["expected_cost"], slow["expected_cost"])
        self.assertEqual(slow["trades"], sorted(slow["trades"], reverse=True))
        extreme = ea.almgren_chriss_schedule(1e6, 50, risk_aversion=1e3, **self.PARAMS)
        self.assertTrue(all(math.isfinite(v) for v in extreme["holdings"]))
        self.assertGreater(extreme["trades"][0], 0.99e6)
        json.dumps(extreme)

    def test_bad_inputs(self) -> None:
        with self.assertRaises(ModellingInputError):
            ea.almgren_chriss_schedule(1e6, 0, risk_aversion=0.0, **self.PARAMS)
        with self.assertRaises(ModellingInputError):
            ea.almgren_chriss_schedule(1e6, 5, risk_aversion=0.0, volatility=0.95, temporary_impact=1e-7,
                                       permanent_impact=2.5e-7, period_length=1.0)
        with self.assertRaises(ModellingInputError):
            ea.almgren_chriss_schedule(1e6, 5, risk_aversion=-1.0, **self.PARAMS)


if __name__ == "__main__":
    unittest.main()
