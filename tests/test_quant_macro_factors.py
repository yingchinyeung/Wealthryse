from __future__ import annotations

import math
import random
import statistics
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import macro_factors as mf  # noqa: E402


def _weekdays(count: int, start: date = date(2022, 1, 3)) -> list[str]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _ar1(count: int, rho: float, scale: float, generator: random.Random) -> list[float]:
    values, value = [], 0.0
    for _ in range(count):
        value = rho * value + generator.gauss(0, scale)
        values.append(value)
    return values


def _brute_newey_west(y, x, lags):
    """Slope standard error of y = α + βx + u by explicit sums over t and lags."""
    n = len(y)
    mx, my = statistics.fmean(x), statistics.fmean(y)
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sum((a - mx) ** 2 for a in x)
    alpha = my - beta * mx
    u = [b - alpha - beta * a for a, b in zip(x, y)]
    rows = [(1.0, a) for a in x]
    s = [[0.0, 0.0], [0.0, 0.0]]
    for lag in range(lags + 1):
        weight = 1.0 if lag == 0 else 1.0 - lag / (lags + 1)
        for t in range(lag, n):
            for i in range(2):
                for j in range(2):
                    term = u[t] * u[t - lag] * rows[t][i] * rows[t - lag][j]
                    s[i][j] += weight * term if lag == 0 else weight * (term + u[t] * u[t - lag] * rows[t - lag][i] * rows[t][j])
    sxx = [[n, sum(x)], [sum(x), sum(a * a for a in x)]]
    det = sxx[0][0] * sxx[1][1] - sxx[0][1] ** 2
    inv = [[sxx[1][1] / det, -sxx[0][1] / det], [-sxx[0][1] / det, sxx[0][0] / det]]
    v = [[sum(inv[i][a] * s[a][b] * inv[b][j] for a in range(2) for b in range(2)) for j in range(2)] for i in range(2)]
    return math.sqrt(v[1][1] * n / (n - 2))


class AlignTests(unittest.TestCase):
    def test_hand_example_with_a_holiday_and_a_missing_level(self) -> None:
        d1, d2, d3, d4, d5 = _weekdays(5)
        portfolio = {d1: 0.01, d2: -0.02, d3: 0.03, d4: 0.01, d5: -0.01}
        levels = {
            "US10Y": {d1: 4.00, d2: 4.05, d4: 3.95, d5: None},   # US holiday on d3; d5 not published yet
            "USDCNH": {d1: 7.10, d2: 7.12, d3: 7.15, d4: 7.13, d5: 7.20},
        }
        result = mf.align_series(portfolio, levels, transforms={"US10Y": "diff_bp", "USDCNH": "log_return"})
        self.assertEqual(result["dates"], [d2, d4])
        self.assertAlmostEqual(result["portfolio"][0], -0.02, places=15)
        self.assertAlmostEqual(result["portfolio"][1], 1.03 * 1.01 - 1, places=15)
        self.assertAlmostEqual(result["macro_changes"]["US10Y"][0], 5.0, places=10)
        self.assertAlmostEqual(result["macro_changes"]["US10Y"][1], -10.0, places=10)
        self.assertAlmostEqual(result["macro_changes"]["USDCNH"][1], math.log(7.13 / 7.12), places=15)
        self.assertEqual(result["multi_session_observations"], 1)
        self.assertEqual(result["skipped_levels"], {"US10Y": 1, "USDCNH": 0})

    def test_date_objects_and_weekly_levels(self) -> None:
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(15)]
        portfolio = {day: 0.001 for day in days}
        fridays = {day: 100.0 + i for i, day in enumerate(days) if day.weekday() == 4}
        result = mf.align_series(portfolio, {"Oil": fridays}, transforms={"Oil": "log_return"})
        self.assertEqual(result["dates"], ["2024-01-12"])
        self.assertAlmostEqual(result["portfolio"][0], 1.001 ** 7 - 1, places=14)
        self.assertAlmostEqual(result["macro_changes"]["Oil"][0], math.log(111.0 / 104.0), places=14)

    def test_bad_input(self) -> None:
        d1, d2, d3 = _weekdays(3)
        portfolio = {d1: 0.01, d2: 0.0, d3: 0.01}
        good = {d1: 4.0, d2: 4.1, d3: 4.2}
        cases = [
            (portfolio, {"US10Y": good}, {"US10Y": "pct"}),
            (portfolio, {"US10Y": good}, {}),
            (portfolio, {"Oil": {d1: 70.0, d2: -1.0, d3: 71.0}}, {"Oil": "log_return"}),
            ({d1: 0.01, d2: None, d3: 0.01}, {"US10Y": good}, {"US10Y": "diff_bp"}),
            (portfolio, {"US10Y": {"2021-01-04": 1.0}}, {"US10Y": "diff_bp"}),
            ({"yesterday": 0.01}, {"US10Y": good}, {"US10Y": "diff_bp"}),
            (portfolio, {}, {}),
        ]
        for returns, levels, transforms in cases:
            with self.assertRaises(ModellingInputError):
                mf.align_series(returns, levels, transforms=transforms)


class ExposureTests(unittest.TestCase):
    def test_recovers_known_betas(self) -> None:
        generator = random.Random(7)
        n = 1500
        changes = {
            "US10Y": [generator.gauss(0, 6.0) for _ in range(n)],
            "USDCNH": [generator.gauss(0, 0.003) for _ in range(n)],
            "Oil": [generator.gauss(0, 0.02) for _ in range(n)],
        }
        y = [0.0003 - 0.0005 * a + 0.4 * b + 0.05 * c + generator.gauss(0, 0.006)
             for a, b, c in zip(changes["US10Y"], changes["USDCNH"], changes["Oil"])]
        dates = _weekdays(n)
        result = mf.macro_exposures(y, changes, dates=dates,
                                    transforms={"US10Y": "diff_bp", "USDCNH": "log_return", "Oil": "log_return"})
        betas = {row["factor"]: row for row in result["exposures"]}
        for name, true in (("US10Y", -0.0005), ("USDCNH", 0.4), ("Oil", 0.05)):
            self.assertAlmostEqual(betas[name]["beta"], true, delta=3 * betas[name]["standard_error"])
            self.assertTrue(betas[name]["significant"])
        self.assertEqual(betas["US10Y"]["unit"], "portfolio return per 1bp")
        self.assertEqual(result["window"], {"start": dates[0], "end": dates[-1], "observations": n})
        self.assertGreater(result["r_squared"], 0.2)
        self.assertEqual(len(result["beta_covariance"]), 3)
        self.assertNotIn("nan", repr(result).lower())

    def test_newey_west_matches_the_direct_formula(self) -> None:
        generator = random.Random(3)
        x = _ar1(80, 0.6, 0.01, generator)
        y = [0.5 * a + e for a, e in zip(x, _ar1(80, 0.5, 0.004, generator))]
        for lags in (0, 3):
            result = mf.macro_exposures(y, {"X": x}, lags=lags)
            self.assertAlmostEqual(result["exposures"][0]["standard_error"], _brute_newey_west(y, x, lags), places=12)

    def test_newey_west_widens_for_autocorrelated_residuals(self) -> None:
        generator = random.Random(5)
        n = 2000
        x = _ar1(n, 0.8, 1.0, generator)
        y = [0.2 * a + e for a, e in zip(x, _ar1(n, 0.8, 1.0, generator))]
        persistent = mf.macro_exposures(y, {"X": x}, lags=10)["exposures"][0]
        self.assertGreater(persistent["ols_t_statistic"] / persistent["t_statistic"], 1.5)
        x = [generator.gauss(0, 1) for _ in range(n)]
        y = [0.2 * a + generator.gauss(0, 1) for a in x]
        independent = mf.macro_exposures(y, {"X": x}, lags=10)["exposures"][0]
        self.assertAlmostEqual(independent["ols_t_statistic"] / independent["t_statistic"], 1.0, delta=0.15)

    def test_bad_input(self) -> None:
        generator = random.Random(1)
        x = [generator.gauss(0, 1) for _ in range(100)]
        y = [0.1 * a + generator.gauss(0, 1) for a in x]
        cases = [
            lambda: mf.macro_exposures(y[:40], {"X": x[:40]}),
            lambda: mf.macro_exposures(y, {"X": x[:-1]}),
            lambda: mf.macro_exposures(y, {"Flat": [0.0] * 100}),
            lambda: mf.macro_exposures(y, {"X": x, "Same": [2 * a for a in x]}),
            lambda: mf.macro_exposures(y, {"X": x}, lags=-1),
            lambda: mf.macro_exposures(y, {"X": x}, lags=60),
            lambda: mf.macro_exposures(y[:-1] + [math.nan], {"X": x}),
            lambda: mf.macro_exposures(y, {"X": x}, dates=["2024-01-02"]),
            lambda: mf.macro_exposures(y, {}),
        ]
        for call in cases:
            with self.assertRaises(ModellingInputError):
                call()


HAND_EXPOSURES = {
    "exposures": [
        {"factor": "US10Y", "beta": -0.0004, "transform": "diff_bp", "significant": True},
        {"factor": "USDCNH", "beta": 0.3, "transform": "log_return", "significant": False},
    ],
    "beta_covariance": [[1e-8, 2e-7], [2e-7, 0.0025]],
    "r_squared": 0.3,
}


class SensitivityTests(unittest.TestCase):

    def test_hand_computed_shocks(self) -> None:
        result = mf.macro_sensitivity(HAND_EXPOSURES, {"US10Y": 100, "USDCNH": -0.05})
        parts = {row["factor"]: row["impact"] for row in result["factors"]}
        self.assertAlmostEqual(parts["US10Y"], -0.04, places=15)
        self.assertAlmostEqual(parts["USDCNH"], 0.3 * math.log(0.95), places=15)
        self.assertAlmostEqual(result["impact"], sum(parts.values()), places=15)
        s = [100.0, math.log(0.95)]
        variance = s[0] ** 2 * 1e-8 + 2 * s[0] * s[1] * 2e-7 + s[1] ** 2 * 0.0025
        self.assertAlmostEqual(result["impact_standard_error"], math.sqrt(variance), places=15)
        low, high = result["range_95"]
        self.assertAlmostEqual((low + high) / 2, result["impact"], places=15)
        self.assertEqual(result["factors"][0]["factor"], "US10Y")
        single = mf.macro_sensitivity(HAND_EXPOSURES, {"US10Y": -25})
        self.assertAlmostEqual(single["impact"], 0.01, places=15)
        self.assertAlmostEqual(single["impact_standard_error"], 25 * 1e-4, places=15)

    def test_unknown_factors_and_bounds(self) -> None:
        result = mf.macro_sensitivity(HAND_EXPOSURES, {"US10Y": 50, "VIX": 5})
        self.assertEqual(result["factors_not_in_model"], ["VIX"])
        for shocks in ({"VIX": 5}, {}, {"US10Y": 5000}, {"USDCNH": -0.95}, {"USDCNH": 3.0}, {"US10Y": math.nan}):
            with self.assertRaises(ModellingInputError):
                mf.macro_sensitivity(HAND_EXPOSURES, shocks)

    def test_end_to_end(self) -> None:
        generator = random.Random(9)
        days = _weekdays(400)
        yield_level, fx, portfolio, yields, cnh = 4.0, 7.2, {}, {}, {}
        for day in days:
            dy, dfx = generator.gauss(0, 0.06), generator.gauss(0, 0.003)
            yield_level += dy
            fx *= math.exp(dfx)
            yields[day], cnh[day] = yield_level, fx
            portfolio[day] = -0.0003 * dy * 100 - 0.5 * dfx + generator.gauss(0, 0.004)
        aligned = mf.align_series(portfolio, {"US10Y": yields, "USDCNH": cnh},
                                  transforms={"US10Y": "diff_bp", "USDCNH": "log_return"})
        model = mf.macro_exposures(aligned["portfolio"], aligned["macro_changes"], dates=aligned["dates"],
                                   transforms=aligned["transforms"])
        shock = mf.macro_sensitivity(model, {"US10Y": 100})
        self.assertAlmostEqual(shock["impact"], -0.03, delta=0.01)
        low, high = shock["range_95"]
        self.assertLess(low, -0.03)
        self.assertGreater(high, -0.03)


if __name__ == "__main__":
    unittest.main()
