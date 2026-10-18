from __future__ import annotations

import json
import math
import random
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError, covariance_matrix, risk_contributions  # noqa: E402
from ibkr_etl.quant import pods  # noqa: E402


def _days(count: int) -> list[str]:
    start = date(2025, 1, 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


def _correlated_returns(count: int = 750, seed: int = 3) -> dict[str, list[float]]:
    """Three sleeves at 0.5%, 1% and 2% daily volatility sharing a common factor."""
    generator = random.Random(seed)
    result = {"alpha": [], "beta": [], "gamma": []}
    for _ in range(count):
        common = generator.gauss(0, 1)
        for name, vol, loading in (("alpha", 0.005, 0.3), ("beta", 0.01, 0.6), ("gamma", 0.02, 0.8)):
            result[name].append(vol * (loading * common + math.sqrt(1 - loading ** 2) * generator.gauss(0, 1)))
    return result


class DrawdownRuleTests(unittest.TestCase):
    def test_deepest_rule_and_breach_dates(self) -> None:
        days = _days(6)
        navs = {
            "A": dict(zip(days, [100.0, 110.0, 104.5, 103.0, 101.75, 105.0])),
            "B": dict(zip(days, [100.0, 97.0, 99.0, 99.5, 98.0, 99.0])),
            "C": dict(zip(days, [100.0, 94.0, 96.0, 101.0, 100.5, 100.8])),
            "D": {days[0]: None, days[1]: float("nan")},
        }
        result = pods.drawdown_rules(navs)
        by_name = {row["sleeve"]: row for row in result["sleeves"]}
        a = by_name["A"]
        self.assertEqual((a["peak"], a["peak_date"]), (110.0, days[1]))
        self.assertAlmostEqual(a["current_drawdown"], 105.0 / 110.0 - 1.0)
        self.assertAlmostEqual(a["deepest_drawdown_since_peak"], -0.075)
        self.assertEqual(a["action"], "stop")                      # stays in force after the bounce
        self.assertEqual(a["rule_breached"]["date"], days[4])
        self.assertEqual([b["date"] for b in a["breaches"]], [days[2], days[4]])
        self.assertEqual(by_name["B"]["action"], "none")
        self.assertIsNone(by_name["B"]["rule_breached"])
        c = by_name["C"]                                           # breached, then a new high reset it
        self.assertEqual(c["action"], "none")
        self.assertAlmostEqual(c["maximum_drawdown"], -0.06)
        self.assertEqual(c["peak_date"], days[3])
        self.assertEqual(result["skipped"], ["D"])
        json.dumps(result)

    def test_rule_order_does_not_matter_and_bad_rules_are_refused(self) -> None:
        navs = {"A": dict(zip(_days(3), [100.0, 93.0, 94.0]))}
        reverse = pods.drawdown_rules(navs, rules=((-0.075, "stop"), (-0.05, "halve")))
        self.assertEqual(reverse["sleeves"][0]["action"], "halve")
        self.assertEqual([r["threshold"] for r in reverse["rules"]], [-0.05, -0.075])
        for rules in (((0.05, "halve"),), ((-1.5, "stop"),), (), ((-0.05,),)):
            with self.assertRaises(ModellingInputError):
                pods.drawdown_rules(navs, rules=rules)
        with self.assertRaises(ModellingInputError):
            pods.drawdown_rules({"A": {}})


class RiskBudgetTests(unittest.TestCase):
    def test_equal_risk_contributions_and_target_volatility(self) -> None:
        returns = _correlated_returns()
        result = pods.pod_risk_budget(returns, total_volatility=0.10)
        names = [row["sleeve"] for row in result["sleeves"]]
        cov = covariance_matrix([returns[n] for n in names])
        weights = [row["weight"] for row in result["sleeves"]]
        self.assertAlmostEqual(sum(weights), 1.0)
        for share in risk_contributions(weights, cov):
            self.assertAlmostEqual(share, 1 / 3, places=6)
        for row in result["sleeves"]:
            self.assertAlmostEqual(row["volatility_budget"], 0.10 / 3, places=6)
        scaled = [row["scaled_weight"] for row in result["sleeves"]]
        variance = sum(scaled[i] * scaled[j] * cov[i][j] for i in range(3) for j in range(3))
        self.assertAlmostEqual(math.sqrt(variance), 0.10)
        self.assertGreater(weights[0], weights[1])                 # the calmest sleeve gets the most capital
        self.assertGreater(weights[1], weights[2])
        json.dumps(result)

    def test_two_sleeves_equal_risk_is_inverse_volatility(self) -> None:
        returns = {k: v for k, v in _correlated_returns(300, seed=8).items() if k != "beta"}
        equal = pods.pod_risk_budget(returns, total_volatility=0.08)
        inverse = pods.pod_risk_budget(returns, total_volatility=0.08, method="inverse_volatility")
        for a, b in zip(equal["sleeves"], inverse["sleeves"]):
            self.assertAlmostEqual(a["weight"], b["weight"], places=8)
        vols = [row["volatility"] for row in inverse["sleeves"]]
        self.assertAlmostEqual(inverse["sleeves"][0]["weight"], (1 / vols[0]) / (1 / vols[0] + 1 / vols[1]))

    def test_dated_returns_are_aligned_and_edge_cases(self) -> None:
        days = _days(120)
        generator = random.Random(1)
        dated = {"x": {d: generator.gauss(0, 0.01) for d in days},
                 "y": {d: generator.gauss(0, 0.01) for d in days[5:]}}
        dated["y"][days[50]] = float("nan")
        result = pods.pod_risk_budget(dated, total_volatility=0.1)
        self.assertEqual(result["observations"], 114)
        self.assertEqual(result["window_start"], days[5])
        single = pods.pod_risk_budget({"only": [generator.gauss(0, 0.01) for _ in range(100)]}, total_volatility=0.1)
        self.assertEqual(single["sleeves"][0]["weight"], 1.0)
        self.assertAlmostEqual(single["scale"] * single["sleeves"][0]["volatility"], 0.1)
        with self.assertRaises(ModellingInputError):
            pods.pod_risk_budget({"flat": [0.0] * 100, "x": dated["x"].values()}, total_volatility=0.1)
        with self.assertRaises(ModellingInputError):
            pods.pod_risk_budget({"flat": [0.0] * 100, "x": list(dated["x"].values())[:100]}, total_volatility=0.1)
        with self.assertRaises(ModellingInputError):
            pods.pod_risk_budget({"short": [0.01, -0.01] * 20}, total_volatility=0.1)
        with self.assertRaises(ModellingInputError):
            pods.pod_risk_budget(dated, total_volatility=0.1, method="kelly")


class CapitalReallocationTests(unittest.TestCase):
    def test_known_answer_with_caps(self) -> None:
        stats = {"A": {"information_ratio": 1.0, "track_years": 4}, "B": {"information_ratio": 0.0, "track_years": 4}}
        result = pods.capital_reallocation(stats, {"A": 100.0, "B": 100.0})
        rows = {row["sleeve"]: row for row in result["sleeves"]}
        self.assertAlmostEqual(rows["A"]["shrinkage"], 0.5)        # 4 years against a 4-year prior
        self.assertAlmostEqual(rows["A"]["shrunk_information_ratio"], 0.5)
        self.assertAlmostEqual(rows["A"]["new_capital"], 110.0)    # 12.5 wanted, capped at 10%
        self.assertAlmostEqual(rows["B"]["new_capital"], 90.0)
        self.assertTrue(rows["A"]["capped"])
        gentle = pods.capital_reallocation(stats, {"A": 100.0, "B": 100.0}, sensitivity=0.1)
        self.assertAlmostEqual(gentle["sleeves"][0]["change"], 2.5)
        json.dumps(result)

    def test_unequal_caps_are_balanced(self) -> None:
        stats = {"A": {"information_ratio": 1.0, "track_years": 4},
                 "B": {"information_ratio": 0.0, "observations": 1008}}
        result = pods.capital_reallocation(stats, {"A": 100.0, "B": 300.0})
        rows = {row["sleeve"]: row for row in result["sleeves"]}
        self.assertAlmostEqual(result["average_shrunk_information_ratio"], 0.125)
        self.assertAlmostEqual(rows["A"]["change"], 10.0)
        self.assertAlmostEqual(rows["B"]["change"], -10.0)         # wanted −18.75, scaled to match A's capped gain
        self.assertAlmostEqual(rows["B"]["track_years"], 4.0)

    def test_random_sleeves_keep_the_total_and_the_caps(self) -> None:
        generator = random.Random(6)
        for _ in range(100):
            names = [f"s{i}" for i in range(generator.randint(2, 8))]
            capital = {n: generator.uniform(0, 1e6) for n in names}
            stats = {n: {"information_ratio": generator.gauss(0.2, 0.6), "track_years": generator.uniform(0.2, 10)}
                     for n in names}
            result = pods.capital_reallocation(stats, capital, max_step=0.15)
            self.assertAlmostEqual(sum(r["new_capital"] for r in result["sleeves"]) / sum(capital.values()), 1.0)
            average = result["average_shrunk_information_ratio"]
            for row in result["sleeves"]:
                self.assertLessEqual(abs(row["change"]), 0.15 * row["current_capital"] + 1e-6)
                if row["shrunk_information_ratio"] > average:
                    self.assertGreaterEqual(row["change"], 0.0)

    def test_longer_tracks_are_trusted_more_and_unscored_sleeves_stay(self) -> None:
        stats = {"new": {"information_ratio": 1.5, "track_years": 0.5},
                 "old": {"information_ratio": 1.0, "track_years": 10},
                 "blank": {"information_ratio": None, "track_years": 3},
                 "gone": {"information_ratio": 2.0, "track_years": 3}}
        result = pods.capital_reallocation(stats, {"new": 100.0, "old": 100.0, "blank": 50.0})
        rows = {row["sleeve"]: row for row in result["sleeves"]}
        self.assertGreater(rows["old"]["change"], 0.0)              # 1.0 over 10 years beats 1.5 over 6 months
        self.assertLess(rows["new"]["change"], 0.0)
        self.assertEqual(rows["blank"]["change"], 0.0)
        self.assertEqual(result["unscored"], ["blank"])
        self.assertEqual(result["ignored"], ["gone"])
        for bad in ({"max_step": 0.0}, {"max_step": 1.5}, {"sensitivity": -1}):
            with self.assertRaises(ModellingInputError):
                pods.capital_reallocation(stats, {"new": 100.0}, **bad)
        with self.assertRaises(ModellingInputError):
            pods.capital_reallocation(stats, {"new": -1.0})


class ExposureLimitTests(unittest.TestCase):
    def test_breaches_default_limit_and_composite(self) -> None:
        exposures = {"X": {"Mkt": 1.3, "HML": 0.1}, "Y": {"Mkt": 0.9, "SMB": -0.6, "MOM": float("nan")}}
        result = pods.exposure_limits_check(exposures, {"Mkt": (0.8, 1.2), "*": 0.5}, weights={"X": 0.5, "Y": 0.5})
        found = {(b["sleeve"], b["factor"]): b for b in result["breaches"]}
        self.assertEqual(set(found), {("X", "Mkt"), ("Y", "SMB")})
        self.assertAlmostEqual(found[("X", "Mkt")]["excess"], 0.1)
        self.assertAlmostEqual(found[("Y", "SMB")]["excess"], -0.1)
        self.assertEqual(result["missing"], [{"sleeve": "Y", "factor": "MOM"}])
        composite = result["composite"]
        self.assertAlmostEqual(composite["exposures"]["Mkt"], 1.1)
        self.assertAlmostEqual(composite["exposures"]["SMB"], -0.3)
        self.assertTrue(composite["within_limits"])
        self.assertFalse(result["within_limits"])
        json.dumps(result)

    def test_stacked_bets_breach_only_at_the_composite(self) -> None:
        exposures = {"X": {"MOM": 0.4}, "Y": {"MOM": 0.4}, "Z": {"Oil": 2.0}}
        result = pods.exposure_limits_check(exposures, {"MOM": 0.3}, weights={"X": 1.0, "Y": 1.0})
        self.assertEqual({b["sleeve"] for b in result["breaches"]}, {"X", "Y"})
        self.assertEqual(result["unchecked"], [{"sleeve": "Z", "factor": "Oil"}])
        within = pods.exposure_limits_check(exposures, {"MOM": 0.5}, weights={"X": 1.0, "Y": 1.0})
        self.assertEqual(within["breaches"], [])
        self.assertAlmostEqual(within["composite"]["breaches"][0]["excess"], 0.3)
        with self.assertRaises(ModellingInputError):
            pods.exposure_limits_check(exposures, {"MOM": (0.5, 0.1)})


if __name__ == "__main__":
    unittest.main()
