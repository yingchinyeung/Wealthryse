from __future__ import annotations

import math
import random
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import calibration as cal  # noqa: E402

NORMAL = statistics.NormalDist()


class StatisticsTests(unittest.TestCase):
    def test_binomial_test_matches_a_direct_sum(self) -> None:
        self.assertAlmostEqual(cal.binomial_p_value(10, 10, 0.5), 2 * 0.5 ** 10, places=15)
        for successes, trials, p in ((3, 20, 0.3), (17, 25, 0.5), (0, 12, 0.1), (40, 50, 0.9)):
            pmf = [math.comb(trials, k) * p ** k * (1 - p) ** (trials - k) for k in range(trials + 1)]
            expected = sum(v for v in pmf if v <= pmf[successes] * (1 + 1e-7))
            self.assertAlmostEqual(cal.binomial_p_value(successes, trials, p), min(1.0, expected), places=12)

    def test_chi_square_known_values(self) -> None:
        self.assertAlmostEqual(cal.chi_square_survival(3.841458820694124, 1), 0.05, places=10)
        self.assertAlmostEqual(cal.chi_square_survival(18.307038053275146, 10), 0.05, places=10)
        self.assertAlmostEqual(cal.chi_square_survival(2.087900736, 9), 0.99, places=8)
        for x in (0.5, 3.0, 12.0):
            self.assertAlmostEqual(cal.chi_square_survival(x, 2), math.exp(-x / 2), places=13)
        self.assertEqual(cal.chi_square_survival(0.0, 4), 1.0)


class IntervalTests(unittest.TestCase):
    def test_coverage_by_hand(self) -> None:
        outcomes = [0.5] * 90 + [2.0] * 6 + [-2.0] * 4
        result = cal.interval_coverage([-1.0] * 100, [1.0] * 100, outcomes, level=0.9)
        self.assertEqual((result["hits"], result["misses_above"], result["misses_below"]), (90, 6, 4))
        self.assertAlmostEqual(result["hit_rate"], 0.9)
        self.assertAlmostEqual(result["p_value"], 1.0, places=9)
        self.assertIn("in line", result["verdict"])
        sure = cal.interval_coverage([0.0] * 20, [1.0] * 20, [0.5] * 20, level=0.5)
        self.assertAlmostEqual(sure["p_value"], 2 * 0.5 ** 20, places=15)
        self.assertIn("wider than needed", sure["verdict"])

    def test_narrow_ranges_are_caught_and_missing_items_skipped(self) -> None:
        generator = random.Random(1)
        outcomes = [generator.gauss(0, 1) for _ in range(500)]
        # ±1 holds about 68% of standard normal outcomes, not the 90% claimed.
        result = cal.interval_coverage([-1.0] * 500, [1.0] * 500, outcomes, level=0.9)
        self.assertAlmostEqual(result["hit_rate"], 0.683, delta=0.05)
        self.assertLess(result["p_value"], 1e-6)
        self.assertIn("too narrow", result["verdict"])
        honest = cal.interval_coverage([-1.645] * 500, [1.645] * 500, outcomes, level=0.9)
        self.assertGreater(honest["p_value"], 0.05)
        gappy = cal.interval_coverage([-1.0] * 22, [1.0] * 22, [0.0] * 20 + [None, float("nan")], level=0.9)
        self.assertEqual((gappy["observations"], gappy["skipped"]), (20, 2))

    def test_bad_ranges_are_refused(self) -> None:
        with self.assertRaises(ModellingInputError):
            cal.interval_coverage([1.0] * 20, [0.0] * 20, [0.5] * 20, level=0.9)
        with self.assertRaises(ModellingInputError):
            cal.interval_coverage([0.0] * 19, [1.0] * 19, [0.5] * 19, level=0.9)
        with self.assertRaises(ModellingInputError):
            cal.interval_coverage([0.0] * 20, [1.0] * 20, [0.5] * 21, level=0.9)
        with self.assertRaises(ModellingInputError):
            cal.interval_coverage([0.0] * 20, [1.0] * 20, [0.5] * 20, level=1.0)


class QuantileTests(unittest.TestCase):
    def test_shares_below_and_pinball_loss_by_hand(self) -> None:
        outcomes = [float(v) for v in range(1, 101)]
        result = cal.quantile_calibration({0.9: [90.0] * 100, 0.1: [10.5] * 100, 0.5: [50.0] * 100}, outcomes)
        levels = {row["level"]: row for row in result["levels"]}
        self.assertEqual([row["level"] for row in result["levels"]], [0.1, 0.5, 0.9])
        self.assertAlmostEqual(levels[0.1]["share_at_or_below"], 0.10)
        self.assertAlmostEqual(levels[0.5]["share_at_or_below"], 0.50)
        self.assertAlmostEqual(levels[0.9]["share_at_or_below"], 0.90)
        pinball = sum((0.5 - (1.0 if y < 50 else 0.0)) * (y - 50) for y in outcomes) / 100
        self.assertAlmostEqual(levels[0.5]["pinball_loss"], pinball, places=12)
        self.assertAlmostEqual(result["mean_absolute_error"], 0.0, places=12)
        self.assertEqual(result["crossings"], 0)

    def test_miscalibrated_quantiles_and_crossings(self) -> None:
        generator = random.Random(3)
        outcomes = [generator.gauss(0, 2) for _ in range(400)]
        forecasts = {level: [NORMAL.inv_cdf(level)] * 400 for level in (0.05, 0.5, 0.95)}   # scale 1, truth 2
        forecasts[0.5][0] = 5.0                                                             # crosses the 95% quantile
        result = cal.quantile_calibration(forecasts, outcomes)
        tail = result["levels"][0]
        self.assertAlmostEqual(tail["share_at_or_below"], NORMAL.cdf(-1.645 / 2), delta=0.04)
        self.assertLess(tail["p_value"], 1e-4)
        self.assertEqual(result["crossings"], 1)
        with self.assertRaises(ModellingInputError):
            cal.quantile_calibration({1.2: [0.0] * 30}, [0.0] * 30)
        with self.assertRaises(ModellingInputError):
            cal.quantile_calibration({}, [0.0] * 30)


class PitTests(unittest.TestCase):
    def test_uniform_values_pass(self) -> None:
        values = [(i + 0.5) / 200 for i in range(200)]
        result = cal.pit_histogram(values, bins=10)
        self.assertEqual([b["count"] for b in result["bins"]], [20] * 10)
        self.assertEqual(result["chi_square"], 0.0)
        self.assertEqual(result["p_value"], 1.0)
        self.assertIn("consistent", result["verdict"])

    def test_too_narrow_and_too_wide_forecasts(self) -> None:
        generator = random.Random(5)
        truth = [generator.gauss(0, 1) for _ in range(2000)]
        narrow = cal.pit_histogram([NORMAL.cdf(y / 0.5) for y in truth] + [None])     # forecast sd half the truth
        self.assertGreater(narrow["variance"], 1 / 12)
        self.assertLess(narrow["p_value"], 1e-6)
        self.assertIn("too narrow", narrow["verdict"])
        self.assertEqual(narrow["skipped"], 1)
        wide = cal.pit_histogram([NORMAL.cdf(y / 2.0) for y in truth])
        self.assertLess(wide["variance"], 1 / 12)
        self.assertIn("wider than needed", wide["verdict"])
        biased = cal.pit_histogram([NORMAL.cdf(y - 0.5) for y in truth])     # forecast centred 0.5 too high
        self.assertIn("below the forecast centre", biased["verdict"])
        self.assertNotIn("wider", biased["verdict"])
        self.assertNotIn("narrow", biased["verdict"])
        self.assertAlmostEqual(biased["normal_variance"], 1.0, delta=0.1)
        calibrated = cal.pit_histogram([NORMAL.cdf(y) for y in truth])
        self.assertGreater(calibrated["p_value"], 0.01)

    def test_bad_values_are_refused(self) -> None:
        with self.assertRaises(ModellingInputError):
            cal.pit_histogram([0.5] * 49 + [1.2])
        with self.assertRaises(ModellingInputError):
            cal.pit_histogram([0.5] * 30, bins=10)        # under 5 a bin
        with self.assertRaises(ModellingInputError):
            cal.pit_histogram([0.5] * 100, bins=1)


class ConformalTests(unittest.TestCase):
    def test_rank_by_hand(self) -> None:
        result = cal.conformal_factor([float(v) for v in range(19, 0, -1)], level=0.9, nominal_factor=12.0)
        self.assertEqual(result["rank"], 18)                 # ⌈20 × 0.9⌉
        self.assertEqual(result["factor"], 18.0)
        self.assertAlmostEqual(result["widening"], 0.5)
        self.assertEqual(cal.conformal_factor([1.0] * 19, level=0.95)["rank"], 19)   # ⌈20 × 0.95⌉ despite rounding
        with self.assertRaises(ModellingInputError):
            cal.conformal_factor([1.0] * 8, level=0.9)       # needs 9
        with self.assertRaises(ModellingInputError):
            cal.conformal_factor([1.0, -0.5] * 10, level=0.5)

    def test_recovers_the_true_multiple_and_covers_new_outcomes(self) -> None:
        generator = random.Random(7)
        centres = [generator.gauss(0, 0.05) for _ in range(4000)]
        scales = [generator.uniform(0.5, 1.5) for _ in range(4000)]
        outcomes = [c + generator.gauss(0, 2 * s) for c, s in zip(centres, scales)]   # true spread twice the model's
        scores = cal.nonconformity_scores(centres[:2000], scales[:2000], outcomes[:2000])
        result = cal.conformal_factor(scores, level=0.9, nominal_factor=1.645)
        self.assertAlmostEqual(result["factor"], 2 * 1.645, delta=0.15)
        self.assertAlmostEqual(result["widening"], 1.0, delta=0.1)
        covered = 0
        for c, s, y in zip(centres[2000:], scales[2000:], outcomes[2000:]):
            low, high = cal.widen_interval(c, s, result["factor"])
            covered += low <= y <= high
        self.assertAlmostEqual(covered / 2000, 0.9, delta=0.02)

    def test_scores_skip_bad_items_and_widen_interval(self) -> None:
        self.assertEqual(cal.nonconformity_scores([0.0, 1.0, 2.0, None], [1.0, 0.0, 2.0, 1.0], [0.5, 3.0, 1.0, 1.0]),
                         [0.5, 0.5])
        self.assertEqual(cal.widen_interval(1.0, 0.5, 2.0), (0.0, 2.0))
        with self.assertRaises(ModellingInputError):
            cal.widen_interval(1.0, -0.5, 2.0)


class ReliabilityTests(unittest.TestCase):
    def test_murphy_decomposition_by_hand(self) -> None:
        probabilities = [0.2] * 10 + [0.8] * 10
        outcomes = [1, 1] + [0] * 8 + [True] * 8 + [False] * 2
        result = cal.probability_reliability(probabilities, outcomes)
        self.assertAlmostEqual(result["brier_score"], 0.16, places=14)
        self.assertAlmostEqual(result["reliability"], 0.0, places=14)
        self.assertAlmostEqual(result["resolution"], 0.09, places=14)
        self.assertAlmostEqual(result["uncertainty"], 0.25, places=14)
        self.assertAlmostEqual(result["within_bin"], 0.0, places=14)
        self.assertAlmostEqual(result["brier_skill_score"], 1 - 0.16 / 0.25, places=14)

    def test_parts_add_up_and_overconfidence_shows_in_reliability(self) -> None:
        generator = random.Random(9)
        truth = [generator.random() for _ in range(3000)]
        outcomes = [1 if generator.random() < p else 0 for p in truth]
        honest = cal.probability_reliability(truth, outcomes)
        bold = cal.probability_reliability([min(1.0, max(0.0, 0.5 + 1.8 * (p - 0.5))) for p in truth], outcomes)
        for result in (honest, bold):
            parts = result["reliability"] - result["resolution"] + result["uncertainty"] + result["within_bin"]
            self.assertAlmostEqual(parts, result["brier_score"], places=12)
        self.assertLess(honest["reliability"], 0.005)
        self.assertGreater(bold["reliability"], 5 * honest["reliability"])
        self.assertGreater(honest["brier_skill_score"], bold["brier_skill_score"])

    def test_bad_inputs(self) -> None:
        with self.assertRaises(ModellingInputError):
            cal.probability_reliability([0.5] * 20, [2] * 20)
        with self.assertRaises(ModellingInputError):
            cal.probability_reliability([1.5] * 20, [1] * 20)
        with self.assertRaises(ModellingInputError):
            cal.probability_reliability([0.5] * 19, [1] * 19)
        skipped = cal.probability_reliability([0.5] * 20 + [None], [1, 0] * 10 + [1])
        self.assertEqual(skipped["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
