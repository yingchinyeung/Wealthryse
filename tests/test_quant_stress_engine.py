from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl import risk_forecasting as rf  # noqa: E402
from ibkr_etl.modelling import ModellingInputError, _solve_inverse  # noqa: E402
from ibkr_etl.quant import stress_engine as se  # noqa: E402

TWO = {"A": {"A": 0.04, "B": 0.012}, "B": {"A": 0.012, "B": 0.09}}
THREE = {name: {other: value * 1e-4 for other, value in zip("ABC", row)}
         for name, row in zip("ABC", ([4, 2, 1], [2, 9, 3], [1, 3, 16]))}


def _random_covariance(names: str, seed: int) -> dict[str, dict[str, float]]:
    generator = random.Random(seed)
    loadings = [[generator.gauss(0, 0.03) for _ in range(3)] for _ in names]
    return {a: {b: sum(x * y for x, y in zip(loadings[i], loadings[j])) + (0.0004 if i == j else 0.0)
                for j, b in enumerate(names)} for i, a in enumerate(names)}


def _mahalanobis(cov: dict[str, dict[str, float]], move: dict[str, float]) -> float:
    names = list(cov)
    inverse = _solve_inverse([[cov[a][b] for b in names] for a in names])
    vector = [move[n] for n in names]
    return math.sqrt(sum(vector[i] * inverse[i][j] * vector[j] for i in range(len(names)) for j in range(len(names))))


class ConditionalScenarioTests(unittest.TestCase):
    def test_two_factors_by_hand(self) -> None:
        result = se.conditional_scenario(TWO, {"A": -0.2})
        self.assertEqual((result["set"], result["inferred"]), (["A"], ["B"]))
        self.assertAlmostEqual(result["moves"]["B"], 0.012 / 0.04 * -0.2)
        self.assertAlmostEqual(result["sensitivities"]["B"]["A"], 0.3)
        b_row = next(r for r in result["factors"] if r["factor"] == "B")
        self.assertAlmostEqual(b_row["conditional_sd"], math.sqrt(0.09 - 0.012 ** 2 / 0.04))
        self.assertEqual(b_row["source"], "inferred")
        with_means = se.conditional_scenario(TWO, {"A": -0.2}, factor_means={"A": 0.01, "B": 0.02})
        self.assertAlmostEqual(with_means["moves"]["B"], 0.02 + 0.3 * (-0.2 - 0.01))
        self.assertEqual(result["disclaimer"], rf.DISCLAIMER)

    def test_three_factors_by_hand(self) -> None:
        # Σss = [[4, 2], [2, 9]]e-4, inverse = [[9, -2], [-2, 4]]e4 / 32; Σss⁻¹ s = (-62.5, 25).
        result = se.conditional_scenario(THREE, {"A": -0.02, "B": 0.01})
        self.assertAlmostEqual(result["moves"]["C"], 1e-4 * (1 * -62.5 + 3 * 25))
        self.assertAlmostEqual(result["sensitivities"]["C"]["A"], 3 / 32)
        self.assertAlmostEqual(result["sensitivities"]["C"]["B"], 10 / 32)
        c_row = next(r for r in result["factors"] if r["factor"] == "C")
        self.assertAlmostEqual(c_row["conditional_sd"], math.sqrt(1e-4 * (16 - 33 / 32)))
        one = se.conditional_scenario(THREE, {"A": -0.02})
        self.assertAlmostEqual(one["moves"]["B"], 2 / 4 * -0.02)
        self.assertAlmostEqual(one["moves"]["C"], 1 / 4 * -0.02)
        self.assertEqual(one["moves"]["A"], -0.02)

    def test_shocking_every_factor_infers_nothing(self) -> None:
        result = se.conditional_scenario(THREE, {"A": 0.01, "B": -0.01, "C": 0.02})
        self.assertEqual(result["inferred"], [])
        self.assertEqual(result["moves"], {"A": 0.01, "B": -0.01, "C": 0.02})

    def test_bad_input_is_refused(self) -> None:
        collinear = {"A": {"A": 0.04, "B": 0.04}, "B": {"A": 0.04, "B": 0.04}}
        flat = {"A": {"A": 0.0, "B": 0.0}, "B": {"A": 0.0, "B": 0.04}}
        cases = [
            (TWO, {}, "at least one"),
            (TWO, {"Z": 0.1}, "no history for Z"),
            (TWO, {"A": float("nan")}, "finite"),
            (collinear, {"A": -0.1, "B": -0.1}, "told apart"),
            (flat, {"A": -0.1}, "no variance"),
            ({"A": {"A": 0.01, "B": 0.05}, "B": {"A": 0.05, "B": 0.01}}, {"A": -0.1}, "beyond"),
            ({"A": {"A": 0.01}, "B": {"B": 0.01}}, {"A": -0.1}, "no entry"),
        ]
        for cov, shocks, message in cases:
            with self.assertRaisesRegex(ModellingInputError, message):
                se.conditional_scenario(cov, shocks)


class ScenarioImpactTests(unittest.TestCase):
    def test_impact_and_shares(self) -> None:
        scenario = se.conditional_scenario(THREE, {"A": -0.02})
        result = se.scenario_impact({"A": 1.2, "B": -0.5, "C": 0.3}, scenario)
        expected = 1.2 * -0.02 + -0.5 * -0.01 + 0.3 * -0.005
        self.assertAlmostEqual(result["portfolio_return"], expected)
        self.assertAlmostEqual(sum(r["share"] for r in result["factors"]), 1.0)
        self.assertEqual([r["factor"] for r in result["factors"]], ["A", "B", "C"])
        model = {"exposures": [{"factor": "A", "beta": 1.2}, {"factor": "B", "beta": -0.5},
                               {"factor": "C", "beta": 0.3}]}
        self.assertEqual(se.scenario_impact(model, scenario)["portfolio_return"], result["portfolio_return"])

    def test_matches_one_factor_stress_and_lists_gaps(self) -> None:
        model = {"exposures": [{"factor": "Mkt-RF", "label": "Market", "beta": 1.1, "significant": True},
                               {"factor": "HML", "label": "Value", "beta": -0.3, "significant": True}]}
        shocks = {"Mkt-RF": -0.15, "SMB": 0.02}
        naive = rf.factor_stress(model, shocks)
        result = se.scenario_impact(model, shocks)
        self.assertAlmostEqual(result["portfolio_return"], naive["impact"])
        self.assertEqual(result["factors_without_move"], ["HML"])
        self.assertEqual(result["factors_not_in_portfolio"], ["SMB"])
        self.assertIsNone(se.scenario_impact({"A": 1.0}, {"A": 0.0})["factors"][0]["share"])


class ReverseStressTests(unittest.TestCase):
    NAMES = "ABCD"

    def test_hits_the_loss_at_minimum_distance(self) -> None:
        cov = _random_covariance(self.NAMES, seed=1)
        betas = {"A": 1.1, "B": -0.4, "C": 0.6, "D": 0.2}
        result = se.reverse_stress(betas, cov, 0.2)
        moves = result["moves"]
        self.assertAlmostEqual(sum(betas[n] * moves[n] for n in self.NAMES), -0.2, places=12)
        self.assertAlmostEqual(result["portfolio_return"], -0.2, places=12)
        best = result["mahalanobis_distance"]
        self.assertAlmostEqual(_mahalanobis(cov, moves), best, places=9)
        beta = [betas[n] for n in self.NAMES]
        norm = sum(b * b for b in beta)
        generator = random.Random(2)
        for _ in range(500):
            z = [generator.gauss(0, 0.2) for _ in self.NAMES]
            shift = (sum(b * x for b, x in zip(beta, z)) + 0.2) / norm
            alternative = dict(zip(self.NAMES, (x - shift * b for x, b in zip(z, beta))))
            self.assertAlmostEqual(sum(betas[n] * alternative[n] for n in self.NAMES), -0.2, places=12)
            self.assertGreaterEqual(_mahalanobis(cov, alternative), best - 1e-12)
        self.assertAlmostEqual(sum(r["share_of_loss"] for r in result["factors"]), 1.0)
        sizes = [abs(r["move"]) for r in result["factors"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertAlmostEqual(result["chi_square_tail_probability"],
                               se._chi_square_tail(best ** 2, 4))

    def test_single_factor_by_hand(self) -> None:
        result = se.reverse_stress({"Mkt": 1.25}, {"Mkt": {"Mkt": 0.04}}, 0.2)
        self.assertAlmostEqual(result["moves"]["Mkt"], -0.16)
        self.assertAlmostEqual(result["mahalanobis_distance"], 0.2 / (1.25 * 0.2))
        self.assertAlmostEqual(result["chi_square_tail_probability"], math.erfc(0.8 / math.sqrt(2)))
        self.assertAlmostEqual(result["probability_of_loss_at_least"], 0.5 * math.erfc(0.8 / math.sqrt(2)))
        self.assertAlmostEqual(result["factors"][0]["move_in_sd"], -0.8)

    def test_factor_means_shift_the_scenario(self) -> None:
        cov = _random_covariance(self.NAMES, seed=3)
        betas = {"A": 0.9, "B": 0.3, "C": -0.2, "D": 0.5}
        means = {"A": 0.006, "B": 0.002, "C": -0.001, "D": 0.003}
        result = se.reverse_stress(betas, cov, 0.15, factor_means=means)
        self.assertAlmostEqual(sum(betas[n] * result["moves"][n] for n in self.NAMES), -0.15, places=12)
        gap = {n: result["moves"][n] - means[n] for n in self.NAMES}
        self.assertAlmostEqual(_mahalanobis(cov, gap), result["mahalanobis_distance"], places=9)
        self.assertAlmostEqual(result["expected_return"], sum(betas[n] * means[n] for n in self.NAMES))

    def test_bad_input_is_refused(self) -> None:
        with self.assertRaisesRegex(ModellingInputError, "positive"):
            se.reverse_stress({"A": 1.0}, TWO, -0.1)
        with self.assertRaisesRegex(ModellingInputError, "no factor risk"):
            se.reverse_stress({"A": 0.0, "B": 0.0}, TWO, 0.1)
        with self.assertRaisesRegex(ModellingInputError, "MOM"):
            se.reverse_stress({"A": 1.0, "MOM": 0.2}, TWO, 0.1)


class RegimeRiskTests(unittest.TestCase):
    def _returns(self, count: int = 800, seed: int = 4) -> list[float]:
        generator = random.Random(seed)
        return [generator.gauss(0.0004, 0.01) * (2.5 if generator.random() < 0.1 else 1.0) for _ in range(count)]

    def test_one_regime_is_plain_historical_var(self) -> None:
        returns = self._returns()
        result = se.regime_risk(returns, [[0.7]] * len(returns))
        regime = result["regimes"][0]
        self.assertEqual(regime["regime"], "All days")
        self.assertAlmostEqual(regime["share_of_days"], 1.0)
        self.assertAlmostEqual(regime["effective_days"], len(returns))
        for key, level in (("95", 0.95), ("99", 0.99)):
            var, es = rf._tail(sorted(returns), level)
            self.assertAlmostEqual(regime["levels"][key]["var"], var, places=12)
            self.assertAlmostEqual(regime["levels"][key]["expected_shortfall"], es, places=12)
            self.assertAlmostEqual(result["all_days"]["levels"][key]["var"], var, places=12)

    def test_hard_and_even_regimes(self) -> None:
        returns = self._returns()
        calm = [abs(r) < 0.012 for r in returns]
        hard = se.regime_risk(returns, [[1.0, 0.0] if c else [0.0, 1.0] for c in calm])
        for j, members in enumerate(([r for r, c in zip(returns, calm) if c], [r for r, c in zip(returns, calm) if not c])):
            var, es = rf._tail(sorted(members), 0.99)
            self.assertAlmostEqual(hard["regimes"][j]["levels"]["99"]["var"], var, places=12)
            self.assertAlmostEqual(hard["regimes"][j]["levels"]["99"]["expected_shortfall"], es, places=12)
            self.assertEqual(hard["regimes"][j]["days_most_likely"], len(members))
        self.assertEqual([r["regime"] for r in hard["regimes"]], ["Calm", "Stressed"])
        even = se.regime_risk(returns, [[0.5, 0.5]] * len(returns))
        var, _ = rf._tail(sorted(returns), 0.95)
        for regime in even["regimes"]:
            self.assertAlmostEqual(regime["levels"]["95"]["var"], var, places=12)

    def test_with_the_hidden_markov_model(self) -> None:
        generator, state, returns = random.Random(5), 0, []
        for _ in range(1000):
            if generator.random() < (0.01 if state == 0 else 0.04):
                state = 1 - state
            returns.append(generator.gauss(0.0006, 0.006) if state == 0 else generator.gauss(-0.001, 0.02))
        model = rf.fit_hmm(returns, 2)
        result = se.regime_risk(returns, model["filtered"])
        calm, stressed = result["regimes"]
        self.assertAlmostEqual(calm["share_of_days"] + stressed["share_of_days"], 1.0)
        self.assertAlmostEqual(calm["share_of_days"], model["shares"][0])
        self.assertGreater(stressed["levels"]["99"]["var"], 2 * calm["levels"]["99"]["var"])
        self.assertGreater(stressed["levels"]["95"]["expected_shortfall"], stressed["levels"]["95"]["var"])
        all_days = result["all_days"]["levels"]["99"]["var"]
        self.assertLess(calm["levels"]["99"]["var"], all_days)
        self.assertGreater(stressed["levels"]["99"]["var"], all_days)

    def test_bad_input_is_refused_and_gaps_skipped(self) -> None:
        returns = self._returns(300)
        probabilities = [[0.6, 0.4]] * 300
        with self.assertRaisesRegex(ModellingInputError, "one row"):
            se.regime_risk(returns, probabilities[:-1])
        with self.assertRaisesRegex(ModellingInputError, "at least 126"):
            se.regime_risk(returns[:100], probabilities[:100])
        with self.assertRaisesRegex(ModellingInputError, "zero or more"):
            se.regime_risk(returns, [[1.2, -0.2]] + probabilities[1:])
        with self.assertRaisesRegex(ModellingInputError, "same regimes"):
            se.regime_risk(returns, [[1.0]] + probabilities[1:])
        with self.assertRaisesRegex(ModellingInputError, "one label"):
            se.regime_risk(returns, probabilities, labels=["Only"])
        gappy = [None] + returns[1:]
        result = se.regime_risk(gappy, probabilities)
        self.assertEqual((result["observations"], result["missing_skipped"]), (299, 1))


if __name__ == "__main__":
    unittest.main()
