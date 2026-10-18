from __future__ import annotations

import json
import math
import random
import statistics
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import crowding as cr  # noqa: E402


class ConcentrationTests(unittest.TestCase):
    def test_hand_computed(self) -> None:
        result = cr.concentration({"a": 0.5, "b": 0.3, "c": 0.2})
        self.assertAlmostEqual(result["herfindahl"], 0.25 + 0.09 + 0.04)
        self.assertAlmostEqual(result["effective_holdings"], 1 / 0.38)
        self.assertEqual(result["largest"], "a")
        self.assertEqual(result["top_weights"], {"1": 0.5, "5": 1.0, "10": 1.0})

    def test_equal_weights_give_n_effective_holdings(self) -> None:
        result = cr.concentration([1 / 12] * 12)
        self.assertAlmostEqual(result["herfindahl"], 1 / 12)
        self.assertAlmostEqual(result["effective_holdings"], 12)
        self.assertAlmostEqual(result["top_weights"]["10"], 10 / 12)
        single = cr.concentration({"only": 0.7})
        self.assertAlmostEqual(single["herfindahl"], 1.0)
        self.assertAlmostEqual(single["top_shares"]["1"], 1.0)

    def test_shorts_count_by_their_size(self) -> None:
        result = cr.concentration({"long": 0.6, "short": -0.6, "flat": 0.0})
        self.assertAlmostEqual(result["gross_weight"], 1.2)
        self.assertAlmostEqual(result["effective_holdings"], 2.0)
        self.assertAlmostEqual(result["top_weights"]["1"], 0.6)
        self.assertAlmostEqual(result["top_shares"]["1"], 0.5)
        self.assertEqual(result["holdings"], 2)

    def test_bad_input_is_refused(self) -> None:
        for weights in ({}, {"a": 0.0}, {"a": float("nan")}, {"a": None}, {"a": True}, []):
            with self.assertRaises(ModellingInputError, msg=weights):
                cr.concentration(weights)


PORTFOLIO = {"AAPL": 0.5, "MSFT": 0.3, "XOM": 0.2}
FUNDS = {
    "Fund 1": {"AAPL": {"value": 100, "shares": 10}, "MSFT": {"value": 100, "shares": 5}},
    "Fund 2": {"XOM": {"value": 50, "shares": 20}, "GE": {"value": 50, "shares": 1}},
    "Twin": {"AAPL": {"value": 500, "shares": 30}, "MSFT": {"value": 300, "shares": 2},
             "XOM": {"value": 200, "shares": 4}},
}
OUTSTANDING = {"AAPL": 100, "MSFT": 50, "XOM": 200, "GE": 10}


class OwnershipOverlapTests(unittest.TestCase):
    def test_hand_computed_overlap(self) -> None:
        result = cr.ownership_overlap(PORTFOLIO, FUNDS, shares_outstanding=OUTSTANDING, top_n=1)
        rows = {r["security"]: r for r in result["holdings"]}
        self.assertAlmostEqual(rows["AAPL"]["ownership"], 40 / 100)
        self.assertAlmostEqual(rows["AAPL"]["crowding_score"], 30 / 100)        # its largest holder, Twin
        self.assertEqual(rows["AAPL"]["largest_holders"][0]["fund"], "Twin")
        self.assertAlmostEqual(rows["MSFT"]["ownership"], 7 / 50)
        self.assertAlmostEqual(rows["XOM"]["ownership"], 24 / 200)
        self.assertEqual(rows["XOM"]["funds_holding"], 2)
        self.assertAlmostEqual(result["weighted_ownership"], 0.5 * 0.4 + 0.3 * 0.14 + 0.2 * 0.12)
        self.assertAlmostEqual(result["weighted_crowding_score"], 0.5 * 0.3 + 0.3 * 0.1 + 0.2 * 0.1)
        self.assertAlmostEqual(result["coverage"], 1.0)
        self.assertAlmostEqual(result["share_in_names_funds_hold"], 1.0)
        similar = {f["fund"]: f for f in result["most_similar_funds"]}
        self.assertEqual(result["most_similar_funds"][0]["fund"], "Twin")
        self.assertAlmostEqual(similar["Twin"]["similarity"], 1.0)
        self.assertAlmostEqual(similar["Fund 1"]["similarity"], 0.4 / math.sqrt(0.38 * 0.5))
        self.assertAlmostEqual(similar["Fund 2"]["similarity"], 0.2 * 0.5 / math.sqrt(0.38 * 0.5))
        self.assertAlmostEqual(similar["Fund 2"]["portfolio_weight_in_fund_names"], 0.2)
        self.assertAlmostEqual(similar["Fund 2"]["fund_weight_in_portfolio_names"], 0.5)
        json.dumps(result, allow_nan=False)

    def test_units_for_bare_numbers(self) -> None:
        shares = cr.ownership_overlap(PORTFOLIO, {"F": {"AAPL": 10, "XOM": 50}}, unit="shares",
                                      shares_outstanding=OUTSTANDING)
        rows = {r["security"]: r for r in shares["holdings"]}
        self.assertAlmostEqual(rows["AAPL"]["ownership"], 0.1)
        self.assertIsNone(rows["MSFT"]["ownership"])
        self.assertAlmostEqual(shares["coverage"], 0.7)
        self.assertAlmostEqual(shares["weighted_ownership"], (0.5 * 0.1 + 0.2 * 0.25) / 0.7)
        self.assertEqual(shares["funds_without_values"], ["F"])
        fraction = cr.ownership_overlap(PORTFOLIO, {"F": {"AAPL": 0.08}}, unit="share_of_outstanding")
        self.assertAlmostEqual(fraction["holdings"][0]["crowding_score"], 0.08)

    def test_without_shares_outstanding_only_the_overlap_is_known(self) -> None:
        result = cr.ownership_overlap(PORTFOLIO, {"F": {"AAPL": 1000.0, "GE": 10.0}})
        self.assertIsNone(result["weighted_ownership"])
        self.assertEqual(result["coverage"], 0.0)
        self.assertAlmostEqual(result["share_in_names_funds_hold"], 0.5)
        self.assertGreater(result["most_similar_funds"][0]["similarity"], 0.0)

    def test_short_positions_point_the_other_way(self) -> None:
        result = cr.ownership_overlap({"AAPL": -0.3, "MSFT": 0.1}, {"F": {"AAPL": 100.0}})
        self.assertLess(result["most_similar_funds"][0]["similarity"], 0.0)

    def test_ownership_above_outstanding_is_flagged(self) -> None:
        result = cr.ownership_overlap({"AAPL": 1.0}, {"F": {"AAPL": 0.7}, "G": {"AAPL": 0.6}},
                                      unit="share_of_outstanding")
        self.assertEqual(result["ownership_above_outstanding"], ["AAPL"])

    def test_bad_input_is_refused(self) -> None:
        bad = [
            dict(institutional_holdings={"F": {"AAPL": -1}}),
            dict(institutional_holdings={"F": {"AAPL": 1.5}}, unit="share_of_outstanding"),
            dict(institutional_holdings={"F": {"AAPL": 1}}, unit="percent"),
            dict(institutional_holdings={}),
            dict(institutional_holdings=FUNDS, top_n=0),
            dict(institutional_holdings=FUNDS, shares_outstanding={"AAPL": 0}),
        ]
        for arguments in bad:
            with self.assertRaises(ModellingInputError, msg=arguments):
                cr.ownership_overlap(PORTFOLIO, **arguments)
        with self.assertRaises(ModellingInputError):
            cr.ownership_overlap({"AAPL": 0.0}, FUNDS)


def _groups(seed: int = 7, days: int = 500) -> dict[str, list[float]]:
    """Three groups of three holdings sharing a group factor (correlation about 0.9) and two loners."""
    generator = random.Random(seed)
    columns = {}
    for group in "XYZ":
        factor = [generator.gauss(0, 0.01) for _ in range(days)]
        for member in range(3):
            columns[f"{group}{member}"] = [f + generator.gauss(0, 0.0033) for f in factor]
    for loner in ("L1", "L2"):
        columns[loner] = [generator.gauss(0, 0.01) for _ in range(days)]
    return columns


class CorrelationClusterTests(unittest.TestCase):
    def test_recovers_the_groups(self) -> None:
        weights = {"X0": 0.1, "X1": 0.1, "X2": 0.05, "Y0": 0.2, "Y1": -0.05, "Z0": 0.02, "L1": 0.3}
        result = cr.correlation_clusters(_groups(), threshold=0.7, weights=weights)
        groups = sorted(tuple(sorted(c["members"])) for c in result["clusters"])
        self.assertEqual(groups, [("L1",), ("L2",), ("X0", "X1", "X2"), ("Y0", "Y1", "Y2"), ("Z0", "Z1", "Z2")])
        self.assertEqual(result["multi_holding_clusters"], 3)
        by_first = {tuple(sorted(c["members"])): c for c in result["clusters"]}
        y = by_first[("Y0", "Y1", "Y2")]
        self.assertAlmostEqual(y["combined_weight"], 0.15)
        self.assertAlmostEqual(y["gross_weight"], 0.25)
        self.assertEqual(y["members"][0], "Y0")
        self.assertEqual(result["clusters"][0]["members"], ["L1"])           # the largest bet first
        self.assertGreater(y["average_correlation"], 0.85)
        self.assertEqual(len(result["merges"]), 6)
        self.assertTrue(all(m["equivalent_correlation"] >= 0.7 - 1e-12 for m in result["merges"]))
        json.dumps(result, allow_nan=False)

    def test_average_internal_correlation_is_the_mean_pairwise_correlation(self) -> None:
        columns = _groups()
        result = cr.correlation_clusters(columns, threshold=0.7)
        x = next(c for c in result["clusters"] if "X0" in c["members"])
        pairs = [statistics.correlation(columns[a], columns[b]) for a, b in (("X0", "X1"), ("X0", "X2"), ("X1", "X2"))]
        self.assertAlmostEqual(x["average_correlation"], statistics.fmean(pairs), places=12)

    def test_average_linkage_on_a_hand_made_distance_matrix(self) -> None:
        distance = [[0.0, 0.1, 0.5, 0.6],
                    [0.1, 0.0, 0.7, 0.8],
                    [0.5, 0.7, 0.0, 0.2],
                    [0.6, 0.8, 0.2, 0.0]]
        groups, merges = cr._average_linkage(distance, cut=0.64)
        self.assertEqual(sorted(groups), [[0, 1], [2, 3]])
        self.assertEqual([round(m["distance"], 12) for m in merges], [0.1, 0.2])
        groups, merges = cr._average_linkage(distance, cut=0.65)
        self.assertEqual(groups, [[0, 1, 2, 3]])
        self.assertAlmostEqual(merges[-1]["distance"], (0.5 + 0.6 + 0.7 + 0.8) / 4)   # mean of all cross pairs

    def test_threshold_ends(self) -> None:
        generator = random.Random(1)
        base = [generator.gauss(0, 1) for _ in range(60)]
        other = [generator.gauss(0, 1) for _ in range(60)]
        columns = [base, [2 * v + 1 for v in base], [-v for v in base], other]
        names = ["base", "scaled", "mirror", "other"]
        exact = cr.correlation_clusters(columns, names, threshold=1.0)
        self.assertEqual(sorted(len(c["members"]) for c in exact["clusters"]), [1, 1, 2])
        self.assertEqual(exact["distance_cut"], 0.0)
        everything = cr.correlation_clusters(columns, names, threshold=-1.0)
        self.assertEqual(everything["cluster_count"], 1)

    def test_a_holding_that_never_moves_stands_alone(self) -> None:
        columns = _groups()
        columns["CASH"] = [0.0] * 500
        result = cr.correlation_clusters(columns)
        self.assertEqual(result["not_moving"], ["CASH"])
        self.assertIn(["CASH"], [c["members"] for c in result["clusters"]])

    def test_bad_input_is_refused(self) -> None:
        columns = list(_groups().values())
        with self.assertRaises(ModellingInputError):
            cr.correlation_clusters(columns, ["a"])
        with self.assertRaises(ModellingInputError):
            cr.correlation_clusters(columns[:2], ["a", "a"])
        with self.assertRaises(ModellingInputError):
            cr.correlation_clusters(_groups(), threshold=1.5)
        with self.assertRaises(ModellingInputError):
            cr.correlation_clusters({"a": [0.01] * 10, "b": [0.02] * 10})
        with self.assertRaises(ModellingInputError):
            cr.correlation_clusters({"a": [0.01, None] * 20, "b": [0.02] * 40})

    def test_sixty_holdings_ten_years_runs_quickly(self) -> None:
        generator = random.Random(9)
        market = [generator.gauss(0, 0.01) for _ in range(2520)]
        columns = {f"H{i}": [(i % 3) * m + generator.gauss(0, 0.01) for m in market] for i in range(60)}
        started = time.perf_counter()
        cr.correlation_clusters(columns, threshold=0.5)
        self.assertLess(time.perf_counter() - started, 5.0)


if __name__ == "__main__":
    unittest.main()
