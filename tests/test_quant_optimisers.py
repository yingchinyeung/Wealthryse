from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl import modelling  # noqa: E402
from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import optimisers as opt  # noqa: E402


def _columns(n: int, count: int = 504, seed: int = 1) -> list[list[float]]:
    """Daily returns driven by one market factor, with different betas, noise and drifts."""
    generator = random.Random(seed)
    market = [generator.gauss(0.0003, 0.01) for _ in range(count)]
    columns = []
    for i in range(n):
        beta, noise = generator.uniform(0.6, 1.4), generator.uniform(0.005, 0.015)
        columns.append([beta * m + generator.gauss(0.0001 * i, noise) for m in market])
    return columns


def _cov(n: int, seed: int = 1) -> list[list[float]]:
    return modelling.covariance_matrix(_columns(n, seed=seed))


def _brute_tail_loss(losses: list[float], m: float) -> float:
    """CVaR as Rockafellar and Uryasev define it: min over a of a + Σ max(0, L − a) / m."""
    return min(a + sum(max(0.0, v - a) for v in losses) / m for a in losses)


class ProjectionTests(unittest.TestCase):
    def test_projection_matches_modelling(self) -> None:
        generator = random.Random(4)
        for _ in range(20):
            values = [generator.gauss(0.1, 0.4) for _ in range(8)]
            cap = generator.uniform(0.15, 1.0)
            ours, theirs = opt._project(values, cap), modelling.project_capped_simplex(values, cap)
            self.assertAlmostEqual(sum(ours), 1.0, places=12)
            for a, b in zip(ours, theirs):
                self.assertAlmostEqual(a, b, places=9)

    def test_shrink_project_is_the_exact_proximal_step(self) -> None:
        values, anchor, threshold, cap = [0.7, 0.1, 0.35], [0.3, 0.3, 0.4], 0.08, 0.6
        result = opt._shrink_project(values, anchor, threshold, cap)

        def objective(w):
            return 0.5 * sum((a - b) ** 2 for a, b in zip(w, values)) + threshold * sum(abs(a - b) for a, b in zip(w, anchor))

        best = min(objective([i / 1000, j / 1000, 1 - (i + j) / 1000])
                   for i in range(601) for j in range(601) if 0.4 <= (i + j) / 1000 <= 1.0)
        self.assertAlmostEqual(sum(result), 1.0, places=12)
        self.assertTrue(all(0.0 <= w <= cap + 1e-12 for w in result))
        self.assertLessEqual(objective(result), best + 1e-12)
        self.assertAlmostEqual(objective(result), best, delta=1e-5)


class BlackLittermanTests(unittest.TestCase):
    COV = [[0.04, 0.006], [0.006, 0.09]]

    def test_no_views_returns_the_market(self) -> None:
        cov = _cov(5)
        market = [0.3, 0.25, 0.2, 0.15, 0.1]
        result = opt.black_litterman(cov, market, cap=1.0)
        implied = [2.5 * sum(a * b for a, b in zip(row, market)) for row in cov]
        for a, b in zip(result["posterior_returns"], implied):
            self.assertAlmostEqual(a, b, places=14)
        for row, original in zip(result["posterior_covariance"], cov):
            for a, b in zip(row, original):
                self.assertAlmostEqual(a, 1.05 * b, places=14)
        for a, b, c in zip(result["unconstrained_weights"], result["weights"], market):
            self.assertAlmostEqual(a, c, places=12)
            self.assertAlmostEqual(b, c, places=6)

    def test_hand_computed_two_asset_view(self) -> None:
        # π = 2.5Σw = [0.066, 0.099]; with Ω = τpΣp' the view return moves halfway from 6.6% to 10%.
        result = opt.black_litterman(self.COV, [0.6, 0.4], views=[{"weights": [1, 0], "return": 0.10}], cap=1.0)
        self.assertAlmostEqual(result["implied_returns"][0], 0.066, places=12)
        self.assertAlmostEqual(result["implied_returns"][1], 0.099, places=12)
        self.assertAlmostEqual(result["posterior_returns"][0], 0.083, places=12)
        self.assertAlmostEqual(result["posterior_returns"][1], 0.099 + 0.05 * 0.006 * 8.5, places=12)
        post = result["posterior_covariance"]
        self.assertAlmostEqual(post[0][0], 0.04 + 0.002 - 0.0025 * 0.0016 / 0.004, places=12)
        self.assertEqual(post[0][1], post[1][0])
        self.assertEqual(result["views"][0]["omega_method"], "He-Litterman default")
        self.assertEqual(result["views"][0]["kind"], "absolute")

    def test_full_confidence_holds_each_view_exactly(self) -> None:
        cov = _cov(4)
        views = [{"weights": [1, 0, 0, 0], "return": 0.12, "confidence": 1.0},
                 {"weights": [0, 1, -1, 0], "return": 0.03, "confidence": 1.0}]
        result = opt.black_litterman(cov, [0.25] * 4, views=views)
        self.assertAlmostEqual(result["views"][0]["posterior_return"], 0.12, places=10)
        self.assertAlmostEqual(result["views"][1]["posterior_return"], 0.03, places=10)
        self.assertEqual(result["views"][1]["kind"], "relative")

    def test_idzorek_tilt_is_the_confidence_share_of_the_full_tilt(self) -> None:
        cov, market = _cov(4), [0.4, 0.3, 0.2, 0.1]
        view = {"weights": [0, 1, 0, -1], "return": 0.05}

        def tilt(confidence):
            weights = opt.black_litterman(cov, market, views=[{**view, "confidence": confidence}])["unconstrained_weights"]
            return [w - m for w, m in zip(weights, market)]

        full = tilt(1.0)
        self.assertGreater(max(abs(v) for v in full), 0.01)
        for confidence in (0.2, 0.5, 0.85):
            for a, b in zip(tilt(confidence), full):
                self.assertAlmostEqual(a, confidence * b, places=12)
        default = opt.black_litterman(cov, market, views=[view])["posterior_returns"]
        half = opt.black_litterman(cov, market, views=[{**view, "confidence": 0.5}])["posterior_returns"]
        for a, b in zip(default, half):
            self.assertAlmostEqual(a, b, places=14)

    def test_bad_views_are_refused(self) -> None:
        bad = (
            [{"weights": [1, 0], "return": 0.1, "confidence": 0.0}],
            [{"weights": [1, 0], "return": 0.1, "confidence": 1.5}],
            [{"weights": [1, 0, 0], "return": 0.1}],
            [{"weights": [0, 0], "return": 0.1}],
            [{"weights": [1, 0], "return": None}],
            [{"weights": [1, -1], "return": 0.02, "confidence": 1.0}, {"weights": [-1, 1], "return": 0.01, "confidence": 1.0}],
        )
        for views in bad:
            with self.assertRaises(ModellingInputError):
                opt.black_litterman(self.COV, [0.5, 0.5], views=views)
        with self.assertRaises(ModellingInputError):
            opt.black_litterman(self.COV, [1.2, -0.2])
        with self.assertRaises(ModellingInputError):
            opt.black_litterman([[0.04, 0.01], [0.02, 0.09]], [0.5, 0.5])


class HierarchicalRiskParityTests(unittest.TestCase):
    def test_two_assets_split_by_inverse_variance(self) -> None:
        result = opt.hierarchical_risk_parity([[0.04, 0.03], [0.03, 0.09]])
        self.assertAlmostEqual(result["weights"][0], 0.09 / 0.13, places=12)
        self.assertAlmostEqual(sum(result["risk_contributions"]), 1.0, places=12)

    def test_uncorrelated_holdings_get_inverse_variance_weights(self) -> None:
        variances = [0.01, 0.04, 0.02, 0.09, 0.0625]
        cov = [[v if i == j else 0.0 for j in range(5)] for i, v in enumerate(variances)]
        weights = opt.hierarchical_risk_parity(cov)["weights"]
        total = sum(1.0 / v for v in variances)
        for w, v in zip(weights, variances):
            self.assertAlmostEqual(w, (1.0 / v) / total, places=12)

    def test_two_blocks_by_hand(self) -> None:
        sd = [0.2, 0.3, 0.1, 0.4]
        corr = [[1, 0.8, 0.1, 0.1], [0.8, 1, 0.1, 0.1], [0.1, 0.1, 1, 0.8], [0.1, 0.1, 0.8, 1]]
        cov = [[corr[i][j] * sd[i] * sd[j] for j in range(4)] for i in range(4)]
        result = opt.hierarchical_risk_parity(cov)
        order = result["order"]
        self.assertEqual({frozenset(order[:2]), frozenset(order[2:])}, {frozenset({0, 1}), frozenset({2, 3})})
        self.assertAlmostEqual(result["linkage"][0][2], math.sqrt(2 * 0.1), places=12)

        def cluster_variance(members):
            inverse = [1 / cov[i][i] for i in members]
            w = [v / sum(inverse) for v in inverse]
            return sum(w[a] * w[b] * cov[i][j] for a, i in enumerate(members) for b, j in enumerate(members))

        first, second = cluster_variance([0, 1]), cluster_variance([2, 3])
        share = second / (first + second)
        expected = [share * 0.09 / 0.13, share * 0.04 / 0.13, (1 - share) * 0.16 / 0.17, (1 - share) * 0.01 / 0.17]
        for a, b in zip(result["weights"], expected):
            self.assertAlmostEqual(a, b, places=12)

    def test_cap_single_holding_and_zero_variance(self) -> None:
        cov = [[0.01, 0, 0], [0, 0.09, 0], [0, 0, 0.16]]
        capped = opt.hierarchical_risk_parity(cov, cap=0.5)["weights"]
        self.assertAlmostEqual(capped[0], 0.5)
        self.assertAlmostEqual(sum(capped), 1.0, places=12)
        self.assertAlmostEqual(capped[1] / capped[2], 0.16 / 0.09, places=12)   # excess shared pro rata
        self.assertEqual(opt.hierarchical_risk_parity([[0.04]])["weights"], [1.0])
        with self.assertRaises(ModellingInputError):
            opt.hierarchical_risk_parity([[0.04, 0.0], [0.0, 0.0]])


class MinCvarTests(unittest.TestCase):
    def test_tail_loss_definition(self) -> None:
        self.assertAlmostEqual(opt._tail_loss([5, 4, 3, 2, 1], 2.5), (5 + 4 + 0.5 * 3) / 2.5)
        generator = random.Random(2)
        for m in (3.0, 7.4, 12.0):
            losses = [generator.gauss(0, 1) for _ in range(100)]
            self.assertAlmostEqual(opt._tail_loss(sorted(losses, reverse=True), m), _brute_tail_loss(losses, m), places=12)

    @staticmethod
    def _scenarios(count: int, seed: int) -> list[list[float]]:
        generator, rows = random.Random(seed), []
        for _ in range(count):
            market = generator.gauss(0.0005, 0.01)
            crash = -0.06 if generator.random() < 0.05 else 0.0
            rows.append([market + generator.gauss(0.0, 0.004),
                         0.5 * market + generator.gauss(0.0004, 0.012) + crash,
                         -0.3 * market + generator.gauss(0.0002, 0.009)])
        return rows

    def _cvar(self, rows, weights, m):
        return opt._tail_loss(sorted((-sum(a * b for a, b in zip(r, weights)) for r in rows), reverse=True), m)

    def test_matches_brute_force_on_two_assets(self) -> None:
        rows = [r[:2] for r in self._scenarios(200, seed=3)]
        result = opt.min_cvar_weights(rows, level=0.9, cap=1.0)
        grid = min(self._cvar(rows, [i / 2000, 1 - i / 2000], 20.0) for i in range(2001))
        self.assertLessEqual(result["expected_tail_loss"], grid + 1e-9)
        self.assertGreater(result["expected_tail_loss"], grid - 1e-4)
        self.assertAlmostEqual(result["expected_tail_loss"], self._cvar(rows, result["weights"], 20.0), places=14)

    def test_matches_brute_force_on_three_assets(self) -> None:
        rows = self._scenarios(120, seed=5)
        result = opt.min_cvar_weights(rows, level=0.9, cap=1.0)
        grid = min(self._cvar(rows, [i / 100, j / 100, 1 - (i + j) / 100], 12.0)
                   for i in range(101) for j in range(101 - i))
        self.assertLessEqual(result["expected_tail_loss"], grid + 1e-9)
        self.assertLess(result["expected_tail_loss"], result["equal_weight_expected_tail_loss"])
        self.assertAlmostEqual(sum(result["weights"]), 1.0, places=12)

    def test_dominant_holding_takes_the_cap(self) -> None:
        generator = random.Random(9)
        rows = []
        for _ in range(300):
            base = [generator.gauss(0, 0.01) for _ in range(3)]
            rows.append([max(base) + 0.001, base[1], base[2]])     # holding 0 beats the others in every scenario
        result = opt.min_cvar_weights(rows, level=0.95, cap=0.5)
        self.assertAlmostEqual(result["weights"][0], 0.5, places=6)
        self.assertLessEqual(max(result["weights"]), 0.5 + 1e-9)

    def test_missing_rows_skipped_and_short_history_refused(self) -> None:
        rows = self._scenarios(220, seed=1)
        rows[5][1] = None
        rows[9][0] = float("nan")
        rows[12] = None
        result = opt.min_cvar_weights(rows, level=0.95, cap=1.0)
        self.assertEqual(result["skipped_scenarios"], 3)
        self.assertEqual(result["scenarios"], 217)
        self.assertGreaterEqual(result["expected_tail_loss"], result["value_at_risk"])
        with self.assertRaises(ModellingInputError):
            opt.min_cvar_weights(self._scenarios(150, seed=1), level=0.95)
        with self.assertRaises(ModellingInputError):
            opt.min_cvar_weights(rows, level=1.0)
        self.assertEqual(opt.min_cvar_weights([[0.01 * (i % 7 - 3)] for i in range(300)])["weights"], [1.0])


class MeanVarianceCostTests(unittest.TestCase):
    MU = [0.06, 0.09, 0.12, 0.08]

    def _objective(self, weights, cov, mu, lam, cost, current):
        variance = sum(a * b for a, b in zip(weights, modelling._matvec(cov, weights)))
        return (sum(w * m for w, m in zip(weights, mu)) - lam * variance
                - cost * sum(abs(a - b) for a, b in zip(weights, current)))

    def test_zero_cost_matches_plain_mean_variance(self) -> None:
        cov = _cov(4)
        result = opt.mean_variance_with_costs(self.MU, cov, [0.25] * 4, risk_aversion=3.0, cost_bps=0, cap=0.6)
        plain = modelling.mean_variance_weights(cov, self.MU, risk_aversion_inverse=1 / 3.0, cap=0.6, iterations=20000)
        for a, b in zip(result["weights"], plain):
            self.assertAlmostEqual(a, b, places=7)

    def test_large_cost_stays_at_current_weights(self) -> None:
        cov, current = _cov(4), [0.4, 0.3, 0.2, 0.1]
        result = opt.mean_variance_with_costs(self.MU, cov, current, risk_aversion=3.0, cost_bps=500, cap=0.6)
        for a, b in zip(result["weights"], current):
            self.assertAlmostEqual(a, b, places=10)
        self.assertAlmostEqual(result["turnover"], 0.0, places=9)

    def test_costs_cut_turnover_and_the_answer_is_optimal(self) -> None:
        cov, current = _cov(4), [0.4, 0.3, 0.2, 0.1]
        turnovers = [opt.mean_variance_with_costs(self.MU, cov, current, risk_aversion=3.0, cost_bps=c, cap=0.6)["turnover"]
                     for c in (0, 20, 100)]
        self.assertGreater(turnovers[0], turnovers[1])
        self.assertGreater(turnovers[1], turnovers[2])
        result = opt.mean_variance_with_costs(self.MU, cov, current, risk_aversion=3.0, cost_bps=20, cap=0.6)
        best = self._objective(result["weights"], cov, self.MU, 3.0, 0.002, current)
        generator = random.Random(7)
        for _ in range(300):
            nearby = opt._project([w + generator.gauss(0, 0.02) for w in result["weights"]], 0.6)
            self.assertLessEqual(self._objective(nearby, cov, self.MU, 3.0, 0.002, current), best + 1e-12)
        self.assertAlmostEqual(result["trading_cost"], 0.002 * result["turnover"], places=14)

    def test_turnover_limit_matches_brute_force(self) -> None:
        cov, mu, current, limit = [[0.04, 0.01], [0.01, 0.09]], [0.05, 0.15], [0.8, 0.2], 0.3
        result = opt.mean_variance_with_costs(mu, cov, current, risk_aversion=2.0, cost_bps=10, turnover_limit=limit, cap=1.0)
        self.assertTrue(result["turnover_limit_binding"])
        self.assertAlmostEqual(result["turnover"], limit, delta=1e-6)
        self.assertLessEqual(result["turnover"], limit + 1e-8)
        self.assertGreater(result["turnover_shadow_cost_bps"], 0)
        grid = max((self._objective([x, 1 - x], cov, mu, 2.0, 0.001, current), x)
                   for x in (i / 100_000 for i in range(100_001)) if 2 * abs(x - 0.8) <= limit)
        self.assertAlmostEqual(result["weights"][0], grid[1], delta=1e-5)
        loose = opt.mean_variance_with_costs(mu, cov, current, risk_aversion=2.0, cost_bps=10, turnover_limit=2.0, cap=1.0)
        self.assertFalse(loose["turnover_limit_binding"])
        self.assertEqual(loose["turnover_shadow_cost_bps"], 0.0)

    def test_turnover_limit_is_the_shadow_priced_cost(self) -> None:
        cov, current = _cov(4), [0.4, 0.3, 0.2, 0.1]
        limited = opt.mean_variance_with_costs(self.MU, cov, current, risk_aversion=3.0, cost_bps=10,
                                               turnover_limit=0.1, cap=0.6)
        priced = opt.mean_variance_with_costs(self.MU, cov, current, risk_aversion=3.0,
                                              cost_bps=10 + limited["turnover_shadow_cost_bps"], cap=0.6)
        for a, b in zip(limited["weights"], priced["weights"]):
            self.assertAlmostEqual(a, b, places=6)

    def test_bad_inputs(self) -> None:
        cov = _cov(3)
        mu = self.MU[:3]
        with self.assertRaises(ModellingInputError):   # selling down to the 50% cap alone needs 40% turnover
            opt.mean_variance_with_costs(mu, cov, [0.7, 0.2, 0.1], risk_aversion=2, cost_bps=10, turnover_limit=0.2, cap=0.5)
        for kwargs in ({"cost_bps": -1}, {"cost_bps": 10, "turnover_limit": 3.0}, {"cost_bps": 10, "risk_aversion": 0}):
            with self.assertRaises(ModellingInputError):
                opt.mean_variance_with_costs(mu, cov, [0.4, 0.3, 0.3], **{"risk_aversion": 2, **kwargs})
        with self.assertRaises(ModellingInputError):
            opt.mean_variance_with_costs(mu, cov, [0.5, 0.6, -0.1], risk_aversion=2, cost_bps=10)
        with self.assertRaises(ModellingInputError):
            opt.mean_variance_with_costs([0.1, None, 0.2], cov, [0.4, 0.3, 0.3], risk_aversion=2, cost_bps=10)


class RobustMeanVarianceTests(unittest.TestCase):
    def test_kappa_zero_matches_plain_mean_variance(self) -> None:
        cov, mu = _cov(5), [0.05, 0.08, 0.11, 0.07, 0.1]
        result = opt.robust_mean_variance(mu, cov, uncertainty_covariance=cov, kappa=0.0, risk_aversion=2.0, cap=0.5)
        plain = modelling.mean_variance_weights(cov, mu, risk_aversion_inverse=0.5, cap=0.5, iterations=20000)
        for a, b in zip(result["weights"], plain):
            self.assertAlmostEqual(a, b, places=7)
        self.assertEqual(result["weights"], result["plain_weights"])

    def test_matches_brute_force_on_two_assets(self) -> None:
        cov, mu = [[0.04, 0.012], [0.012, 0.09]], [0.06, 0.14]
        omega = [[0.004, 0.0], [0.0, 0.02]]
        result = opt.robust_mean_variance(mu, cov, uncertainty_covariance=omega, kappa=1.5, risk_aversion=1.0, cap=1.0)

        def objective(x):
            w = [x, 1 - x]
            quad = lambda m: sum(w[i] * m[i][j] * w[j] for i in range(2) for j in range(2))  # noqa: E731
            return w[0] * mu[0] + w[1] * mu[1] - 1.5 * math.sqrt(quad(omega)) - quad(cov)

        best = max((objective(i / 100_000), i / 100_000) for i in range(100_001))
        self.assertAlmostEqual(result["weights"][0], best[1], delta=1e-5)
        self.assertGreater(result["worst_case_return"], result["plain_worst_case_return"])
        self.assertLess(result["expected_return"], opt._point(result["plain_weights"], cov, mu)["expected_return"])

    def test_bad_inputs(self) -> None:
        cov = _cov(3)
        with self.assertRaises(ModellingInputError):
            opt.robust_mean_variance([0.1] * 3, cov, uncertainty_covariance=cov, kappa=-1, risk_aversion=2)
        with self.assertRaises(ModellingInputError):
            opt.robust_mean_variance([0.1] * 3, cov, uncertainty_covariance=[[0.01]], kappa=1, risk_aversion=2)


if __name__ == "__main__":
    unittest.main()
