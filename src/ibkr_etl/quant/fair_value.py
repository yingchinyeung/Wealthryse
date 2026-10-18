"""Fair value and relative value between related instruments.

Pure standard-library functions, like :mod:`ibkr_etl.modelling`. Series are either
date → value mappings (aligned on the dates they share) or equal-length lists.

* :func:`dual_listing_parity` – the fair price of one listing from the other (for
  example a Hong Kong share from its US ADR through USD/HKD), the premium or discount,
  its z-score and the half-life of its mean reversion.
* :func:`etf_premium` – an ETF's premium or discount to its NAV per share, with its
  z-score and half-life.
* :func:`engle_granger` – the Engle and Granger (1987) two-step cointegration test:
  an OLS hedge ratio, then an augmented Dickey-Fuller test on the residuals judged
  against MacKinnon (2010) critical values for two variables.
* :func:`spread_signal` – the latest z-score of a spread and whether it is beyond the
  entry level, with the spread's half-life.

Half-lives come from an AR(1) fit, xₜ = a + b·xₜ₋₁ + eₜ, as ln(½)/ln|b|. Signals are
hypothetical and say so.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError, _solve_inverse

MODEL_VERSION = "fair-value-v1"
STATISTICS_MIN_OBSERVATIONS = 20
COINTEGRATION_MIN_OBSERVATIONS = 50
ADF_MAX_LAG = 4
SPREAD_MIN_LOOKBACK = 10
FLAT = 1e-12
# MacKinnon (2010), Table 2, constant and no trend, N = 2 variables: τ(T) = β∞ + β₁/T + β₂/T².
MACKINNON_2010_TWO_VARIABLES = {
    "1%": (-3.89644, -10.9519, -33.527),
    "5%": (-3.33613, -6.1101, -6.823),
    "10%": (-3.04445, -4.2412, -2.720),
}


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_by_date(series: Mapping[Any, Any], label: str) -> dict[str, float]:
    if not isinstance(series, Mapping):
        raise ModellingInputError(f"Give the {label} as a mapping of date to value.")
    result = {}
    for day, raw in series.items():
        value = _number(raw)
        if value is not None and value > 0:
            result[str(day)[:10]] = value
    return result


def _aligned(*series: Any) -> tuple[list[list[float]], list[str] | None]:
    """Columns on the observations every series has; missing or NaN values drop the observation."""

    if all(isinstance(s, Mapping) for s in series):
        lookups = [{str(k)[:10]: _number(v) for k, v in s.items()} for s in series]
        keys = sorted(set.intersection(*(set(lookup) for lookup in lookups)))
        rows = [(key, [lookup[key] for lookup in lookups]) for key in keys]
        rows = [(key, values) for key, values in rows if None not in values]
        return [[values[i] for _, values in rows] for i in range(len(series))], [key for key, _ in rows]
    if any(isinstance(s, Mapping) for s in series):
        raise ModellingInputError("Give both series the same way: both by date or both as lists.")
    lists = [list(s) for s in series]
    if len({len(s) for s in lists}) != 1:
        raise ModellingInputError("The series must have the same length; give them by date to align them.")
    rows = [values for values in zip(*([_number(v) for v in s] for s in lists)) if None not in values]
    return [[values[i] for values in rows] for i in range(len(series))], None


def _flat(values: Sequence[float]) -> bool:
    return statistics.pstdev(values) <= FLAT * max(1.0, max(abs(v) for v in values))


def ar1(values: Sequence[float]) -> dict[str, Any] | None:
    """AR(1) fit xₜ = a + b·xₜ₋₁ by OLS, with the half-life of a deviation; None if too short or flat."""

    if len(values) < 3:
        return None
    lagged, current = values[:-1], values[1:]
    if _flat(lagged):
        return None
    mean_x, mean_y = statistics.fmean(lagged), statistics.fmean(current)
    sxx = sum((x - mean_x) ** 2 for x in lagged)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(lagged, current)) / sxx
    intercept = mean_y - slope * mean_x
    residuals = [y - intercept - slope * x for x, y in zip(lagged, current)]
    dof = len(residuals) - 2
    error = math.sqrt(sum(r * r for r in residuals) / dof / sxx) if dof > 0 else None
    magnitude = abs(slope)
    if magnitude < 1.0:
        half_life = 0.0 if magnitude == 0 else math.log(0.5) / math.log(magnitude)
    else:
        half_life = None
    return {
        "coefficient": slope,
        "standard_error": error,
        "intercept": intercept,
        "long_run_mean": intercept / (1.0 - slope) if slope != 1.0 else None,
        "mean_reverting": magnitude < 1.0,
        "half_life": half_life,
    }


def _premium_statistics(premiums: Sequence[float]) -> tuple[dict[str, Any], list[str]]:
    warnings = []
    n = len(premiums)
    stats: dict[str, Any] = {"observations": n, "latest": premiums[-1], "mean": statistics.fmean(premiums),
                             "standard_deviation": None, "z_score": None, "ar1": None, "half_life": None}
    if n < STATISTICS_MIN_OBSERVATIONS:
        warnings.append(f"The z-score and half-life need at least {STATISTICS_MIN_OBSERVATIONS} dates; there are {n}.")
        return stats, warnings
    if _flat(premiums):
        warnings.append("The premium does not vary, so it has no z-score or half-life.")
        return stats, warnings
    deviation = statistics.stdev(premiums)
    fit = ar1(premiums)
    stats.update({
        "standard_deviation": deviation,
        "z_score": (premiums[-1] - stats["mean"]) / deviation,
        "ar1": fit,
        "half_life": fit["half_life"] if fit else None,
    })
    return stats, warnings


# 1. Dual listings and ETFs ---------------------------------------------------------------


def dual_listing_parity(
    local_prices: Mapping[str, float],
    foreign_prices: Mapping[str, float],
    *,
    shares_per_foreign_unit: float,
    fx_rates: Mapping[str, float],
) -> dict[str, Any]:
    """Fair price of each listing from the other, and the local listing's premium.

    ``local_prices`` are in the local currency (HKD per Hong Kong share),
    ``foreign_prices`` in the foreign currency (USD per ADR), ``fx_rates`` local
    currency per unit of foreign currency (HKD per USD), and
    ``shares_per_foreign_unit`` the local shares one foreign unit represents (8 for
    Alibaba's ADR). The fair local price is foreign price × FX ÷ shares per unit, and
    the premium is local ÷ fair local − 1: above zero, the local listing is dear
    against the foreign one. Only dates with all three positive values are used; close
    prices in different time zones are not synchronous, so use quotes taken at the
    same moment where they exist.
    """

    ratio = _number(shares_per_foreign_unit)
    if ratio is None or ratio <= 0:
        raise ModellingInputError("The number of local shares per foreign unit must be positive.")
    local = _positive_by_date(local_prices, "local prices")
    foreign = _positive_by_date(foreign_prices, "foreign prices")
    fx = _positive_by_date(fx_rates, "FX rates")
    dates = sorted(set(local) & set(foreign) & set(fx))
    if not dates:
        raise ModellingInputError("The two listings and the FX rate have no date in common.")
    rows = []
    for day in dates:
        fair_local = foreign[day] * fx[day] / ratio
        rows.append({
            "date": day,
            "local_price": local[day],
            "foreign_price": foreign[day],
            "fx_rate": fx[day],
            "fair_local_price": fair_local,
            "fair_foreign_price": local[day] * ratio / fx[day],
            "premium": local[day] / fair_local - 1.0,
        })
    stats, warnings = _premium_statistics([row["premium"] for row in rows])
    _add_z(rows, stats)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Fair local price = foreign price × FX rate ÷ local shares per foreign unit; the premium is the local "
            "price over that, less one. The z-score is the latest premium against the mean and standard deviation "
            "of the whole window; the half-life comes from an AR(1) fit of the daily premium."
        ),
        "shares_per_foreign_unit": ratio,
        "latest": rows[-1],
        "statistics": stats,
        "series": rows,
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }


def _add_z(rows: list[dict[str, Any]], stats: Mapping[str, Any]) -> None:
    deviation = stats["standard_deviation"]
    for row in rows:
        row["z_score"] = (row["premium"] - stats["mean"]) / deviation if deviation else None


def etf_premium(etf_prices: Mapping[str, float], nav_per_share: Mapping[str, float]) -> dict[str, Any]:
    """An ETF's premium (price ÷ NAV per share − 1) on each shared date, with z-score and half-life."""

    prices = _positive_by_date(etf_prices, "ETF prices")
    navs = _positive_by_date(nav_per_share, "NAV per share")
    dates = sorted(set(prices) & set(navs))
    if not dates:
        raise ModellingInputError("The ETF prices and its NAV per share have no date in common.")
    rows = [{"date": d, "price": prices[d], "nav_per_share": navs[d], "premium": prices[d] / navs[d] - 1.0}
            for d in dates]
    stats, warnings = _premium_statistics([row["premium"] for row in rows])
    _add_z(rows, stats)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Premium = market price ÷ NAV per share − 1 (negative is a discount). The z-score is the latest premium "
            "against the window's mean and standard deviation; the half-life comes from an AR(1) fit. NAV is struck "
            "at the fund's valuation time, so a market close at a different time adds noise."
        ),
        "latest": rows[-1],
        "statistics": stats,
        "series": rows,
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }


# 2. Cointegration ------------------------------------------------------------------------


def _ols_through_origin(
    y: Sequence[float], columns: Sequence[Sequence[float]]
) -> tuple[list[float], list[float], float]:
    """Coefficients, standard errors and residual sum of squares of y on columns, without a constant."""

    k, n = len(columns), len(y)
    xtx = [[sum(a * b for a, b in zip(columns[i], columns[j])) for j in range(k)] for i in range(k)]
    xty = [sum(a * b for a, b in zip(columns[i], y)) for i in range(k)]
    try:
        inverse = _solve_inverse(xtx)
    except ModellingInputError:
        raise ModellingInputError("The Dickey-Fuller regression is singular; the spread may be constant.") from None
    beta = [sum(inverse[i][j] * xty[j] for j in range(k)) for i in range(k)]
    ssr = sum((value - sum(beta[i] * columns[i][t] for i in range(k))) ** 2 for t, value in enumerate(y))
    sigma2 = ssr / (n - k) if n > k else float("nan")
    return beta, [math.sqrt(max(0.0, sigma2 * inverse[i][i])) for i in range(k)], ssr


def _adf_design(series: Sequence[float], lags: int, start: int) -> tuple[list[float], list[list[float]]]:
    """Δuₜ on uₜ₋₁ and Δuₜ₋₁ … Δuₜ₋ₚ for t from ``start``."""

    diff = [0.0] + [series[t] - series[t - 1] for t in range(1, len(series))]
    times = range(start, len(series))
    columns = [[series[t - 1] for t in times]] + [[diff[t - i] for t in times] for i in range(1, lags + 1)]
    return [diff[t] for t in times], columns


def augmented_dickey_fuller(series: Sequence[float], *, max_lag: int = ADF_MAX_LAG) -> dict[str, Any]:
    """ADF regression without a constant, lag order by AIC from 0 to ``max_lag``.

    As in the usual implementation (statsmodels' ``adfuller``), every lag order is
    compared on the same sample, starting after ``max_lag`` + 1 observations; the
    chosen order is then refitted on all the observations it can use.
    """

    common_start = max_lag + 1
    best = None
    for lags in range(max_lag + 1):
        y, columns = _adf_design(series, lags, common_start)
        _, _, ssr = _ols_through_origin(y, columns)
        if ssr <= 0:
            continue
        aic = len(y) * math.log(ssr / len(y)) + 2 * (lags + 1)
        if best is None or aic < best[0]:
            best = (aic, lags)
    if best is None:
        raise ModellingInputError("The spread fits its own lags exactly, so it cannot be tested.")
    lags = best[1]
    y, columns = _adf_design(series, lags, lags + 1)
    beta, errors, _ = _ols_through_origin(y, columns)
    return {"statistic": beta[0] / errors[0] if errors[0] > 0 else -math.inf, "lags": lags,
            "rho": beta[0], "observations": len(y)}


def mackinnon_critical_values(observations: int) -> dict[str, float]:
    """MacKinnon (2010) Engle-Granger critical values for two variables (constant, no trend)."""

    t = float(observations)
    return {level: b0 + b1 / t + b2 / (t * t) for level, (b0, b1, b2) in MACKINNON_2010_TWO_VARIABLES.items()}


def engle_granger(y: Any, x: Any, *, max_lag: int = ADF_MAX_LAG) -> dict[str, Any]:
    """Engle and Granger (1987) two-step test that y and x are cointegrated.

    Step one regresses y on x with a constant (the hedge ratio). Step two runs an
    augmented Dickey-Fuller test on the residuals, without a constant, with the lag
    order chosen by AIC up to ``max_lag``, and compares its t-statistic with the
    MacKinnon (2010) critical values for two variables, about −3.90 at 1%, −3.34 at
    5% and −3.04 at 10% (adjusted for sample size with T = observations − 1, as
    statsmodels' ``coint`` does). Cointegrated means the 5% value is rejected. Pass
    prices (or log prices), not returns. The spread returned is y − intercept −
    hedge ratio × x.
    """

    if isinstance(max_lag, bool) or not isinstance(max_lag, int) or not 0 <= max_lag <= 12:
        raise ModellingInputError("The maximum lag must be a whole number from 0 to 12.")
    (ys, xs), dates = _aligned(y, x)
    n = len(ys)
    if n < COINTEGRATION_MIN_OBSERVATIONS:
        raise ModellingInputError(
            f"A cointegration test needs at least {COINTEGRATION_MIN_OBSERVATIONS} paired observations; there are {n}."
        )
    if _flat(xs):
        raise ModellingInputError("The second series does not vary, so there is no hedge ratio.")
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((v - mean_x) ** 2 for v in xs)
    hedge = sum((a - mean_x) * (b - mean_y) for a, b in zip(xs, ys)) / sxx
    intercept = mean_y - hedge * mean_x
    spread = [b - intercept - hedge * a for a, b in zip(xs, ys)]
    critical = mackinnon_critical_values(n - 1)
    base = {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Engle-Granger two-step test: y is regressed on x with a constant, then an augmented Dickey-Fuller "
            f"regression without a constant, with up to {max_lag} lags chosen by AIC, tests the residual spread for a "
            "unit root. A t-statistic below the MacKinnon (2010) critical value for two variables rejects 'no "
            "cointegration'."
        ),
        "observations": n,
        "window_start": dates[0] if dates else None,
        "window_end": dates[-1] if dates else None,
        "hedge_ratio": hedge,
        "intercept": intercept,
        "critical_values": critical,
        "spread": spread,
    }
    if _flat(spread):
        return {**base, "exact_fit": True, "adf_statistic": None, "lags": None, "cointegrated": True,
                "significance": None, "half_life": 0.0,
                "warnings": ["y is an exact linear function of x, so the spread never moves."]}
    test = augmented_dickey_fuller(spread, max_lag=max_lag)
    rejected = [level for level in ("1%", "5%", "10%") if test["statistic"] < critical[level]]
    fit = ar1(spread)
    return {
        **base,
        "exact_fit": False,
        "adf_statistic": test["statistic"] if math.isfinite(test["statistic"]) else None,
        "lags": test["lags"],
        "cointegrated": "5%" in rejected,
        "significance": rejected[0] if rejected else None,
        "half_life": fit["half_life"] if fit else None,
        "warnings": [],
    }


# 3. Spread signal ------------------------------------------------------------------------


def spread_signal(spread: Any, *, lookback: int = 60, entry_z: float = 2.0) -> dict[str, Any]:
    """Latest z-score of a spread over the last ``lookback`` observations and the entry signal.

    ``spread`` is a list or a date → value mapping (missing and NaN values are
    skipped). The z-score uses the mean and standard deviation of the last
    ``lookback`` values, the latest included. At or above ``entry_z`` the spread is
    rich (sell it: sell y, buy the hedge ratio of x); at or below −``entry_z`` it is
    cheap (buy it). The half-life is from an AR(1) fit of the whole spread.
    """

    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < SPREAD_MIN_LOOKBACK:
        raise ModellingInputError(f"The lookback must be a whole number of at least {SPREAD_MIN_LOOKBACK}.")
    threshold = _number(entry_z)
    if threshold is None or threshold <= 0:
        raise ModellingInputError("The entry z-score must be a positive number.")
    (values,), dates = _aligned(spread)
    if len(values) < lookback:
        raise ModellingInputError(f"The spread has {len(values)} usable values; the lookback needs {lookback}.")
    window = values[-lookback:]
    if _flat(window):
        raise ModellingInputError(f"The spread does not vary over the last {lookback} values, so it has no z-score.")
    mean, deviation = statistics.fmean(window), statistics.stdev(window)
    z = (values[-1] - mean) / deviation
    position = "rich" if z >= threshold else "cheap" if z <= -threshold else "within"
    fit = ar1(values) if len(values) >= STATISTICS_MIN_OBSERVATIONS else None
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            f"The latest spread against the mean and standard deviation of its last {lookback} values. Beyond "
            f"±{threshold:g} standard deviations the spread is rich (a signal to sell it) or cheap (to buy it), on "
            "the view that it reverts; the half-life from an AR(1) fit says how fast it has reverted."
        ),
        "as_of": dates[-1] if dates else None,
        "latest": values[-1],
        "mean": mean,
        "standard_deviation": deviation,
        "z_score": z,
        "entry_z": threshold,
        "lookback": lookback,
        "beyond_entry": position != "within",
        "position": position,
        "signal": {"rich": "sell_spread", "cheap": "buy_spread"}.get(position),
        "half_life": fit["half_life"] if fit else None,
        "mean_reverting": fit["mean_reverting"] if fit else None,
        "disclaimer": DISCLAIMER,
    }
