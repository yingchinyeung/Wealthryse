from __future__ import annotations

import json
import math
import random
import statistics
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import fair_value as fv  # noqa: E402


def _weekdays(count: int) -> list[str]:
    days, day = [], date(2023, 1, 2)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _random_walk(count: int, generator: random.Random, start: float = 100.0, step: float = 1.0) -> list[float]:
    values = [start]
    for _ in range(count - 1):
        values.append(values[-1] + generator.gauss(0, step))
    return values


class DualListingTests(unittest.TestCase):
    def test_parity_is_exact_on_a_fixed_ratio_and_fx(self) -> None:
        generator = random.Random(1)
        days = _weekdays(250)
        adr = [100 * math.exp(0.02 * generator.gauss(0, 1) * math.sqrt(i + 1) / 10) for i in range(250)]
        fx = [7.8 + 0.02 * generator.uniform(-1, 1) for _ in days]
        local = {d: a * f / 8 for d, a, f in zip(days, adr, fx)}
        result = fv.dual_listing_parity(local, dict(zip(days, adr)), shares_per_foreign_unit=8,
                                        fx_rates=dict(zip(days, fx)))
        for row in result["series"]:
            self.assertLess(abs(row["premium"]), 1e-12)
            self.assertAlmostEqual(row["fair_local_price"] / row["local_price"], 1.0, places=12)
            self.assertAlmostEqual(row["fair_foreign_price"] / row["foreign_price"], 1.0, places=12)
        self.assertIsNone(result["statistics"]["z_score"])
        self.assertIsNone(result["statistics"]["half_life"])
        self.assertIn("does not vary", result["warnings"][0])
        json.dumps(result)

    def test_known_answer_and_misaligned_dates(self) -> None:
        result = fv.dual_listing_parity(
            {"2024-05-02": 100.0, "2024-05-03": 101.0, "2024-05-06": None},
            {"2024-05-02": 102.4, "2024-05-03": 103.0, "2024-05-06": 104.0, "2024-05-07": 99.0},
            shares_per_foreign_unit=8, fx_rates={"2024-05-02": 7.8, "2024-05-06": 7.81})
        self.assertEqual(len(result["series"]), 1)
        row = result["latest"]
        self.assertAlmostEqual(row["fair_local_price"], 102.4 * 7.8 / 8)       # 99.84 HKD
        self.assertAlmostEqual(row["premium"], 100.0 / 99.84 - 1.0)
        self.assertAlmostEqual(row["fair_foreign_price"], 100.0 * 8 / 7.8)
        self.assertIn("at least 20", result["warnings"][0])
        with self.assertRaises(ModellingInputError):
            fv.dual_listing_parity({"2024-05-02": 1.0}, {"2024-05-03": 1.0}, shares_per_foreign_unit=1,
                                   fx_rates={"2024-05-02": 1.0})
        with self.assertRaises(ModellingInputError):
            fv.dual_listing_parity({}, {}, shares_per_foreign_unit=0, fx_rates={})

    def test_recovers_the_half_life_of_a_mean_reverting_premium(self) -> None:
        generator = random.Random(4)
        days = _weekdays(3000)
        premium, local, adr, fx = 0.0, {}, {}, {}
        for day in days:
            premium = 0.9 * premium + generator.gauss(0, 0.002)
            adr[day], fx[day] = 80 + generator.random(), 7.8
            local[day] = adr[day] * 7.8 / 8 * (1 + premium)
        result = fv.dual_listing_parity(local, adr, shares_per_foreign_unit=8, fx_rates=fx)
        stats = result["statistics"]
        self.assertAlmostEqual(stats["ar1"]["coefficient"], 0.9, delta=0.02)
        self.assertAlmostEqual(stats["half_life"], math.log(0.5) / math.log(0.9), delta=1.2)
        premiums = [row["premium"] for row in result["series"]]
        expected = (premiums[-1] - statistics.fmean(premiums)) / statistics.stdev(premiums)
        self.assertAlmostEqual(stats["z_score"], expected)
        self.assertAlmostEqual(result["series"][-1]["z_score"], stats["z_score"])


class EtfPremiumTests(unittest.TestCase):
    def test_premium_and_z_score(self) -> None:
        days = _weekdays(30)
        navs = {d: 100.0 for d in days}
        prices = {d: 100.0 + (0.1 if i % 2 else -0.1) for i, d in enumerate(days)}
        prices[days[-1]] = 101.0
        prices[days[3]] = float("nan")
        result = fv.etf_premium(prices, navs)
        self.assertEqual(result["statistics"]["observations"], 29)
        self.assertAlmostEqual(result["latest"]["premium"], 0.01)
        premiums = [row["premium"] for row in result["series"]]
        self.assertAlmostEqual(result["statistics"]["z_score"],
                               (0.01 - statistics.fmean(premiums)) / statistics.stdev(premiums))
        self.assertLess(result["statistics"]["ar1"]["coefficient"], 0.0)  # alternating sign
        with self.assertRaises(ModellingInputError):
            fv.etf_premium({days[0]: 1.0}, {days[1]: 1.0})


class AR1Tests(unittest.TestCase):
    def test_geometric_decay_has_a_known_half_life(self) -> None:
        fit = fv.ar1([10 * 0.5 ** t + 3 for t in range(30)])
        self.assertAlmostEqual(fit["coefficient"], 0.5)
        self.assertAlmostEqual(fit["half_life"], 1.0)
        self.assertAlmostEqual(fit["long_run_mean"], 3.0)
        explosive = fv.ar1([1.1 ** t for t in range(30)])
        self.assertFalse(explosive["mean_reverting"])
        self.assertIsNone(explosive["half_life"])
        self.assertIsNone(fv.ar1([2.0] * 30))


class EngleGrangerTests(unittest.TestCase):
    def test_critical_values(self) -> None:
        asymptotic = fv.mackinnon_critical_values(10 ** 9)
        self.assertAlmostEqual(asymptotic["1%"], -3.90, places=2)
        self.assertAlmostEqual(asymptotic["5%"], -3.34, places=2)
        self.assertAlmostEqual(asymptotic["10%"], -3.04, places=2)
        self.assertAlmostEqual(fv.mackinnon_critical_values(100)["5%"], -3.33613 - 0.061101 - 0.0006823)

    def test_accepts_a_cointegrated_pair(self) -> None:
        generator = random.Random(7)
        days = _weekdays(500)
        x = _random_walk(500, generator)
        noise, y = 0.0, []
        for value in x:
            noise = 0.5 * noise + generator.gauss(0, 1)
            y.append(3.0 + 1.5 * value + noise)
        result = fv.engle_granger(dict(zip(days, y)), dict(zip(days, x)))
        self.assertTrue(result["cointegrated"])
        self.assertEqual(result["significance"], "1%")
        self.assertAlmostEqual(result["hedge_ratio"], 1.5, delta=0.05)
        self.assertLess(result["half_life"], 3.0)
        self.assertEqual(result["window_start"], days[0])
        self.assertEqual(len(result["spread"]), 500)
        json.dumps(result)

    def test_rejects_independent_random_walks_in_most_seeds(self) -> None:
        found = 0
        for seed in range(20):
            generator = random.Random(100 + seed)
            found += fv.engle_granger(_random_walk(400, generator), _random_walk(400, generator))["cointegrated"]
        self.assertLessEqual(found, 3)

    def test_matches_a_hand_built_dickey_fuller_regression(self) -> None:
        generator = random.Random(2)
        u = [0.0]
        for _ in range(200):
            u.append(0.7 * u[-1] + generator.gauss(0, 1))
        test = fv.augmented_dickey_fuller(u, max_lag=0)
        lagged, change = u[:-1], [b - a for a, b in zip(u, u[1:])]
        rho = sum(a * b for a, b in zip(lagged, change)) / sum(a * a for a in lagged)
        residual = [c - rho * a for a, c in zip(lagged, change)]
        error = math.sqrt(sum(r * r for r in residual) / (len(change) - 1) / sum(a * a for a in lagged))
        self.assertAlmostEqual(test["rho"], rho)
        self.assertAlmostEqual(test["statistic"], rho / error)
        self.assertEqual(test["observations"], 200)

    def test_edge_cases(self) -> None:
        x = list(range(100))
        exact = fv.engle_granger([2 * v + 1 for v in x], x)
        self.assertTrue(exact["exact_fit"])
        self.assertAlmostEqual(exact["hedge_ratio"], 2.0)
        with self.assertRaises(ModellingInputError):
            fv.engle_granger(x[:30], x[:30])
        with self.assertRaises(ModellingInputError):
            fv.engle_granger(x, [1.0] * 100)
        with self.assertRaises(ModellingInputError):
            fv.engle_granger(x, x[:-1])
        with self.assertRaises(ModellingInputError):
            fv.engle_granger(x, x, max_lag=-1)


class SpreadSignalTests(unittest.TestCase):
    def test_rich_cheap_and_within(self) -> None:
        base = [(-1.0) ** i for i in range(59)]
        rich = fv.spread_signal(base + [5.0])
        window = base + [5.0]
        self.assertAlmostEqual(rich["z_score"], (5.0 - statistics.fmean(window)) / statistics.stdev(window))
        self.assertEqual((rich["position"], rich["signal"], rich["beyond_entry"]), ("rich", "sell_spread", True))
        cheap = fv.spread_signal(base + [-5.0])
        self.assertEqual(cheap["signal"], "buy_spread")
        calm = fv.spread_signal(base + [0.5])
        self.assertEqual((calm["position"], calm["signal"]), ("within", None))
        self.assertTrue(calm["mean_reverting"])
        json.dumps(rich)

    def test_dated_spread_and_bad_inputs(self) -> None:
        days = _weekdays(80)
        generator = random.Random(3)
        spread = {d: generator.gauss(0, 1) for d in days}
        spread[days[10]] = None
        result = fv.spread_signal(spread, lookback=20, entry_z=1.5)
        self.assertEqual(result["as_of"], days[-1])
        self.assertEqual(result["lookback"], 20)
        with self.assertRaises(ModellingInputError):
            fv.spread_signal(list(spread.values())[:30], lookback=60)
        with self.assertRaises(ModellingInputError):
            fv.spread_signal([1.0] * 60)
        with self.assertRaises(ModellingInputError):
            fv.spread_signal(list(range(60)), lookback=5)
        with self.assertRaises(ModellingInputError):
            fv.spread_signal(list(range(60)), entry_z=0)


if __name__ == "__main__":
    unittest.main()
