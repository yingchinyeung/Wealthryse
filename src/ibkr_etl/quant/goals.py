"""Goals-based planning for the Modelling page.

Pure standard-library functions, like :mod:`ibkr_etl.modelling`:

* :func:`goal_projection` – Monte Carlo of wealth with planned contributions and
  withdrawals, and the odds of reaching a target: monthly returns are drawn from a
  pool of the portfolio's own monthly returns (a block bootstrap, as
  :func:`ibkr_etl.modelling.project_wealth` does), or switch between market regimes.
* :func:`required_contribution` – the monthly saving that reaches the target with a
  chosen probability, on the same simulated markets so the answer is stable.
* :func:`monthly_regime_model` – turns a daily regime model fitted by
  :func:`ibkr_etl.risk_forecasting.fit_hmm` into the monthly shape the projection takes.

A plan's flows are lists of ``{"amount": 500, "month": 1, "repeat": "monthly",
"until": 120}``: month 0 is today, before any return; month m ≥ 1 is the end of month
m, after that month's return. ``repeat`` is None (once), "monthly" or "yearly" and
``until`` (inclusive, default the horizon) ends the repeats. Amounts are positive;
withdrawals are a separate list. Amounts and the target are in the same currency and
in nominal terms: there is no inflation adjustment.

A path runs out of money when a withdrawal cannot be met in full; it then stays at
zero and later contributions are not made. Everything here is hypothetical.
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any, Mapping, Sequence

from ..modelling import (
    DISCLAIMER,
    PROJECTION_PERCENTILES,
    ModellingInputError,
    _quantile,
    _seed,
    monthly_block_returns,
)
from ..risk_forecasting import MONTH, REGIME_LABELS, _draw, itertools_accumulate

MODEL_VERSION = "quant-goals-v1"
MAX_MONTHS = 600              # fifty years
MIN_POOL = 12                 # a year of monthly returns
MIN_REGIME_POOL = 6
MIN_PATHS, MAX_PATHS = 100, 10_000
DEFAULT_PATHS = 2000
REPEATS = {None: None, "monthly": 1, "yearly": 12}


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _month(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ModellingInputError(f"The {label} must be a whole number of months from today (0 or more).")
    return value


# Inputs ---------------------------------------------------------------------------------


def _schedule(items: Sequence[Mapping[str, Any]], horizon: int, label: str) -> list[float]:
    """Amount of each kind of flow at months 0..horizon."""

    flows = [0.0] * (horizon + 1)
    for item in items:
        amount = _finite(item.get("amount"))
        if amount is None or amount < 0:
            raise ModellingInputError(f"Each {label} needs an amount of zero or more.")
        month = _month(item.get("month", 1), f"month of a {label}")
        if month > horizon:
            raise ModellingInputError(f"A {label} at month {month} falls after the {horizon}-month horizon.")
        repeat = item.get("repeat")
        if repeat not in REPEATS:
            raise ModellingInputError(f"A {label} repeats 'monthly', 'yearly' or not at all.")
        step = REPEATS[repeat]
        if step is None:
            flows[month] += amount
            continue
        until = _month(item.get("until", horizon), f"last month of a repeating {label}")
        if until < month:
            raise ModellingInputError(f"A repeating {label} ends before it starts.")
        for m in range(month, min(until, horizon) + 1, step):
            flows[m] += amount
    return flows


def _validated_plan(start_value: Any, target: Any, horizon_months: Any, paths: Any) -> tuple[float, float, int, int]:
    start = _finite(start_value)
    if start is None or start < 0:
        raise ModellingInputError("The starting value must be zero or a positive amount.")
    goal = _finite(target)
    if goal is None or goal <= 0:
        raise ModellingInputError("The target must be a positive amount.")
    if isinstance(horizon_months, bool) or not isinstance(horizon_months, int) or not 1 <= horizon_months <= MAX_MONTHS:
        raise ModellingInputError(f"Choose a horizon between 1 and {MAX_MONTHS} months.")
    if isinstance(paths, bool) or not isinstance(paths, int) or not MIN_PATHS <= paths <= MAX_PATHS:
        raise ModellingInputError(f"Choose between {MIN_PATHS:,} and {MAX_PATHS:,} simulated paths.")
    return start, goal, horizon_months, paths


def _growth_pool(values: Sequence[Any], minimum: int, label: str) -> tuple[list[float], int]:
    """Growth factors 1 + r (floored at 0) from a pool, skipping missing values."""

    clean = [v for v in (_finite(x) for x in values) if v is not None]
    if len(clean) < minimum:
        raise ModellingInputError(f"{label} needs at least {minimum} monthly returns; it has {len(clean)}.")
    return [max(1.0 + v, 0.0) for v in clean], len(values) - len(clean)


def _stationary(transition: Sequence[Sequence[float]]) -> list[float]:
    k = len(transition)
    state = [1.0 / k] * k
    for _ in range(2000):
        following = [sum(state[i] * transition[i][j] for i in range(k)) for j in range(k)]
        if max(abs(a - b) for a, b in zip(following, state)) < 1e-14:
            return following
        state = following
    return state


def _regimes(model: Mapping[str, Any]) -> dict[str, Any]:
    """Validated regime model: monthly transition matrix, per-regime monthly return pools, start probabilities."""

    transition, pools = model.get("transition"), model.get("pools")
    if not transition or not pools or len(transition) != len(pools):
        raise ModellingInputError("A regime model needs a transition matrix and one pool of monthly returns per regime.")
    k = len(transition)
    rows = []
    for row in transition:
        values = [_finite(v) for v in row]
        if len(values) != k or any(v is None or v < 0 for v in values) or abs(sum(values) - 1.0) > 1e-6:
            raise ModellingInputError("Each row of the regime transition matrix must hold non-negative odds adding up to 1.")
        rows.append([v / sum(values) for v in values])
    growth, skipped = [], 0
    for index, pool in enumerate(pools):
        factors, missing = _growth_pool(pool, MIN_REGIME_POOL, f"Regime {index + 1}")
        growth.append(factors)
        skipped += missing
    start = model.get("start")
    if start is None:
        start = _stationary(rows)
    else:
        start = [_finite(v) for v in start]
        if len(start) != k or any(v is None or v < 0 for v in start) or sum(start) <= 0:
            raise ModellingInputError("The starting regime odds need one non-negative value per regime.")
        start = [v / sum(start) for v in start]
    return {"transition": rows, "growth": growth, "start": start, "skipped": skipped,
            "labels": list(model.get("labels") or [f"Regime {i + 1}" for i in range(k)])}


def _market_paths(monthly_return_pool, regime_model, horizon: int, paths: int) -> tuple[list[list[float]], dict[str, Any]]:
    """Growth factor of every simulated month. The seed depends only on the market inputs,
    so plans with different flows meet the same markets (common random numbers)."""

    if (monthly_return_pool is None) == (regime_model is None):
        raise ModellingInputError("Give either a pool of monthly returns or a regime model, not both.")
    if monthly_return_pool is not None:
        growth, skipped = _growth_pool(monthly_return_pool, MIN_POOL, "The return pool")
        generator = random.Random(_seed(growth, horizon, paths, "goals"))
        simulated = [generator.choices(growth, k=horizon) for _ in range(paths)]
        logs = [math.log(g) for g in growth if g > 0]
        source = {"return_source": "bootstrap", "monthly_returns": len(growth), "skipped_returns": skipped,
                  "annual_return": math.expm1(12.0 * statistics.fmean(logs)) if logs else None,
                  "annual_volatility": statistics.pstdev(growth) * math.sqrt(12)}
        return simulated, source
    regimes = _regimes(regime_model)
    flat = [v for row in regimes["transition"] for v in row]
    generator = random.Random(_seed(*regimes["growth"], flat, regimes["start"], horizon, paths, "goals-regimes"))
    cumulative = [itertools_accumulate(row) for row in regimes["transition"]]
    initial = itertools_accumulate(regimes["start"])
    pools = regimes["growth"]
    simulated = []
    for _ in range(paths):
        state, path = _draw(initial, generator), []
        for _month_index in range(horizon):
            state = _draw(cumulative[state], generator)
            pool = pools[state]
            path.append(pool[int(generator.random() * len(pool))])
        simulated.append(path)
    source = {"return_source": "regimes", "regimes": [
        {"regime": label, "start_probability": p, "monthly_returns": len(pool),
         "average_monthly_return": statistics.fmean(pool) - 1.0}
        for label, p, pool in zip(regimes["labels"], regimes["start"], pools)
    ], "skipped_returns": regimes["skipped"]}
    return simulated, source


# Simulation ------------------------------------------------------------------------------


def _run(simulated: Sequence[Sequence[float]], start: float, flows: Sequence[float], checkpoints: Sequence[int]):
    """Wealth of every path at the checkpoints and at the end, and the month each ran out (or None)."""

    horizon = len(flows) - 1
    columns: list[list[float]] = [[] for _ in checkpoints]
    finals, ran_out = [], []
    for growth in simulated:
        value = start + flows[0]
        out = 0 if value < 0 else None
        if out is not None:
            value = 0.0
        cursor = 0
        for month in range(1, horizon + 1):
            if out is None:
                value = value * growth[month - 1] + flows[month]
                if value < 0:
                    value, out = 0.0, month
            if month == checkpoints[cursor]:
                columns[cursor].append(value)
                cursor += 1
        finals.append(value)
        ran_out.append(out)
    return columns, finals, ran_out


def _checkpoints(horizon: int) -> list[int]:
    points = list(range(12, horizon + 1, 12))
    return points if points and points[-1] == horizon else points + [horizon]


def _summary(start: float, target: float, flows_in: Sequence[float], flows_out: Sequence[float],
             simulated, source: Mapping[str, Any], paths: int) -> dict[str, Any]:
    horizon = len(flows_in) - 1
    net = [a - b for a, b in zip(flows_in, flows_out)]
    points = _checkpoints(horizon)
    columns, finals, ran_out = _run(simulated, start, net, points)
    ordered = sorted(finals)
    shortfalls = sorted(target - v for v in finals if v < target)
    out_months = sorted(m for m in ran_out if m is not None)
    series = [{"month": 0, **{f"p{round(p * 100)}": start + net[0] for p in PROJECTION_PERCENTILES}}]
    for month, column in zip(points, columns):
        column.sort()
        series.append({"month": month, **{f"p{round(p * 100)}": _quantile(column, p) for p in PROJECTION_PERCENTILES}})
    return {
        "probability_target": sum(1 for v in finals if v >= target) / paths,
        "probability_depleted": len(out_months) / paths,
        "median_shortfall": _quantile(shortfalls, 0.5) if shortfalls else None,
        "median_month_depleted": _quantile(out_months, 0.5) if out_months else None,
        "terminal": {**{f"p{round(p * 100)}": _quantile(ordered, p) for p in PROJECTION_PERCENTILES},
                     "mean": statistics.fmean(finals)},
        "series": series,
        "assumptions": dict(source),
        "total_contributions": sum(flows_in),
        "total_withdrawals": sum(flows_out),
    }


def _method(source: Mapping[str, Any], horizon: int, paths: int) -> str:
    if source["return_source"] == "bootstrap":
        draws = (f"draws one of {source['monthly_returns']} past monthly returns at random (a block bootstrap of "
                 "overlapping 21-session windows)")
    else:
        draws = (f"moves between {len(source['regimes'])} market regimes with the fitted monthly odds and draws a "
                 "past monthly return from the regime it is in")
    return (f"Each of {paths:,} simulated paths {draws}, applies it to the wealth, then adds that month's "
            f"contributions and takes its withdrawals, for {horizon} months. A path runs out of money when a "
            "withdrawal cannot be met in full; it then stays at zero. The target counts as reached when wealth at "
            "the end is at or above it. Amounts are nominal, with no inflation adjustment.")


def goal_projection(
    start_value: float,
    *,
    monthly_return_pool: Sequence[float] | None = None,
    regime_model: Mapping[str, Any] | None = None,
    contributions: Sequence[Mapping[str, Any]] = (),
    withdrawals: Sequence[Mapping[str, Any]] = (),
    target: float,
    horizon_months: int,
    paths: int = DEFAULT_PATHS,
) -> dict[str, Any]:
    """Odds of reaching a target with planned contributions and withdrawals (Monte Carlo).

    ``monthly_return_pool`` is a list of monthly returns, usually
    ``modelling.monthly_block_returns(daily_returns)``; missing values are skipped and
    counted. ``regime_model`` is ``{"transition": k×k monthly odds, "pools": k lists of
    monthly returns, "start": k probabilities (default: the long-run mix), "labels": names}``,
    as :func:`monthly_regime_model` builds. Give exactly one.
    """

    start, goal, horizon, paths = _validated_plan(start_value, target, horizon_months, paths)
    flows_in = _schedule(contributions, horizon, "contribution")
    flows_out = _schedule(withdrawals, horizon, "withdrawal")
    simulated, source = _market_paths(monthly_return_pool, regime_model, horizon, paths)
    summary = _summary(start, goal, flows_in, flows_out, simulated, source, paths)
    warnings = []
    if source["return_source"] == "bootstrap" and source["monthly_returns"] < 36:
        warnings.append(f"Based on {source['monthly_returns']} monthly returns. A short history can make the range "
                        "look narrower than a full market cycle would.")
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "start_value": start,
        "target": goal,
        "horizon_months": horizon,
        "paths": paths,
        **summary,
        "warnings": warnings,
        "method": _method(source, horizon, paths),
        "disclaimer": DISCLAIMER,
    }


def required_contribution(
    start_value: float,
    *,
    monthly_return_pool: Sequence[float] | None = None,
    regime_model: Mapping[str, Any] | None = None,
    contributions: Sequence[Mapping[str, Any]] = (),
    withdrawals: Sequence[Mapping[str, Any]] = (),
    target: float,
    horizon_months: int,
    probability: float = 0.8,
    paths: int = DEFAULT_PATHS,
) -> dict[str, Any]:
    """The extra monthly contribution (end of months 1..horizon) that reaches the target with ``probability``.

    Uses common random numbers: the markets are simulated once and every candidate
    contribution meets the same paths (and the same paths :func:`goal_projection`
    uses), so the answer is stable and adding it to the plan there gives at least the
    chosen probability. Wealth is non-decreasing in the contribution on every path, so
    each path has a threshold contribution; the answer is the smallest one met by the
    chosen share of paths, which is exactly where bisection on the contribution would
    converge. On a path, wealth with contribution c is A_m + c·B_m while it stays solvent,
    so the threshold is max over m of −A_m/B_m (never run out) and (target − A_T)/B_T.
    """

    start, goal, horizon, paths = _validated_plan(start_value, target, horizon_months, paths)
    chance = _finite(probability)
    if chance is None or not 0.0 < chance < 1.0:
        raise ModellingInputError("The probability must be above 0% and below 100%.")
    flows_in = _schedule(contributions, horizon, "contribution")
    flows_out = _schedule(withdrawals, horizon, "withdrawal")
    net = [a - b for a, b in zip(flows_in, flows_out)]
    if start + net[0] < 0:
        raise ModellingInputError("The plan runs out of money today, before any monthly contribution is made.")
    simulated, source = _market_paths(monthly_return_pool, regime_model, horizon, paths)
    thresholds = []
    for growth in simulated:
        a, b, solvent = start + net[0], 0.0, -math.inf
        for month in range(1, horizon + 1):
            a = a * growth[month - 1] + net[month]
            b = b * growth[month - 1] + 1.0
            if a < 0:
                solvent = max(solvent, -a / b)
        thresholds.append(max(solvent, (goal - a) / b))
    thresholds.sort()
    rank = math.ceil(chance * paths - 1e-9)
    needed = thresholds[rank - 1]
    without = sum(1 for v in thresholds if v <= 0) / paths
    monthly = 0.0 if needed <= 0 else needed + 1e-9 * max(1.0, goal)   # nudge past rounding on the boundary path
    plan_in = [v + (monthly if m >= 1 else 0.0) for m, v in enumerate(flows_in)]
    summary = _summary(start, goal, plan_in, flows_out, simulated, source, paths)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "start_value": start,
        "target": goal,
        "horizon_months": horizon,
        "paths": paths,
        "probability_wanted": chance,
        "monthly_contribution": monthly,
        "already_on_track": needed <= 0,
        "probability_without": without,
        "extra_contributions": monthly * horizon,
        "with_contribution": summary,
        "method": (
            f"The extra amount saved at the end of every month for {horizon} months so that at least "
            f"{chance:.0%} of {paths:,} simulated paths reach the target. Every candidate amount meets the same "
            "simulated markets, so the answer does not move with the random draws; it is the exact point a "
            "bisection on the amount would converge to. " + _method(source, horizon, paths)
        ),
        "disclaimer": DISCLAIMER,
    }


# Regimes from a daily model -----------------------------------------------------------------


def monthly_regime_model(daily_returns: Sequence[float], model: Mapping[str, Any], *,
                         sessions: int = MONTH) -> dict[str, Any]:
    """Monthly regime model for :func:`goal_projection` from a daily one fitted by ``risk_forecasting.fit_hmm``.

    The monthly odds are the daily transition matrix to the power ``sessions``. Each
    overlapping ``sessions``-day window's compounded return goes to the regime with the
    highest average filtered probability over the window; a regime with fewer than
    ``MIN_REGIME_POOL`` windows takes the windows where it was most likely. The start is
    today's filtered regime probabilities. The model must be fitted on these returns.
    """

    filtered, transition = model.get("filtered"), model.get("transition")
    if not filtered or not transition:
        raise ModellingInputError("The regime model needs its transition matrix and filtered probabilities.")
    if len(filtered) != len(daily_returns):
        raise ModellingInputError("The regime model was fitted on a different set of daily returns.")
    if len(daily_returns) < sessions + MIN_REGIME_POOL:
        raise ModellingInputError("Too few daily returns to build monthly regimes.")
    k = len(transition)
    monthly = [list(row) for row in transition]
    for _ in range(sessions - 1):
        monthly = [[sum(row[i] * transition[i][j] for i in range(k)) for j in range(k)] for row in monthly]
    blocks = monthly_block_returns(daily_returns)
    running = [[0.0] * k]
    for row in filtered:
        running.append([a + b for a, b in zip(running[-1], row)])
    averages = [[(running[s + sessions][i] - running[s][i]) / sessions for i in range(k)] for s in range(len(blocks))]
    pools: list[list[float]] = [[] for _ in range(k)]
    for value, average in zip(blocks, averages):
        pools[max(range(k), key=lambda i: average[i])].append(value)
    for i in range(k):
        if len(pools[i]) < MIN_REGIME_POOL:
            likeliest = sorted(range(len(blocks)), key=lambda s: -averages[s][i])[:MIN_REGIME_POOL]
            pools[i] = [blocks[s] for s in likeliest]
    return {
        "transition": monthly,
        "pools": pools,
        "start": list(filtered[-1]),
        "labels": list(REGIME_LABELS.get(k, [f"Regime {i + 1}" for i in range(k)])),
        "sessions": sessions,
    }
