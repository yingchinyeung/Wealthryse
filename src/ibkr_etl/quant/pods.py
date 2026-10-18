"""Running each sleeve of a composite like a multi-manager pod.

Pure standard-library functions, like :mod:`ibkr_etl.modelling`. A sleeve is one
account or strategy inside a composite.

* :func:`drawdown_rules` – each sleeve's high-water mark, drawdown and the deepest
  drawdown rule it has breached (for example halve risk at −5%, stop at −7.5%), with
  the date of the breach.
* :func:`pod_risk_budget` – capital per sleeve so each carries an equal share of the
  composite's risk (or in inverse proportion to its volatility), scaled to a target
  volatility.
* :func:`capital_reallocation` – capital moved towards sleeves with better
  risk-adjusted results, each sleeve's change capped and the total kept unchanged.
* :func:`exposure_limits_check` – factor exposures beyond their limits, per sleeve and
  for the composite.

Budgets and reallocations are hypothetical models and say so.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..modelling import (
    DISCLAIMER,
    PROJECTION_MIN_RETURNS,
    TRADING_DAYS,
    ModellingInputError,
    _dot,
    _matvec,
    covariance_matrix,
    risk_contributions,
    risk_parity_weights,
)

MODEL_VERSION = "pod-risk-v1"
DEFAULT_RULES = ((-0.05, "halve"), (-0.075, "stop"))
BUDGET_METHODS = ("equal_risk", "inverse_volatility")
PRIOR_IR_SPREAD = 0.5   # spread of true information ratios across managers (Grinold and Kahn 2000)
TOLERANCE = 1e-12


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# 1. Drawdown rules ---------------------------------------------------------------------


def _parse_rules(rules: Sequence[Sequence[Any]]) -> list[tuple[float, str]]:
    parsed = []
    for rule in rules:
        try:
            threshold, action = rule
        except (TypeError, ValueError):
            raise ModellingInputError("Each drawdown rule is a (threshold, action) pair, such as (-0.05, 'halve').")
        level = _number(threshold)
        if level is None or not -1.0 < level < 0.0:
            raise ModellingInputError("Drawdown thresholds must be negative fractions above -100%, such as -0.05.")
        parsed.append((level, str(action)))
    if not parsed:
        raise ModellingInputError("Give at least one drawdown rule.")
    return sorted(parsed, reverse=True)  # shallowest first


def _sleeve_drawdown(points: Sequence[tuple[str, float]], rules: Sequence[tuple[float, str]]) -> dict[str, Any]:
    peak_date, peak = points[0]
    worst_since_peak = maximum = 0.0
    breached: dict[float, str] = {}
    for day, value in points:
        if value >= peak:  # back at or above the high-water mark: rules reset
            peak, peak_date, worst_since_peak, breached = value, day, 0.0, {}
            continue
        drawdown = value / peak - 1.0
        worst_since_peak = min(worst_since_peak, drawdown)
        maximum = min(maximum, drawdown)
        for threshold, _ in rules:
            if drawdown <= threshold + TOLERANCE and threshold not in breached:
                breached[threshold] = day
    deepest = min(breached) if breached else None
    actions = dict(rules)
    last_day, last = points[-1]
    return {
        "as_of": last_day,
        "current_value": last,
        "peak": peak,
        "peak_date": peak_date,
        "current_drawdown": last / peak - 1.0,
        "deepest_drawdown_since_peak": worst_since_peak,
        "maximum_drawdown": maximum,
        "breaches": [{"threshold": t, "action": actions[t], "date": breached[t]} for t, _ in rules if t in breached],
        "rule_breached": (
            {"threshold": deepest, "action": actions[deepest], "date": breached[deepest]}
            if deepest is not None else None
        ),
        "action": actions[deepest] if deepest is not None else "none",
    }


def drawdown_rules(
    sleeve_nav: Mapping[str, Mapping[str, float]],
    rules: Sequence[Sequence[Any]] = DEFAULT_RULES,
) -> dict[str, Any]:
    """Each sleeve's drawdown from its high-water mark and the deepest rule breached.

    ``sleeve_nav`` maps sleeve → date → value. Pass a performance index (growth of
    one unit, net of contributions and withdrawals), not raw NAV, so money moved in or
    out does not count as a gain or a loss. A rule is breached when the drawdown since
    the latest high-water mark reaches its threshold; the breach stays in force until
    the sleeve is back at that mark, as pod drawdown limits usually work. Values that
    are missing, NaN or not positive are skipped; a sleeve with none is listed.
    """

    parsed = _parse_rules(rules)
    sleeves, skipped = [], []
    for name in sorted(sleeve_nav):
        points = sorted(
            (str(day)[:10], value) for day, raw in (sleeve_nav[name] or {}).items()
            if (value := _number(raw)) is not None and value > 0
        )
        if not points:
            skipped.append(name)
            continue
        sleeves.append({"sleeve": name, **_sleeve_drawdown(points, parsed)})
    if not sleeves:
        raise ModellingInputError("No sleeve has a positive value to measure drawdowns from.")
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Drawdown is the fall from the sleeve's highest value so far (its high-water mark). A rule is breached "
            "once the drawdown since the latest mark reaches its threshold, and stays in force until the sleeve is "
            "back at the mark; the action shown is the deepest rule breached."
        ),
        "rules": [{"threshold": t, "action": a} for t, a in parsed],
        "sleeves": sleeves,
        "skipped": skipped,
    }


# 2. Risk budget ------------------------------------------------------------------------


def _aligned(sleeve_returns: Mapping[str, Any]) -> tuple[list[str], list[list[float]], list[str] | None]:
    """Sleeve names and return columns on the observations every sleeve has."""

    names = sorted(sleeve_returns)
    if not names:
        raise ModellingInputError("Give the returns of at least one sleeve.")
    series = [sleeve_returns[name] for name in names]
    if all(isinstance(s, Mapping) for s in series):
        keys = sorted(set.intersection(*({str(k)[:10] for k in s} for s in series)))
        lookups = [{str(k)[:10]: v for k, v in s.items()} for s in series]
        rows = [(key, [_number(lookup[key]) for lookup in lookups]) for key in keys]
        rows = [(key, values) for key, values in rows if None not in values]
        return names, [[values[i] for _, values in rows] for i in range(len(names))], [key for key, _ in rows]
    if any(isinstance(s, Mapping) for s in series):
        raise ModellingInputError("Give every sleeve's returns the same way: all by date or all as lists.")
    if len({len(s) for s in series}) != 1:
        raise ModellingInputError("Return lists must all have the same length; give them by date to align them.")
    rows = [values for values in zip(*([_number(v) for v in s] for s in series)) if None not in values]
    return names, [[values[i] for values in rows] for i in range(len(names))], None


def pod_risk_budget(
    sleeve_returns: Mapping[str, Any],
    *,
    total_volatility: float,
    method: str = "equal_risk",
) -> dict[str, Any]:
    """Capital per sleeve from the sleeves' covariance, scaled to a target volatility.

    ``sleeve_returns`` maps sleeve → daily returns, either date → return or equal-length
    lists; only observations every sleeve has are used, and missing or NaN returns drop
    that observation. ``equal_risk`` gives each sleeve the same share of the
    composite's variance (equal risk contribution, Maillard, Roncalli and Teïletche
    2010); ``inverse_volatility`` weights each sleeve by 1/σ. ``weight`` sums to one;
    ``scaled_weight`` multiplies it so the composite's annual volatility equals
    ``total_volatility`` (above one in total means leverage, below means cash).
    """

    if method not in BUDGET_METHODS:
        raise ModellingInputError(f"The budget method must be one of {', '.join(BUDGET_METHODS)}.")
    target = _number(total_volatility)
    if target is None or target <= 0:
        raise ModellingInputError("The target volatility must be a positive annual fraction, such as 0.10.")
    names, columns, dates = _aligned(sleeve_returns)
    if len(columns[0]) < PROJECTION_MIN_RETURNS:
        raise ModellingInputError(
            f"A risk budget needs at least {PROJECTION_MIN_RETURNS} days on which every sleeve has a return; "
            f"there are {len(columns[0])}."
        )
    cov = covariance_matrix(columns)
    for index, name in enumerate(names):
        if cov[index][index] <= 0:
            raise ModellingInputError(f"The returns of sleeve {name} do not vary, so its risk cannot be budgeted.")
    volatilities = [math.sqrt(cov[i][i]) for i in range(len(names))]
    if method == "equal_risk":
        weights = risk_parity_weights(cov)
    else:
        inverse = [1.0 / v for v in volatilities]
        weights = [v / sum(inverse) for v in inverse]
    composite = math.sqrt(max(0.0, _dot(weights, _matvec(cov, weights))))
    scale = target / composite
    shares = risk_contributions(weights, cov)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            f"Annualised covariance of the sleeves' daily returns over {len(columns[0])} common days, with "
            "correlations shrunk 20% towards zero. "
            + ("Weights give each sleeve an equal share of the composite's variance (equal risk contribution). "
               if method == "equal_risk" else "Weights are in inverse proportion to each sleeve's volatility. ")
            + "Scaled weights are the same weights multiplied to reach the target volatility."
        ),
        "budget_method": method,
        "window_start": dates[0] if dates else None,
        "window_end": dates[-1] if dates else None,
        "observations": len(columns[0]),
        "target_volatility": target,
        "unscaled_volatility": composite,
        "scale": scale,
        "sleeves": [
            {
                "sleeve": name,
                "volatility": volatilities[i],
                "weight": weights[i],
                "scaled_weight": weights[i] * scale,
                "risk_contribution": shares[i],
                "volatility_budget": shares[i] * target,
            }
            for i, name in enumerate(names)
        ],
        "disclaimer": DISCLAIMER,
    }


# 3. Capital reallocation ---------------------------------------------------------------


def _track_years(stats: Mapping[str, Any]) -> float | None:
    years = _number(stats.get("track_years"))
    if years is None and _number(stats.get("observations")) is not None:
        years = _number(stats.get("observations")) / TRADING_DAYS
    return years if years is not None and years > 0 else None


def _balanced(changes: dict[str, float]) -> dict[str, float]:
    """Scale down whichever side (increases or decreases) is larger so the changes sum to zero."""

    up = sum(v for v in changes.values() if v > 0)
    down = -sum(v for v in changes.values() if v < 0)
    if up <= 0 or down <= 0:
        return {name: 0.0 for name in changes}
    up_scale, down_scale = (down / up, 1.0) if up > down else (1.0, up / down)
    return {name: v * (up_scale if v > 0 else down_scale) for name, v in changes.items()}


def capital_reallocation(
    sleeve_stats: Mapping[str, Mapping[str, Any]],
    current_capital: Mapping[str, float],
    *,
    max_step: float = 0.10,
    sensitivity: float = 0.5,
    prior_spread: float = PRIOR_IR_SPREAD,
) -> dict[str, Any]:
    """Move capital towards sleeves with better shrunk information ratios.

    ``sleeve_stats`` maps sleeve → {"information_ratio" (annualised), "track_years"
    (or "observations" in trading days)}. Each information ratio is shrunk towards
    zero by its track length: with a normal prior of spread s on the true ratio
    (s = 0.5, about a top-quartile manager in Grinold and Kahn 2000) and a
    measurement error of about 1/√T over T years (Lo 2002), the posterior mean is
    IR × T/(T + 1/s²). Each sleeve's change is ``sensitivity`` × its capital × (its
    shrunk ratio − the capital-weighted average), capped at ``max_step`` of its
    capital; the larger side of the increases and decreases is then scaled down so the
    total is unchanged. Sleeves without usable statistics keep their capital.
    """

    step = _number(max_step)
    if step is None or not 0.0 < step <= 1.0:
        raise ModellingInputError("The maximum step must be a fraction between 0 and 1, such as 0.10.")
    strength = _number(sensitivity)
    spread = _number(prior_spread)
    if strength is None or strength <= 0 or spread is None or spread <= 0:
        raise ModellingInputError("The sensitivity and the prior spread must be positive numbers.")
    capital: dict[str, float] = {}
    for name, raw in current_capital.items():
        value = _number(raw)
        if value is None or value < 0:
            raise ModellingInputError(f"Sleeve {name}'s capital must be zero or a positive amount.")
        capital[str(name)] = value
    if not capital:
        raise ModellingInputError("Give the current capital of at least one sleeve.")
    prior_years = 1.0 / (spread * spread)
    scored: dict[str, dict[str, float]] = {}
    for name in capital:
        stats = sleeve_stats.get(name) or {}
        ratio, years = _number(stats.get("information_ratio")), _track_years(stats)
        if ratio is not None and years is not None:
            shrink = years / (years + prior_years)
            scored[name] = {"information_ratio": ratio, "track_years": years, "shrinkage": shrink,
                            "score": ratio * shrink}
    weight = sum(capital[n] for n in scored)
    average = sum(capital[n] * scored[n]["score"] for n in scored) / weight if weight > 0 else None
    raw_changes, capped = {}, set()
    for name in capital:
        change = 0.0
        if average is not None and name in scored:
            change = strength * capital[name] * (scored[name]["score"] - average)
            limit = step * capital[name]
            if abs(change) > limit:
                change = math.copysign(limit, change)
                capped.add(name)
        raw_changes[name] = change
    changes = _balanced(raw_changes)
    total = sum(capital.values())
    rows = []
    for name in sorted(capital):
        stats = scored.get(name, {})
        rows.append({
            "sleeve": name,
            "current_capital": capital[name],
            "new_capital": capital[name] + changes[name],
            "change": changes[name],
            "change_share": changes[name] / capital[name] if capital[name] else 0.0,
            "information_ratio": stats.get("information_ratio"),
            "track_years": stats.get("track_years"),
            "shrinkage": stats.get("shrinkage"),
            "shrunk_information_ratio": stats.get("score"),
            "capped": name in capped,
        })
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            f"Information ratios are shrunk towards zero by track length, IR × T ÷ (T + {prior_years:g}) for T years "
            f"(a prior spread of {spread:g}). Each sleeve gains or loses {strength:g} × its capital × the gap between "
            f"its shrunk ratio and the capital-weighted average, at most {step:.0%} of its capital, and the moves are "
            "balanced so the total capital is unchanged."
        ),
        "total_capital": total,
        "average_shrunk_information_ratio": average,
        "sleeves": rows,
        "unscored": sorted(set(capital) - set(scored)),
        "ignored": sorted(set(map(str, sleeve_stats)) - set(capital)),
        "disclaimer": DISCLAIMER,
    }


# 4. Exposure limits --------------------------------------------------------------------


def _bounds(limit: Any) -> tuple[float, float]:
    if isinstance(limit, (list, tuple)):
        if len(limit) != 2:
            raise ModellingInputError("A range limit is a (lowest, highest) pair.")
        low, high = _number(limit[0]), _number(limit[1])
        if low is None or high is None or low > high:
            raise ModellingInputError("A range limit needs a lowest value no higher than its highest.")
        return low, high
    value = _number(limit)
    if value is None or value < 0:
        raise ModellingInputError("A limit is a non-negative number (for ± that much) or a (lowest, highest) pair.")
    return -value, value


def _check(name: str, exposures: Mapping[str, float], limits: Mapping[str, tuple[float, float]],
           default: tuple[float, float] | None) -> tuple[list[dict[str, Any]], int, list[str]]:
    breaches, checked, unchecked = [], 0, []
    for factor in sorted(exposures):
        value = exposures[factor]
        bounds = limits.get(factor, default)
        if bounds is None:
            unchecked.append(factor)
            continue
        checked += 1
        low, high = bounds
        if value > high + TOLERANCE or value < low - TOLERANCE:
            breaches.append({"sleeve": name, "factor": factor, "exposure": value, "lower": low, "upper": high,
                             "excess": value - high if value > high else value - low})
    return breaches, checked, unchecked


def exposure_limits_check(
    sleeve_exposures: Mapping[str, Mapping[str, float]],
    limits: Mapping[str, Any],
    *,
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Factor exposures beyond their limits, per sleeve and (with ``weights``) for the composite.

    ``sleeve_exposures`` maps sleeve → factor → exposure (a beta, or a share of NAV).
    ``limits`` maps factor → a number L (allowed from −L to L) or a (lowest, highest)
    pair; the key "*" sets the limit for every factor not named. Exposures that are
    missing or NaN are listed, not checked. With ``weights`` (sleeve → share of the
    composite) the composite's exposure is the weighted sum, a sleeve without an
    exposure to a factor counting as zero, and it is checked against the same limits.
    """

    parsed = {str(f): _bounds(v) for f, v in limits.items() if f != "*"}
    default = _bounds(limits["*"]) if "*" in limits else None
    breaches, sleeves, unchecked, missing = [], [], [], []
    clean: dict[str, dict[str, float]] = {}
    for name in sorted(sleeve_exposures):
        values = {}
        for factor, raw in (sleeve_exposures[name] or {}).items():
            value = _number(raw)
            if value is None:
                missing.append({"sleeve": name, "factor": str(factor)})
            else:
                values[str(factor)] = value
        clean[name] = values
        found, checked, left = _check(name, values, parsed, default)
        breaches.extend(found)
        unchecked.extend({"sleeve": name, "factor": f} for f in left)
        sleeves.append({"sleeve": name, "checked": checked, "breaches": len(found), "within_limits": not found})
    composite = None
    if weights is not None:
        shares = {}
        for name, raw in weights.items():
            share = _number(raw)
            if share is None:
                raise ModellingInputError(f"Sleeve {name}'s weight is not a number.")
            shares[str(name)] = share
        factors = sorted({f for name in shares for f in clean.get(name, {})})
        exposures = {f: sum(shares[n] * clean.get(n, {}).get(f, 0.0) for n in shares) for f in factors}
        found, checked, _ = _check("composite", exposures, parsed, default)
        composite = {"exposures": exposures, "checked": checked, "breaches": found, "within_limits": not found}
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Each sleeve's factor exposures against the limits set for each factor; anything outside its range is "
            "an unintended bet to cut or justify. The composite's exposure is the capital-weighted sum of the "
            "sleeves', since bets made separately can add up."
        ),
        "within_limits": not breaches and (composite is None or composite["within_limits"]),
        "breaches": breaches,
        "sleeves": sleeves,
        "composite": composite,
        "unchecked": unchecked,
        "missing": missing,
    }
