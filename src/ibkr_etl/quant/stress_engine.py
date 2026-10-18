"""Stress scenarios that hang together, reverse stress tests and risk by market regime.

Pure standard-library Python. :func:`ibkr_etl.risk_forecasting.factor_stress` shocks
the factors you name and leaves the rest at zero; this module fills in the rest:

* :func:`conditional_scenario` shocks some factors and moves the others by what their
  history says they do at the same time, the Gaussian conditional expectation
  E[u | s] = μu + Σus Σss⁻¹ (s − μs) (Kupiec 1998, "Stress testing in a value at risk
  framework").
* :func:`scenario_impact` turns a full set of factor moves into a portfolio return
  through the factor betas, with each factor's share of it.
* :func:`reverse_stress` finds the most likely factor move that loses a given amount:
  the point on the loss line nearest to normal in Mahalanobis distance (Breuer,
  Jandačka, Rheinberger and Summer 2009, "How to find plausible, severe and useful
  stress scenarios"), s* = −L Σβ / (β'Σβ) for factor means of zero.
* :func:`regime_risk` gives VaR and expected shortfall in each market regime, by
  historical simulation with each day weighted by the chance it belonged to that
  regime (weighted historical simulation, as in Boudoukh, Richardson and Whitelaw
  1998, with regime probabilities from :func:`ibkr_etl.risk_forecasting.fit_hmm`).

Factor covariances are nested mappings, ``{"Mkt-RF": {"Mkt-RF": 0.03, "HML": -0.004,
...}, ...}``, over the scenario's horizon: a monthly covariance gives one-month
scenarios. Moves and returns are fractions (0.01 = 1%). Every result is hypothetical
and says so.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

from ..modelling import DISCLAIMER, ModellingInputError, _dot, _matvec, _solve_inverse
from ..risk_forecasting import MIN_RETURNS, REGIME_LABELS, _tail
from .tail_risk import _chi_square_tail, _level_key

MODEL_VERSION = "stress-engine-v1"
REGIME_MIN_DAYS = MIN_RETURNS
REGIME_LEVELS = (0.95, 0.99)


# Shared helpers -----------------------------------------------------------------------


def _finite(value: Any, what: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ModellingInputError(f"{what} must be a number.") from None
    if not math.isfinite(number):
        raise ModellingInputError(f"{what} must be a finite number.")
    return number


def _covariance(factor_covariance: Mapping[str, Mapping[str, float]]) -> tuple[list[str], list[list[float]]]:
    """Factor names and a symmetric matrix (the average of the two triangles)."""

    names = list(factor_covariance)
    if not names:
        raise ModellingInputError("The factor covariance is empty.")
    matrix = []
    for a in names:
        row = []
        for b in names:
            try:
                upper, lower = factor_covariance[a][b], factor_covariance[b][a]
            except (KeyError, TypeError):
                raise ModellingInputError(f"The factor covariance has no entry for {a} with {b}.") from None
            row.append((_finite(upper, f"The covariance of {a} with {b}") + _finite(lower, f"The covariance of {b} with {a}")) / 2.0)
        matrix.append(row)
    for i, a in enumerate(names):
        if matrix[i][i] < 0:
            raise ModellingInputError(f"The variance of {a} is negative.")
        for j in range(i):
            if abs(matrix[i][j]) > math.sqrt(matrix[i][i] * matrix[j][j]) * (1 + 1e-9) + 1e-300:
                raise ModellingInputError(f"The covariance of {a} with {names[j]} implies a correlation beyond ±1.")
    return names, matrix


def _inverse(matrix: Sequence[Sequence[float]], names: Sequence[str], singular: str) -> list[list[float]]:
    """Inverse via the correlation matrix, so the pivot test does not depend on the units."""

    sds = []
    for i, name in enumerate(names):
        if matrix[i][i] <= 0:
            raise ModellingInputError(f"{name} has no variance in the covariance, so it cannot be used here.")
        sds.append(math.sqrt(matrix[i][i]))
    k = len(names)
    correlation = [[matrix[i][j] / (sds[i] * sds[j]) for j in range(k)] for i in range(k)]
    try:
        inverse = _solve_inverse(correlation)
    except ModellingInputError:
        raise ModellingInputError(singular) from None
    if max(abs(v) for row in inverse for v in row) > 1e10:
        raise ModellingInputError(singular)
    return [[inverse[i][j] / (sds[i] * sds[j]) for j in range(k)] for i in range(k)]


def _means(factor_means: Mapping[str, float] | None, names: Sequence[str]) -> list[float]:
    if factor_means is None:
        return [0.0] * len(names)
    return [_finite(factor_means.get(name, 0.0), f"The mean of {name}") for name in names]


def _betas(exposures: Mapping[str, Any]) -> dict[str, float]:
    """Factor betas from ``{factor: beta}`` or from a :func:`ibkr_etl.modelling.factor_model` result."""

    if isinstance(exposures, Mapping) and isinstance(exposures.get("exposures"), list):
        pairs = [(row["factor"], row["beta"]) for row in exposures["exposures"]]
    else:
        pairs = list(exposures.items())
    if not pairs:
        raise ModellingInputError("There are no factor exposures.")
    return {str(factor): _finite(beta, f"The beta to {factor}") for factor, beta in pairs}


# Conditional scenarios ----------------------------------------------------------------


def conditional_scenario(factor_covariance: Mapping[str, Mapping[str, float]], shocks: Mapping[str, float], *,
                         factor_means: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Shock some factors and move the rest by their Gaussian conditional expectation.

    The factors you set (s) keep their shocks; each other factor u moves by
    E[u | s] = μu + Σus Σss⁻¹ (s − μs), with means of zero unless ``factor_means`` gives
    them. Each inferred factor also gets its conditional standard deviation, the spread
    left once the shocks are known, and its sensitivity to each shocked factor (the
    regression coefficients Σus Σss⁻¹).
    """

    names, cov = _covariance(factor_covariance)
    if not shocks:
        raise ModellingInputError("Choose at least one factor shock.")
    unknown = [f for f in shocks if f not in names]
    if unknown:
        raise ModellingInputError(f"The covariance has no history for {', '.join(map(str, unknown))}.")
    values = {f: _finite(v, f"The shock to {f}") for f, v in shocks.items()}
    means = _means(factor_means, names)
    set_index = [i for i, name in enumerate(names) if name in values]
    free_index = [i for i, name in enumerate(names) if name not in values]
    inverse = _inverse([[cov[i][j] for j in set_index] for i in set_index], [names[i] for i in set_index],
                       "The shocked factors move so closely together that their shocks cannot be told apart; "
                       "shock fewer of them.")
    weights = _matvec(inverse, [values[names[i]] - means[i] for i in set_index])   # Σss⁻¹ (s − μs)
    rows, moves, sensitivities = [], {}, {}
    for i, name in enumerate(names):
        sd = math.sqrt(cov[i][i])
        if i in set_index:
            move, conditional_sd, source = values[name], 0.0, "set"
        else:
            cross = [cov[i][j] for j in set_index]
            move = means[i] + _dot(cross, weights)
            coefficients = _matvec(inverse, cross)
            conditional_sd = math.sqrt(max(cov[i][i] - _dot(cross, coefficients), 0.0))
            sensitivities[name] = {names[j]: c for j, c in zip(set_index, coefficients)}
            source = "inferred"
        moves[name] = move
        rows.append({"factor": name, "move": move, "source": source, "mean": means[i], "volatility": sd,
                     "move_in_sd": (move - means[i]) / sd if sd > 0 else None, "conditional_sd": conditional_sd})
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "moves": moves,
        "set": [names[i] for i in set_index],
        "inferred": [names[i] for i in free_index],
        "factors": rows,
        "sensitivities": sensitivities,
        "method": ("The factors you shock keep their shocks. Every other factor moves by its expected value given "
                   "those shocks, from the factors' covariance: E[u | s] = μu + Σus Σss⁻¹ (s − μs) (Kupiec 1998). "
                   "The conditional standard deviation is how far it could still land either side of that."),
        "disclaimer": DISCLAIMER,
    }


def scenario_impact(exposures: Mapping[str, Any], scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Portfolio return from factor betas times factor moves, and each factor's share of it.

    ``exposures`` is ``{factor: beta}`` or a factor model result; ``scenario`` is
    ``{factor: move}`` or a :func:`conditional_scenario` result. A factor with a beta
    but no move counts as unmoved; a moved factor the portfolio has no beta to adds
    nothing. Both are listed.
    """

    betas = _betas(exposures)
    raw = scenario["moves"] if isinstance(scenario.get("moves"), Mapping) else scenario
    moves = {str(f): _finite(v, f"The move in {f}") for f, v in raw.items()}
    rows = [{"factor": f, "beta": b, "move": moves.get(f, 0.0), "contribution": b * moves.get(f, 0.0)}
            for f, b in betas.items()]
    total = math.fsum(r["contribution"] for r in rows)
    for row in rows:
        row["share"] = row["contribution"] / total if abs(total) > 1e-15 else None
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "portfolio_return": total,
        "factors": sorted(rows, key=lambda r: -abs(r["contribution"])),
        "factors_without_move": [f for f in betas if f not in moves],
        "factors_not_in_portfolio": [f for f in moves if f not in betas],
        "method": ("Each factor's move times the portfolio's beta to it, summed; a factor's share is its part of that "
                   "total. Alpha and the part of returns the factors do not explain are left out."),
        "disclaimer": DISCLAIMER,
    }


# Reverse stress -------------------------------------------------------------------------


def reverse_stress(exposures: Mapping[str, Any], factor_covariance: Mapping[str, Mapping[str, float]], loss: float, *,
                   factor_means: Mapping[str, float] | None = None) -> dict[str, Any]:
    """The most likely factor move that makes the portfolio lose ``loss`` (0.2 for 20%).

    Among all moves s with β's = −L, the one nearest the mean μ in Mahalanobis distance
    is s* = μ − (L + β'μ) Σβ / (β'Σβ), at distance d = |L + β'μ| / √(β'Σβ). Under
    Gaussian factors d² is chi-square with as many degrees of freedom as factors, so
    ``chi_square_tail_probability`` is the chance of a factor move at least this unusual
    in some direction, and ``probability_of_loss_at_least`` = Φ(−(L + β'μ)/√(β'Σβ)) the
    chance of losing L or more. Real factor tails are heavier, so both understate how
    often it happens.
    """

    loss = _finite(loss, "The loss")
    if loss <= 0:
        raise ModellingInputError("Give the loss as a positive fraction, such as 0.2 for a 20% fall.")
    betas = _betas(exposures)
    names, cov = _covariance(factor_covariance)
    missing = [f for f, b in betas.items() if f not in names and b != 0]
    if missing:
        raise ModellingInputError(f"The covariance has no history for {', '.join(missing)}, "
                                  "which the portfolio is exposed to.")
    beta = [betas.get(name, 0.0) for name in names]
    sigma_beta = _matvec(cov, beta)
    variance = _dot(beta, sigma_beta)
    if variance <= 0:
        raise ModellingInputError("The portfolio has no factor risk, so no factor move produces a loss.")
    means = _means(factor_means, names)
    expected = _dot(beta, means)
    needed = loss + expected
    moves = [m - needed * sb / variance for m, sb in zip(means, sigma_beta)]
    volatility = math.sqrt(variance)
    distance = abs(needed) / volatility
    rows = []
    for i, name in enumerate(names):
        sd = math.sqrt(cov[i][i])
        contribution = beta[i] * moves[i]
        rows.append({"factor": name, "move": moves[i], "move_in_sd": (moves[i] - means[i]) / sd if sd > 0 else None,
                     "beta": beta[i], "contribution": contribution, "share_of_loss": -contribution / loss})
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "loss": loss,
        "portfolio_return": _dot(beta, moves),
        "expected_return": expected,
        "factor_volatility": volatility,
        "mahalanobis_distance": distance,
        "degrees_of_freedom": len(names),
        "chi_square_tail_probability": _chi_square_tail(distance * distance, len(names)),
        "probability_of_loss_at_least": 0.5 * math.erfc(needed / (volatility * math.sqrt(2.0))),
        "moves": dict(zip(names, moves)),
        "factors": sorted(rows, key=lambda r: -abs(r["move"])),
        "method": ("The most likely combination of factor moves that loses this much, assuming factor returns are "
                   "jointly normal with this covariance: the point on the loss line closest to normal in Mahalanobis "
                   "distance (Breuer et al. 2009). Each factor moves in proportion to its covariance with the "
                   "portfolio. The probabilities are rough: real markets have fatter tails than the normal."),
        "disclaimer": DISCLAIMER,
    }


# Risk by regime -------------------------------------------------------------------------


def _weighted_quantile(values: Sequence[float], weights: Sequence[float], probability: float) -> float:
    """Quantile of sorted ``values`` with positive ``weights``.

    The weighted form of the linear interpolation :func:`ibkr_etl.modelling._quantile`
    uses: each value sits at the weight below it, scaled so the lowest is at 0 and the
    highest at 1. With equal weights it is exactly that quantile.
    """

    span = math.fsum(weights) - weights[-1]
    if span <= 0:
        return values[-1]
    target, below = probability * span, 0.0
    for i in range(len(values) - 1):
        above = below + weights[i]
        if target <= above:
            fraction = (target - below) / weights[i]
            if fraction <= 0:
                return values[i]
            if fraction >= 1:
                return values[i + 1]
            return values[i] + (values[i + 1] - values[i]) * fraction
        below = above
    return values[-1]


def _weighted_tail(values: Sequence[float], weights: Sequence[float], level: float) -> tuple[float, float]:
    """VaR and expected shortfall (positive) of weighted sorted returns, like risk_forecasting._tail."""

    cut = _weighted_quantile(values, weights, 1.0 - level)
    tail = [(v, w) for v, w in zip(values, weights) if v <= cut]
    weight = math.fsum(w for _, w in tail)
    return -cut, -math.fsum(v * w for v, w in tail) / weight


def _probability_rows(state_probabilities: Sequence[Sequence[float]], keep: Sequence[int]) -> list[list[float]]:
    rows, width = [], None
    for t in keep:
        row = [_finite(p, "A regime probability") for p in state_probabilities[t]]
        width = width or len(row)
        if not row or len(row) != width:
            raise ModellingInputError("Every day needs a probability for each regime, the same regimes every day.")
        total = math.fsum(row)
        if min(row) < 0 or total <= 0:
            raise ModellingInputError("Regime probabilities must be zero or more and not all zero on a day.")
        rows.append([p / total for p in row])
    return rows


def regime_risk(daily_returns: Sequence[float | None], state_probabilities: Sequence[Sequence[float]], *,
                levels: Sequence[float] = REGIME_LEVELS, labels: Sequence[str] | None = None) -> dict[str, Any]:
    """VaR and expected shortfall within each market regime, and how much time each regime took.

    ``state_probabilities[t]`` is the chance that day t was in each regime: the smoothed
    probabilities of a hidden Markov model, or the filtered ones
    :func:`ibkr_etl.risk_forecasting.fit_hmm` returns as ``filtered``. Each row is
    rescaled to sum to 1. A regime's VaR and expected shortfall are the historical ones
    with each day weighted by its probability of that regime, so with a single regime
    they equal plain historical VaR and expected shortfall. ``effective_days`` (Kish's
    effective sample size) says how many days really inform each regime's figures.
    Days with a missing return are skipped and counted.
    """

    if len(daily_returns) != len(state_probabilities):
        raise ModellingInputError("There must be one row of regime probabilities for each daily return.")
    for level in levels:
        if not 0.0 < level < 1.0:
            raise ModellingInputError("Each confidence level must be between 0 and 1, such as 0.99.")
    keep = [t for t, r in enumerate(daily_returns) if r is not None and not (isinstance(r, float) and math.isnan(r))]
    returns = [_finite(daily_returns[t], "A daily return") for t in keep]
    if len(returns) < REGIME_MIN_DAYS:
        raise ModellingInputError(f"Risk by regime needs at least {REGIME_MIN_DAYS} daily returns; "
                                  f"there are {len(returns)}.")
    probabilities = _probability_rows(state_probabilities, keep)
    k = len(probabilities[0])
    names = list(labels) if labels else list(REGIME_LABELS.get(k, ())) or \
        (["All days"] if k == 1 else [f"Regime {i + 1}" for i in range(k)])
    if len(names) != k:
        raise ModellingInputError(f"Give one label for each of the {k} regimes.")
    order = sorted(range(len(returns)), key=lambda i: returns[i])
    ordered = [returns[i] for i in order]
    regimes = []
    for j, name in enumerate(names):
        pairs = [(returns[i], probabilities[i][j]) for i in order if probabilities[i][j] > 0]
        weight = math.fsum(w for _, w in pairs)
        row: dict[str, Any] = {"regime": name, "share_of_days": weight / len(returns),
                               "days_most_likely": sum(1 for p in probabilities if max(range(k), key=p.__getitem__) == j)}
        if not pairs:
            regimes.append({**row, "effective_days": 0.0, "levels": None})
            continue
        values, weights = [v for v, _ in pairs], [w for _, w in pairs]
        mean = math.fsum(v * w for v, w in pairs) / weight
        variance = math.fsum(w * (v - mean) ** 2 for v, w in pairs) / weight
        effective = weight * weight / math.fsum(w * w for w in weights)
        measures = {}
        for level in levels:
            var, es = _weighted_tail(values, weights, level)
            measures[_level_key(level)] = {"level": level, "var": var, "expected_shortfall": es,
                                           "effective_tail_days": effective * (1.0 - level)}
        regimes.append({**row, "effective_days": effective, "mean_daily_return": mean,
                        "daily_volatility": math.sqrt(variance), "annual_volatility": math.sqrt(variance * 252),
                        "levels": measures})
    all_days = {_level_key(level): dict(zip(("var", "expected_shortfall"), _tail(ordered, level)), level=level)
                for level in levels}
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "observations": len(returns),
        "missing_skipped": len(daily_returns) - len(returns),
        "regimes": regimes,
        "all_days": {"levels": all_days, "daily_volatility": statistics.pstdev(returns)},
        "method": ("Historical VaR and expected shortfall within each regime: every past day counts toward a regime in "
                   "proportion to the chance it was in that regime, from the regime model. A regime with few "
                   "effective days, or few in its tail, gives a rough figure."),
        "disclaimer": DISCLAIMER,
    }
