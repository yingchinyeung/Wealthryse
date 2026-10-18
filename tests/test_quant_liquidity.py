from __future__ import annotations

import math
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import DISCLAIMER, ModellingInputError  # noqa: E402
from ibkr_etl.quant import liquidity as lq  # noqa: E402

POSITIONS = {
    "A": {"quantity": 1000, "average_daily_volume": 1000, "value": 50.0},     # 5 days at 20%
    "B": {"quantity": 100, "average_daily_volume": 10000, "value": 30.0},     # 0.05 days
    "C": {"quantity": -5000, "average_daily_volume": 500, "value": -20.0},    # a short: 50 days
}


class AverageDailyVolumeTests(unittest.TestCase):
    def test_holidays_and_gaps_are_skipped(self) -> None:
        result = lq.average_daily_volume([100, 0, 200, None, 300])
        self.assertEqual(result["average_daily_volume"], 200)
        self.assertEqual((result["days_used"], result["days_skipped"], result["complete"]), (3, 2, False))

    def test_window_takes_the_latest_trading_days(self) -> None:
        result = lq.average_daily_volume([10, 20, 30, 0, 40], window=2)
        self.assertEqual(result["average_daily_volume"], 35)
        self.assertEqual((result["days_used"], result["days_skipped"], result["complete"]), (2, 1, True))
        dated = {"2026-10-02": 40, "2026-09-30": 10, "2026-10-01": 0, "2026-09-29": 30}
        self.assertEqual(lq.average_daily_volume(dated, window=2)["average_daily_volume"], 25)
        pairs = [("2026-10-02", 40), ("2026-09-30", 10), ("2026-10-01", None)]
        self.assertEqual(lq.average_daily_volume(pairs, window=2)["average_daily_volume"], 25)
        self.assertEqual(lq.average_daily_volume([5, 1, 3, 1000])["median_daily_volume"], 4)

    def test_no_volume_and_bad_input(self) -> None:
        empty = lq.average_daily_volume([0, None, 0])
        self.assertIsNone(empty["average_daily_volume"])
        self.assertEqual(empty["days_skipped"], 3)
        with self.assertRaises(ModellingInputError):
            lq.average_daily_volume([100, -5])
        with self.assertRaises(ModellingInputError):
            lq.average_daily_volume([100], window=0)


class DaysToLiquidateTests(unittest.TestCase):
    def test_hand_computed_profile(self) -> None:
        result = lq.days_to_liquidate(POSITIONS)
        days = {r["key"]: r["days"] for r in result["holdings"]}
        self.assertAlmostEqual(days["A"], 1000 / (0.2 * 1000))
        self.assertAlmostEqual(days["B"], 100 / (0.2 * 10000))
        self.assertAlmostEqual(days["C"], 5000 / (0.2 * 500))
        self.assertEqual([r["key"] for r in result["holdings"]], ["C", "A", "B"])
        sellable = result["sellable_within"]
        self.assertAlmostEqual(sellable["1"], (50 * 1 / 5 + 30 + 20 * 1 / 50) / 100)
        self.assertAlmostEqual(sellable["5"], (50 + 30 + 20 * 5 / 50) / 100)
        self.assertAlmostEqual(sellable["20"], (50 + 30 + 20 * 20 / 50) / 100)
        self.assertAlmostEqual(result["days_to_sell_everything"], 50)
        self.assertAlmostEqual(result["value_weighted_days"], (50 * 5 + 30 * 0.05 + 20 * 50) / 100)
        self.assertEqual(result["share_without_volume_data"], 0.0)
        self.assertEqual(result["disclaimer"], DISCLAIMER)

    def test_participation_scales_days(self) -> None:
        slow = lq.days_to_liquidate(POSITIONS, participation=0.10)
        fast = lq.days_to_liquidate(POSITIONS, participation=0.40)
        self.assertAlmostEqual(slow["days_to_sell_everything"], 4 * fast["days_to_sell_everything"])
        everything = lq.days_to_liquidate(POSITIONS, participation=1.0, horizons=(10,))
        self.assertAlmostEqual(everything["sellable_within"]["10"], 1.0)

    def test_missing_volume_counts_as_unsold(self) -> None:
        positions = {**POSITIONS, "D": {"quantity": 10, "value": 100.0,
                                        "average_daily_volume": lq.average_daily_volume([0, None])}}
        result = lq.days_to_liquidate(positions)
        self.assertEqual(result["no_volume_data"], ["D"])
        self.assertAlmostEqual(result["share_without_volume_data"], 0.5)
        self.assertIsNone(result["days_to_sell_everything"])
        self.assertAlmostEqual(result["sellable_within"]["5"], (50 + 30 + 2) / 200)
        self.assertEqual(result["holdings"][-1]["key"], "D")
        with_adv = {"E": {"quantity": 600, "value": 10.0, "average_daily_volume": lq.average_daily_volume([3000, 0, 3000])}}
        self.assertAlmostEqual(lq.days_to_liquidate(with_adv)["holdings"][0]["days"], 1.0)

    def test_bad_input_is_refused(self) -> None:
        for kwargs in ({"participation": 0.0}, {"participation": 1.5}, {"horizons": (0,)}):
            with self.assertRaises(ModellingInputError):
                lq.days_to_liquidate(POSITIONS, **kwargs)
        with self.assertRaises(ModellingInputError):
            lq.days_to_liquidate({})
        with self.assertRaises(ModellingInputError):
            lq.days_to_liquidate({"A": {"quantity": None, "value": 1.0, "average_daily_volume": 10}})
        with self.assertRaises(ModellingInputError):
            lq.days_to_liquidate({"A": {"quantity": 5, "value": 0.0, "average_daily_volume": 10}})


class LiquidityAdjustedVarTests(unittest.TestCase):
    def test_bangia_adjustment_by_hand(self) -> None:
        positions = {"A": {"value": 60.0, "spreads": [0.001, 0.003]},
                     "B": {"value": 40.0, "spreads": [0.01, 0.01, 0.01, None]}}
        result = lq.liquidity_adjusted_var(0.02, positions)
        cost_a = 0.5 * (0.002 + 3 * statistics.stdev([0.001, 0.003]))
        cost_b = 0.5 * 0.01
        self.assertAlmostEqual(result["liquidity_cost"], 0.6 * cost_a + 0.4 * cost_b)
        self.assertAlmostEqual(result["liquidity_adjusted_var"], 0.02 + 0.6 * cost_a + 0.4 * cost_b)
        self.assertAlmostEqual(result["liquidity_share"], result["liquidity_cost"] / result["liquidity_adjusted_var"])
        rows = {r["key"]: r for r in result["holdings"]}
        self.assertEqual(rows["B"]["spread_days"], 3)
        self.assertAlmostEqual(sum(r["contribution"] for r in result["holdings"]), result["liquidity_cost"])
        self.assertAlmostEqual(lq.liquidity_adjusted_var(0.02, positions, k=0)["liquidity_cost"],
                               0.6 * 0.001 + 0.4 * 0.005)

    def test_zero_spreads_leave_var_unchanged(self) -> None:
        result = lq.liquidity_adjusted_var(0.031, {"A": {"value": 5.0, "spreads": [0.0] * 20}})
        self.assertEqual(result["liquidity_adjusted_var"], 0.031)

    def test_missing_spreads_are_listed(self) -> None:
        positions = {"A": {"value": 75.0, "spreads": [0.002] * 5}, "B": {"value": 25.0}}
        result = lq.liquidity_adjusted_var(0.02, positions)
        self.assertEqual(result["no_spread_data"], ["B"])
        self.assertAlmostEqual(result["share_without_spread_data"], 0.25)
        self.assertAlmostEqual(result["liquidity_cost"], 0.75 * 0.001)
        self.assertTrue(math.isfinite(result["liquidity_adjusted_var"]))

    def test_bad_input_is_refused(self) -> None:
        good = {"A": {"value": 1.0, "spreads": [0.001]}}
        for args, kwargs in (((-0.01, good), {}), ((0.02, good), {"k": -1}), ((0.02, {}), {}),
                             ((0.02, {"A": {"value": 1.0, "spreads": [-0.001]}}), {})):
            with self.assertRaises(ModellingInputError):
                lq.liquidity_adjusted_var(*args, **kwargs)


if __name__ == "__main__":
    unittest.main()
