from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import dynamic_correlation as dc  # noqa: E402
from ibkr_etl.quant.holdings_risk import _cholesky  # noqa: E402

TARGET = [[1.0, 0.5, 0.3], [0.5, 1.0, 0.2], [0.3, 0.2, 1.0]]


def _simulate_dcc(a: float, b: float, days: int, seed: int) -> list[list[float]]:
    """Three GARCH(1,1) series whose standardised shocks follow a DCC(1,1) with target TARGET."""
    generator, n = random.Random(seed), len(TARGET)
    q, variance, columns = [row[:] for row in TARGET], [1e-4] * n, [[] for _ in range(n)]
    for _ in range(days):
        lower = _cholesky(dc._correlation(q))
        z = [generator.gauss(0, 1) for _ in range(n)]
        shocks = [sum(lower[i][k] * z[k] for k in range(i + 1)) for i in range(n)]
        for i, shock in enumerate(shocks):
            value = math.sqrt(variance[i]) * shock
            columns[i].append(value)
            variance[i] = 2e-6 + 0.08 * value * value + 0.9 * variance[i]
        q = [[(1 - a - b) * t + a * shocks[i] * shocks[j] + b * v for j, (t, v) in enumerate(zip(trow, qrow))]
             for i, (trow, qrow) in enumerate(zip(TARGET, q))]
    return columns


def _regimes(days: int, seed: int, count: int = 4) -> list[list[float]]:
    """Calm stretches (correlation 0.2, 0.8% a day) and stressed ones (0.8, 2.5%, falling)."""
    generator, state, columns = random.Random(seed), 0, [[] for _ in range(count)]
    for _ in range(days):
        if generator.random() < (0.01 if state == 0 else 0.05):
            state = 1 - state
        rho, vol, drift = (0.2, 0.008, 0.0005) if state == 0 else (0.8, 0.025, -0.004)
        market = generator.gauss(0, 1)
        for column in columns:
            column.append(drift + vol * (math.sqrt(rho) * market + math.sqrt(1 - rho) * generator.gauss(0, 1)))
    return columns


def _is_psd(matrix, tolerance: float = 1e-10) -> bool:
    n = len(matrix)
    lower = [[0.0] * n for _ in range(n)]
    for j in range(n):
        pivot = matrix[j][j] + tolerance - sum(v * v for v in lower[j][:j])
        if pivot <= 0:
            return False
        lower[j][j] = math.sqrt(pivot)
        for i in range(j + 1, n):
            lower[i][j] = (matrix[i][j] - sum(x * y for x, y in zip(lower[i][:j], lower[j][:j]))) / lower[j][j]
    return True


def _assert_correlation_matrix(test: unittest.TestCase, matrix) -> None:
    n = len(matrix)
    for i in range(n):
        test.assertEqual(matrix[i][i], 1.0)
        for j in range(n):
            test.assertEqual(matrix[i][j], matrix[j][i])
            test.assertLessEqual(abs(matrix[i][j]), 1.0)
    test.assertTrue(_is_psd(matrix))


class DccTests(unittest.TestCase):
    def test_recovers_known_parameters(self) -> None:
        result = dc.dcc_correlation(_simulate_dcc(0.05, 0.90, 2500, seed=1))
        self.assertAlmostEqual(result["a"], 0.05, delta=0.03)
        self.assertAlmostEqual(result["b"], 0.90, delta=0.06)
        self.assertEqual(result["fit"]["pairs_used"], 3)
        self.assertGreater(result["fit"]["composite_log_likelihood"],
                           result["fit"]["constant_correlation_log_likelihood"] + 10)
        self.assertAlmostEqual(result["half_life_days"], math.log(0.5) / math.log(result["a"] + result["b"]), places=12)
        self.assertTrue(all(v["method"] == "garch" for v in result["volatility_models"]))
        latest = result["latest_correlation"]
        self.assertAlmostEqual(latest[0][1], 0.5, delta=0.35)
        self.assertNotIn("nan", repr(result).lower())

    def test_constant_correlation_gives_a_near_zero(self) -> None:
        result = dc.dcc_correlation(_simulate_dcc(0.0, 0.0, 2500, seed=3))
        self.assertLess(result["a"], 0.02)
        series = result["average_correlation"]["series"]
        self.assertLess(max(series) - min(series), 0.1)
        unconditional = result["average_correlation"]["unconditional"]
        self.assertAlmostEqual(unconditional, (0.5 + 0.3 + 0.2) / 3, delta=0.05)

    def test_matrices_stay_symmetric_with_unit_diagonal_and_psd(self) -> None:
        columns = _simulate_dcc(0.08, 0.9, 400, seed=5) + _simulate_dcc(0.08, 0.9, 400, seed=6)
        residuals = [dc._standardise(column)[0] for column in columns]
        target = [[sum(a * b for a, b in zip(x, y)) / 400 for y in residuals] for x in residuals]
        averages, next_q, matrices = dc._dcc_path(residuals, target, 0.08, 0.9, [1 / 6] * 6, keep=True)
        self.assertEqual(len(matrices), 400)
        self.assertEqual(len(averages), 400)
        for matrix in matrices[::25] + [dc._correlation(next_q)]:
            _assert_correlation_matrix(self, matrix)
        for day in (0, 199, 399):
            m = matrices[day]
            pairs = [(i, j) for i in range(6) for j in range(6) if i != j]
            self.assertAlmostEqual(averages[day], sum(m[i][j] for i, j in pairs) / len(pairs), places=12)
        _assert_correlation_matrix(self, dc.dcc_correlation(columns)["latest_correlation"])

    def test_correlation_rises_on_stress_days(self) -> None:
        result = dc.dcc_correlation(_regimes(1500, seed=2), weights=[0.4, 0.3, 0.2, 0.1])
        average = result["average_correlation"]
        self.assertGreater(result["a"], 0.01)
        self.assertEqual(result["stress_days"], 75)
        self.assertGreater(average["stress_days"], average["calm_days"] + 0.1)
        self.assertLess(result["stress_threshold"], 0)

    def test_largest_holdings_are_modelled_and_the_rest_named(self) -> None:
        generator = random.Random(4)
        market = [generator.gauss(0, 0.01) for _ in range(260)]
        columns = {f"H{i:02d}": [0.7 * m + generator.gauss(0, 0.01) for m in market] for i in range(34)}
        columns["CASH"] = [0.0] * 260
        weights = {name: 1.0 + i for i, name in enumerate(columns)}
        weights["H00"] = 0.0
        result = dc.dcc_correlation(columns, weights=weights)
        self.assertEqual(len(result["names"]), dc.MAX_SERIES)
        reasons = {row["name"]: row["reason"] for row in result["not_modelled"]}
        self.assertIn("zero weight", reasons["H00"])
        self.assertIn("did not move", reasons["CASH"])
        self.assertEqual(sorted(name for name, reason in reasons.items() if "largest" in reason), ["H01", "H02", "H03"])
        total = sum(weights.values())
        self.assertAlmostEqual(result["weight_covered"], (total - 35 - 2 - 3 - 4) / total, places=12)  # CASH, H01-H03
        self.assertEqual(result["fit"]["pairs_used"], dc.MAX_PAIRS)
        self.assertEqual(result["fit"]["pairs_available"], 30 * 29 // 2)
        again = dc.dcc_correlation(columns, weights=weights)
        self.assertEqual(result["a"], again["a"])
        self.assertEqual(result["latest_correlation"], again["latest_correlation"])

    def test_bad_input(self) -> None:
        columns = _simulate_dcc(0.05, 0.9, 300, seed=8)
        with self.assertRaises(ModellingInputError):
            dc.dcc_correlation([column[:100] for column in columns])
        with self.assertRaises(ModellingInputError):
            dc.dcc_correlation(columns[:1])
        with self.assertRaises(ModellingInputError):
            dc.dcc_correlation(columns, weights=[1.0, 0.0, 0.0])
        with self.assertRaises(ModellingInputError):
            dc.dcc_correlation(columns, weights=[0.5, 0.5])
        with self.assertRaises(ModellingInputError):
            dc.dcc_correlation(columns, dates=["2024-01-02"] * 10)
        with self.assertRaises(ModellingInputError):
            dc.dcc_correlation([columns[0], columns[1][:-1] + [None]])


if __name__ == "__main__":
    unittest.main()
