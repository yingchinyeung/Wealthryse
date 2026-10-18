from __future__ import annotations

import json
import math
import random
import statistics
import sys
import unittest
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import skill_statistics as ss  # noqa: E402

# Twenty days alternating a + b and a − b: mean a, 1/n standard deviation b, skewness 0, kurtosis 1.
A, B, N = 0.001, 0.01, 20
TWO_POINT = [A + B, A - B] * (N // 2)
SMALL_SERIES = [0.012, -0.004, 0.007, 0.015, -0.021, 0.003, 0.009, -0.002, 0.018, -0.011,
                0.004, 0.006, -0.008, 0.013, 0.001, -0.015, 0.010, 0.005, -0.003, 0.022, -0.006, 0.002]


def _weekdays(count: int) -> list[str]:
    days, day = [], date(2022, 1, 3)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _market_and_portfolio(count: int, alpha: float, beta: float, noise: float, seed: int):
    generator = random.Random(seed)
    market = [generator.gauss(0.0004, 0.01) for _ in range(count)]
    portfolio = [alpha + beta * m + generator.gauss(0.0, noise) for m in market]
    return portfolio, market


def _by_hand(values: list[float]) -> tuple[float, float, float]:
    """Per-period Sharpe (n − 1 standard deviation), skewness and kurtosis (1/n moments), written out."""
    n = len(values)
    mean = sum(values) / n
    m2 = sum((v - mean) ** 2 for v in values) / n
    m3 = sum((v - mean) ** 3 for v in values) / n
    m4 = sum((v - mean) ** 4 for v in values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    return mean / sd, m3 / m2 ** 1.5, m4 / m2 ** 2


class ProbabilisticSharpeTests(unittest.TestCase):
    def test_two_point_series_closed_form(self) -> None:
        result = ss.probabilistic_sharpe_ratio(TWO_POINT)
        # SR = (a/b)√((n−1)/n), skew 0, kurtosis 1, so PSR(0) = Φ(SR√(n−1)) = Φ((a/b)(n−1)/√n).
        self.assertAlmostEqual(result["sharpe_ratio"], 0.1 * math.sqrt(19 / 20), places=12)
        self.assertAlmostEqual(result["skewness"], 0.0, places=9)
        self.assertAlmostEqual(result["kurtosis"], 1.0, places=9)
        self.assertAlmostEqual(result["z_score"], 0.1 * 19 / math.sqrt(20), places=10)
        self.assertAlmostEqual(result["probabilistic_sharpe_ratio"], NormalDist().cdf(0.1 * 19 / math.sqrt(20)), places=10)
        self.assertAlmostEqual(result["probabilistic_sharpe_ratio"], 0.664528, places=6)
        self.assertAlmostEqual(result["sharpe_ratio_annual"], result["sharpe_ratio"] * math.sqrt(252), places=12)
        json.dumps(result)

    def test_skewed_series_matches_published_formula(self) -> None:
        sharpe, skew, kurt = _by_hand(SMALL_SERIES)
        target = 0.5 / math.sqrt(252)  # annual 0.5, per period
        spread = 1 - skew * sharpe + (kurt - 1) / 4 * sharpe ** 2
        z = (sharpe - target) * math.sqrt(len(SMALL_SERIES) - 1) / math.sqrt(spread)
        result = ss.probabilistic_sharpe_ratio(SMALL_SERIES, benchmark_sharpe=0.5)
        self.assertAlmostEqual(result["skewness"], skew, places=12)
        self.assertAlmostEqual(result["kurtosis"], kurt, places=12)
        self.assertAlmostEqual(result["z_score"], z, places=12)
        self.assertAlmostEqual(result["probabilistic_sharpe_ratio"], NormalDist().cdf(z), places=12)

    def test_benchmark_equal_to_the_estimate_gives_one_half(self) -> None:
        estimate = ss.probabilistic_sharpe_ratio(SMALL_SERIES)["sharpe_ratio_annual"]
        at_estimate = ss.probabilistic_sharpe_ratio(SMALL_SERIES, estimate)
        self.assertAlmostEqual(at_estimate["probabilistic_sharpe_ratio"], 0.5, places=12)

    def test_fat_left_tail_lowers_the_probability(self) -> None:
        symmetric = ss.probabilistic_sharpe_ratio([0.002 + 0.01 * ((-1) ** i) for i in range(100)])
        crashing = ss.probabilistic_sharpe_ratio([0.0045] * 95 + [-0.045 + 0.0] * 5)
        self.assertLess(crashing["skewness"], -3)
        same_sharpe = ss._psr(crashing["sharpe_ratio"], 0.0, 100, 0.0, 3.0)[0]
        self.assertLess(crashing["probabilistic_sharpe_ratio"], same_sharpe)
        self.assertGreater(symmetric["probabilistic_sharpe_ratio"], 0.5)

    def test_bad_inputs_are_refused(self) -> None:
        for values in (TWO_POINT[:10], [0.001] * 30, TWO_POINT[:-1] + [None], TWO_POINT[:-1] + [float("nan")]):
            with self.subTest(values=values[-1]), self.assertRaises(ModellingInputError):
                ss.probabilistic_sharpe_ratio(values)


class TrackRecordTests(unittest.TestCase):
    def test_two_point_series_closed_form(self) -> None:
        result = ss.minimum_track_record_length(TWO_POINT)
        sharpe = 0.1 * math.sqrt(19 / 20)
        self.assertAlmostEqual(result["minimum_periods"], 1 + (NormalDist().inv_cdf(0.95) / sharpe) ** 2, places=9)
        self.assertAlmostEqual(result["minimum_periods"], 285.794048, places=5)
        self.assertAlmostEqual(result["minimum_years"], result["minimum_periods"] / 252, places=12)
        self.assertFalse(result["enough_history"])

    def test_skewed_series_matches_published_formula(self) -> None:
        sharpe, skew, kurt = _by_hand(SMALL_SERIES)
        target = 0.2 / math.sqrt(252)
        spread = 1 - skew * sharpe + (kurt - 1) / 4 * sharpe ** 2
        expected = 1 + spread * (NormalDist().inv_cdf(0.9) / (sharpe - target)) ** 2
        result = ss.minimum_track_record_length(SMALL_SERIES, target_sharpe=0.2, confidence=0.9)
        self.assertAlmostEqual(result["minimum_periods"], expected, places=9)

    def test_record_at_the_minimum_length_reaches_the_confidence(self) -> None:
        sharpe, skew, kurt = 0.08, -0.7, 6.0
        length = ss._minimum_track_record(sharpe, 0.01, skew, kurt, 0.95)
        self.assertAlmostEqual(ss._psr(sharpe, 0.01, length, skew, kurt)[0], 0.95, places=12)
        portfolio, _ = _market_and_portfolio(600, 0.0003, 1.0, 0.004, 3)
        for count in (100, 300, 600):
            mintrl = ss.minimum_track_record_length(portfolio[:count])
            psr = ss.probabilistic_sharpe_ratio(portfolio[:count])["probabilistic_sharpe_ratio"]
            self.assertEqual(mintrl["enough_history"], psr >= 0.95)

    def test_no_length_is_enough_below_the_target(self) -> None:
        result = ss.minimum_track_record_length([-v for v in TWO_POINT])
        self.assertIsNone(result["minimum_periods"])
        self.assertFalse(result["enough_history"])
        self.assertIn("not above the target", result["reason"])
        with self.assertRaises(ModellingInputError):
            ss.minimum_track_record_length(TWO_POINT, confidence=1.0)


class DeflatedSharpeTests(unittest.TestCase):
    def test_published_example(self) -> None:
        # Bailey and López de Prado (2014): 100 trials whose annual Sharpe ratios have variance 0.5, the best
        # at 2.5 a year over 1,250 days (250 a year), skewness -3, kurtosis 10: SR0 = 0.1132 a day, DSR = 0.9004.
        threshold = ss._expected_maximum_sharpe(100, 0.5 / 250)
        self.assertAlmostEqual(threshold, 0.1132, places=4)
        self.assertAlmostEqual(ss._psr(2.5 / math.sqrt(250), threshold, 1250, -3.0, 10.0)[0], 0.9004, places=4)

    def test_expected_maximum_matches_the_formula(self) -> None:
        gamma = 0.5772156649015329
        normal = NormalDist()
        expected = math.sqrt(0.002) * ((1 - gamma) * normal.inv_cdf(1 - 1 / 10)
                                       + gamma * normal.inv_cdf(1 - 1 / (10 * math.e)))
        self.assertAlmostEqual(ss._expected_maximum_sharpe(10, 0.002), expected, places=15)
        self.assertEqual(ss._expected_maximum_sharpe(1, 0.002), 0.0)

    def test_falls_as_trials_rise(self) -> None:
        portfolio, _ = _market_and_portfolio(1000, 0.0004, 1.0, 0.004, 5)
        values = [ss.deflated_sharpe_ratio(portfolio, trials=t, trial_sharpe_variance=0.25)["deflated_sharpe_ratio"]
                  for t in (1, 2, 10, 100, 1000)]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertGreater(values[0] - values[-1], 0.1)
        self.assertAlmostEqual(values[0], ss.probabilistic_sharpe_ratio(portfolio)["probabilistic_sharpe_ratio"], places=12)
        more_spread = ss.deflated_sharpe_ratio(portfolio, trials=100, trial_sharpe_variance=1.0)["deflated_sharpe_ratio"]
        self.assertLess(more_spread, values[3])

    def test_bad_inputs_are_refused(self) -> None:
        for kwargs in ({"trials": 0, "trial_sharpe_variance": 0.1}, {"trials": 2.5, "trial_sharpe_variance": 0.1},
                       {"trials": True, "trial_sharpe_variance": 0.1}, {"trials": 5, "trial_sharpe_variance": -0.1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ModellingInputError):
                ss.deflated_sharpe_ratio(SMALL_SERIES, **kwargs)


class StationaryBootstrapTests(unittest.TestCase):
    @staticmethod
    def _mean_run(samples: list[list[int]], length: int) -> float:
        runs = 0
        for indices in samples:
            runs += 1 + sum(1 for a, b in pairwise(indices) if b != (a + 1) % length)
        return sum(len(s) for s in samples) / runs

    def test_indices_cover_the_record_with_the_requested_runs(self) -> None:
        samples = list(ss.stationary_bootstrap_indices(1000, samples=60, seed=4))
        self.assertEqual(len(samples), 60)
        for indices in samples:
            self.assertEqual(len(indices), 1000)
            self.assertTrue(all(0 <= i < 1000 for i in indices))
        self.assertAlmostEqual(self._mean_run(samples, 1000), 10.0, delta=0.6)
        independent = list(ss.stationary_bootstrap_indices(1000, samples=20, mean_block=1, seed=4))
        self.assertAlmostEqual(self._mean_run(independent, 1000), 1.0, delta=0.01)

    def test_reproducible_and_seeded(self) -> None:
        first = list(ss.stationary_bootstrap_indices(50, samples=5))
        self.assertEqual(first, list(ss.stationary_bootstrap_indices(50, samples=5)))
        self.assertNotEqual(list(ss.stationary_bootstrap_indices(50, samples=5, seed=1)),
                            list(ss.stationary_bootstrap_indices(50, samples=5, seed=2)))

    def test_bad_settings_are_refused_at_once(self) -> None:
        for args, kwargs in (((0,), {}), ((50,), {"mean_block": 0.5}), ((50,), {"samples": 0}), ((5,), {"mean_block": 10})):
            with self.subTest(args=args, kwargs=kwargs), self.assertRaises(ModellingInputError):
                ss.stationary_bootstrap_indices(*args, **kwargs)


class BootstrapConfidenceTests(unittest.TestCase):
    def test_sharpe_range_contains_the_estimate_and_narrows_with_history(self) -> None:
        generator = random.Random(8)
        returns = [generator.gauss(0.0005, 0.01) for _ in range(2000)]
        short = ss.bootstrap_confidence(returns[:250], "sharpe_ratio")
        long = ss.bootstrap_confidence(returns, "sharpe_ratio")
        for result in (short, long):
            self.assertLess(result["low"], result["estimate"])
            self.assertLess(result["estimate"], result["high"])
            self.assertEqual(result["samples_used"], 2000)
        self.assertLess(long["high"] - long["low"], 0.6 * (short["high"] - short["low"]))
        self.assertEqual(long, ss.bootstrap_confidence(returns, "sharpe_ratio"))
        json.dumps(long)

    def test_standard_error_matches_theory_for_independent_returns(self) -> None:
        generator = random.Random(10)
        returns = [generator.gauss(0.0005, 0.01) for _ in range(1000)]
        result = ss.bootstrap_confidence(returns, "sharpe_ratio", mean_block=1)
        daily = statistics.fmean(returns) / statistics.stdev(returns)
        theory = math.sqrt(252 * (1 + daily ** 2 / 2) / 1000)  # Lo (2002), normal returns
        self.assertAlmostEqual(result["standard_error"], theory, delta=0.1 * theory)

    def test_alpha_and_information_ratio_resample_jointly(self) -> None:
        portfolio, market = _market_and_portfolio(1500, 0.0004, 0.8, 0.004, 2)
        alpha = ss.bootstrap_confidence(portfolio, "alpha", benchmark=market)
        self.assertLess(alpha["low"], alpha["estimate"])
        self.assertLess(alpha["estimate"], alpha["high"])
        self.assertTrue(alpha["low"] < 0.0004 * 252 < alpha["high"])
        self.assertGreater(alpha["low"], 0)
        self.assertGreater(alpha["probability_positive"], 0.95)
        ir = ss.bootstrap_confidence(portfolio, "information_ratio", benchmark=market, level=0.8)
        self.assertLess(ir["low"], ir["estimate"])
        self.assertLess(ir["estimate"], ir["high"])
        self.assertEqual(ir["level"], 0.8)
        # Resampling days jointly keeps the benchmark's beta out of alpha: shuffling the benchmark breaks that.
        shuffled = market[::-1]
        apart = ss.bootstrap_confidence(portfolio, "alpha", benchmark=shuffled)
        self.assertGreater(apart["standard_error"], alpha["standard_error"])

    def test_dated_inputs_are_matched_by_date(self) -> None:
        portfolio, market = _market_and_portfolio(300, 0.0004, 0.8, 0.004, 6)
        days = _weekdays(300)
        result = ss.bootstrap_confidence(list(zip(days, portfolio)), "alpha",
                                         benchmark=list(zip(days, market))[1:], samples=200)
        self.assertEqual(result["observations"], 299)
        self.assertEqual(result["window_start"], days[1])
        as_mapping = ss.bootstrap_confidence(dict(zip(days, portfolio)), "alpha",
                                             benchmark=dict(list(zip(days, market))[1:]), samples=200)
        self.assertEqual(as_mapping["low"], result["low"])

    def test_bad_requests_are_refused(self) -> None:
        returns = [0.001 * ((-1) ** i) + 0.0002 for i in range(100)]
        for args, kwargs in (
            (("alpha",), {}),
            (("sortino",), {}),
            (("sharpe_ratio",), {"level": 1.2}),
            (("sharpe_ratio",), {"samples": 0}),
            (("information_ratio",), {"benchmark": returns}),       # identical: undefined
            (("information_ratio",), {"benchmark": returns[:-1]}),  # misaligned
        ):
            with self.subTest(args=args, kwargs=str(kwargs)[:40]), self.assertRaises(ModellingInputError):
                ss.bootstrap_confidence(returns, *args, **kwargs)


class SkillSummaryTests(unittest.TestCase):
    def test_short_record_is_not_enough(self) -> None:
        result = ss.skill_summary([0.012, -0.01] * 30)
        self.assertEqual(result["verdict_code"], "too_short")
        self.assertTrue(result["verdict"].startswith("Not enough history to tell skill from luck"))
        self.assertLess(result["probabilistic_sharpe_ratio"], 0.95)
        self.assertAlmostEqual(result["minimum_track_record_years"], result["minimum_track_record_days"] / 252, places=12)
        self.assertGreater(result["minimum_track_record_days"], 60)
        self.assertFalse(result["enough_history"])
        self.assertNotIn("alpha", result["confidence"])
        json.dumps(result)

    def test_losing_record_shows_no_skill(self) -> None:
        result = ss.skill_summary([-0.012, 0.01] * 30)
        self.assertEqual(result["verdict_code"], "no_skill")
        self.assertIsNone(result["minimum_track_record_days"])

    def test_strong_record_against_a_benchmark(self) -> None:
        portfolio, market = _market_and_portfolio(1500, 0.0006, 0.8, 0.004, 1)
        result = ss.skill_summary(portfolio, market)
        self.assertEqual(result["verdict_code"], "skill_likely", result["verdict"])
        self.assertTrue(result["enough_history"])
        self.assertGreater(result["deflated_sharpe_ratio"], 0.95)
        self.assertAlmostEqual(result["estimates"]["beta"], 0.8, delta=0.03)
        for name in ("sharpe_ratio", "alpha", "information_ratio"):
            band = result["confidence"][name]
            self.assertLess(band["low"], band["estimate"])
            self.assertLess(band["estimate"], band["high"])
        self.assertEqual(result["confidence"]["alpha"]["estimate"],
                         ss.bootstrap_confidence(portfolio, "alpha", benchmark=market)["estimate"])
        self.assertEqual(result["confidence"]["alpha"]["low"],
                         ss.bootstrap_confidence(portfolio, "alpha", benchmark=market)["low"])
        json.dumps(result)

    def test_many_trials_deflate_the_record(self) -> None:
        portfolio, _ = _market_and_portfolio(1500, 0.0006, 0.8, 0.004, 1)
        deflated = [ss.skill_summary(portfolio, trials=t, samples=200)["deflated_sharpe_ratio"] for t in (1, 10, 1000)]
        self.assertEqual(deflated, sorted(deflated, reverse=True))
        many = ss.skill_summary(portfolio, trials=1000, samples=200)
        self.assertEqual(many["verdict_code"], "not_after_trials", many["verdict"])
        self.assertIn("1000 strategies", many["verdict"])

    def test_beating_cash_but_not_the_benchmark(self) -> None:
        portfolio, market = _market_and_portfolio(1500, 0.00002, 1.0, 0.004, 1)
        market = [m + 0.0004 for m in market]
        portfolio = [p + 0.0004 for p in portfolio]
        result = ss.skill_summary(portfolio, market)
        self.assertEqual(result["verdict_code"], "benchmark_edge_unproven", result["verdict"])
        self.assertLessEqual(result["confidence"]["alpha"]["low"], 0)

    def test_dates_risk_free_and_bad_inputs(self) -> None:
        portfolio, market = _market_and_portfolio(200, 0.0004, 0.8, 0.004, 7)
        days = _weekdays(200)
        result = ss.skill_summary(list(zip(days, portfolio)), list(zip(days, market))[:-5],
                                  risk_free_daily=0.0001, samples=200)
        self.assertEqual(result["observations"], 195)
        self.assertEqual(result["window_end"], days[194])
        plain = ss.skill_summary(portfolio[:195], market[:195], samples=200)
        self.assertLess(result["estimates"]["sharpe_ratio"], plain["estimates"]["sharpe_ratio"])
        self.assertAlmostEqual(result["estimates"]["information_ratio"], plain["estimates"]["information_ratio"], places=12)
        with self.assertRaises(ModellingInputError):
            ss.skill_summary(list(zip(days, portfolio)), market)
        with self.assertRaises(ModellingInputError):
            ss.skill_summary(portfolio[:10])
        with self.assertRaises(ModellingInputError):
            ss.skill_summary(portfolio, trials=0)


if __name__ == "__main__":
    unittest.main()
