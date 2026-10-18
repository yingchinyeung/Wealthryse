from __future__ import annotations

import json
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import attribution as at  # noqa: E402

PORTFOLIO_WEIGHTS = {"A": 0.5, "B": 0.3, "C": 0.2}
PORTFOLIO_RETURNS = {"A": 0.04, "B": 0.01, "C": -0.02}
BENCHMARK_WEIGHTS = {"A": 0.4, "B": 0.4, "C": 0.2}
BENCHMARK_RETURNS = {"A": 0.03, "B": 0.02, "C": -0.01}


def _row(result: dict, segment: str) -> dict:
    return next(row for row in result["segments"] if row["segment"] == segment)


def _random_weights(generator: random.Random, keys: list[str]) -> dict[str, float]:
    raw = [generator.random() for _ in keys]
    return {key: value / sum(raw) for key, value in zip(keys, raw)}


def _random_month(generator: random.Random, segments: int = 11) -> dict:
    keys = [f"S{i}" for i in range(segments)]
    return at.brinson_fachler(
        _random_weights(generator, keys), {k: generator.gauss(0.008, 0.05) for k in keys},
        _random_weights(generator, keys), {k: generator.gauss(0.008, 0.05) for k in keys},
    )


def _cumulative_excess(periods: list[dict]) -> float:
    portfolio = math.prod(1.0 + p["portfolio_return"] for p in periods) - 1.0
    benchmark = math.prod(1.0 + p["benchmark_return"] for p in periods) - 1.0
    return portfolio - benchmark


class BrinsonFachlerTests(unittest.TestCase):
    def test_hand_computed_example(self) -> None:
        result = at.brinson_fachler(PORTFOLIO_WEIGHTS, PORTFOLIO_RETURNS, BENCHMARK_WEIGHTS, BENCHMARK_RETURNS)
        # Rp = .5(.04) + .3(.01) + .2(-.02) = .019; Rb = .4(.03) + .4(.02) + .2(-.01) = .018.
        self.assertAlmostEqual(result["portfolio_return"], 0.019, places=15)
        self.assertAlmostEqual(result["benchmark_return"], 0.018, places=15)
        expected = {
            "A": (0.1 * (0.03 - 0.018), 0.4 * 0.01, 0.1 * 0.01),
            "B": (-0.1 * (0.02 - 0.018), 0.4 * -0.01, -0.1 * -0.01),
            "C": (0.0, 0.2 * -0.01, 0.0),
        }
        for segment, (allocation, selection, interaction) in expected.items():
            row = _row(result, segment)
            self.assertAlmostEqual(row["allocation"], allocation, places=15)
            self.assertAlmostEqual(row["selection"], selection, places=15)
            self.assertAlmostEqual(row["interaction"], interaction, places=15)
        self.assertAlmostEqual(result["totals"]["allocation"], 0.001, places=15)
        self.assertAlmostEqual(result["totals"]["selection"], -0.002, places=15)
        self.assertAlmostEqual(result["totals"]["interaction"], 0.002, places=15)
        self.assertAlmostEqual(result["totals"]["total"], result["excess_return"], places=16)
        self.assertEqual(result["notes"], [])
        json.dumps(result)

    def test_segment_not_in_benchmark_uses_benchmark_total(self) -> None:
        weights = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
        returns = {**PORTFOLIO_RETURNS, "D": 0.05}
        result = at.brinson_fachler(weights, returns, BENCHMARK_WEIGHTS, BENCHMARK_RETURNS)
        row = _row(result, "D")
        self.assertTrue(row["benchmark_return_assumed"])
        self.assertAlmostEqual(row["benchmark_return"], 0.018, places=15)
        self.assertEqual((row["allocation"], row["selection"]), (0.0, 0.0))
        self.assertAlmostEqual(row["interaction"], 0.1 * (0.05 - 0.018), places=15)
        self.assertEqual(result["segments_not_in_benchmark"], ["D"])
        self.assertIn("D is not in the benchmark", result["notes"][0])
        self.assertAlmostEqual(result["totals"]["total"], result["excess_return"], places=16)

    def test_proxy_return_for_a_segment_outside_the_benchmark_is_used(self) -> None:
        weights = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
        returns = {**PORTFOLIO_RETURNS, "D": 0.05}
        result = at.brinson_fachler(weights, returns, BENCHMARK_WEIGHTS, {**BENCHMARK_RETURNS, "D": 0.06})
        row = _row(result, "D")
        self.assertFalse(row["benchmark_return_assumed"])
        self.assertAlmostEqual(row["allocation"], 0.1 * (0.06 - 0.018), places=15)
        self.assertAlmostEqual(row["interaction"], 0.1 * (0.05 - 0.06), places=15)
        self.assertEqual(result["notes"], [])
        self.assertAlmostEqual(result["totals"]["total"], result["excess_return"], places=16)

    def test_segment_not_held_has_only_allocation(self) -> None:
        result = at.brinson_fachler({"A": 0.6, "B": 0.4}, {"A": 0.04, "B": 0.01}, BENCHMARK_WEIGHTS, BENCHMARK_RETURNS)
        row = _row(result, "C")
        self.assertEqual((row["selection"], row["interaction"]), (0.0, 0.0))
        self.assertAlmostEqual(row["allocation"], -0.2 * (-0.01 - 0.018), places=15)
        self.assertEqual(result["segments_not_held"], ["C"])
        self.assertAlmostEqual(result["totals"]["total"], result["excess_return"], places=16)

    def test_effects_add_up_for_random_portfolios(self) -> None:
        generator = random.Random(11)
        for _ in range(50):
            result = _random_month(generator, segments=generator.randint(1, 15))
            self.assertAlmostEqual(result["totals"]["total"], result["excess_return"], delta=1e-15)

    def test_rounded_weights_are_rescaled(self) -> None:
        result = at.brinson_fachler({"A": 0.50004, "B": 0.5}, {"A": 0.01, "B": 0.02}, {"A": 1.0}, {"A": 0.01})
        self.assertAlmostEqual(sum(r["portfolio_weight"] for r in result["segments"]), 1.0, places=15)
        self.assertAlmostEqual(result["totals"]["total"], result["excess_return"], places=16)

    def test_bad_inputs_are_refused(self) -> None:
        cases = [
            ({"A": 0.5, "B": 0.3}, PORTFOLIO_RETURNS, BENCHMARK_WEIGHTS, BENCHMARK_RETURNS),        # 80%
            (PORTFOLIO_WEIGHTS, {**PORTFOLIO_RETURNS, "A": float("nan")}, BENCHMARK_WEIGHTS, BENCHMARK_RETURNS),
            (PORTFOLIO_WEIGHTS, {"A": 0.04, "B": 0.01}, BENCHMARK_WEIGHTS, BENCHMARK_RETURNS),   # no return for C
            (PORTFOLIO_WEIGHTS, PORTFOLIO_RETURNS, BENCHMARK_WEIGHTS, {"A": 0.03, "B": None, "C": 0.0}),
            ({"A": None, "B": 0.5, "C": 0.5}, PORTFOLIO_RETURNS, BENCHMARK_WEIGHTS, BENCHMARK_RETURNS),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ModellingInputError):
                at.brinson_fachler(*case)


class LinkingTests(unittest.TestCase):
    LINKERS = (at.link_carino, at.link_menchero)

    def test_linked_effects_add_up_to_compounded_excess(self) -> None:
        generator = random.Random(5)
        months = [_random_month(generator) for _ in range(36)]
        target = _cumulative_excess(months)
        for link in self.LINKERS:
            with self.subTest(link=link.__name__):
                result = link(months)
                self.assertAlmostEqual(result["excess_return"], target, delta=1e-15)
                self.assertAlmostEqual(result["total"], target, delta=1e-12)
                self.assertAlmostEqual(sum(result["totals"].values()), target, delta=1e-12)
                self.assertEqual(set(result["totals"]), {"allocation", "selection", "interaction"})
                self.assertEqual(set(result["effects"]["S3"]), {"allocation", "selection", "interaction"})
                json.dumps(result)

    def test_carino_hand_example(self) -> None:
        periods = [
            {"portfolio_return": 0.10, "benchmark_return": 0.05, "effects": {"X": {"allocation": 0.02, "selection": 0.03}}},
            {"portfolio_return": -0.02, "benchmark_return": 0.01,
             "effects": {"X": {"allocation": -0.01, "selection": -0.02}}},
        ]
        k1 = (math.log(1.10) - math.log(1.05)) / 0.05
        k2 = (math.log(0.98) - math.log(1.01)) / -0.03
        rp, rb = 1.10 * 0.98 - 1.0, 1.05 * 1.01 - 1.0
        big_k = (math.log(1.0 + rp) - math.log(1.0 + rb)) / (rp - rb)
        result = at.link_carino(periods)
        self.assertAlmostEqual(result["coefficients"][0]["coefficient"], k1 / big_k, places=12)
        self.assertAlmostEqual(result["coefficients"][1]["coefficient"], k2 / big_k, places=12)
        self.assertAlmostEqual(result["effects"]["X"]["allocation"], (0.02 * k1 - 0.01 * k2) / big_k, places=12)
        self.assertAlmostEqual(result["total"], rp - rb, delta=1e-15)

    def test_menchero_hand_example(self) -> None:
        periods = [
            {"portfolio_return": 0.10, "benchmark_return": 0.05, "effects": {"allocation": 0.02, "selection": 0.03}},
            {"portfolio_return": -0.02, "benchmark_return": 0.01, "effects": {"allocation": -0.01, "selection": -0.02}},
        ]
        rp, rb = 1.10 * 0.98 - 1.0, 1.05 * 1.01 - 1.0
        m = ((rp - rb) / 2.0) / (math.sqrt(1.0 + rp) - math.sqrt(1.0 + rb))
        d = [0.05, -0.03]
        alphas = [((rp - rb) - m * sum(d)) / sum(v * v for v in d) * v for v in d]
        result = at.link_menchero(periods)
        for row, alpha in zip(result["coefficients"], alphas):
            self.assertAlmostEqual(row["coefficient"], m + alpha, places=12)
        self.assertAlmostEqual(result["effects"]["selection"], 0.03 * (m + alphas[0]) - 0.02 * (m + alphas[1]), places=12)
        self.assertAlmostEqual(result["total"], rp - rb, delta=1e-15)

    def test_single_period_is_unchanged(self) -> None:
        month = at.brinson_fachler(PORTFOLIO_WEIGHTS, PORTFOLIO_RETURNS, BENCHMARK_WEIGHTS, BENCHMARK_RETURNS)
        for link in self.LINKERS:
            result = link([month])
            self.assertAlmostEqual(result["coefficients"][0]["coefficient"], 1.0, places=12)
            self.assertAlmostEqual(result["effects"]["A"]["selection"], month["effects"]["A"]["selection"], places=15)

    def test_period_with_equal_returns_uses_the_limit(self) -> None:
        generator = random.Random(9)
        months = [_random_month(generator) for _ in range(5)]
        flat = {"portfolio_return": 0.01, "benchmark_return": 0.01, "period": "2026-03",
                "effects": {"S0": {"allocation": 0.004, "selection": -0.003, "interaction": -0.001}}}
        periods = months[:2] + [flat] + months[2:]
        target = _cumulative_excess(periods)
        carino = at.link_carino(periods)
        rp = math.prod(1.0 + p["portfolio_return"] for p in periods) - 1.0
        rb = math.prod(1.0 + p["benchmark_return"] for p in periods) - 1.0
        big_k = (math.log1p(rp) - math.log1p(rb)) / (rp - rb)
        self.assertAlmostEqual(carino["coefficients"][2]["coefficient"], (1.0 / 1.01) / big_k, places=12)
        self.assertEqual(carino["coefficients"][2]["period"], "2026-03")
        for link in self.LINKERS:
            self.assertAlmostEqual(link(periods)["total"], target, delta=1e-12)

    def test_equal_compounded_returns(self) -> None:
        periods = [
            {"portfolio_return": 0.1, "benchmark_return": 0.0, "effects": {"allocation": 0.06, "selection": 0.04}},
            {"portfolio_return": 0.0, "benchmark_return": 0.1, "effects": {"allocation": -0.02, "selection": -0.08}},
        ]
        for link in self.LINKERS:
            result = link(periods)
            self.assertEqual(result["excess_return"], 0.0)
            self.assertAlmostEqual(result["total"], 0.0, delta=1e-15)
        self.assertAlmostEqual(at.link_menchero(periods)["coefficients"][0]["coefficient"]
                               + at.link_menchero(periods)["coefficients"][1]["coefficient"],
                               2.0 * math.sqrt(1.1), places=12)

    def test_all_periods_with_zero_excess(self) -> None:
        periods = [{"portfolio_return": r, "benchmark_return": r, "effects": {"allocation": e, "selection": -e}}
                   for r, e in ((0.02, 0.01), (-0.01, -0.005), (0.03, 0.002))]
        for link in self.LINKERS:
            result = link(periods)
            self.assertAlmostEqual(result["total"], 0.0, delta=1e-15)
            self.assertTrue(all(math.isfinite(c["coefficient"]) for c in result["coefficients"]))

    def test_bad_periods_are_refused(self) -> None:
        bad = [
            [],
            [{"portfolio_return": 0.02, "benchmark_return": 0.01, "effects": {"allocation": 0.002}}],  # gap
            [{"portfolio_return": -1.0, "benchmark_return": 0.01, "effects": {"allocation": -1.01}}],
            [{"portfolio_return": None, "benchmark_return": 0.01, "effects": {}}],
            [{"portfolio_return": 0.02, "benchmark_return": 0.01, "effects": {"X": 0.01}},
             {"portfolio_return": 0.02, "benchmark_return": 0.01, "effects": {"X": {"allocation": 0.01}}}],
        ]
        for periods in bad:
            for link in self.LINKERS:
                with self.subTest(periods=periods, link=link.__name__), self.assertRaises(ModellingInputError):
                    link(periods)


class CurrencyAttributionTests(unittest.TestCase):
    USD = 0.004  # USD rose 0.4% against HKD
    SEGMENTS = {"HK equities": "HKD", "US equities": "usd"}

    def _example(self, **kwargs) -> dict:
        return at.currency_attribution(
            {"HK equities": 0.4, "US equities": 0.6},
            {"HK equities": 0.02, "US equities": 1.03 * (1.0 + self.USD) - 1.0},
            {"HK equities": 0.5, "US equities": 0.5},
            {"HK equities": 0.015, "US equities": 1.025 * (1.0 + self.USD) - 1.0},
            self.SEGMENTS, {"USD": self.USD}, base_currency="HKD", **kwargs,
        )

    def test_hand_computed_unhedged_example(self) -> None:
        result = self._example()
        # Local: HK .02 vs .015, US .03 vs .025; Rp_local = .026, Rb_local = .02.
        self.assertAlmostEqual(result["portfolio_local_return"], 0.026, places=14)
        self.assertAlmostEqual(result["benchmark_local_return"], 0.02, places=14)
        us = _row(result, "US equities")
        self.assertAlmostEqual(us["portfolio_local_return"], 0.03, places=14)
        self.assertAlmostEqual(us["allocation"], 0.1 * 0.005, places=14)
        self.assertAlmostEqual(us["selection"], 0.5 * 0.005, places=14)
        self.assertAlmostEqual(us["currency_compounding"], (0.6 * 0.03 - 0.5 * 0.025) * self.USD, places=14)
        totals = result["totals"]
        self.assertAlmostEqual(totals["allocation"], 0.001, places=14)
        self.assertAlmostEqual(totals["selection"], 0.005, places=14)
        self.assertAlmostEqual(totals["interaction"], 0.0, places=14)
        self.assertAlmostEqual(totals["currency_allocation"], 0.1 * self.USD, places=15)
        self.assertAlmostEqual(totals["currency_compounding"], 0.000022, places=15)
        # Base: Rp = .4(.02) + .6(.03412) = .028472; Rb = .5(.015) + .5(.0291) = .02205.
        self.assertAlmostEqual(result["portfolio_return"], 0.028472, places=14)
        self.assertAlmostEqual(result["benchmark_return"], 0.02205, places=14)
        self.assertAlmostEqual(result["excess_return"], 0.006422, places=14)
        self.assertAlmostEqual(totals["total"], result["excess_return"], delta=1e-15)
        hkd = next(c for c in result["currencies"] if c["currency"] == "HKD")
        self.assertEqual(hkd["currency_allocation"], 0.0)
        json.dumps(result)

    def test_hedged_currency_weights(self) -> None:
        result = self._example(portfolio_currency_weights={"HKD": 0.7, "USD": 0.3})
        # Half the USD hedged back to HKD: the overlay earns (0.3 - 0.6) x 0.4%.
        self.assertAlmostEqual(result["portfolio_return"], 0.028472 - 0.3 * self.USD, places=14)
        self.assertAlmostEqual(result["totals"]["currency_allocation"], (0.3 - 0.5) * self.USD, places=15)
        self.assertAlmostEqual(result["totals"]["total"], result["excess_return"], delta=1e-15)
        self.assertTrue(result["notes"])

    def test_effects_add_up_for_random_multi_currency_portfolios(self) -> None:
        generator = random.Random(21)
        currencies = {"HKD": 0.0, "USD": 0.003, "CNH": -0.01, "JPY": 0.02}
        for trial in range(30):
            segments = {f"M{i}": generator.choice(list(currencies)) for i in range(8)}
            held = [s for s in segments if generator.random() < 0.8] or ["M0"]
            benchmark = [s for s in segments if generator.random() < 0.8 and s != held[0]] or ["M1"]
            hedge = _random_weights(generator, list(currencies)) if trial % 3 == 0 else None
            result = at.currency_attribution(
                _random_weights(generator, held), {s: generator.gauss(0.01, 0.04) for s in held},
                _random_weights(generator, benchmark), {s: generator.gauss(0.01, 0.04) for s in benchmark},
                segments, currencies, base_currency="HKD", portfolio_currency_weights=hedge,
            )
            self.assertAlmostEqual(result["totals"]["total"], result["excess_return"], delta=1e-15)

    def test_months_link_in_the_base_currency(self) -> None:
        generator = random.Random(4)
        months = []
        for _ in range(12):
            usd = generator.gauss(0.0, 0.002)
            months.append(at.currency_attribution(
                {"HK": 0.45, "US": 0.55}, {"HK": generator.gauss(0.01, 0.05), "US": generator.gauss(0.01, 0.05)},
                {"HK": 0.5, "US": 0.5}, {"HK": generator.gauss(0.01, 0.05), "US": generator.gauss(0.01, 0.05)},
                {"HK": "HKD", "US": "USD"}, {"USD": usd}, base_currency="HKD",
            ))
        target = _cumulative_excess(months)
        for link in (at.link_carino, at.link_menchero):
            result = link(months)
            self.assertAlmostEqual(result["total"], target, delta=1e-12)
            self.assertIn("currency_allocation", result["effects"]["currencies"]["USD"])
            self.assertEqual(set(result["totals"]), {"allocation", "selection", "interaction",
                                                     "currency_allocation", "currency_compounding"})

    def test_bad_currency_inputs_are_refused(self) -> None:
        weights, returns = {"HK": 0.5, "US": 0.5}, {"HK": 0.01, "US": 0.02}
        both = {"HK": "HKD", "US": "USD"}
        cases = [
            ({"HK": "HKD"}, {"USD": 0.0}, None),                       # US has no currency
            (both, {}, None),                                          # no USD return
            (both, {"USD": 0.0, "HKD": 0.01}, None),                   # the base currency moved
            (both, {"USD": 0.0}, {"HKD": 0.5, "EUR": 0.5}),            # hedged into a currency with no return
        ]
        for currencies, moves, hedge in cases:
            with self.subTest(currencies=currencies, moves=moves), self.assertRaises(ModellingInputError):
                at.currency_attribution(weights, returns, {"HK": 1.0}, {"HK": 0.01}, currencies, moves,
                                        base_currency="HKD", portfolio_currency_weights=hedge)


def _factors(count: int, seed: int) -> dict[str, list[float]]:
    generator = random.Random(seed)
    return {
        "Mkt-RF": [generator.gauss(0.0004, 0.01) for _ in range(count)],
        "SMB": [generator.gauss(0.0, 0.005) for _ in range(count)],
        "HML": [generator.gauss(0.0001, 0.005) for _ in range(count)],
    }


class FactorAttributionTests(unittest.TestCase):
    def test_recovers_exposures_and_parts_add_up(self) -> None:
        factors = _factors(1000, 2)
        generator = random.Random(3)
        y = [0.0002 + 1.2 * m - 0.5 * s + generator.gauss(0, 0.002)
             for m, s in zip(factors["Mkt-RF"], factors["SMB"])]
        result = at.factor_attribution(y, factors)
        betas = {x["factor"]: x["beta"] for x in result["exposures"]}
        self.assertAlmostEqual(betas["Mkt-RF"], 1.2, delta=0.02)
        self.assertAlmostEqual(betas["SMB"], -0.5, delta=0.04)
        self.assertAlmostEqual(betas["HML"], 0.0, delta=0.04)
        self.assertAlmostEqual(result["alpha"]["per_period"], 0.0002, delta=0.00015)
        self.assertGreater(result["r_squared"], 0.95)
        for row, value in zip(result["periods"], y):
            parts = sum(row["factors"].values()) + row["alpha"] + row["residual"]
            self.assertAlmostEqual(parts, value, delta=1e-15)
        compounded = math.prod(1.0 + v for v in y) - 1.0
        self.assertAlmostEqual(result["cumulative_excess_return"], compounded, delta=1e-15)
        cumulative = (result["factors_cumulative"] + result["alpha"]["cumulative_contribution"]
                      + result["residual"]["cumulative_contribution"])
        self.assertAlmostEqual(cumulative, compounded, delta=1e-12)
        annual = sum(x["contribution_annual"] for x in result["exposures"]) + result["alpha"]["annual"]
        self.assertAlmostEqual(annual, result["average_excess_return_annual"], delta=1e-12)
        json.dumps(result)

    def test_exact_fit_has_no_residual(self) -> None:
        factors = _factors(60, 8)
        y = [0.001 + 0.8 * m + 0.3 * h for m, h in zip(factors["Mkt-RF"], factors["HML"])]
        result = at.factor_attribution(y, factors, dates=[f"2026-01-{i % 28 + 1:02d}" for i in range(60)])
        self.assertAlmostEqual(result["r_squared"], 1.0, places=10)
        self.assertAlmostEqual(result["residual"]["cumulative_contribution"], 0.0, delta=1e-12)
        self.assertAlmostEqual(result["alpha"]["annual"], 0.001 * 252, delta=1e-9)
        self.assertEqual(result["window_start"], "2026-01-01")

    def test_bad_inputs_are_refused(self) -> None:
        factors = _factors(40, 1)
        y = [0.001] * 39 + [0.002]
        cases = [
            (y[:10], {k: v[:10] for k, v in factors.items()}),                 # too short
            (y, {**factors, "SMB": factors["SMB"][:-1]}),                        # misaligned
            (y, {**factors, "HML": [0.001] * 40}),                               # constant factor
            ([None] + y[1:], factors),
            (y, {}),
        ]
        for case in cases:
            with self.subTest(case=str(case)[:60]), self.assertRaises(ModellingInputError):
                at.factor_attribution(*case)


def _styles(count: int, seed: int, correlated: bool = False) -> dict[str, list[float]]:
    generator = random.Random(seed)
    common = [generator.gauss(0.0003, 0.01) for _ in range(count)]
    return {
        name: [(c if correlated else 0.0) + generator.gauss(0.0002, 0.004 if correlated else 0.01) for c in common]
        for name in ("Large value", "Large growth", "Small cap", "Bonds")
    }


def _objective(weights, styles, y) -> float:
    difference = [v - sum(w * styles[name][t] for name, w in weights.items()) for t, v in enumerate(y)]
    mean = sum(difference) / len(difference)
    return sum((d - mean) ** 2 for d in difference)


class StyleAnalysisTests(unittest.TestCase):
    def test_recovers_a_known_mix(self) -> None:
        styles = _styles(800, 4, correlated=True)
        generator = random.Random(6)
        y = [0.6 * a + 0.4 * b + generator.gauss(0.0001, 0.001)
             for a, b in zip(styles["Large value"], styles["Large growth"])]
        result = at.style_analysis(y, styles)
        weights = result["weights"]
        self.assertAlmostEqual(weights["Large value"], 0.6, delta=0.03)
        self.assertAlmostEqual(weights["Large growth"], 0.4, delta=0.03)
        self.assertLess(weights["Small cap"] + weights["Bonds"], 0.04)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=12)
        self.assertTrue(all(w >= 0 for w in weights.values()))
        self.assertGreater(result["r_squared"], 0.95)
        self.assertAlmostEqual(result["selection_return_annual"], 0.0001 * 252, delta=0.02)
        self.assertIsNone(result["rolling"])
        json.dumps(result)

    def test_solution_is_optimal(self) -> None:
        styles = _styles(300, 12)
        generator = random.Random(13)
        y = [0.5 * a - 0.2 * b + generator.gauss(0, 0.006) for a, b in zip(styles["Small cap"], styles["Bonds"])]
        result = at.style_analysis(y, styles)
        best = _objective(result["weights"], styles, y)
        for _ in range(300):
            raw = [generator.random() ** 3 for _ in styles]
            other = {name: value / sum(raw) for name, value in zip(styles, raw)}
            self.assertLessEqual(best, _objective(other, styles, y) * (1 + 1e-9))
        # KKT: every held index has the same gradient, and no unheld index has a lower one.
        names = list(styles)
        n = len(y)
        means = {k: sum(v) / n for k, v in styles.items()}
        mean_y = sum(y) / n

        def cov(a, ma, b, mb):
            return sum((p - ma) * (q - mb) for p, q in zip(a, b)) / (n - 1)

        gradient = {i: 2 * (sum(result["weights"][j] * cov(styles[i], means[i], styles[j], means[j]) for j in names)
                            - cov(styles[i], means[i], y, mean_y)) for i in names}
        held = [gradient[i] for i in names if result["weights"][i] > 1e-6]
        scale = max(abs(g) for g in gradient.values())
        self.assertLess(max(held) - min(held), 1e-6 * scale)
        for name in names:
            if result["weights"][name] <= 1e-6:
                self.assertGreaterEqual(gradient[name], min(held) - 1e-6 * scale)

    def test_rolling_window_follows_a_change_of_style(self) -> None:
        styles = _styles(400, 7)
        y = styles["Large value"][:200] + styles["Bonds"][200:]
        dates = [f"d{i:03d}" for i in range(400)]
        result = at.style_analysis(y, styles, window=100, step=25, dates=dates)
        fits = result["rolling"]["fits"]
        self.assertEqual(result["rolling"]["step"], 25)
        self.assertEqual((fits[0]["start"], fits[0]["end"]), ("d000", "d099"))
        self.assertEqual(fits[-1]["end"], "d399")
        self.assertGreater(fits[0]["weights"]["Large value"], 0.99)
        self.assertGreater(fits[-1]["weights"]["Bonds"], 0.99)
        self.assertGreater(fits[0]["r_squared"], 0.99)
        for fit in fits:
            self.assertAlmostEqual(sum(fit["weights"].values()), 1.0, places=12)
        default = at.style_analysis(y, styles, window=100)
        self.assertEqual(default["rolling"]["step"], 5)
        self.assertEqual(default["rolling"]["fits"][-1]["end"], 400)  # periods count from 1 without dates

    def test_single_index_and_cash(self) -> None:
        styles = _styles(50, 3)
        single = at.style_analysis(styles["Bonds"], {"Bonds": styles["Bonds"]})
        self.assertEqual(single["weights"], {"Bonds": 1.0})
        self.assertAlmostEqual(single["r_squared"], 1.0, places=12)
        half = [0.5 * v + 0.5 * 0.0001 for v in styles["Large value"]]
        mixed = at.style_analysis(half, {"Large value": styles["Large value"], "Cash": [0.0001] * 50})
        self.assertAlmostEqual(mixed["weights"]["Large value"], 0.5, delta=1e-6)
        self.assertAlmostEqual(mixed["weights"]["Cash"], 0.5, delta=1e-6)

    def test_bad_inputs_are_refused(self) -> None:
        styles = _styles(40, 1)
        y = styles["Bonds"]
        cases = [
            ((y[:8], {k: v[:8] for k, v in styles.items()}), {}),
            ((y, {"Cash": [0.0] * 40}), {}),
            ((y, styles), {"window": 5}),
            ((y, styles), {"window": 41}),
            ((y + [float("inf")], {k: v + [0.0] for k, v in styles.items()}), {}),
        ]
        for args, kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ModellingInputError):
                at.style_analysis(*args, **kwargs)


if __name__ == "__main__":
    unittest.main()
