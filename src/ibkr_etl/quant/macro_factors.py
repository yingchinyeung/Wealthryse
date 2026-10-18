"""Macro factor exposures: what rates, currencies, oil and credit do to the portfolio.

Pure standard-library Python, like :mod:`ibkr_etl.modelling`:

* :func:`align_series` – puts the portfolio's daily returns and macro series
  (yields, spreads, FX, oil, indices) on one calendar and turns each macro
  level into a change: basis points for yields and spreads, log returns for
  prices.
* :func:`macro_exposures` – regresses the portfolio's returns on those changes
  together (ordinary least squares with an intercept) with Newey and West
  (1987) standard errors, which allow for autocorrelated and heteroscedastic
  residuals.
* :func:`macro_sensitivity` – the portfolio move the fitted betas imply for
  named shocks, such as +100bp on the US 10-year yield or −5% on USD/CNH, with
  each factor's part and a range from the betas' uncertainty.

Every result is a hypothetical model built from history and says so.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError, _solve_inverse, ols
from .holdings_risk import _dot, _number

MODEL_VERSION = "macro-factors-v1"
TRANSFORMS = {
    "diff_bp": "change in basis points (levels in percent, as 4.25 for 4.25%)",
    "log_return": "log return of the level",
}
MIN_OBSERVATIONS = 60           # about three months of sessions
DEFAULT_LAGS = 5
MAX_BP_SHOCK = 1000.0
MIN_RELATIVE_SHOCK, MAX_RELATIVE_SHOCK = -0.9, 2.0
SIGNIFICANCE_T = 2.0
RANGE_Z = 1.96


def _iso(day: Any) -> str:
    text = day.isoformat()[:10] if hasattr(day, "isoformat") else str(day)[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ModellingInputError(f"{day!r} is not a date; use ISO dates such as 2024-03-28.") from None
    return text


def _change(previous: float, current: float, transform: str) -> float:
    return (current - previous) * 100.0 if transform == "diff_bp" else math.log(current / previous)


# Alignment ---------------------------------------------------------------------------


def _clean_levels(name: str, series: Mapping[Any, Any], transform: str) -> tuple[dict[str, float], int]:
    """ISO date → level, skipping missing values (counted) and rejecting levels a log return cannot use."""

    levels, skipped = {}, 0
    for day, value in series.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = math.nan
        if not math.isfinite(number):
            skipped += 1
            continue
        key = _iso(day)
        if transform == "log_return" and number <= 0:
            raise ModellingInputError(f"{name} is {number} on {key}; a log return needs positive levels.")
        levels[key] = number
    return levels, skipped


def align_series(
    portfolio_returns: Mapping[Any, float],
    macro_levels: Mapping[str, Mapping[Any, float]],
    *,
    transforms: Mapping[str, str],
) -> dict[str, Any]:
    """Portfolio returns and macro changes over the same intervals.

    Observations are the dates on which the portfolio has a return and every
    macro series has a level. Each macro change runs from one such date to the
    next, and the portfolio's daily returns in between are compounded to cover
    the same interval, so a holiday in one market does not misalign the other.
    Passing weekly (say Friday) levels therefore gives weekly observations,
    which suits a Hong Kong portfolio against US series that close later in
    the day. Missing or non-numeric macro levels are skipped and counted; a
    missing portfolio return is an error, since it would break the compounding.

    ``transforms`` maps each macro series to ``"diff_bp"`` (yields and spreads
    in percent: 4.25 → 4.30 is +5bp) or ``"log_return"`` (FX, oil, indices).
    """

    if not macro_levels:
        raise ModellingInputError("Give at least one macro series.")
    portfolio = {}
    for day, value in portfolio_returns.items():
        key = _iso(day)
        portfolio[key] = _number(value, f"The portfolio return on {key}")
    levels, skipped = {}, {}
    for name, series in macro_levels.items():
        transform = transforms.get(name)
        if transform not in TRANSFORMS:
            raise ModellingInputError(f"Say how to turn {name} into changes: 'diff_bp' or 'log_return'.")
        levels[name], skipped[name] = _clean_levels(name, series, transform)
    common = set(portfolio).intersection(*levels.values())
    dates, returns, changes = [], [], {name: [] for name in levels}
    previous, growth, sessions, spanning = None, 1.0, 0, 0
    for day in sorted(portfolio):
        if previous is not None:
            growth *= 1.0 + portfolio[day]
            sessions += 1
        if day not in common:
            continue
        if previous is not None:
            dates.append(day)
            returns.append(growth - 1.0)
            for name, series in levels.items():
                changes[name].append(_change(series[previous], series[day], transforms[name]))
            spanning += sessions > 1
        previous, growth, sessions = day, 1.0, 0
    if not dates:
        raise ModellingInputError("The portfolio and the macro series have fewer than two dates in common.")
    return {
        "dates": dates,
        "portfolio": returns,
        "macro_changes": changes,
        "transforms": {name: transforms[name] for name in levels},
        "observations": len(dates),
        "multi_session_observations": spanning,
        "skipped_levels": skipped,
    }


# Exposures ---------------------------------------------------------------------------


def _newey_west(
    design: Sequence[Sequence[float]], residuals: Sequence[float], inverse: Sequence[Sequence[float]], lags: int
) -> list[list[float]]:
    """(XᵀX)⁻¹ Ŝ (XᵀX)⁻¹ · n/(n − k), with Ŝ = Γ₀ + Σ_l (1 − l/(L+1)) (Γ_l + Γ_lᵀ) (Bartlett weights)."""

    n, k = len(residuals), len(design)
    scores = [[x * u for x, u in zip(column, residuals)] for column in design]
    meat = [[_dot(scores[a], scores[b]) for b in range(k)] for a in range(k)]
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1.0)
        later = [column[lag:] for column in scores]
        earlier = [column[:-lag] for column in scores]
        gamma = [[_dot(later[a], earlier[b]) for b in range(k)] for a in range(k)]
        for a in range(k):
            for b in range(k):
                meat[a][b] += weight * (gamma[a][b] + gamma[b][a])
    scale = n / (n - k)
    left = [[_dot(row, [meat[m][j] for m in range(k)]) for j in range(k)] for row in inverse]
    return [[scale * _dot(row, [inverse[m][j] for m in range(k)]) for j in range(k)] for row in left]


def macro_exposures(
    portfolio_returns: Sequence[float],
    macro_changes: Mapping[str, Sequence[float]],
    *,
    lags: int = DEFAULT_LAGS,
    dates: Sequence[str] | None = None,
    transforms: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Betas of the portfolio's returns to macro changes, with Newey-West t-statistics.

    Multivariate OLS with an intercept (:func:`ibkr_etl.modelling.ols`).
    Standard errors are Newey and West (1987, Econometrica): Bartlett weights
    up to ``lags`` and the n/(n − k) small-sample factor (the Stata ``newey``
    convention). ``dates`` and ``transforms`` (from :func:`align_series`) label
    the window and the unit of each beta; :func:`macro_sensitivity` needs the
    transforms to read shocks in basis points or percent.
    """

    y = [_number(value, f"The portfolio return at position {i + 1}") for i, value in enumerate(portfolio_returns)]
    names = [str(name) for name in macro_changes]
    if not names:
        raise ModellingInputError("Give at least one macro series.")
    columns = []
    for name, series in macro_changes.items():
        values = [_number(value, f"The change in {name} at position {i + 1}") for i, value in enumerate(series)]
        if len(values) != len(y):
            raise ModellingInputError(f"{name} has {len(values)} changes for {len(y)} portfolio returns; align them first.")
        if max(values) == min(values):
            raise ModellingInputError(f"{name} does not change over the window, so no beta to it can be fitted.")
        columns.append(values)
    n, k = len(y), len(columns) + 1
    if n < max(MIN_OBSERVATIONS, k + 10):
        raise ModellingInputError(f"The macro model needs at least {max(MIN_OBSERVATIONS, k + 10)} observations; there are {n}.")
    if isinstance(lags, bool) or not isinstance(lags, int) or not 0 <= lags < n // 2:
        raise ModellingInputError(f"The Newey-West lag must be a whole number from 0 to {n // 2 - 1}.")
    if dates is not None and len(dates) != n:
        raise ModellingInputError(f"There are {len(dates)} dates for {n} observations.")
    fit = ols(y, columns)
    design = [[1.0] * n, *columns]
    coefficients = fit["coefficients"]
    residuals = [value - coefficients[0] - sum(c * column[i] for c, column in zip(coefficients[1:], columns))
                 for i, value in enumerate(y)]
    inverse = _solve_inverse([[_dot(a, b) for b in design] for a in design])
    covariance = _newey_west(design, residuals, inverse, lags)
    errors = [math.sqrt(max(0.0, covariance[i][i])) for i in range(k)]
    transforms = dict(transforms or {})
    exposures = []
    for index, name in enumerate(names, start=1):
        t_stat = coefficients[index] / errors[index] if errors[index] else None
        transform = transforms.get(name)
        exposures.append({
            "factor": name,
            "beta": coefficients[index],
            "standard_error": errors[index],
            "t_statistic": t_stat,
            "ols_t_statistic": fit["t_statistics"][index],
            "significant": t_stat is not None and abs(t_stat) >= SIGNIFICANCE_T,
            "transform": transform,
            "unit": {"diff_bp": "portfolio return per 1bp", "log_return": "portfolio return per 1.0 log return"}.get(
                transform, "portfolio return per unit change"),
        })
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "window": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None, "observations": n},
        "lags": lags,
        "intercept": coefficients[0],
        "intercept_t_statistic": coefficients[0] / errors[0] if errors[0] else None,
        "r_squared": fit["r_squared"],
        "adjusted_r_squared": fit["adjusted_r_squared"],
        "residual_volatility": fit["residual_volatility"],
        "exposures": exposures,
        "beta_covariance": [row[1:] for row in covariance[1:]],
        "method": (
            "The portfolio's returns regressed on the macro changes together, with an intercept (ordinary least "
            "squares). Each beta is the portfolio return per unit change in one series with the others held fixed. "
            f"t-statistics use Newey-West (1987) standard errors with {lags} lags, which allow for residuals that are "
            "autocorrelated or change in size; |t| ≥ 2 is marked significant. R² is the share of the portfolio's "
            "moves the macro series explain together."
        ),
        "disclaimer": DISCLAIMER,
    }


# Sensitivity -------------------------------------------------------------------------


def _shock_change(factor: str, shock: float, transform: str | None) -> float:
    """The change in the regressor's own units that a shock to the level means."""

    if transform == "diff_bp":
        if abs(shock) > MAX_BP_SHOCK:
            raise ModellingInputError(f"A shock to {factor} must be within ±{MAX_BP_SHOCK:.0f}bp.")
        return shock
    if transform == "log_return":
        if not MIN_RELATIVE_SHOCK <= shock <= MAX_RELATIVE_SHOCK:
            raise ModellingInputError(
                f"A shock to {factor} must be between {MIN_RELATIVE_SHOCK:.0%} and +{MAX_RELATIVE_SHOCK:.0%} of its level."
            )
        return math.log1p(shock)
    return shock


def macro_sensitivity(exposures: Mapping[str, Any], shocks: Mapping[str, float]) -> dict[str, Any]:
    """Portfolio move the macro betas imply for one-off shocks, with each factor's part.

    A shock is in basis points for a ``diff_bp`` series (+100 is a 1-point
    rise in the yield) and a fraction of the level for a ``log_return`` series
    (−0.05 is a 5% fall, applied to the series as quoted, so for USD/CNH it is
    a stronger yuan). Factors left out of ``shocks`` stay unchanged.
    """

    if not shocks:
        raise ModellingInputError("Choose at least one macro shock.")
    rows_by_factor = {row["factor"]: row for row in exposures["exposures"]}
    order = [row["factor"] for row in exposures["exposures"]]
    vector = [0.0] * len(order)
    rows, ignored = [], []
    for factor, shock in shocks.items():
        value = _number(shock, f"The shock to {factor}")
        row = rows_by_factor.get(factor)
        if row is None:
            ignored.append(factor)
            continue
        change = _shock_change(factor, value, row.get("transform"))
        vector[order.index(factor)] = change
        rows.append({"factor": factor, "shock": value, "change": change, "beta": row["beta"],
                     "significant": row["significant"], "impact": row["beta"] * change})
    if not rows:
        raise ModellingInputError("None of the shocked series is in the fitted macro model.")
    impact = math.fsum(row["impact"] for row in rows)
    covariance = [[_number(v, "A beta covariance") for v in row] for row in exposures.get("beta_covariance") or []]
    error = None
    if covariance:
        error = math.sqrt(max(0.0, _dot(vector, [_dot(row, vector) for row in covariance])))
    return {
        "impact": impact,
        "impact_standard_error": error,
        "range_95": [impact - RANGE_Z * error, impact + RANGE_Z * error] if error is not None else None,
        "factors": sorted(rows, key=lambda r: -abs(r["impact"])),
        "factors_not_in_model": ignored,
        "r_squared": exposures.get("r_squared"),
        "method": (
            "Each shock is turned into the change the model was fitted on (basis points for yields and spreads, the "
            "log return for prices), multiplied by the portfolio's beta to that series and summed. This is an "
            "instant, one-off move with the other series unchanged; the intercept is left out. The range is ±1.96 "
            "standard errors of that sum from the betas' Newey-West covariance. The macro series explain only part "
            "of the portfolio's moves (R²), and large shocks stretch a linear model."
        ),
        "disclaimer": DISCLAIMER,
    }
