"""Correlations that move: dynamic conditional correlation (DCC) of the holdings.

One average correlation hides the fact that holdings move together more when
markets fall. :func:`dcc_correlation` fits the DCC(1,1) model of Engle (2002,
"Dynamic conditional correlation", Journal of Business and Economic Statistics)
in pure standard-library Python:

1. Each holding's demeaned daily returns get a GARCH(1,1) volatility from
   :func:`ibkr_etl.risk_forecasting.fit_volatility` (EWMA when the history is
   short or the fit fails) and are divided by it: standardised residuals ε.
2. Q_t = (1 − a − b) Q̄ + a ε_{t−1} ε_{t−1}ᵀ + b Q_{t−1}, with Q̄ the sample
   second-moment matrix of ε (correlation targeting) and Q_1 = Q̄. The
   correlation matrix for day t is R_t = diag(Q_t)^−½ Q_t diag(Q_t)^−½.
3. (a, b) maximise the correlation part of the Gaussian quasi-likelihood with
   a ≥ 0, b ≥ 0 and a + b < 1.

Two choices keep a 60-holding, ten-year portfolio within a few seconds:

* The likelihood is the composite likelihood of Engle, Shephard and Sheppard
  (2008, "Fitting vast dimensional time-varying covariance models"): the sum of
  the bivariate DCC likelihoods of a seeded random sample of at most
  ``MAX_PAIRS`` pairs, instead of the full likelihood, which needs an N × N
  inverse and determinant every day. It estimates the same (a, b) consistently.
* With more than ``MAX_SERIES`` holdings, the model covers the ``MAX_SERIES``
  largest by absolute weight (the first ones given when there are no weights);
  the result names the holdings left out and the weight the model covers.

Every result is a hypothetical model built from history and says so.
"""
from __future__ import annotations

import math
import random
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError, _quantile, _seed
from ..risk_forecasting import _nelder_mead, fit_volatility
from .holdings_risk import _clean_columns, _cross_products, _dot, _weight_vector

MODEL_VERSION = "dynamic-correlation-v1"
MIN_OBSERVATIONS = 250          # a year of sessions
MAX_SERIES = 30
MAX_PAIRS = 10
MAX_PERSISTENCE = 0.999
STRESS_QUANTILE = 0.05
START_GRID = tuple((a, b) for a in (0.01, 0.03, 0.07) for b in (0.85, 0.93, 0.97))
OPTIMISER_ITERATIONS = 100


def _standardise(column: Sequence[float]) -> tuple[list[float], dict[str, Any]]:
    """Demeaned returns divided by their GARCH(1,1) (or EWMA) conditional volatility."""

    mean = math.fsum(column) / len(column)
    demeaned = [value - mean for value in column]
    fit = fit_volatility(demeaned)
    return [value / math.sqrt(variance) for value, variance in zip(demeaned, fit["variances"])], fit


def _pair_data(left: Sequence[float], right: Sequence[float], target: tuple[float, float, float]) -> tuple:
    return [v * v for v in left], [v * v for v in right], [a * b for a, b in zip(left, right)], target


def _pair_loglik(pair: tuple, a: float, b: float) -> float:
    """Bivariate DCC correlation log-likelihood, −½ Σ_t [log(1 − ρ_t²) + (ε₁² + ε₂² − 2ρ_t ε₁ε₂)/(1 − ρ_t²)]."""

    x11, x22, x12, (s11, s22, s12) = pair
    c = 1.0 - a - b
    k11, k22, k12 = c * s11, c * s22, c * s12
    q11, q22, q12 = s11, s22, s12
    sqrt, log = math.sqrt, math.log
    total = 0.0
    for e11, e22, e12 in zip(x11, x22, x12):
        rho = q12 / sqrt(q11 * q22)
        spread = max(1.0 - rho * rho, 1e-12)
        total += log(spread) + (e11 + e22 - 2.0 * rho * e12) / spread
        q11 = k11 + a * e11 + b * q11
        q22 = k22 + a * e22 + b * q22
        q12 = k12 + a * e12 + b * q12
    return -0.5 * total


def _composite_loglik(pairs: Sequence[tuple], a: float, b: float) -> float:
    if a < 0 or b < 0 or a + b >= MAX_PERSISTENCE:
        return -math.inf
    return sum(_pair_loglik(pair, a, b) for pair in pairs)


def _fit_dcc(pairs: Sequence[tuple]) -> tuple[float, float, float]:
    """(a, b) maximising the composite likelihood, from the best point of a small grid."""

    def objective(point: Sequence[float]) -> float:
        return _composite_loglik(pairs, point[0], point[1])

    start = max(START_GRID, key=objective)
    a, b = _nelder_mead(objective, start, step=0.02, iterations=OPTIMISER_ITERATIONS)
    return max(0.0, a), max(0.0, b), objective([a, b])


def _correlation(q: Sequence[Sequence[float]]) -> list[list[float]]:
    """diag(Q)^−½ Q diag(Q)^−½ with an exact unit diagonal and exact symmetry."""

    n = len(q)
    scale = [1.0 / math.sqrt(q[i][i]) for i in range(n)]
    matrix = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i][j] = matrix[j][i] = max(-1.0, min(1.0, q[i][j] * scale[i] * scale[j]))
    return matrix


def _weighted_average(q: Sequence[Sequence[float]], weights: Sequence[float]) -> float:
    """Σ_{i≠j} w_i w_j ρ_ij / Σ_{i≠j} w_i w_j, computed as uᵀQu with u_i = w_i / √q_ii."""

    u = [w / math.sqrt(row[i]) for i, (w, row) in enumerate(zip(weights, q))]
    total = sum(ui * _dot(row, u) for ui, row in zip(u, q))
    squares = sum(w * w for w in weights)
    return (total - squares) / (sum(weights) ** 2 - squares)


def _dcc_path(
    residuals: Sequence[Sequence[float]],
    target: Sequence[Sequence[float]],
    a: float,
    b: float,
    weights: Sequence[float],
    *,
    keep: bool = False,
) -> tuple[list[float], list[list[float]], list[list[list[float]]]]:
    """Each day's weighted average correlation (from information before that day), the next day's Q and,
    when ``keep``, every day's correlation matrix."""

    c = 1.0 - a - b
    base = [[c * v for v in row] for row in target]
    q = [row[:] for row in target]
    averages, matrices = [], []
    for shocks in zip(*residuals):
        averages.append(_weighted_average(q, weights))
        if keep:
            matrices.append(_correlation(q))
        scaled = [a * e for e in shocks]
        q = [[k + s * e + b * v for k, e, v in zip(krow, shocks, qrow)] for krow, s, qrow in zip(base, scaled, q)]
    return averages, q, matrices


def _select(
    names: Sequence[str], columns: Sequence[Sequence[float]], weights: Sequence[float]
) -> tuple[list[int], list[dict[str, str]]]:
    """Indices of the holdings to model, largest absolute weight first, and why the others are left out."""

    order = sorted(range(len(names)), key=lambda i: -abs(weights[i]))
    chosen, left_out = [], []
    for i in order:
        if weights[i] == 0:
            left_out.append({"name": names[i], "reason": "not held (zero weight)"})
        elif max(columns[i]) == min(columns[i]):
            left_out.append({"name": names[i], "reason": "its price did not move, so it has no correlation"})
        elif len(chosen) >= MAX_SERIES:
            left_out.append({"name": names[i], "reason": f"outside the {MAX_SERIES} largest holdings by weight"})
        else:
            chosen.append(i)
    return sorted(chosen), left_out


def dcc_correlation(
    columns: Mapping[str, Sequence[float]] | Sequence[Sequence[float]],
    *,
    weights: Mapping[str, float] | Sequence[float] | None = None,
    dates: Sequence[str] | None = None,
) -> dict[str, Any]:
    """DCC(1,1) correlations of the holdings' daily returns, and how they behave on stress days.

    ``columns`` are aligned daily returns, one per holding; ``weights`` are
    today's weights (equal when left out); ``dates``, if given, label the daily
    series. Holdings with zero weight or no price movement are left out.
    Returns the estimated (a, b), the correlation matrix forecast for the next
    session, the weighted average pairwise correlation for every day, and its
    mean on stress days (today's weights' return in its worst 5%) against all
    other (calm) days.
    """

    names, raw = _clean_columns(columns, minimum=MIN_OBSERVATIONS)
    days = len(raw[0])
    if dates is not None and len(dates) != days:
        raise ModellingInputError(f"There are {len(dates)} dates for {days} days of returns.")
    w = [1.0 / len(names)] * len(names) if weights is None else _weight_vector(weights, names, "weights")
    chosen, left_out = _select(names, raw, w)
    if len(chosen) < 2:
        raise ModellingInputError("Dynamic correlation needs at least two held holdings whose prices move.")
    modelled = [names[i] for i in chosen]
    residuals, volatility = [], []
    for i in chosen:
        series, fit = _standardise(raw[i])
        residuals.append(series)
        volatility.append({"name": names[i], "method": fit["method"], "alpha": fit["alpha"], "beta": fit["beta"],
                           "persistence": fit["persistence"]})
    target = _cross_products(residuals, days)
    pairs = [(i, j) for i in range(len(chosen)) for j in range(i + 1, len(chosen))]
    sample = pairs
    if len(pairs) > MAX_PAIRS:
        sample = sorted(random.Random(_seed("dcc-pairs", "|".join(modelled), days)).sample(pairs, MAX_PAIRS))
    pair_data = [_pair_data(residuals[i], residuals[j], (target[i][i], target[j][j], target[i][j])) for i, j in sample]
    a, b, likelihood = _fit_dcc(pair_data)
    average_weights = [abs(w[i]) for i in chosen]
    averages, next_q, _ = _dcc_path(residuals, target, a, b, average_weights)
    portfolio = [_dot(row, w) for row in zip(*raw)]
    threshold = _quantile(sorted(portfolio), STRESS_QUANTILE)
    stress = [value for value, r in zip(averages, portfolio) if r <= threshold]
    calm = [value for value, r in zip(averages, portfolio) if r > threshold]
    persistence = a + b
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "a": a,
        "b": b,
        "persistence": persistence,
        "half_life_days": math.log(0.5) / math.log(persistence) if 0 < persistence < 1 else None,
        "names": modelled,
        "not_modelled": left_out,
        "weight_covered": sum(average_weights) / sum(abs(v) for v in w),
        "latest_correlation": _correlation(next_q),
        "average_correlation": {
            "dates": list(dates) if dates is not None else None,
            "series": averages,
            "latest": _weighted_average(next_q, average_weights),
            "unconditional": _weighted_average(target, average_weights),
            "stress_days": math.fsum(stress) / len(stress) if stress else None,
            "calm_days": math.fsum(calm) / len(calm) if calm else None,
        },
        "stress_days": len(stress),
        "stress_threshold": threshold,
        "fit": {
            "pairs_used": len(sample),
            "pairs_available": len(pairs),
            "composite_log_likelihood": likelihood,
            "constant_correlation_log_likelihood": _composite_loglik(pair_data, 0.0, 0.0),
        },
        "volatility_models": volatility,
        "observations": days,
        "method": (
            "Dynamic conditional correlation, DCC(1,1) of Engle (2002). Each holding's daily returns are divided by "
            "their GARCH(1,1) volatility; the correlation of those standardised returns then moves each day: a "
            "controls how far one day's co-movement shifts it, b how long the shift lasts, and it reverts to the "
            "long-run correlation. (a, b) are fitted by maximum composite likelihood over "
            f"{'a seeded sample of ' if len(sample) < len(pairs) else ''}{len(sample)} pairs of holdings "
            "(Engle, Shephard and Sheppard 2008), which avoids inverting the full matrix each day. "
            f"With more than {MAX_SERIES} holdings only the largest {MAX_SERIES} by weight are modelled. The average "
            "correlation is weighted by each pair's product of weights and, for each day, uses only earlier data. "
            "Stress days are the 5% of days on which today's weights would have lost the most; calm days are the rest."
        ),
        "disclaimer": DISCLAIMER,
    }
