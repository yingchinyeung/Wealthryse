from __future__ import annotations

import random
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl import risk_forecasting as rf  # noqa: E402
from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import goals  # noqa: E402

MONTHLY = {"amount": 100.0, "month": 1, "repeat": "monthly"}


def _pool(count: int = 120, seed: int = 1, mean: float = 0.006, sd: float = 0.04) -> list[float]:
    generator = random.Random(seed)
    return [generator.gauss(mean, sd) for _ in range(count)]


def _two_regime_daily(count: int = 1300, seed: int = 1) -> list[float]:
    """Calm stretches at 0.6% a day and stressed ones at 2%, switching rarely (as in test_risk_forecasting)."""
    generator, state, returns = random.Random(seed), 0, []
    for _ in range(count):
        if generator.random() < (0.01 if state == 0 else 0.04):
            state = 1 - state
        returns.append(generator.gauss(0.0006, 0.006) if state == 0 else generator.gauss(-0.001, 0.02))
    return returns


class ScheduleTests(unittest.TestCase):
    def test_one_off_monthly_and_yearly_flows(self) -> None:
        flows = goals._schedule([{"amount": 50, "month": 0}, {"amount": 10, "month": 2, "repeat": "monthly", "until": 4},
                                 {"amount": 7, "month": 12, "repeat": "yearly"}], 36, "contribution")
        self.assertEqual(flows[0], 50)
        self.assertEqual(flows[1:6], [0, 10, 10, 10, 0])
        self.assertEqual([m for m, v in enumerate(flows) if v == 7], [12, 24, 36])

    def test_bad_flows_are_refused(self) -> None:
        for item in ({"amount": -5, "month": 1}, {"amount": 5, "month": 40}, {"amount": 5, "month": 1.5},
                     {"amount": 5, "month": 1, "repeat": "weekly"}, {"amount": 5, "month": 6, "repeat": "monthly", "until": 3},
                     {"amount": None, "month": 1}):
            with self.assertRaises(ModellingInputError):
                goals._schedule([item], 36, "contribution")


class ProjectionTests(unittest.TestCase):
    def test_constant_returns_match_the_annuity_formula(self) -> None:
        growth, months = 1.01, 24
        wealth = 1000 * growth ** months + 100 * sum(growth ** j for j in range(months))
        hit = goals.goal_projection(1000, monthly_return_pool=[0.01] * 12, contributions=[MONTHLY],
                                    target=wealth - 1, horizon_months=months, paths=200)
        self.assertEqual(hit["probability_target"], 1.0)
        for value in hit["terminal"].values():
            self.assertAlmostEqual(value, wealth, places=6)
        self.assertEqual(hit["series"][-1]["month"], months)
        self.assertAlmostEqual(hit["total_contributions"], 100 * months)
        miss = goals.goal_projection(1000, monthly_return_pool=[0.01] * 12, contributions=[MONTHLY],
                                     target=wealth + 500, horizon_months=months, paths=200)
        self.assertEqual(miss["probability_target"], 0.0)
        self.assertAlmostEqual(miss["median_shortfall"], 500, places=6)

    def test_bootstrap_recovers_the_pool_mean(self) -> None:
        pool = _pool()
        result = goals.goal_projection(10_000, monthly_return_pool=pool, target=20_000, horizon_months=12, paths=4000)
        expected = 10_000 * (1 + statistics.fmean(pool)) ** 12
        self.assertAlmostEqual(result["terminal"]["mean"], expected, delta=0.015 * expected)
        self.assertLessEqual(result["terminal"]["p5"], result["terminal"]["p50"])
        self.assertLessEqual(result["terminal"]["p50"], result["terminal"]["p95"])

    def test_withdrawals_run_out_of_money(self) -> None:
        result = goals.goal_projection(10_000, monthly_return_pool=[0.0] * 12,
                                       withdrawals=[{"amount": 1000, "month": 1, "repeat": "monthly"}],
                                       target=1, horizon_months=24, paths=100)
        self.assertEqual(result["probability_depleted"], 1.0)
        self.assertEqual(result["median_month_depleted"], 11)     # month 10 empties it exactly; month 11 cannot be paid
        self.assertEqual(result["probability_target"], 0.0)
        self.assertEqual(result["median_shortfall"], 1)
        saver = goals.goal_projection(0, monthly_return_pool=[0.0] * 12, contributions=[MONTHLY],
                                      target=1200, horizon_months=12, paths=100)
        self.assertEqual(saver["probability_depleted"], 0.0)
        self.assertEqual(saver["probability_target"], 1.0)

    def test_reproducible_and_markets_shared_across_plans(self) -> None:
        pool = _pool(seed=4)
        first = goals.goal_projection(5000, monthly_return_pool=pool, target=9000, horizon_months=60)
        self.assertEqual(first, goals.goal_projection(5000, monthly_return_pool=pool, target=9000, horizon_months=60))
        richer = goals.goal_projection(5000, monthly_return_pool=pool, contributions=[MONTHLY], target=9000, horizon_months=60)
        self.assertGreater(richer["probability_target"], first["probability_target"])
        self.assertTrue(all(b["p5"] > a["p5"] for a, b in zip(first["series"][1:], richer["series"][1:])))

    def test_bad_inputs(self) -> None:
        pool = _pool()
        cases = (
            {"monthly_return_pool": pool, "regime_model": {"transition": [[1.0]], "pools": [pool]}},
            {},
            {"monthly_return_pool": pool[:5]},
            {"monthly_return_pool": pool, "horizon_months": 0},
            {"monthly_return_pool": pool, "target": 0},
            {"monthly_return_pool": pool, "paths": 10},
        )
        for case in cases:
            kwargs = {"target": 1000, "horizon_months": 12, **case}
            with self.assertRaises(ModellingInputError):
                goals.goal_projection(500, **kwargs)
        with self.assertRaises(ModellingInputError):
            goals.goal_projection(-1, monthly_return_pool=pool, target=1000, horizon_months=12)
        noisy = pool + [None, float("nan")]
        self.assertEqual(goals.goal_projection(500, monthly_return_pool=noisy, target=1000, horizon_months=12)
                         ["assumptions"]["skipped_returns"], 2)


class RegimeTests(unittest.TestCase):
    CALM, STRESSED = [0.012 + 0.001 * (i % 3) for i in range(12)], [-0.03 + 0.002 * (i % 3) for i in range(12)]

    def test_regimes_that_never_switch_are_deterministic(self) -> None:
        model = {"transition": [[1.0, 0.0], [0.0, 1.0]], "pools": [[0.01] * 6, [-0.02] * 6], "start": [1.0, 0.0]}
        result = goals.goal_projection(1000, regime_model=model, target=1, horizon_months=12, paths=100)
        self.assertAlmostEqual(result["terminal"]["p5"], 1000 * 1.01 ** 12, places=8)
        self.assertAlmostEqual(result["terminal"]["p95"], 1000 * 1.01 ** 12, places=8)

    def test_starting_stressed_lowers_the_odds(self) -> None:
        transition = [[0.95, 0.05], [0.2, 0.8]]
        odds = {}
        for name, start in (("calm", [1.0, 0.0]), ("stressed", [0.0, 1.0]), ("long_run", None)):
            model = {"transition": transition, "pools": [self.CALM, self.STRESSED], "start": start}
            odds[name] = goals.goal_projection(1000, regime_model=model, target=1100, horizon_months=12,
                                               paths=3000)["probability_target"]
        self.assertGreater(odds["calm"], odds["long_run"])
        self.assertGreater(odds["long_run"], odds["stressed"])
        stationary = goals._stationary(transition)
        self.assertAlmostEqual(stationary[0], 0.8, places=10)    # 0.2 / (0.05 + 0.2)

    def test_monthly_regime_model_from_a_fitted_hmm(self) -> None:
        daily = _two_regime_daily()
        fit = rf.fit_hmm(daily, 2)
        model = goals.monthly_regime_model(daily, fit)
        row = rf._matrix_power_row([1.0, 0.0], fit["transition"], 21)
        for a, b in zip(model["transition"][0], row):
            self.assertAlmostEqual(a, b, places=12)
        self.assertEqual(sum(len(p) for p in model["pools"]), len(daily) - 20)
        self.assertLess(statistics.pstdev(model["pools"][0]), statistics.pstdev(model["pools"][1]))
        self.assertEqual(model["start"], fit["filtered"][-1])
        self.assertEqual(model["labels"], ["Calm", "Stressed"])
        result = goals.goal_projection(1000, regime_model=model, target=1100, horizon_months=24, paths=500)
        self.assertEqual(result["assumptions"]["return_source"], "regimes")
        with self.assertRaises(ModellingInputError):
            goals.monthly_regime_model(daily[:-1], fit)

    def test_bad_regime_models(self) -> None:
        for model in ({"transition": [[0.5, 0.4], [0.5, 0.5]], "pools": [self.CALM, self.STRESSED]},
                      {"transition": [[1.0]], "pools": [self.CALM, self.STRESSED]},
                      {"transition": [[0.9, 0.1], [0.1, 0.9]], "pools": [self.CALM, [0.01, 0.02]]},
                      {"transition": [[0.9, 0.1], [0.1, 0.9]], "pools": [self.CALM, self.STRESSED], "start": [1.0]}):
            with self.assertRaises(ModellingInputError):
                goals.goal_projection(1000, regime_model=model, target=1100, horizon_months=12)


class RequiredContributionTests(unittest.TestCase):
    def test_constant_returns_match_the_closed_form(self) -> None:
        growth, months, target = 1.005, 60, 20_000
        result = goals.required_contribution(5000, monthly_return_pool=[0.005] * 12, target=target,
                                             horizon_months=months, paths=200)
        expected = (target - 5000 * growth ** months) / sum(growth ** j for j in range(months))
        self.assertAlmostEqual(result["monthly_contribution"], expected, delta=1e-4)   # nudged 1e-9 × target
        self.assertEqual(result["with_contribution"]["probability_target"], 1.0)
        self.assertFalse(result["already_on_track"])

    def test_answer_round_trips_through_the_projection(self) -> None:
        pool = _pool(seed=8)
        plan = {"monthly_return_pool": pool, "target": 60_000, "horizon_months": 120,
                "withdrawals": [{"amount": 3000, "month": 36}]}
        result = goals.required_contribution(10_000, probability=0.8, **plan)
        amount = result["monthly_contribution"]
        self.assertGreater(amount, 0)
        self.assertEqual(result, goals.required_contribution(10_000, probability=0.8, **plan))

        def odds(extra):
            return goals.goal_projection(10_000, contributions=[{"amount": extra, "month": 1, "repeat": "monthly"}],
                                         **plan)["probability_target"]

        self.assertGreaterEqual(odds(amount), 0.8)
        self.assertLess(odds(amount * 0.99), 0.8)
        self.assertEqual(result["with_contribution"]["probability_target"], odds(amount))
        higher = goals.required_contribution(10_000, probability=0.95, **plan)["monthly_contribution"]
        self.assertGreater(higher, amount)

    def test_survival_binds_when_withdrawals_come_first(self) -> None:
        # 1,000 is withdrawn at month 1 from 500 with no growth: the contribution must cover the gap
        # even though the target alone would need less.
        result = goals.required_contribution(500, monthly_return_pool=[0.0] * 12,
                                             withdrawals=[{"amount": 1000, "month": 1}],
                                             target=100, horizon_months=12, paths=100)
        self.assertAlmostEqual(result["monthly_contribution"], 500, delta=1e-5)
        self.assertEqual(result["with_contribution"]["probability_depleted"], 0.0)

    def test_on_track_and_impossible_plans(self) -> None:
        result = goals.required_contribution(10_000, monthly_return_pool=[0.01] * 12, target=10_500, horizon_months=12)
        self.assertTrue(result["already_on_track"])
        self.assertEqual(result["monthly_contribution"], 0.0)
        self.assertEqual(result["probability_without"], 1.0)
        with self.assertRaises(ModellingInputError):
            goals.required_contribution(100, monthly_return_pool=[0.01] * 12, withdrawals=[{"amount": 200, "month": 0}],
                                        target=1000, horizon_months=12)
        with self.assertRaises(ModellingInputError):
            goals.required_contribution(100, monthly_return_pool=[0.01] * 12, target=1000, horizon_months=12,
                                        probability=1.0)


if __name__ == "__main__":
    unittest.main()
