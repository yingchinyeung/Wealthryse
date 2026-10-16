from __future__ import annotations

import math
import random
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl import risk_forecasting as rf  # noqa: E402
from ibkr_etl.modelling import ModellingInputError  # noqa: E402


def _weekdays(count: int) -> list[str]:
    days, day = [], date(2021, 1, 4)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _two_regimes(count: int = 1300, seed: int = 1) -> list[tuple[str, float]]:
    """Calm stretches at 0.6% a day and stressed ones at 2%, switching rarely."""
    generator, state, returns = random.Random(seed), 0, []
    for _ in range(count):
        if generator.random() < (0.01 if state == 0 else 0.04):
            state = 1 - state
        returns.append(generator.gauss(0.0006, 0.006) if state == 0 else generator.gauss(-0.001, 0.02))
    return list(zip(_weekdays(count), returns))


class VolatilityTests(unittest.TestCase):
    def test_garch_recovers_known_parameters(self) -> None:
        generator = random.Random(3)
        omega, alpha, beta, variance, returns = 2e-6, 0.08, 0.9, 1e-4, []
        for _ in range(3000):
            value = generator.gauss(0, math.sqrt(variance))
            returns.append(value)
            variance = omega + alpha * value * value + beta * variance
        fit = rf.fit_volatility(returns)
        self.assertEqual(fit["method"], "garch")
        self.assertAlmostEqual(fit["alpha"], alpha, delta=0.03)
        self.assertAlmostEqual(fit["beta"], beta, delta=0.04)
        path = rf.variance_path(fit, 500)
        self.assertAlmostEqual(path[-1], fit["long_run_variance"], delta=fit["long_run_variance"] * 0.05)

    def test_short_history_uses_ewma(self) -> None:
        generator = random.Random(2)
        fit = rf.fit_volatility([generator.gauss(0, 0.01) for _ in range(150)])
        self.assertEqual(fit["method"], "ewma")
        self.assertEqual(rf.variance_path(fit, 3), [fit["next_variance"]] * 3)

    def test_forecast_backtest_and_tails(self) -> None:
        result = rf.volatility_forecast(_two_regimes())
        self.assertNotIn("nan", repr(result).lower())
        day, month = result["one_day"], result["one_month"]
        self.assertGreater(day["99"]["var"], day["95"]["var"])
        self.assertGreaterEqual(day["95"]["expected_tail_loss"], day["95"]["var"])
        self.assertGreater(month["95"]["var"], day["95"]["var"])
        backtest = result["backtest"]
        self.assertTrue(backtest["available"])
        self.assertAlmostEqual(backtest["breach_rate"], 0.05, delta=0.02)
        self.assertGreater(backtest["kupiec_p_value"], 0.01)
        with self.assertRaises(ModellingInputError):
            rf.volatility_forecast(_two_regimes(100))

    def test_kupiec_rejects_far_too_many_breaches(self) -> None:
        self.assertGreater(rf._kupiec(50, 1000, 0.05), 0.5)
        self.assertLess(rf._kupiec(120, 1000, 0.05), 0.001)


class DrawdownTests(unittest.TestCase):
    def test_odds_rise_with_volatility_and_fall_with_threshold(self) -> None:
        returns = [r for _, r in _two_regimes()]
        normal = rf.drawdown_odds(returns)
        doubled = rf.drawdown_odds(returns, volatility_scale=2.0)
        odds = [x["probability"] for x in normal["probabilities"]]
        self.assertEqual(odds, sorted(odds, reverse=True))
        self.assertGreater(doubled["probabilities"][1]["probability"], normal["probabilities"][1]["probability"])
        self.assertLessEqual(normal["bad_case_worst_fall"], normal["median_worst_fall"])

    def test_flat_returns_never_fall(self) -> None:
        result = rf.drawdown_odds([0.001] * 300)
        self.assertEqual([x["probability"] for x in result["probabilities"]], [0.0, 0.0, 0.0])


class FactorStressTests(unittest.TestCase):
    MODEL = {"exposures": [
        {"factor": "Mkt-RF", "label": "Market", "beta": 1.2, "significant": True},
        {"factor": "HML", "label": "Value", "beta": -0.4, "significant": True},
    ], "r_squared": 0.8, "residual_volatility_annual": 0.06}

    def test_impact_is_beta_times_shock(self) -> None:
        result = rf.factor_stress(self.MODEL, {"Mkt-RF": -0.2, "HML": 0.05, "MOM": -0.1})
        self.assertAlmostEqual(result["impact"], 1.2 * -0.2 + -0.4 * 0.05)
        self.assertEqual(result["factors_not_in_model"], ["MOM"])
        self.assertAlmostEqual(result["unexplained_monthly_range"], 1.645 * 0.06 / math.sqrt(12))

    def test_bad_shocks_are_refused(self) -> None:
        for shocks in ({}, {"Mkt-RF": -0.9}, {"MOM": 0.1}):
            with self.assertRaises(ModellingInputError):
                rf.factor_stress(self.MODEL, shocks)

    def test_presets_use_known_factors(self) -> None:
        from ibkr_etl.modelling import FACTOR_NAMES

        for _, shocks in rf.STRESS_PRESETS.values():
            self.assertTrue(set(shocks) <= set(FACTOR_NAMES))


class RegimeTests(unittest.TestCase):
    def test_finds_calm_and_stressed_states(self) -> None:
        result = rf.regime_outlook(_two_regimes())
        calm, stressed = result["regimes"][0], result["regimes"][-1]
        self.assertAlmostEqual(calm["annual_volatility"], 0.006 * math.sqrt(252), delta=0.02)
        self.assertAlmostEqual(stressed["annual_volatility"], 0.02 * math.sqrt(252), delta=0.06)
        self.assertGreater(calm["share_of_time"], stressed["share_of_time"])
        self.assertAlmostEqual(sum(r["probability_now"] for r in result["regimes"]), 1.0)
        self.assertAlmostEqual(sum(r["probability_in_a_month"] for r in result["regimes"]), 1.0)
        for row in result["outlook"]:
            self.assertLessEqual(row["p5"], row["p25"])
            self.assertLessEqual(row["p25"], row["p50"])
            self.assertLessEqual(row["p75"], row["p95"])
        self.assertLess(result["outlook"][0]["p95"] - result["outlook"][0]["p5"],
                        result["outlook"][-1]["p95"] - result["outlook"][-1]["p5"])
        score = result["score"]
        self.assertTrue(score["available"])
        self.assertGreater(score["coverage_90"], 0.7)
        self.assertIn("past months", score["verdict"])

    def test_outlook_is_reproducible_and_needs_a_year(self) -> None:
        daily = _two_regimes(400, seed=5)
        first = rf.regime_outlook(daily, score=False)
        self.assertEqual(first["outlook"], rf.regime_outlook(daily, score=False)["outlook"])
        self.assertFalse(rf.regime_outlook(daily)["score"]["available"])
        with self.assertRaises(ModellingInputError):
            rf.regime_outlook(daily[:200])

    def test_hmm_prefers_one_regime_shape_when_returns_are_alike(self) -> None:
        generator = random.Random(8)
        daily = list(zip(_weekdays(600), [generator.gauss(0.0003, 0.01) for _ in range(600)]))
        result = rf.regime_outlook(daily, score=False)
        vols = [r["annual_volatility"] for r in result["regimes"]]
        self.assertLess(vols[-1] / vols[0], 2.0)  # no invented stressed regime


if __name__ == "__main__":
    unittest.main()
