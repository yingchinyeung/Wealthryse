from __future__ import annotations

import json
import math
import random
import statistics
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import DISCLAIMER, ModellingInputError, _quantile  # noqa: E402
from ibkr_etl.quant import holdings_risk as hr  # noqa: E402
from ibkr_etl.quant import pre_trade as pt  # noqa: E402

NAV = 50000.0
POSITIONS = {"A": {"quantity": 1000, "price": 10}, "B": {"quantity": 500, "price": 40},
             "C": {"quantity": 200, "value": 10000}}                     # weights 0.2, 0.4, 0.2; cash 0.2
ORDERS = [{"holding": "D", "side": "BUY", "quantity": 100, "price": 100},  # a new holding at 0.2
          {"holding": "B", "quantity": -250}]                               # B down to 0.2


def _history(days: int = 300, seed: int = 3, names: str = "ABCD") -> tuple[dict[str, list[float]], list[float]]:
    generator = random.Random(seed)
    market = [generator.gauss(0, 0.01) for _ in range(days)]
    columns = {name: [(0.6 + 0.3 * i) * m + generator.gauss(0, 0.01) for m in market] for i, name in enumerate(names)}
    return columns, market


HISTORY, MARKET = _history()


class WeightsAndConcentrationTests(unittest.TestCase):
    def test_orders_move_weights_and_cash_takes_the_difference(self) -> None:
        result = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV)
        weights = {w["holding"]: (w["before"], w["after"]) for w in result["weights"]}
        self.assertEqual({k: tuple(round(v, 12) for v in pair) for k, pair in weights.items()},
                         {"A": (0.2, 0.2), "B": (0.4, 0.2), "C": (0.2, 0.2), "D": (0.0, 0.2)})
        before, after = result["before"], result["after"]
        self.assertAlmostEqual(before["cash_weight"], 0.2)
        self.assertAlmostEqual(after["cash_weight"], 0.2)
        self.assertEqual(before["largest_position"], "B")
        self.assertAlmostEqual(before["largest_position_weight"], 0.4)
        self.assertAlmostEqual(after["largest_position_weight"], 0.2)
        self.assertAlmostEqual(before["effective_holdings"], 1 / (0.25 ** 2 + 0.5 ** 2 + 0.25 ** 2))
        self.assertAlmostEqual(after["effective_holdings"], 4.0)
        self.assertAlmostEqual(after["top_5_weight"], 0.8)
        self.assertAlmostEqual(result["change"]["largest_position_weight"], -0.2)
        self.assertEqual([o["weight_change"] for o in result["orders"]], [0.2, -0.2])
        self.assertTrue(result["all_orders_checked"])

    def test_selling_everything_leaves_only_cash(self) -> None:
        orders = [{"holding": k, "quantity": -v["quantity"]} for k, v in POSITIONS.items()]
        after = pt.pre_trade_check(POSITIONS, orders, returns_history=HISTORY, nav=NAV,
                                   average_daily_volumes={"A": 1e5})["after"]
        self.assertEqual((after["gross_exposure"], after["ex_ante_volatility"], after["var_1d"]), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(after["cash_weight"], 1.0)
        self.assertIsNone(after["effective_holdings"])
        self.assertIsNone(after["liquidity_days"])


class RiskTests(unittest.TestCase):
    def test_volatility_and_var_match_the_engines(self) -> None:
        result = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV)
        covariance = hr.ledoit_wolf_covariance({k: HISTORY[k] for k in "ABCD"})
        for state, weights in (("before", {"A": 0.2, "B": 0.4, "C": 0.2}), ("after", dict.fromkeys("ABCD", 0.2))):
            expected = hr.ex_ante_risk(weights, covariance)["volatility"]
            self.assertAlmostEqual(result[state]["ex_ante_volatility"], expected, places=12)
            series = sorted(sum(w * HISTORY[k][t] for k, w in weights.items()) for t in range(300))
            cut = _quantile(series, 0.05)
            self.assertAlmostEqual(result[state]["var_1d"], -cut, places=12)
            self.assertAlmostEqual(result[state]["expected_tail_loss_1d"],
                                   -statistics.fmean([v for v in series if v <= cut]), places=12)
        self.assertEqual(result["risk_model"], "Ledoit-Wolf shrinkage covariance of the return history")
        self.assertEqual(result["history_days"], 300)
        at_99 = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV, var_level=0.99)
        self.assertGreater(at_99["after"]["var_1d"], result["after"]["var_1d"])

    def test_factor_model_gives_exposures_and_risk(self) -> None:
        model = hr.factor_risk_model(HISTORY, {"MKT": MARKET})
        result = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV, factor_model=model)
        betas = {name: row[0] for name, row in zip(model["names"], model["betas"])}
        self.assertAlmostEqual(result["before"]["factor_exposures"]["MKT"],
                               0.2 * betas["A"] + 0.4 * betas["B"] + 0.2 * betas["C"], places=12)
        self.assertAlmostEqual(result["after"]["factor_exposures"]["MKT"], 0.2 * sum(betas.values()), places=12)
        self.assertAlmostEqual(result["after"]["ex_ante_volatility"],
                               hr.ex_ante_risk(dict.fromkeys("ABCD", 0.2), model)["volatility"], places=12)
        self.assertAlmostEqual(result["change"]["factor_exposures"]["MKT"],
                               result["after"]["factor_exposures"]["MKT"] - result["before"]["factor_exposures"]["MKT"])
        self.assertIn("factor model", result["risk_model"])
        given = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV, factor_model=model,
                                   covariance=hr.ledoit_wolf_covariance(HISTORY))
        self.assertEqual(given["risk_model"], "the covariance given")

    def test_a_holding_without_history_is_listed_as_unmodelled(self) -> None:
        orders = ORDERS + [{"holding": "E", "quantity": 50, "price": 20}]
        result = pt.pre_trade_check(POSITIONS, orders, returns_history=HISTORY, nav=NAV)
        self.assertEqual(result["after"]["unmodelled"], ["E"])
        self.assertAlmostEqual(result["after"]["unmodelled_weight"], 0.02)
        self.assertEqual(result["before"]["unmodelled"], [])
        expected = hr.ex_ante_risk(dict.fromkeys("ABCD", 0.2), hr.ledoit_wolf_covariance(HISTORY))["volatility"]
        self.assertAlmostEqual(result["after"]["ex_ante_volatility"], expected, places=12)


class CostAndLiquidityTests(unittest.TestCase):
    def test_hand_computed_trading_cost(self) -> None:
        result = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV, impact_coefficient=0.5,
                                    average_daily_volumes={"D": 10000, "B": {"average_daily_volume": 25000}},
                                    spreads={"D": 0.002, "B": {"bid": 39.98, "ask": 40.02}})
        d, b = result["orders"]
        self.assertAlmostEqual(d["spread_cost"], 0.5 * 0.002 * 10000)
        sigma = statistics.stdev(HISTORY["D"])
        self.assertAlmostEqual(d["impact_cost"], 0.5 * sigma * math.sqrt(100 / 10000) * 10000, places=9)
        self.assertAlmostEqual(b["relative_spread"], 0.04 / 40)
        self.assertAlmostEqual(b["spread_cost"], 0.5 * 0.001 * 10000)
        self.assertAlmostEqual(b["participation"], 250 / 25000)
        cost = result["trading_cost"]
        self.assertAlmostEqual(cost["total"], d["cost"] + b["cost"])
        self.assertAlmostEqual(cost["bps_of_traded_value"], cost["total"] / 20000 * 10000)
        self.assertAlmostEqual(cost["share_of_nav"], cost["total"] / NAV)
        self.assertEqual(cost["orders_missing_cost_data"], [])
        bare = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV)
        self.assertEqual(bare["trading_cost"]["orders_missing_cost_data"], [1, 2])
        self.assertEqual(bare["orders"][0]["missing_cost_data"], ["spread", "impact"])

    def test_large_order_warns_that_the_impact_law_is_stretched(self) -> None:
        result = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV,
                                    average_daily_volumes={"D": 500})
        self.assertTrue(result["orders"][0]["warnings"])

    def test_days_to_liquidate_before_and_after(self) -> None:
        volumes = {"A": 5000, "B": 1000, "C": 4000, "D": 100}
        result = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV,
                                    average_daily_volumes=volumes, participation=0.1)
        self.assertAlmostEqual(result["before"]["liquidity_days"], 500 / (0.1 * 1000))
        self.assertAlmostEqual(result["after"]["liquidity_days"], 100 / (0.1 * 100))
        self.assertAlmostEqual(result["after"]["value_weighted_liquidity_days"],
                               (1000 / 500 + 250 / 100 + 200 / 400 + 100 / 10) / 4)


class LimitAndRefusalTests(unittest.TestCase):
    def test_new_and_cleared_breaches(self) -> None:
        orders = ORDERS + [{"holding": "A", "quantity": 1500}]                  # A to 0.5 of NAV
        limits = {"largest_position_weight": 0.3, "cash_weight": (0.0, 1.0), "ex_ante_volatility": 1.0}
        result = pt.pre_trade_check(POSITIONS, orders, returns_history=HISTORY, nav=NAV, limits=limits)
        checks = result["limits"]
        self.assertEqual(checks["status_before"], "red")                       # B at 0.4
        self.assertEqual([b["metric"] for b in checks["breaches_after"]], ["cash_weight", "largest_position_weight"])
        self.assertEqual([b["metric"] for b in checks["new_breaches"]], ["cash_weight"])   # cash now -0.1
        self.assertAlmostEqual(result["after"]["cash_weight"], -0.1)
        cleared = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV, limits=limits)["limits"]
        self.assertEqual(cleared["breaches_cleared"], ["largest_position_weight"])
        self.assertEqual(cleared["status_after"], "green")

    def test_orders_without_a_price_are_refused_by_name(self) -> None:
        positions = dict(POSITIONS, X={"quantity": 10})
        orders = ORDERS + [{"holding": "Y", "quantity": 5}, {"holding": "X", "quantity": -10}]
        result = pt.pre_trade_check(positions, orders, returns_history=HISTORY, nav=NAV)
        self.assertEqual([(r["index"], r["holding"]) for r in result["refused_orders"]], [(3, "Y"), (4, "X")])
        self.assertIn("no price for Y", result["refused_orders"][0]["reason"])
        self.assertFalse(result["all_orders_checked"])
        self.assertEqual(result["unpriced_positions"], ["X"])
        self.assertEqual([o["holding"] for o in result["orders"]], ["D", "B"])
        with self.assertRaises(ModellingInputError) as raised:
            pt.pre_trade_check(POSITIONS, [{"holding": "Y", "quantity": 5}], returns_history=HISTORY, nav=NAV)
        self.assertIn("Y", str(raised.exception))

    def test_states_plainly_that_it_is_hypothetical(self) -> None:
        result = pt.pre_trade_check(POSITIONS, ORDERS, returns_history=HISTORY, nav=NAV)
        self.assertEqual(result["disclaimer"], DISCLAIMER)
        self.assertIn("not advice", result["notice"])
        self.assertIn("Hypothetical", result["notice"])
        json.dumps(result, allow_nan=False)

    def test_bad_input_is_refused(self) -> None:
        cases = [
            dict(nav=0),
            dict(var_level=1.0),
            dict(impact_coefficient=-1),
            dict(returns_history={k: v[:10] for k, v in HISTORY.items()}),
            dict(covariance=[[1.0]]),
        ]
        for extra in cases:
            arguments = {"returns_history": HISTORY, "nav": NAV, **extra}
            with self.assertRaises(ModellingInputError, msg=extra):
                pt.pre_trade_check(POSITIONS, ORDERS, **arguments)
        for orders in ([], [{"quantity": 5}], [{"holding": "A", "quantity": 0}],
                       [{"holding": "A", "side": "SHORT", "quantity": 1}]):
            with self.assertRaises(ModellingInputError, msg=orders):
                pt.pre_trade_check(POSITIONS, orders, returns_history=HISTORY, nav=NAV)


class PerformanceTests(unittest.TestCase):
    def test_sixty_holdings_ten_years_runs_quickly(self) -> None:
        generator = random.Random(60)
        names = [f"H{i:02d}" for i in range(60)]
        market = [generator.gauss(0, 0.01) for _ in range(2520)]
        history = {n: [m + generator.gauss(0, 0.01) for m in market] for n in names}
        positions = {n: {"quantity": 100, "price": 10 + i} for i, n in enumerate(names)}
        nav = sum(v["quantity"] * v["price"] for v in positions.values()) * 1.1
        started = time.perf_counter()
        pt.pre_trade_check(positions, [{"holding": "H00", "quantity": 500}], returns_history=history, nav=nav,
                           average_daily_volumes=dict.fromkeys(names, 1e5), spreads=dict.fromkeys(names, 0.001))
        self.assertLess(time.perf_counter() - started, 5.0)


if __name__ == "__main__":
    unittest.main()
