from __future__ import annotations

import math
import random
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import holdings_risk as hr  # noqa: E402


def _draw(covariance: list[list[float]], days: int, seed: int) -> list[list[float]]:
    """Columns of multivariate normal daily returns with the given covariance."""
    lower, generator = hr._cholesky(covariance), random.Random(seed)
    rows = []
    for _ in range(days):
        z = [generator.gauss(0.0, 1.0) for _ in covariance]
        rows.append([sum(lower[i][k] * z[k] for k in range(i + 1)) for i in range(len(covariance))])
    return [list(column) for column in zip(*rows)]


def _constant_correlation(vols: list[float], rho: float) -> list[list[float]]:
    return [[a * b * (1.0 if i == j else rho) for j, b in enumerate(vols)] for i, a in enumerate(vols)]


def _frobenius(left, right) -> float:
    return math.sqrt(sum((a - b) ** 2 for ra, rb in zip(left, right) for a, b in zip(ra, rb)))


def _is_psd(matrix, tolerance: float = 1e-10) -> bool:
    """Cholesky of matrix + tolerance·trace·I succeeds only for a positive semi-definite matrix."""
    n = len(matrix)
    shift = tolerance * max(1.0, sum(matrix[i][i] for i in range(n)))
    lower = [[0.0] * n for _ in range(n)]
    for j in range(n):
        pivot = matrix[j][j] + shift - sum(v * v for v in lower[j][:j])
        if pivot <= 0:
            return False
        lower[j][j] = math.sqrt(pivot)
        for i in range(j + 1, n):
            lower[i][j] = (matrix[i][j] - sum(a * b for a, b in zip(lower[i][:j], lower[j][:j]))) / lower[j][j]
    return True


def _demeaned_sample(columns):
    x = [[v - statistics.fmean(c) for v in c] for c in columns]
    days = len(x[0])
    return x, [[sum(a * b for a, b in zip(xi, xj)) / days for xj in x] for xi in x]


def _one_factor(days: int, seed: int, count: int = 6) -> list[list[float]]:
    generator = random.Random(seed)
    market = [generator.gauss(0, 0.01) for _ in range(days)]
    other = [generator.gauss(0, 0.01) for _ in range(days)]
    return [[(0.3 + 0.3 * i) * market[t] + (0.8 if i % 2 else -0.4) * other[t] + generator.gauss(0, 0.006 * (1 + i))
             for t in range(days)] for i in range(count)]


class LedoitWolfTests(unittest.TestCase):
    def test_identity_intensity_matches_the_definition(self) -> None:
        columns = _one_factor(40, seed=5, count=5)
        x, sample = _demeaned_sample(columns)
        n, days = len(x), len(x[0])
        mu = sum(sample[i][i] for i in range(n)) / n
        d2 = sum((sample[i][j] - (mu if i == j else 0.0)) ** 2 for i in range(n) for j in range(n)) / n
        b2 = sum(sum((x[i][t] * x[j][t] - sample[i][j]) ** 2 for i in range(n) for j in range(n)) / n
                 for t in range(days)) / days ** 2
        result = hr.ledoit_wolf_covariance(columns)
        self.assertAlmostEqual(result["shrinkage_intensity"], min(b2, d2) / d2, places=12)
        delta = result["shrinkage_intensity"]
        expected = (delta * mu + (1 - delta) * sample[0][0]) * 252
        self.assertAlmostEqual(result["covariance"][0][0], expected, places=12)
        self.assertAlmostEqual(result["covariance"][0][1], (1 - delta) * sample[0][1] * 252, places=12)

    def test_constant_correlation_intensity_matches_the_definition(self) -> None:
        columns = _one_factor(120, seed=120)
        x, s = _demeaned_sample(columns)
        n, days = len(x), len(x[0])
        sd = [math.sqrt(s[i][i]) for i in range(n)]
        r_bar = sum(s[i][j] / (sd[i] * sd[j]) for i in range(n) for j in range(n) if i != j) / (n * (n - 1))
        prior = [[s[i][i] if i == j else r_bar * sd[i] * sd[j] for j in range(n)] for i in range(n)]
        pi = [[sum((x[i][t] * x[j][t] - s[i][j]) ** 2 for t in range(days)) / days for j in range(n)] for i in range(n)]

        def theta(i, j):  # asymptotic covariance of s_ii and s_ij
            return sum((x[i][t] ** 2 - s[i][i]) * (x[i][t] * x[j][t] - s[i][j]) for t in range(days)) / days

        rho = sum(pi[i][i] for i in range(n)) + sum(
            r_bar / 2 * (sd[j] / sd[i] * theta(i, j) + sd[i] / sd[j] * theta(j, i))
            for i in range(n) for j in range(n) if i != j)
        gamma = sum((s[i][j] - prior[i][j]) ** 2 for i in range(n) for j in range(n))
        expected = max(0.0, min(1.0, (sum(map(sum, pi)) - rho) / gamma / days))
        result = hr.ledoit_wolf_covariance(columns, target="constant_correlation")
        self.assertGreater(expected, 0.1)
        self.assertLess(expected, 0.9)
        self.assertAlmostEqual(result["shrinkage_intensity"], expected, places=12)
        self.assertAlmostEqual(result["average_correlation"], r_bar, places=12)

    def test_shrinkage_beats_the_sample_matrix_on_short_histories(self) -> None:
        truth = _constant_correlation([0.01 + 0.0015 * i for i in range(20)], 0.1)
        annual = [[v * 252 for v in row] for row in truth]
        errors = {"sample": [], "identity": [], "constant_correlation": []}
        for seed in range(5):
            columns = _draw(truth, 30, seed)
            x = [[v - statistics.fmean(c) for v in c] for c in columns]
            unbiased = [[sum(a * b for a, b in zip(xi, xj)) / 29 * 252 for xj in x] for xi in x]
            errors["sample"].append(_frobenius(unbiased, annual))
            for target in hr.TARGETS:
                result = hr.ledoit_wolf_covariance(columns, target=target)
                self.assertGreaterEqual(result["shrinkage_intensity"], 0.0)
                self.assertLessEqual(result["shrinkage_intensity"], 1.0)
                self.assertTrue(_is_psd(result["covariance"]))
                errors[target].append(_frobenius(result["covariance"], annual))
        for target in hr.TARGETS:
            self.assertLess(statistics.fmean(errors[target]), 0.85 * statistics.fmean(errors["sample"]), target)

    def test_intensity_falls_as_the_history_grows(self) -> None:
        truth = [[1e-4, 3e-5, 0.0], [3e-5, 4e-4, -2e-5], [0.0, -2e-5, 9e-4]]
        result = hr.ledoit_wolf_covariance(_draw(truth, 3000, seed=8))
        self.assertLess(result["shrinkage_intensity"], 0.05)
        for i in range(3):
            self.assertAlmostEqual(result["covariance"][i][i], truth[i][i] * 252, delta=truth[i][i] * 252 * 0.1)

    def test_single_holding_and_bad_input(self) -> None:
        generator = random.Random(4)
        column = [generator.gauss(0, 0.01) for _ in range(50)]
        for target in hr.TARGETS:
            result = hr.ledoit_wolf_covariance({"AAPL": column}, target=target)
            self.assertEqual(result["shrinkage_intensity"], 0.0)
            self.assertEqual(result["names"], ["AAPL"])
            self.assertAlmostEqual(result["covariance"][0][0], statistics.pvariance(column) * 252, places=12)
        with self.assertRaises(ModellingInputError):
            hr.ledoit_wolf_covariance([column[:10]])
        with self.assertRaises(ModellingInputError):
            hr.ledoit_wolf_covariance([column, column[:-1]])
        with self.assertRaises(ModellingInputError):
            hr.ledoit_wolf_covariance([column[:-1] + [None]])
        with self.assertRaises(ModellingInputError):
            hr.ledoit_wolf_covariance([column[:-1] + [math.nan]])
        with self.assertRaises(ModellingInputError):
            hr.ledoit_wolf_covariance([column], target="diagonal")

    def test_a_holding_that_does_not_move_is_kept_out_of_the_average_correlation(self) -> None:
        columns = _one_factor(80, seed=3, count=4) + [[0.0] * 80]
        result = hr.ledoit_wolf_covariance(columns, target="constant_correlation")
        without = hr.ledoit_wolf_covariance(columns[:4], target="constant_correlation")
        self.assertAlmostEqual(result["average_correlation"], without["average_correlation"], places=12)
        self.assertEqual(result["covariance"][4], [0.0] * 5)


class ExAnteRiskTests(unittest.TestCase):
    def test_two_holding_hand_example(self) -> None:
        result = hr.ex_ante_risk([0.6, 0.4], [[0.04, 0.006], [0.006, 0.09]], names=["A", "B"])
        sigma = math.sqrt(0.03168)
        self.assertAlmostEqual(result["variance"], 0.03168, places=14)
        self.assertAlmostEqual(result["volatility"], sigma, places=14)
        first, second = result["holdings"]
        self.assertAlmostEqual(first["marginal_contribution"], 0.0264 / sigma, places=14)
        self.assertAlmostEqual(second["marginal_contribution"], 0.0396 / sigma, places=14)
        self.assertAlmostEqual(first["share"], 0.5, places=12)
        self.assertAlmostEqual(second["share"], 0.5, places=12)
        self.assertIsNone(result["tracking_error"])

    def test_contributions_sum_to_volatility_and_tracking_error(self) -> None:
        names = [f"H{i}" for i in range(8)]
        model = hr.ledoit_wolf_covariance(dict(zip(names, _draw(_constant_correlation(
            [0.008 + 0.002 * i for i in range(8)], 0.35), 300, seed=2))))
        weights = {name: w for name, w in zip(names, (0.25, 0.2, 0.15, 0.1, 0.1, 0.1, 0.05, 0.05))}
        benchmark = {name: 1 / 8 for name in names}
        result = hr.ex_ante_risk(weights, model, benchmark_weights=benchmark)
        rows = result["holdings"]
        self.assertAlmostEqual(sum(r["contribution"] for r in rows), result["volatility"], places=14)
        self.assertAlmostEqual(sum(r["share"] for r in rows), 1.0, places=12)
        self.assertAlmostEqual(sum(r["active_contribution"] for r in rows), result["tracking_error"], places=14)
        self.assertAlmostEqual(sum(r["active_share"] for r in rows), 1.0, places=12)
        self.assertGreater(result["tracking_error"], 0)
        for row in rows:
            self.assertAlmostEqual(row["contribution"], row["weight"] * row["marginal_contribution"], places=15)
        # Marginal contribution is the derivative of volatility with respect to the weight.
        bumped = dict(weights, H0=weights["H0"] + 1e-6)
        slope = (hr.ex_ante_risk(bumped, model)["volatility"] - result["volatility"]) / 1e-6
        self.assertAlmostEqual(slope, rows[0]["marginal_contribution"], places=5)
        same = hr.ex_ante_risk(weights, model, benchmark_weights=weights)
        self.assertEqual(same["tracking_error"], 0.0)
        self.assertIsNone(same["holdings"][0]["active_share"])

    def test_zero_variance_and_bad_input(self) -> None:
        result = hr.ex_ante_risk([0.5, 0.5], [[0.0, 0.0], [0.0, 0.0]])
        self.assertEqual(result["volatility"], 0.0)
        self.assertIsNone(result["holdings"][0]["share"])
        single = hr.ex_ante_risk([0.9], [[0.04]])
        self.assertAlmostEqual(single["volatility"], 0.18, places=14)
        self.assertAlmostEqual(single["holdings"][0]["share"], 1.0, places=14)
        bad = [
            lambda: hr.ex_ante_risk([0.5, 0.5], [[0.04, 0.01], [0.02, 0.09]]),        # not symmetric
            lambda: hr.ex_ante_risk([0.5, 0.5, 0.0], [[0.04, 0.01], [0.01, 0.09]]),   # wrong length
            lambda: hr.ex_ante_risk([0.5, None], [[0.04, 0.01], [0.01, 0.09]]),
            lambda: hr.ex_ante_risk({"Z": 1.0}, {"names": ["A", "B"], "covariance": [[0.04, 0.0], [0.0, 0.09]]}),
            lambda: hr.ex_ante_risk([1.0, -1.0], [[0.01, 0.05], [0.05, 0.01]]),        # wᵀΣw = 0.02 − 0.1 < 0
            lambda: hr.ex_ante_risk([1.0], [[-0.01]]),
        ]
        for call in bad:
            with self.assertRaises(ModellingInputError):
                call()


class FactorRiskTests(unittest.TestCase):
    def setUp(self) -> None:
        generator = random.Random(9)
        days = 1500
        self.factors = {"Market": [generator.gauss(0, 0.01) for _ in range(days)],
                        "Rates": [generator.gauss(0, 0.005) for _ in range(days)]}
        self.betas = [(1.2, 0.5), (0.8, -1.0), (1.0, 0.0), (0.5, 2.0), (1.5, 0.3)]
        self.specific = [0.006, 0.01, 0.004, 0.012, 0.008]
        self.assets = {
            f"S{i}": [b[0] * m + b[1] * r + generator.gauss(0, s)
                      for m, r in zip(self.factors["Market"], self.factors["Rates"])]
            for i, (b, s) in enumerate(zip(self.betas, self.specific))
        }
        self.model = hr.factor_risk_model(self.assets, self.factors)

    def test_recovers_betas_and_specific_variance(self) -> None:
        self.assertEqual(self.model["factors"], ["Market", "Rates"])
        for fitted, true in zip(self.model["betas"], self.betas):
            self.assertAlmostEqual(fitted[0], true[0], delta=0.05)
            self.assertAlmostEqual(fitted[1], true[1], delta=0.1)
        for fitted, true in zip(self.model["specific_variances"], self.specific):
            self.assertAlmostEqual(fitted, true * true * 252, delta=true * true * 252 * 0.1)
        self.assertAlmostEqual(self.model["factor_covariance"][0][0], 1e-4 * 252, delta=1e-4 * 252 * 0.1)

    def test_covariance_is_psd_and_the_decomposition_adds_up(self) -> None:
        covariance = self.model["covariance"]
        self.assertTrue(_is_psd(covariance, tolerance=1e-13))
        n = len(covariance)
        self.assertTrue(all(covariance[i][j] == covariance[j][i] for i in range(n) for j in range(n)))
        weights = [0.3, 0.25, 0.2, 0.15, 0.1]
        benchmark = [0.2] * 5
        total = hr.factor_risk_decomposition(weights, self.model)
        direct = hr.ex_ante_risk(weights, self.model)
        self.assertEqual(total["measure"], "volatility")
        self.assertAlmostEqual(total["variance"], direct["variance"], places=14)
        self.assertAlmostEqual(sum(f["variance_contribution"] for f in total["factors"]) + total["specific_variance"],
                               total["variance"], places=14)
        self.assertAlmostEqual(sum(f["share"] for f in total["factors"]) + total["specific_share"], 1.0, places=12)
        self.assertAlmostEqual(sum(f["risk_contribution"] for f in total["factors"]) + total["specific_variance"] / total["risk"],
                               total["risk"], places=14)
        active = hr.factor_risk_decomposition(weights, self.model, benchmark_weights=benchmark)
        self.assertEqual(active["measure"], "tracking_error")
        self.assertAlmostEqual(active["risk"], hr.ex_ante_risk(weights, self.model, benchmark_weights=benchmark)["tracking_error"],
                               places=14)
        market = active["factors"][0]
        self.assertAlmostEqual(market["exposure"], market["portfolio_exposure"] - market["benchmark_exposure"], places=14)

    def test_hand_built_model(self) -> None:
        model = {"names": ["A", "B"], "factors": ["Market"], "betas": [[1.0], [2.0]],
                 "factor_covariance": [[0.04]], "specific_variances": [0.01, 0.02]}
        result = hr.factor_risk_decomposition({"A": 0.5, "B": 0.5}, model)
        self.assertAlmostEqual(result["factors"][0]["exposure"], 1.5, places=15)
        self.assertAlmostEqual(result["factor_variance"], 0.09, places=15)
        self.assertAlmostEqual(result["specific_variance"], 0.0075, places=15)
        self.assertAlmostEqual(result["risk"], math.sqrt(0.0975), places=15)
        hedged = hr.factor_risk_decomposition([1.0, -0.5], model)
        self.assertEqual(hedged["factors"][0]["variance_contribution"], 0.0)

    def test_bad_input(self) -> None:
        with self.assertRaises(ModellingInputError):
            hr.factor_risk_model(self.assets, {k: v[:-1] for k, v in self.factors.items()})
        with self.assertRaises(ModellingInputError):
            hr.factor_risk_model(self.assets, {"Flat": [0.001] * 1500})
        with self.assertRaises(ModellingInputError):
            hr.factor_risk_model({k: v[:12] for k, v in self.assets.items()}, {k: v[:12] for k, v in self.factors.items()})
        with self.assertRaises(ModellingInputError):
            hr.factor_risk_model(self.assets, {"Market": self.factors["Market"],
                                               "Twice": [2 * v for v in self.factors["Market"]]})
        with self.assertRaises(ModellingInputError):
            hr.factor_risk_decomposition({"S9": 1.0}, self.model)


class StatisticalFactorTests(unittest.TestCase):
    def test_two_holdings_known_answer(self) -> None:
        generator = random.Random(1)
        common = [generator.gauss(0, 0.01) for _ in range(250)]
        columns = [[c + generator.gauss(0, 0.008) for c in common] for _ in range(2)]
        rho = statistics.correlation(*columns)
        result = hr.statistical_factors(columns, 2)
        first, second = result["factors"]
        self.assertAlmostEqual(first["explained_share"], (1 + rho) / 2, places=9)
        self.assertAlmostEqual(second["explained_share"], (1 - rho) / 2, places=9)
        self.assertAlmostEqual(second["cumulative_share"], 1.0, places=9)
        for loading in first["loadings"]:
            self.assertAlmostEqual(loading, math.sqrt((1 + rho) / 2), places=6)

    def test_recovers_a_planted_one_factor_structure(self) -> None:
        generator = random.Random(11)
        days, betas = 1000, [0.6 + 0.08 * i for i in range(12)]
        factor = [generator.gauss(0, 0.01) for _ in range(days)]
        columns = [[b * f + generator.gauss(0, 0.007) for f in factor] for b in betas]
        result = hr.statistical_factors(columns, 3)
        first, second, third = result["factors"]
        self.assertTrue(all(c["converged"] for c in result["factors"]))
        self.assertGreater(first["explained_share"], 0.6)
        self.assertLess(second["explained_share"], 0.1)
        self.assertGreaterEqual(second["eigenvalue"], third["eigenvalue"])
        truth = [b * 0.01 / math.sqrt(b * b * 1e-4 + 0.007 ** 2) for b in betas]
        for loading, true in zip(first["loadings"], truth):
            self.assertAlmostEqual(loading, true, delta=0.1)
        self.assertTrue(all(v > 0 for v in first["loadings"]))
        self.assertGreater(abs(statistics.correlation(result["component_scores"]["PC1"], factor)), 0.97)
        self.assertAlmostEqual(statistics.pvariance(result["component_scores"]["PC1"]) * days / (days - 1),
                               first["eigenvalue"], places=6)
        vectors = [c["eigenvector"] for c in result["factors"]]
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(sum(a * b for a, b in zip(vectors[i], vectors[j])), 1.0 if i == j else 0.0, places=6)

    def test_signs_are_deterministic_and_bad_input(self) -> None:
        columns = _one_factor(300, seed=4)
        first = hr.statistical_factors(columns, 3)
        again = hr.statistical_factors(columns, 3)
        flipped = hr.statistical_factors([[-v for v in c] for c in columns], 3)
        self.assertEqual(first["factors"], again["factors"])
        for component in first["factors"]:
            largest = max(component["loadings"], key=abs)
            self.assertGreater(largest, 0)
        self.assertAlmostEqual(first["factors"][0]["eigenvalue"], flipped["factors"][0]["eigenvalue"], places=9)
        with self.assertRaises(ModellingInputError):
            hr.statistical_factors(columns, 0)
        with self.assertRaises(ModellingInputError):
            hr.statistical_factors(columns, 7)
        with self.assertRaises(ModellingInputError):
            hr.statistical_factors(columns + [[0.0] * 300], 1)


if __name__ == "__main__":
    unittest.main()
