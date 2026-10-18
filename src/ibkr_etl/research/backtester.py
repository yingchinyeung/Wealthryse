"""An event-driven daily backtest that can only use what was known at each decision.

:func:`run_backtest` walks the trading calendar. On each rebalance day the signal receives a
:class:`~.point_in_time.PointInTimeView` bound to that day's decision time and returns target
weights; the trades execute ``execution_lag`` sessions later at that day's close, paying
commission, half the bid-ask spread and, if given, a market-impact cost. Between rebalances
the holdings drift with prices. The result carries the daily NAV, returns, turnover, costs
and summary statistics, so a backtest's figures and its costs are always reported together.

Prices are closes in one currency. Weights are fractions of NAV; whatever is not invested is
cash earning ``cash_rate_daily``. Long only by default; ``allow_short=True`` accepts negative
weights. Pure Python; no database, storage or network access.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Mapping, Sequence

from .point_in_time import PointInTimeView

MODEL_VERSION = "research-backtester-v1"
TRADING_DAYS = 252
Signal = Callable[[PointInTimeView, str], Mapping[str, float]]
ImpactModel = Callable[[str, float, str], float]  # (asset, traded value, date) -> cost as a fraction of value


class BacktestError(ValueError):
    """The backtest's inputs cannot produce a meaningful result."""


def _calendar(prices: Mapping[str, Mapping[str, float]], start: str | None, end: str | None) -> list[str]:
    days = sorted({day for series in prices.values() for day in series})
    return [day for day in days if (start is None or day >= start) and (end is None or day <= end)]


def _is_rebalance(day: str, previous: str | None, frequency: str) -> bool:
    if previous is None or frequency == "daily":
        return True
    today, before = date.fromisoformat(day), date.fromisoformat(previous)
    if frequency == "weekly":
        return today.isocalendar()[:2] != before.isocalendar()[:2]
    if frequency == "monthly":
        return (today.year, today.month) != (before.year, before.month)
    raise BacktestError(f"Unknown rebalance frequency {frequency!r}; use daily, weekly or monthly.")


def _max_drawdown(navs: Sequence[float]) -> float:
    peak, worst = navs[0], 0.0
    for value in navs:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def summary_statistics(returns: Sequence[float], *, cash_rate_daily: float = 0.0) -> dict[str, float | None]:
    """Annualised return, volatility, Sharpe and maximum drawdown of daily returns."""
    if not returns:
        return {"annualised_return": None, "annualised_volatility": None, "sharpe_ratio": None,
                "maximum_drawdown": None}
    growth = math.prod(1.0 + r for r in returns)
    annual = growth ** (TRADING_DAYS / len(returns)) - 1.0 if growth > 0 else -1.0
    volatility = statistics.stdev(returns) * math.sqrt(TRADING_DAYS) if len(returns) > 1 else None
    excess = [r - cash_rate_daily for r in returns]
    sd = statistics.stdev(excess) if len(excess) > 1 else 0.0
    navs = [1.0]
    for r in returns:
        navs.append(navs[-1] * (1.0 + r))
    return {
        "annualised_return": annual,
        "annualised_volatility": volatility,
        "sharpe_ratio": statistics.fmean(excess) / sd * math.sqrt(TRADING_DAYS) if sd > 0 else None,
        "maximum_drawdown": _max_drawdown(navs),
    }


def run_backtest(
    prices: Mapping[str, Mapping[str, float]],
    signal: Signal,
    *,
    start: str | None = None,
    end: str | None = None,
    rebalance: str = "monthly",
    execution_lag: int = 1,
    commission_bps: float = 1.0,
    spread_bps: float = 5.0,
    impact: ImpactModel | None = None,
    cash_rate_daily: float = 0.0,
    initial_nav: float = 1.0,
    allow_short: bool = False,
    decision_cutoff: time = time(23, 59),
    extra_series: Mapping[str, Mapping[str, float]] | None = None,
    extra_timing: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run ``signal`` over the price history and report NAV, costs and statistics.

    ``signal(view, day)`` is called at ``decision_cutoff`` UTC on each rebalance day with a
    view of ``prices`` (and ``extra_series``, such as fundamentals with their own publication
    lags in ``extra_timing``) as known then. The targets it returns are traded
    ``execution_lag`` sessions later at the close; ``execution_lag`` must be at least 1 so a
    decision never trades at a price it has just seen.
    """
    if execution_lag < 1:
        raise BacktestError("execution_lag must be at least 1 session, or the backtest trades on prices it has just seen.")
    calendar = _calendar(prices, start, end)
    if len(calendar) < execution_lag + 2:
        raise BacktestError(f"The backtest needs at least {execution_lag + 2} trading days; it has {len(calendar)}.")
    view = PointInTimeView({**prices, **(extra_series or {})}, timing=extra_timing)

    nav, cash = float(initial_nav), float(initial_nav)
    units: dict[str, float] = {}
    last_price: dict[str, float] = {}
    pending: list[tuple[int, dict[str, float], str]] = []
    rows: list[dict[str, Any]] = []
    total_cost = turnover = cost_fraction = 0.0
    previous_day = None

    for index, day in enumerate(calendar):
        for asset, series in prices.items():
            if day in series and series[day] is not None and series[day] > 0:
                last_price[asset] = float(series[day])
        cash *= 1.0 + cash_rate_daily
        nav_before = cash + sum(units[a] * last_price[a] for a in units)

        traded_today = cost_today = 0.0
        while pending and pending[0][0] == index:
            _, targets, decided = pending.pop(0)
            for asset in sorted(set(targets) | set(units)):
                if asset not in last_price:
                    continue  # no price yet, so this asset is not traded
                price = last_price[asset]
                target_value = targets.get(asset, 0.0) * nav_before
                trade_value = target_value - units.get(asset, 0.0) * price
                if abs(trade_value) < 1e-12:
                    continue
                rate = (commission_bps + spread_bps / 2.0) / 10_000.0
                if impact is not None:
                    rate += max(0.0, impact(asset, abs(trade_value), day))
                cost = abs(trade_value) * rate
                units[asset] = units.get(asset, 0.0) + trade_value / price
                cash -= trade_value + cost
                traded_today += abs(trade_value)
                cost_today += cost
            units = {a: u for a, u in units.items() if abs(u) > 1e-15}

        nav = cash + sum(units[a] * last_price[a] for a in units)
        if nav <= 0:
            raise BacktestError(f"The portfolio's value fell to zero or below on {day}.")
        total_cost += cost_today
        cost_fraction += cost_today / nav_before if nav_before > 0 else 0.0
        turnover += traded_today / nav_before / 2.0 if nav_before > 0 else 0.0
        rows.append({"date": day, "nav": nav, "traded": traded_today, "cost": cost_today})

        if index + execution_lag < len(calendar) and _is_rebalance(day, previous_day, rebalance):
            decision = datetime.combine(date.fromisoformat(day), decision_cutoff, tzinfo=timezone.utc)
            targets = {asset: float(weight) for asset, weight in dict(signal(view.at(decision), day)).items()
                       if weight is not None and math.isfinite(float(weight))}
            if not allow_short and any(weight < 0 for weight in targets.values()):
                raise BacktestError(f"The signal returned a negative weight on {day}; pass allow_short=True to allow it.")
            if sum(abs(weight) for weight in targets.values()) > 1.0 + 1e-9 and not allow_short:
                raise BacktestError(f"The signal's weights add up to more than 100% on {day}.")
            pending.append((index + execution_lag, targets, day))
        previous_day = day

    returns = [rows[i]["nav"] / rows[i - 1]["nav"] - 1.0 for i in range(1, len(rows))]
    years = len(returns) / TRADING_DAYS
    stats = summary_statistics(returns, cash_rate_daily=cash_rate_daily)
    return {
        "available": True,
        "model_version": MODEL_VERSION,
        "period_start": calendar[0],
        "period_end": calendar[-1],
        "observations": len(returns),
        "final_nav": rows[-1]["nav"],
        "daily": rows,
        "returns": returns,
        **stats,
        "turnover_per_year": turnover / years if years > 0 else None,
        "total_cost": total_cost,
        "cost_drag_per_year": cost_fraction / years if years > 0 else None,
        "settings": {
            "rebalance": rebalance, "execution_lag": execution_lag, "commission_bps": commission_bps,
            "spread_bps": spread_bps, "impact": impact is not None, "cash_rate_daily": cash_rate_daily,
        },
        "method": (
            f"Signals see only data known at {decision_cutoff.strftime('%H:%M')} UTC on each {rebalance} "
            f"rebalance day and trade {execution_lag} session(s) later at the close, paying "
            f"{commission_bps:g}bp commission and half of a {spread_bps:g}bp spread"
            + (", plus market impact" if impact is not None else "") + ". Holdings drift between rebalances."
        ),
    }
