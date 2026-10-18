from __future__ import annotations

import math
import random
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl import risk_forecasting as rf  # noqa: E402
from ibkr_etl.modelling import ModellingInputError, _quantile  # noqa: E402
from ibkr_etl.quant import tail_risk as tr  # noqa: E402


def _weekdays(count: int) -> list[str]:
    days, day = [], date(2016, 1, 4)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _gpd_sample(count: int, shape: float, scale: float, seed: int) -> list[float]:
    generator = random.Random(seed)
    if shape == 0:
        return [generator.expovariate(1.0 / scale) for _ in range(count)]
    return [scale / shape * ((1.0 - generator.random()) ** -shape - 1.0) for _ in range(count)]


def _student_t(count: int, df: int, scale: float, seed: int) -> list[float]:
    generator = random.Random(seed)
    out = []
    for _ in range(count):
        chi = sum(generator.gauss(0, 1) ** 2 for _ in range(df))
        out.append(scale * generator.gauss(0, 1) / math.sqrt(chi / df) * math.sqrt((df - 2) / df))
    return out


def _known_tail(count: int, seed: int) -> list[float]:
    """Returns whose losses beyond 1.5% are exactly generalised Pareto (shape 0.3, scale 0.8%), 10% of days."""
    generator = random.Random(seed)
    out = []
    for _ in range(count):
        if generator.random() < 0.9:
            loss = generator.uniform(-0.03, 0.015)
        else:
            loss = 0.015 + 0.008 / 0.3 * ((1.0 - generator.random()) ** -0.3 - 1.0)
        out.append(-loss)
    return out


class ChiSquareTests(unittest.TestCase):
    def test_closed_forms_match_published_critical_values(self) -> None:
        five_percent = {1: 3.841459, 2: 5.991465, 3: 7.814728, 4: 9.487729, 5: 11.070498, 6: 12.591587}
        for df, value in five_percent.items():
            self.assertAlmostEqual(tr._chi_square_tail(value, df), 0.05, places=6)
        self.assertAlmostEqual(tr._chi_square_tail(6.634897, 1), 0.01, places=6)
        self.assertEqual(tr._chi_square_tail(0.0, 3), 1.0)


class GpdTests(unittest.TestCase):
    def test_recovers_known_shape_and_scale(self) -> None:
        fit = tr.fit_gpd(_gpd_sample(4000, 0.25, 0.01, seed=1))
        errors = fit["standard_errors"]
        self.assertAlmostEqual(fit["shape"], 0.25, delta=3 * errors["shape"])
        self.assertAlmostEqual(fit["scale"], 0.01, delta=3 * errors["scale"])
        self.assertAlmostEqual(errors["shape"], (1 + fit["shape"]) / math.sqrt(4000))
        low, high = fit["shape_interval_95"]
        self.assertLess(low, 0.25)
        self.assertGreater(high, 0.25)

    def test_exponential_tail_gives_shape_near_zero(self) -> None:
        sample = _gpd_sample(4000, 0.0, 0.02, seed=2)
        fit = tr.fit_gpd(sample)
        self.assertAlmostEqual(fit["shape"], 0.0, delta=0.06)
        self.assertAlmostEqual(fit["scale"], sum(sample) / len(sample), delta=0.002)
        # The likelihood is continuous through shape 0, where it is the exponential one.
        n, total, scale = len(sample), math.fsum(sample), 0.02
        exponential = -n * math.log(scale) - total / scale
        self.assertAlmostEqual(tr._gpd_loglik(sample, 0.0, scale), exponential)
        self.assertAlmostEqual(tr._gpd_loglik(sample, 1e-7, scale), exponential, delta=1e-3)
        self.assertAlmostEqual(tr._gpd_loglik(sample, -1e-7, scale), exponential, delta=1e-3)

    def test_fit_is_a_maximum(self) -> None:
        sample = _gpd_sample(800, 0.15, 0.012, seed=3)
        fit = tr.fit_gpd(sample)
        best = fit["log_likelihood"]
        self.assertGreaterEqual(best, tr._gpd_loglik(sample, fit["start"]["shape"], fit["start"]["scale"]))
        for d_shape, d_scale in ((0.01, 0), (-0.01, 0), (0, 0.01), (0, -0.01)):
            self.assertLess(tr._gpd_loglik(sample, fit["shape"] + d_shape, fit["scale"] * (1 + d_scale)), best)

    def test_standard_errors_match_observed_information(self) -> None:
        sample = _gpd_sample(4000, 0.2, 0.01, seed=4)
        fit = tr.fit_gpd(sample)
        xi, beta = fit["shape"], fit["scale"]
        h = (1e-4, beta * 1e-4)

        def f(a: float, b: float) -> float:
            return tr._gpd_loglik(sample, a, b)

        d11 = (f(xi + h[0], beta) - 2 * f(xi, beta) + f(xi - h[0], beta)) / h[0] ** 2
        d22 = (f(xi, beta + h[1]) - 2 * f(xi, beta) + f(xi, beta - h[1])) / h[1] ** 2
        d12 = (f(xi + h[0], beta + h[1]) - f(xi + h[0], beta - h[1]) - f(xi - h[0], beta + h[1])
               + f(xi - h[0], beta - h[1])) / (4 * h[0] * h[1])
        det = d11 * d22 - d12 * d12
        observed_shape, observed_scale = math.sqrt(-d22 / det), math.sqrt(-d11 / det)
        self.assertAlmostEqual(fit["standard_errors"]["shape"] / observed_shape, 1.0, delta=0.15)
        self.assertAlmostEqual(fit["standard_errors"]["scale"] / observed_scale, 1.0, delta=0.15)

    def test_probability_weighted_moments_by_hand(self) -> None:
        # a0 = 2.5; a1 = (0.8375*1 + 0.5875*2 + 0.3375*3 + 0.0875*4) / 4 = 0.84375.
        shape, scale = tr._pwm([1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(shape, 2 - 2.5 / (2.5 - 2 * 0.84375))
        self.assertAlmostEqual(scale, 2 * 2.5 * 0.84375 / (2.5 - 2 * 0.84375))
        # A uniform sample is the generalised Pareto with shape -1.
        uniform = [i / 1000 for i in range(1, 1001)]
        self.assertAlmostEqual(tr._pwm(uniform)[0], -1.0, delta=0.01)

    def test_bad_input_is_refused(self) -> None:
        with self.assertRaisesRegex(ModellingInputError, "30"):
            tr.fit_gpd([0.01] * 10 + [0.02] * 10)
        with self.assertRaises(ModellingInputError):
            tr.fit_gpd([-0.01] + _gpd_sample(50, 0.1, 0.01, seed=5))
        with self.assertRaisesRegex(ModellingInputError, "same size"):
            tr.fit_gpd([0.01] * 40)
        sample = _gpd_sample(60, 0.1, 0.01, seed=6)
        fit = tr.fit_gpd(sample + [None, float("nan")])
        self.assertEqual(fit["missing_skipped"], 2)
        self.assertEqual(fit["exceedances"], 60)


class PeaksOverThresholdTests(unittest.TestCase):
    def test_tail_formulas_by_hand(self) -> None:
        var, es = tr._pot_measures(0.02, 0.2, 0.01, 0.1, 0.99)
        self.assertAlmostEqual(var, 0.02 + 0.01 / 0.2 * (10 ** 0.2 - 1))
        self.assertAlmostEqual(es, (var + 0.01 - 0.2 * 0.02) / 0.8)
        var, es = tr._pot_measures(0.02, 0.0, 0.01, 0.1, 0.99)
        self.assertAlmostEqual(var, 0.02 + 0.01 * math.log(10))
        self.assertAlmostEqual(es, var + 0.01)
        self.assertIsNone(tr._pot_measures(0.02, 1.2, 0.01, 0.1, 0.99)[1])

    def test_recovers_a_known_tail(self) -> None:
        result = tr.evt_var_es(_known_tail(20000, seed=7), conditional=False)
        true_var = 0.015 + 0.008 / 0.3 * (10 ** 0.3 - 1)
        true_es = (true_var + 0.008 - 0.3 * 0.015) / 0.7
        row = result["unconditional"]["levels"]["99"]
        self.assertAlmostEqual(row["var"] / true_var, 1.0, delta=0.05)
        self.assertAlmostEqual(row["expected_shortfall"] / true_es, 1.0, delta=0.08)
        self.assertAlmostEqual(result["unconditional"]["gpd"]["shape"], 0.3, delta=0.1)
        self.assertAlmostEqual(row["historical_var"] / true_var, 1.0, delta=0.08)
        self.assertIsNone(result["conditional"])

    def test_ordering_and_formula_identity(self) -> None:
        result = tr.evt_var_es(_student_t(2520, 4, 0.01, seed=8))
        self.assertNotIn("nan", repr(result).lower())
        for block in (result["unconditional"], result["conditional"]):
            low, high = block["levels"]["99"], block["levels"]["99.5"]
            self.assertGreater(high["var"], low["var"])
            self.assertGreater(low["expected_shortfall"], low["var"])
        block = result["unconditional"]
        shape, scale, u = block["gpd"]["shape"], block["gpd"]["scale"], block["threshold_loss"]
        row = block["levels"]["99"]
        self.assertAlmostEqual(row["expected_shortfall"], (row["var"] + scale - shape * u) / (1 - shape))
        self.assertGreater(shape, 0.0)          # Student-t with 4 degrees of freedom has a heavy tail
        self.assertEqual(result["disclaimer"], rf.DISCLAIMER)

    def test_conditional_figures_follow_tomorrows_volatility(self) -> None:
        generator = random.Random(9)
        calm = [generator.gauss(0.0003, 0.008) for _ in range(1500)]
        storm = [generator.gauss(0.0, 0.03) for _ in range(15)]
        result = tr.evt_var_es(calm + storm)
        conditional = result["conditional"]
        row = conditional["levels"]["99"]
        self.assertAlmostEqual(row["var"], conditional["next_day_volatility"] * row["standardised_var"]
                               - conditional["mean_daily_return"])
        self.assertGreater(row["var"], result["unconditional"]["levels"]["99"]["var"])

    def test_too_few_exceedances_are_refused(self) -> None:
        with self.assertRaisesRegex(ModellingInputError, "at least 30 losses"):
            tr.evt_var_es(_student_t(200, 5, 0.01, seed=10))
        with self.assertRaisesRegex(ModellingInputError, "not beyond"):
            tr.evt_var_es(_student_t(1000, 5, 0.01, seed=10), levels=(0.85,))
        with self.assertRaises(ModellingInputError):
            tr.evt_var_es([0.001] * 500)


class ChristoffersenTests(unittest.TestCase):
    def test_hand_computed_example(self) -> None:
        # Transitions: n00 = 5, n01 = 2, n10 = 2, n11 = 2; 4 breaches in 12 days.
        result = tr.christoffersen_test([0, 0, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0], level=0.9)
        self.assertEqual(result["transitions"], {"n00": 5, "n01": 2, "n10": 2, "n11": 2})
        lr_uc = -2 * ((8 * math.log(0.9) + 4 * math.log(0.1)) - (8 * math.log(8 / 12) + 4 * math.log(4 / 12)))
        lr_ind = -2 * ((7 * math.log(7 / 11) + 4 * math.log(4 / 11))
                       - (5 * math.log(5 / 7) + 2 * math.log(2 / 7) + 2 * math.log(0.5) + 2 * math.log(0.5)))
        self.assertAlmostEqual(result["unconditional_coverage"]["statistic"], lr_uc)
        self.assertAlmostEqual(result["independence"]["statistic"], lr_ind)
        self.assertAlmostEqual(result["conditional_coverage"]["statistic"], lr_uc + lr_ind)
        self.assertAlmostEqual(result["independence"]["p_value"], math.erfc(math.sqrt(lr_ind / 2)))
        self.assertAlmostEqual(result["conditional_coverage"]["p_value"], math.exp(-(lr_uc + lr_ind) / 2))
        self.assertAlmostEqual(result["breach_after_breach_rate"], 0.5)
        self.assertAlmostEqual(result["breach_after_calm_rate"], 2 / 7)

    def test_unconditional_part_is_kupiec(self) -> None:
        generator = random.Random(11)
        flags = [1 if generator.random() < 0.02 else 0 for _ in range(750)]
        result = tr.christoffersen_test(flags, level=0.99)
        self.assertAlmostEqual(result["unconditional_coverage"]["p_value"], rf._kupiec(sum(flags), 750, 0.01))

    def test_clusters_are_caught_and_scattered_breaches_pass(self) -> None:
        clustered = ([0] * 195 + [1] * 5) * 5
        result = tr.christoffersen_test(clustered, level=0.975)
        self.assertLess(result["independence"]["p_value"], 0.001)
        self.assertTrue(result["clustered"])
        generator = random.Random(12)
        scattered = tr.christoffersen_test([1 if generator.random() < 0.01 else 0 for _ in range(1000)])
        self.assertGreater(scattered["independence"]["p_value"], 0.05)
        self.assertFalse(scattered["clustered"])

    def test_no_breaches_and_bad_input(self) -> None:
        result = tr.christoffersen_test([0] * 300)
        self.assertEqual(result["independence"]["statistic"], 0.0)
        self.assertEqual(result["independence"]["p_value"], 1.0)
        self.assertIsNone(result["breach_after_breach_rate"])
        self.assertAlmostEqual(result["conditional_coverage"]["statistic"],
                               result["unconditional_coverage"]["statistic"])
        with self.assertRaises(ModellingInputError):
            tr.christoffersen_test([0, 2, 1])
        with self.assertRaises(ModellingInputError):
            tr.christoffersen_test([1])


class TrafficLightTests(unittest.TestCase):
    def test_basel_250_day_table(self) -> None:
        # Basel Committee (1996), Table 2: cumulative probability of k or fewer exceptions in 250 days at 99%.
        published = [8.11, 28.58, 54.32, 75.81, 89.22, 95.88, 98.63, 99.60, 99.89, 99.97, 99.99]
        plus = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.40, 6: 0.50, 7: 0.65, 8: 0.75, 9: 0.85, 10: 1.0, 15: 1.0}
        for k in range(16):
            result = tr.traffic_light(k, 250, 0.99)
            self.assertEqual(result["zone"], "green" if k <= 4 else "amber" if k <= 9 else "red", k)
            self.assertEqual(result["green_up_to"], 4)
            self.assertEqual(result["red_from"], 10)
            if k < len(published):
                self.assertAlmostEqual(100 * result["cumulative_probability"], published[k], delta=0.006)
            if k in plus:
                self.assertEqual(result["plus_factor"], plus[k])

    def test_other_windows_follow_the_binomial(self) -> None:
        for n, level in ((500, 0.99), (1000, 0.975)):
            p = 1 - level
            running, cumulative = 0.0, []
            for i in range(80):
                running += math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                cumulative.append(running)
            green = max(k for k, c in enumerate(cumulative) if c < 0.95)
            red = min(k for k, c in enumerate(cumulative) if c >= 0.9999)
            result = tr.traffic_light(green, n, level)
            self.assertEqual((result["zone"], result["green_up_to"], result["red_from"]), ("green", green, red))
            self.assertEqual(tr.traffic_light(green + 1, n, level)["zone"], "amber")
            self.assertEqual(tr.traffic_light(red, n, level)["zone"], "red")
            self.assertIsNone(result["plus_factor"])
        self.assertEqual(tr.traffic_light(30, 2520, 0.99)["zone"], "green")

    def test_bad_counts_are_refused(self) -> None:
        for breaches, observations in ((-1, 250), (251, 250), (0, 0)):
            with self.assertRaises(ModellingInputError):
                tr.traffic_light(breaches, observations)


NORMAL_VAR_975 = 1.959964
NORMAL_ES_975 = math.exp(-NORMAL_VAR_975 ** 2 / 2) / math.sqrt(2 * math.pi) / 0.025


class EsBacktestTests(unittest.TestCase):
    def test_z2_by_hand(self) -> None:
        result = tr.es_backtest([-0.03, 0.01, -0.01, -0.05], [0.02] * 4, [0.025, 0.025, 0.025, 0.04], 0.975,
                                simulations=200)
        self.assertEqual(result["breaches"], 2)
        self.assertAlmostEqual(result["z2"], (-0.03 / 0.025 - 0.05 / 0.04) / (4 * 0.025) + 1)
        self.assertEqual(result["zone_basis"], "simulated p-value")

    def test_right_forecasts_pass_and_low_ones_fail(self) -> None:
        generator = random.Random(13)
        sd = 0.01
        var, es = [NORMAL_VAR_975 * sd] * 1000, [NORMAL_ES_975 * sd] * 1000
        right = tr.es_backtest([generator.gauss(0, sd) for _ in range(1000)], var, es, 0.975)
        self.assertLess(abs(right["z2"]), 0.4)
        self.assertGreater(right["p_value"], 0.05)
        self.assertEqual(right["zone"], "green")
        wrong = tr.es_backtest([generator.gauss(0, 1.6 * sd) for _ in range(1000)], var, es, 0.975)
        self.assertLess(wrong["z2"], -1.0)
        self.assertLess(wrong["p_value"], 0.001)
        self.assertEqual(wrong["zone"], "red")

    def test_simulated_null_reproduces_published_thresholds(self) -> None:
        generator = random.Random(14)
        draws = sorted(tr._es_null_draw([NORMAL_VAR_975] * 250, [NORMAL_ES_975] * 250, 0.025, 0.0, generator)
                       for _ in range(20000))
        self.assertAlmostEqual(_quantile(draws, 0.05), tr.Z2_AMBER, delta=0.04)
        self.assertAlmostEqual(sum(draws) / len(draws), 0.0, delta=0.01)

    def test_published_thresholds_at_250_days(self) -> None:
        # Constant VaR 2% and ES 2.5%; k breaches of exactly 2.5% give Z2 = 1 - k / 6.25.
        for k, zone in ((10, "green"), (11, "amber"), (18, "red")):
            returns = [-0.025] * k + [0.0] * (250 - k)
            result = tr.es_backtest(returns, [0.02] * 250, [0.025] * 250, 0.975, simulations=500)
            self.assertAlmostEqual(result["z2"], 1 - k / 6.25)
            self.assertEqual((result["zone"], result["zone_basis"]), (zone, "published thresholds"))

    def test_repeatable_and_validated(self) -> None:
        generator = random.Random(15)
        returns = [generator.gauss(0, 0.012) for _ in range(300)]
        args = (returns, [0.0235] * 300, [0.028] * 300, 0.975)
        self.assertEqual(tr.es_backtest(*args, simulations=1000), tr.es_backtest(*args, simulations=1000))
        with self.assertRaises(ModellingInputError):
            tr.es_backtest(returns, [0.02] * 299, [0.03] * 300, 0.975)
        with self.assertRaisesRegex(ModellingInputError, "at least as large"):
            tr.es_backtest(returns, [0.03] * 300, [0.02] * 300, 0.975)


class ReportTests(unittest.TestCase):
    def test_walk_forward_on_steady_returns(self) -> None:
        returns = _student_t(1250, 5, 0.01, seed=16)
        daily = list(zip(_weekdays(1250), returns))
        result = tr.tail_backtest_report(daily, simulations=2000)
        self.assertNotIn("nan", repr(result).lower())
        self.assertEqual(result["observations"], 1000)
        for key, level in (("97.5", 0.975), ("99", 0.99)):
            row = result["levels"][key]
            self.assertAlmostEqual(row["breach_rate"], 1 - level, delta=(1 - level) * 0.8)
            self.assertIn(row["traffic_light"]["zone"], ("green", "amber"))
            self.assertGreater(row["christoffersen"]["independence"]["p_value"], 0.01)
            self.assertGreater(row["expected_shortfall"]["p_value"], 0.001)
        self.assertEqual(result["basel"]["observations"], 250)
        self.assertEqual(result["basel_expected_shortfall"]["zone_basis"], "published thresholds")
        self.assertEqual(len(result["series"]), 260)
        self.assertEqual(result, tr.tail_backtest_report(daily, simulations=2000))

    def test_forecasts_use_only_earlier_returns(self) -> None:
        returns = _student_t(600, 5, 0.01, seed=17)
        daily = list(zip(_weekdays(600), returns))
        # The last day (index 599) uses the refit made 16 months of 21 sessions after day 250.
        last = tr.tail_backtest_report(daily, simulations=200)["series"][-1]
        var, es = rf._tail(sorted(returns[:250 + 16 * 21]), 0.99)
        self.assertEqual((last["var_99"], last["es_99"]), (var, es))
        # Never refitted: every day keeps the forecast from the first 250 days.
        var, es = rf._tail(sorted(returns[:250]), 0.99)
        frozen = tr.tail_backtest_report(daily, simulations=200, refit_every=1000)
        self.assertTrue(all(r["var_99"] == var and r["es_99"] == es for r in frozen["series"]))

    def test_volatility_jump_turns_the_lights_red(self) -> None:
        generator = random.Random(18)
        returns = [generator.gauss(0, 0.007) for _ in range(750)] + [generator.gauss(0, 0.025) for _ in range(300)]
        result = tr.tail_backtest_report(list(zip(_weekdays(1050), returns)), simulations=2000)
        row = result["levels"]["99"]
        self.assertEqual(row["traffic_light"]["zone"], "red")
        self.assertEqual(result["basel"]["zone"], "red")
        self.assertTrue(row["christoffersen"]["clustered"])
        es_test = result["levels"]["97.5"]["expected_shortfall"]
        self.assertLess(es_test["z2"], -1.0)
        self.assertEqual(es_test["p_value"], 1 / 2001)     # no simulated history was as bad
        self.assertLess(row["kupiec_p_value"], 0.001)

    def test_short_or_gappy_history(self) -> None:
        returns = _student_t(520, 5, 0.01, seed=19)
        with self.assertRaisesRegex(ModellingInputError, "500 in all"):
            tr.tail_backtest_report(list(zip(_weekdays(400), returns[:400])))
        daily = list(zip(_weekdays(520), returns))
        daily[300] = (daily[300][0], None)
        result = tr.tail_backtest_report(daily, simulations=200)
        self.assertEqual(result["missing_skipped"], 1)
        self.assertEqual(result["observations"], 269)


if __name__ == "__main__":
    unittest.main()
