"""Portfolio modelling for the account Modelling tab.

Four models, all pure functions over plain mappings so the website, tests and
the AI analyst can share them:

* :func:`project_wealth` – Monte Carlo projection of NAV. Monthly returns are
  drawn from every overlapping 21-session window of the portfolio's own daily
  returns (a block bootstrap), so volatility clustering inside a month is kept.
* :func:`what_if` – risk and return of the current holdings against a proposed
  set of weights, both replayed over the same daily price history.
* :func:`optimise` – risk contribution of each holding, the efficient frontier
  and minimum-variance, risk-parity and maximum-Sharpe weights of the current
  holdings (long only, fully invested, with a per-holding cap).
* :func:`factor_model` – weekly regression of portfolio excess returns on the
  Fama-French five factors plus momentum (see :mod:`ibkr_etl.fama_french`).

Everything here is a hypothetical model built from history. Nothing in it is a
forecast or a recommendation; the website labels it that way.
"""
from __future__ import annotations

import calendar
import hashlib
import math
import random
import statistics
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

MODEL_VERSION = "portfolio-modelling-v1"
TRADING_DAYS = 252
MONTH_SESSIONS = 21
WEEKS_PER_YEAR = 52
PRICE_BREAK_THRESHOLD = 0.5  # same rule as the correlation matrix: a split, not a day's move
DISCLAIMER = (
    "Hypothetical model built from historical data. It is not a forecast, a guarantee "
    "or investment advice."
)


class ModellingInputError(ValueError):
    """Inputs that cannot produce a meaningful model; the message is shown to the user."""


# Shared helpers ------------------------------------------------------------------------


def _quantile(ordered: Sequence[float], probability: float) -> float:
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _seed(*parts: object) -> int:
    material = "|".join(
        ",".join(f"{v:.12g}" for v in part) if isinstance(part, (list, tuple)) else repr(part)
        for part in parts
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _max_drawdown(returns: Iterable[float]) -> float:
    level = peak = 1.0
    worst = 0.0
    for value in returns:
        level *= 1.0 + value
        peak = max(peak, level)
        worst = min(worst, level / peak - 1.0)
    return worst


def _matvec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [sum(a * b for a, b in zip(row, vector)) for row in matrix]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


# 1. Wealth projection ------------------------------------------------------------------

PROJECTION_PATHS = 2000
PROJECTION_MAX_YEARS = 30
PROJECTION_MIN_RETURNS = 63  # three months of sessions
PROJECTION_PERCENTILES = (0.05, 0.25, 0.5, 0.75, 0.95)


def monthly_block_returns(daily_returns: Sequence[float]) -> list[float]:
    """Compounded return of every overlapping 21-session window."""

    growth = [1.0 + float(r) for r in daily_returns]
    blocks = []
    for start in range(len(growth) - MONTH_SESSIONS + 1):
        level = 1.0
        for value in growth[start:start + MONTH_SESSIONS]:
            level *= value
        blocks.append(level - 1.0)
    return blocks


def project_wealth(
    daily_returns: Sequence[float],
    *,
    start_value: float,
    start_date: str,
    years: int = 10,
    monthly_flow: float = 0.0,
    target: float | None = None,
    annual_return: float | None = None,
    paths: int = PROJECTION_PATHS,
) -> dict[str, Any]:
    """Fan of simulated NAV paths with regular contributions (+) or withdrawals (-).

    ``annual_return`` replaces the historical average with the user's own
    assumption: every sampled month is shifted in log terms so the average
    compounds to that rate, and the spread of outcomes stays historical.
    """

    if not (isinstance(start_value, (int, float)) and math.isfinite(start_value) and start_value > 0):
        raise ModellingInputError("The starting value must be a positive amount.")
    if not 1 <= int(years) <= PROJECTION_MAX_YEARS:
        raise ModellingInputError(f"Choose a horizon between 1 and {PROJECTION_MAX_YEARS} years.")
    if not math.isfinite(monthly_flow):
        raise ModellingInputError("The monthly contribution must be a number.")
    if annual_return is not None and not (-0.5 < annual_return < 1.0):
        raise ModellingInputError("The return assumption must be between -50% and 100% a year.")
    if len(daily_returns) < PROJECTION_MIN_RETURNS:
        raise ModellingInputError(
            f"A projection needs at least {PROJECTION_MIN_RETURNS} daily returns; this portfolio has {len(daily_returns)}."
        )
    years = int(years)
    months = 12 * years
    blocks = monthly_block_returns(daily_returns)
    logs = [math.log1p(value) for value in blocks]
    historical_log_mean = statistics.fmean(logs)
    if annual_return is not None:
        shift = math.log1p(annual_return) / 12.0 - historical_log_mean
        logs = [value + shift for value in logs]
        blocks = [math.expm1(value) for value in logs]
    generator = random.Random(_seed(list(daily_returns), start_value, years, monthly_flow, annual_return, paths))
    columns: list[list[float]] = [[] for _ in range(months)]
    finals: list[float] = []
    depleted = 0
    for _ in range(paths):
        value = float(start_value)
        gone = False
        for month in range(months):
            if not gone:
                value = value * (1.0 + blocks[generator.randrange(len(blocks))]) + monthly_flow
                if value <= 0:
                    value, gone = 0.0, True
            columns[month].append(value)
        depleted += gone
        finals.append(value)
    first = date.fromisoformat(start_date[:10])
    series = [{"date": first.isoformat(), **{f"p{round(p * 100)}": float(start_value) for p in PROJECTION_PERCENTILES}}]
    for month, column in enumerate(columns, start=1):
        column.sort()
        series.append({
            "date": _add_months(first, month).isoformat(),
            **{f"p{round(p * 100)}": _quantile(column, p) for p in PROJECTION_PERCENTILES},
        })
    finals.sort()
    contributed = monthly_flow * months
    end = series[-1]
    monthly_volatility = statistics.stdev(blocks) if len(blocks) > 1 else 0.0
    assumed = math.expm1(12.0 * statistics.fmean(logs))
    warnings = []
    if len(daily_returns) < 3 * TRADING_DAYS:
        warnings.append(
            f"Based on {len(daily_returns)} daily returns (about {len(daily_returns) / TRADING_DAYS:.1f} years). "
            "A short history can make the outcome range look narrower or the growth rate higher than a full market cycle would."
        )
    if annual_return is None and assumed > 0.2:
        warnings.append("The historical growth rate is above 20% a year. Try a lower return assumption to stress the plan.")
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "method": (
            "Each simulated month draws one of the portfolio's own overlapping 21-session returns at random "
            f"(a block bootstrap of {len(daily_returns)} daily returns), adds the monthly contribution or withdrawal "
            f"at month end and repeats for {months} months across {paths:,} paths. Bands show the 5th, 25th, 50th, "
            "75th and 95th percentile of NAV at each month."
        ),
        "start_value": float(start_value),
        "start_date": first.isoformat(),
        "years": years,
        "paths": paths,
        "monthly_flow": monthly_flow,
        "total_flows": contributed,
        "target": target,
        "assumptions": {
            "return_source": "user" if annual_return is not None else "historical",
            "annual_return": assumed,
            "annual_volatility": monthly_volatility * math.sqrt(12),
            "daily_returns": len(daily_returns),
            "monthly_windows": len(blocks),
        },
        "series": series,
        "summary": {
            "median_end": end["p50"],
            "p5_end": end["p5"],
            "p95_end": end["p95"],
            "probability_target": (
                sum(1 for value in finals if value >= target) / len(finals) if target else None
            ),
            "probability_depleted": depleted / paths,
            "probability_loss": sum(1 for value in finals if value < start_value + contributed) / len(finals),
        },
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }


# Holding price returns ---------------------------------------------------------------

HOLDING_OBSERVATIONS = 252
HOLDING_MIN_COVERAGE = 0.6


def holding_return_matrix(
    prices: Mapping[str, Mapping[str, float]],
    *,
    observations: int = HOLDING_OBSERVATIONS,
    minimum_coverage: float = HOLDING_MIN_COVERAGE,
) -> dict[str, Any]:
    """Daily price returns of each holding on one shared calendar.

    A holding's return on a date is its move from its previous mark; a date on
    which it has no mark (a holiday in its market) counts as 0%, so the next
    return still covers the whole move. Holdings with returns on fewer than
    ``minimum_coverage`` of the window's dates are left out.
    """

    returns: dict[str, dict[str, float]] = {}
    breaks = 0
    all_dates: set[str] = set()
    for key, series_map in prices.items():
        series = sorted((str(day)[:10], float(v)) for day, v in series_map.items() if v and float(v) > 0)
        result: dict[str, float] = {}
        for (_, previous), (day, current) in zip(series, series[1:]):
            change = current / previous - 1.0
            if abs(change) > PRICE_BREAK_THRESHOLD:
                breaks += 1
                continue
            result[day] = change
        returns[key] = result
        all_dates.update(result)
    dates = sorted(all_dates)[-observations:]
    window = set(dates)
    matrix, coverage, excluded = {}, {}, []
    for key, result in returns.items():
        covered = len(window & set(result))
        coverage[key] = covered
        if dates and covered >= minimum_coverage * len(dates):
            matrix[key] = [result.get(day, 0.0) for day in dates]
        else:
            excluded.append(key)
    return {"dates": dates, "returns": matrix, "coverage": coverage, "excluded": excluded,
            "excluded_price_breaks": breaks}


def _series_stats(
    daily: Sequence[float],
    dates: Sequence[str],
    *,
    risk_free: float,
    benchmark: Mapping[str, float] | None,
) -> dict[str, Any]:
    n = len(daily)
    growth = 1.0
    for value in daily:
        growth *= 1.0 + value
    annual_return = growth ** (TRADING_DAYS / n) - 1.0 if n and growth > 0 else None
    volatility = statistics.stdev(daily) * math.sqrt(TRADING_DAYS) if n > 1 else None
    ordered = sorted(daily)
    var_cut = _quantile(ordered, 0.05) if ordered else None
    tail = [v for v in ordered if var_cut is not None and v <= var_cut]
    monthly = monthly_block_returns(daily) if n >= MONTH_SESSIONS else []
    beta = correlation = None
    if benchmark:
        pairs = [(p, benchmark[d]) for p, d in zip(daily, dates) if d in benchmark]
        if len(pairs) >= 40:
            left = [p for p, _ in pairs]
            right = [b for _, b in pairs]
            variance = statistics.variance(right)
            if variance:
                beta = statistics.covariance(left, right) / variance
                if statistics.stdev(left):
                    correlation = statistics.correlation(left, right)
    return {
        "annual_return": annual_return,
        "annual_volatility": volatility,
        "sharpe_ratio": (
            (annual_return - risk_free) / volatility
            if annual_return is not None and volatility else None
        ),
        "var_95_daily": -var_cut if var_cut is not None else None,
        "cvar_95_daily": -statistics.fmean(tail) if tail else None,
        "maximum_drawdown": _max_drawdown(daily),
        "worst_day": min(daily) if daily else None,
        "worst_month": min(monthly) if monthly else None,
        "beta": beta,
        "benchmark_correlation": correlation,
    }


def _weighted_series(
    weights: Mapping[str, float], matrix: Mapping[str, Sequence[float]], length: int, cash_daily: float
) -> tuple[list[float], float]:
    invested = sum(weights.values())
    series = [cash_daily * (1.0 - invested)] * length
    for key, weight in weights.items():
        if weight and key in matrix:
            column = matrix[key]
            series = [total + weight * value for total, value in zip(series, column)]
    return series, invested


# 2. What-if rebalancing ---------------------------------------------------------------

WHAT_IF_MAX_GROSS = 2.0


def what_if(
    current: Mapping[str, float],
    proposed: Mapping[str, float],
    returns: Mapping[str, Any],
    *,
    risk_free: float = 0.0,
    benchmark: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Current versus proposed weights (shares of NAV) over the same price history.

    Weights left over after the holdings are cash earning the risk-free rate; a
    negative remainder is borrowing at that rate. Holdings without enough price
    history are carried at 0% return and listed so the user can see it.
    """

    dates, matrix = returns["dates"], returns["returns"]
    if len(dates) < PROJECTION_MIN_RETURNS:
        raise ModellingInputError("Not enough shared daily price history to compare portfolios.")
    for label, weights in (("current", current), ("proposed", proposed)):
        if any(not math.isfinite(w) or w < 0 for w in weights.values()):
            raise ModellingInputError("Weights must be zero or positive; short positions are not modelled.")
        if sum(weights.values()) > WHAT_IF_MAX_GROSS + 1e-9:
            raise ModellingInputError(f"The {label} weights add up to more than {WHAT_IF_MAX_GROSS:.0%} of NAV.")
    cash_daily = (1.0 + risk_free) ** (1.0 / TRADING_DAYS) - 1.0
    result = {}
    for label, weights in (("current", current), ("proposed", proposed)):
        series, invested = _weighted_series(weights, matrix, len(dates), cash_daily)
        result[label] = {
            **_series_stats(series, dates, risk_free=risk_free, benchmark=benchmark),
            "invested_weight": invested,
            "cash_weight": 1.0 - invested,
            "unmodelled_weight": sum(w for k, w in weights.items() if k not in matrix),
        }
    differences = {
        key: (result["proposed"][key] - result["current"][key])
        if result["proposed"][key] is not None and result["current"][key] is not None else None
        for key in ("annual_return", "annual_volatility", "sharpe_ratio", "var_95_daily",
                    "cvar_95_daily", "maximum_drawdown", "worst_month", "beta")
    }
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "window_start": dates[0],
        "window_end": dates[-1],
        "observations": len(dates),
        "risk_free_rate": risk_free,
        **result,
        "difference": differences,
        "disclaimer": DISCLAIMER,
    }


# 3. Optimisation and risk budget ------------------------------------------------------

OPTIMISE_MAX_HOLDINGS = 20
COVARIANCE_SHRINKAGE = 0.2   # toward the diagonal, which keeps the matrix well conditioned
RETURN_SHRINKAGE = 0.5       # toward the average holding, since one year of means is noisy
FRONTIER_POINTS = 30


def covariance_matrix(columns: Sequence[Sequence[float]]) -> list[list[float]]:
    """Annualised sample covariance shrunk toward its own diagonal."""

    n = len(columns)
    means = [statistics.fmean(c) for c in columns]
    length = len(columns[0])
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            value = sum((a - means[i]) * (b - means[j]) for a, b in zip(columns[i], columns[j])) / (length - 1)
            value *= TRADING_DAYS
            if i != j:
                value *= 1.0 - COVARIANCE_SHRINKAGE
            cov[i][j] = cov[j][i] = value
    return cov


def risk_contributions(weights: Sequence[float], cov: Sequence[Sequence[float]]) -> list[float]:
    """Each holding's share of portfolio variance (sums to 1)."""

    marginal = _matvec(cov, weights)
    variance = _dot(weights, marginal)
    if variance <= 0:
        return [0.0 for _ in weights]
    return [w * m / variance for w, m in zip(weights, marginal)]


def project_capped_simplex(values: Sequence[float], cap: float) -> list[float]:
    """Closest weights to ``values`` that are between 0 and ``cap`` and sum to 1."""

    low, high = min(values) - cap - 1.0, max(values)
    for _ in range(100):
        tau = (low + high) / 2.0
        total = sum(min(cap, max(0.0, v - tau)) for v in values)
        if total > 1.0:
            low = tau
        else:
            high = tau
    tau = (low + high) / 2.0
    weights = [min(cap, max(0.0, v - tau)) for v in values]
    scale = sum(weights)
    return [w / scale for w in weights] if scale else [1.0 / len(values)] * len(values)


def _largest_eigenvalue(cov: Sequence[Sequence[float]]) -> float:
    vector = [1.0] * len(cov)
    value = 1.0
    for _ in range(50):
        product = _matvec(cov, vector)
        value = math.sqrt(_dot(product, product)) or 1.0
        vector = [x / value for x in product]
    return value


def mean_variance_weights(
    cov: Sequence[Sequence[float]],
    mu: Sequence[float],
    *,
    risk_aversion_inverse: float,
    cap: float,
    start: Sequence[float] | None = None,
    iterations: int = 400,
) -> list[float]:
    """Long-only weights minimising w'Σw - λ·μ'w (accelerated projected gradient)."""

    n = len(cov)
    step = 1.0 / (2.0 * _largest_eigenvalue(cov))
    weights = list(start) if start else [1.0 / n] * n
    momentum, t = list(weights), 1.0
    for _ in range(iterations):
        gradient = [2.0 * g - risk_aversion_inverse * m for g, m in zip(_matvec(cov, momentum), mu)]
        updated = project_capped_simplex([w - step * g for w, g in zip(momentum, gradient)], cap)
        t_next = (1.0 + math.sqrt(1.0 + 4.0 * t * t)) / 2.0
        momentum = [u + (t - 1.0) / t_next * (u - w) for u, w in zip(updated, weights)]
        if max(abs(u - w) for u, w in zip(updated, weights)) < 1e-10:
            weights = updated
            break
        weights, t = updated, t_next
    return weights


def risk_parity_weights(cov: Sequence[Sequence[float]], iterations: int = 500) -> list[float]:
    """Equal-risk-contribution weights by cyclical coordinate descent."""

    n = len(cov)
    x = [1.0 / math.sqrt(cov[i][i]) if cov[i][i] > 0 else 1.0 for i in range(n)]
    budget = 1.0 / n
    for _ in range(iterations):
        previous = list(x)
        for i in range(n):
            other = sum(cov[i][j] * x[j] for j in range(n) if j != i)
            a = cov[i][i]
            if a <= 0:
                continue
            x[i] = (-other + math.sqrt(other * other + 4.0 * a * budget)) / (2.0 * a)
        if max(abs(a - b) for a, b in zip(x, previous)) < 1e-12:
            break
    total = sum(x)
    return [value / total for value in x]


def _portfolio_point(weights: Sequence[float], cov, mu, risk_free: float) -> dict[str, float | None]:
    volatility = math.sqrt(max(0.0, _dot(weights, _matvec(cov, weights))))
    expected = _dot(weights, mu)
    return {
        "expected_return": expected,
        "volatility": volatility,
        "sharpe_ratio": (expected - risk_free) / volatility if volatility else None,
    }


def optimise(
    current: Mapping[str, float],
    labels: Mapping[str, Mapping[str, Any]],
    returns: Mapping[str, Any],
    *,
    risk_free: float = 0.0,
    cap: float = 0.25,
) -> dict[str, Any]:
    """Risk budget, efficient frontier and three optimised versions of the current holdings.

    Weights are shares of the invested sleeve (they sum to 100%); cash and
    holdings without enough price history stay outside the optimisation.
    """

    matrix = returns["returns"]
    keys = [k for k in sorted(current, key=lambda k: -current[k]) if k in matrix and current[k] > 0]
    keys = keys[:OPTIMISE_MAX_HOLDINGS]
    if len(keys) < 2:
        raise ModellingInputError("Optimisation needs at least two holdings with a year of daily prices.")
    if len(returns["dates"]) < PROJECTION_MIN_RETURNS:
        raise ModellingInputError("Not enough shared daily price history to estimate risk.")
    if not 0.0 < cap <= 1.0:
        raise ModellingInputError("The maximum weight per holding must be between 1% and 100%.")
    cap = max(cap, 1.0 / len(keys) + 1e-9)
    columns = [matrix[k] for k in keys]
    cov = covariance_matrix(columns)
    raw_mu = [statistics.fmean(c) * TRADING_DAYS for c in columns]
    average = statistics.fmean(raw_mu)
    mu = [RETURN_SHRINKAGE * average + (1.0 - RETURN_SHRINKAGE) * m for m in raw_mu]
    sleeve = sum(current[k] for k in keys)
    weights_now = [current[k] / sleeve for k in keys]

    minimum = mean_variance_weights(cov, mu, risk_aversion_inverse=0.0, cap=cap, iterations=1500)
    parity = risk_parity_weights(cov)
    frontier, start = [], minimum
    top = max(abs(m) for m in mu) or 1.0
    best = None
    for index in range(FRONTIER_POINTS):
        lam = 0.0 if index == 0 else (4.0 * _largest_eigenvalue(cov) / top) * (1.6 ** (index - FRONTIER_POINTS + 8))
        weights = mean_variance_weights(cov, mu, risk_aversion_inverse=lam, cap=cap, start=start)
        start = weights
        point = _portfolio_point(weights, cov, mu, risk_free)
        if frontier and point["volatility"] <= frontier[-1]["volatility"] + 1e-6:
            continue
        frontier.append(point)
        if point["sharpe_ratio"] is not None and (best is None or point["sharpe_ratio"] > best[0]["sharpe_ratio"]):
            best = (point, weights)

    portfolios = {
        "current": weights_now,
        "minimum_variance": minimum,
        "risk_parity": parity,
        "maximum_sharpe": best[1] if best else minimum,
    }
    holdings = []
    contributions = {name: risk_contributions(w, cov) for name, w in portfolios.items()}
    for index, key in enumerate(keys):
        label = labels.get(key, {})
        holdings.append({
            "conid": key,
            "symbol": label.get("symbol") or key,
            "description": label.get("description") or "",
            "annual_volatility": math.sqrt(cov[index][index]),
            "historical_return": raw_mu[index],
            "expected_return": mu[index],
            **{f"{name}_weight": weights[index] for name, weights in portfolios.items()},
            **{f"{name}_risk": contributions[name][index] for name in portfolios},
        })
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "window_start": returns["dates"][0],
        "window_end": returns["dates"][-1],
        "observations": len(returns["dates"]),
        "risk_free_rate": risk_free,
        "maximum_weight": cap,
        "sleeve_weight": sleeve,
        "holdings": holdings,
        "portfolios": {name: _portfolio_point(w, cov, mu, risk_free) for name, w in portfolios.items()},
        "frontier": frontier,
        "excluded": [k for k in current if k not in keys and current[k] > 0],
        "method": (
            f"Covariance of daily price returns over {len(returns['dates'])} sessions, annualised and shrunk "
            f"{COVARIANCE_SHRINKAGE:.0%} toward the diagonal. Expected returns are each holding's historical "
            f"average shrunk {RETURN_SHRINKAGE:.0%} toward the average holding. Long only, fully invested, at most "
            f"{cap:.0%} in one holding (risk parity is uncapped). Maximum Sharpe is the best of the frontier points."
        ),
        "disclaimer": DISCLAIMER,
    }


# 4. Factor model ---------------------------------------------------------------------

FACTOR_NAMES = ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM")
FACTOR_LABELS = {
    "Mkt-RF": "Market",
    "SMB": "Size (small minus big)",
    "HML": "Value (high minus low book-to-market)",
    "RMW": "Profitability (robust minus weak)",
    "CMA": "Investment (conservative minus aggressive)",
    "MOM": "Momentum (winners minus losers)",
}
FACTOR_MIN_WEEKS = 26
FACTOR_MAX_WEEKS = 156


def weekly_returns(daily: Iterable[tuple[str, float]]) -> dict[str, float]:
    """Compound daily returns into ISO weeks keyed by the week's Monday."""

    weeks: dict[str, float] = {}
    for day, value in daily:
        parsed = date.fromisoformat(str(day)[:10])
        monday = date.fromordinal(parsed.toordinal() - parsed.weekday()).isoformat()
        weeks[monday] = (1.0 + weeks.get(monday, 0.0)) * (1.0 + value) - 1.0
    return dict(sorted(weeks.items()))


def _solve_inverse(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    augmented = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-14:
            raise ModellingInputError("The factor returns are collinear over this window; the model cannot be fitted.")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [v / scale for v in augmented[column]]
        for row in range(n):
            if row != column and augmented[row][column]:
                factor = augmented[row][column]
                augmented[row] = [a - factor * b for a, b in zip(augmented[row], augmented[column])]
    return [row[n:] for row in augmented]


def ols(y: Sequence[float], x_columns: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Ordinary least squares with an intercept; returns coefficients, t-statistics and R²."""

    n, k = len(y), len(x_columns) + 1
    design = [[1.0, *(column[i] for column in x_columns)] for i in range(n)]
    xtx = [[sum(row[a] * row[b] for row in design) for b in range(k)] for a in range(k)]
    xty = [sum(row[a] * value for row, value in zip(design, y)) for a in range(k)]
    inverse = _solve_inverse(xtx)
    beta = _matvec(inverse, xty)
    fitted = [_dot(row, beta) for row in design]
    residuals = [value - fit for value, fit in zip(y, fitted)]
    ssr = sum(r * r for r in residuals)
    mean = statistics.fmean(y)
    sst = sum((value - mean) ** 2 for value in y)
    dof = n - k
    sigma2 = ssr / dof if dof > 0 else float("nan")
    errors = [math.sqrt(max(0.0, sigma2 * inverse[i][i])) for i in range(k)]
    return {
        "coefficients": beta,
        "standard_errors": errors,
        "t_statistics": [b / e if e else None for b, e in zip(beta, errors)],
        "r_squared": 1.0 - ssr / sst if sst else None,
        "adjusted_r_squared": 1.0 - (ssr / dof) / (sst / (n - 1)) if sst and dof > 0 else None,
        "residual_volatility": math.sqrt(sigma2) if dof > 0 else None,
        "observations": n,
    }


def factor_model(
    portfolio_daily: Iterable[tuple[str, float]],
    factors_daily: Mapping[str, Mapping[str, float]],
    *,
    region_label: str,
    source_url: str | None = None,
    labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Weekly Fama-French five-factor plus momentum regression of portfolio excess returns.

    ``factors_daily`` maps ISO date to decimal factor returns including ``RF``.
    Weekly returns are used because the portfolio and the factors close at
    different times of day in different markets.
    """

    portfolio = weekly_returns(portfolio_daily)
    available = [name for name in FACTOR_NAMES if any(name in row for row in factors_daily.values())]
    if "Mkt-RF" not in available:
        raise ModellingInputError("The factor data has no market factor.")
    factor_weeks: dict[str, dict[str, float]] = {}
    for day in sorted(factors_daily):
        row = factors_daily[day]
        if any(name not in row for name in (*available, "RF")):
            continue
        parsed = date.fromisoformat(day)
        monday = date.fromordinal(parsed.toordinal() - parsed.weekday()).isoformat()
        week = factor_weeks.setdefault(monday, {name: 0.0 for name in (*available, "RF")})
        for name in (*available, "RF"):
            week[name] = (1.0 + week[name]) * (1.0 + row[name]) - 1.0
    # The last week of either series may be incomplete; drop it.
    common = sorted(set(portfolio) & set(factor_weeks))
    if common and (common[-1] == max(portfolio) or common[-1] == max(factor_weeks)):
        common = common[:-1]
    common = common[-FACTOR_MAX_WEEKS:]
    if len(common) < FACTOR_MIN_WEEKS:
        raise ModellingInputError(
            f"The factor model needs at least {FACTOR_MIN_WEEKS} weeks where both the portfolio and the "
            f"factor data have returns; there are {len(common)}."
        )
    y = [portfolio[w] - factor_weeks[w]["RF"] for w in common]
    columns = [[factor_weeks[w][name] for w in common] for name in available]
    fit = ols(y, columns)
    alpha = fit["coefficients"][0]
    exposures = []
    for index, name in enumerate(available, start=1):
        mean_factor = statistics.fmean(columns[index - 1])
        exposures.append({
            "factor": name,
            "label": (labels or FACTOR_LABELS).get(name, FACTOR_LABELS[name]),
            "beta": fit["coefficients"][index],
            "t_statistic": fit["t_statistics"][index],
            "significant": fit["t_statistics"][index] is not None and abs(fit["t_statistics"][index]) >= 2.0,
            "factor_annual_return": mean_factor * WEEKS_PER_YEAR,
            "return_contribution": fit["coefficients"][index] * mean_factor * WEEKS_PER_YEAR,
        })
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "region": region_label,
        "source_url": source_url,
        "window_start": common[0],
        "window_end": common[-1],
        "weeks": len(common),
        "alpha_annual": alpha * WEEKS_PER_YEAR,
        "alpha_t_statistic": fit["t_statistics"][0],
        "r_squared": fit["r_squared"],
        "adjusted_r_squared": fit["adjusted_r_squared"],
        "residual_volatility_annual": (
            fit["residual_volatility"] * math.sqrt(WEEKS_PER_YEAR) if fit["residual_volatility"] is not None else None
        ),
        "portfolio_excess_return_annual": statistics.fmean(y) * WEEKS_PER_YEAR,
        "exposures": exposures,
        "momentum_included": "MOM" in available,
        "method": (
            "Weekly portfolio returns (time-weighted, net of contributions and withdrawals) minus the risk-free "
            "rate, regressed on weekly factor returns with an intercept. Beta is the sensitivity to each factor; "
            "|t| ≥ 2 is marked significant. Return contribution is beta × the factor's average return over the "
            "window, annualised. Alpha is the average return the factors do not explain."
        ),
        "disclaimer": DISCLAIMER,
    }
