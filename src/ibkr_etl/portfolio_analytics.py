"""Institutional return, drawdown and concentration analytics for the website.

Pure functions only. Callers load the NAV series and net external flows exactly
as for the period returns table and pass plain mappings in, so every figure here
uses the same chain-linked daily Modified Dietz return as
:mod:`ibkr_etl.performance_periods`. Monthly and yearly calendar returns are
computed with :func:`performance_periods.period_return`, so a month in the
calendar always equals that month's figure in the period returns table.

An invalid daily return (a non-positive denominator or a loss of 100% or more)
fails the whole analysis closed instead of being skipped.
"""
from __future__ import annotations

import calendar
import math
import statistics
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from .performance_periods import (
    PerformanceInputError,
    _annualized,
    period_return,
    validated_flows,
    validated_nav,
)


CALCULATION_VERSION = "portfolio-analytics-v1"
TRADING_DAYS_PER_YEAR = 252
ROLLING_VOLATILITY_OBSERVATIONS = 63
ROLLING_RETURN_DAYS = 365
DRAWDOWN_EPISODES = 5
METHODOLOGY = (
    "Daily returns chain-link the same cash-flow-adjusted Modified Dietz returns as the "
    "period returns table, on every validated NAV date. Monthly and calendar-year cells are "
    "time-weighted returns from the last NAV before the period to the last NAV in it. "
    "Volatility and downside deviation are annualised with 252 observations a year; Sortino "
    "uses a 0% minimum acceptable return. Annualised return and Calmar need at least one year "
    "of history. Skewness and excess kurtosis are bias-adjusted sample statistics. Rolling "
    "volatility uses the latest 63 daily returns (about three months); rolling return compares "
    "each NAV with the last NAV at least 12 months earlier."
)


def daily_return_series(
    nav: Mapping[str, float], flows: Mapping[str, float]
) -> tuple[list[tuple[str, float]], list[str]]:
    """Daily ``(date, return)`` pairs and any intervals that failed validation.

    ``nav`` and ``flows`` must be outputs of ``validated_nav`` and
    ``validated_flows`` (sorted ISO-date keys).
    """

    dates = list(nav)
    flow_days = list(flows)
    returns: list[tuple[str, float]] = []
    failures: list[str] = []
    for previous_day, current_day in zip(dates, dates[1:]):
        lo = bisect_right(flow_days, previous_day)
        hi = bisect_right(flow_days, current_day)
        external = sum(flows[day] for day in flow_days[lo:hi])
        denominator = nav[previous_day] + 0.5 * external
        if not denominator > 0:
            failures.append(f"The cash-flow-adjusted denominator is not positive on {current_day}.")
            continue
        daily = (nav[current_day] - nav[previous_day] - external) / denominator
        if not math.isfinite(daily) or daily <= -1:
            failures.append(f"The portfolio return is invalid on {current_day}.")
            continue
        returns.append((current_day, daily))
    return returns, failures


def monthly_calendar(
    nav: Mapping[str, float], flows: Mapping[str, float], as_of: str
) -> list[dict[str, Any]]:
    """One row per calendar year (newest first) with 12 monthly TWRs and the year's TWR."""

    if not nav:
        return []
    first = date.fromisoformat(next(iter(nav)))
    end = date.fromisoformat(as_of)
    rows = []
    for year in range(end.year, first.year - 1, -1):
        months = []
        for month in range(1, 13):
            month_start = date(year, month, 1)
            month_end = date(year, month, calendar.monthrange(year, month)[1])
            if month_end < first or month_start > end:
                months.append(None)
                continue
            result = period_return(
                nav, flows, month_start.isoformat(), min(month_end, end).isoformat(),
                key=f"{year}-{month:02d}", label=month_start.strftime("%b %Y"), kind="month",
            )
            months.append(_calendar_cell(result, month_end > end))
        year_result = period_return(
            nav, flows, f"{year}-01-01", min(date(year, 12, 31), end).isoformat(),
            key=f"year_{year}", label=str(year), kind="calendar_year",
        )
        rows.append({
            "year": year,
            "months": months,
            "year_return": _calendar_cell(year_result, date(year, 12, 31) > end),
        })
    return rows


def _calendar_cell(result, to_date: bool) -> dict[str, Any] | None:
    if result.status == "empty":
        return None
    return {
        "twr": result.twr,
        "status": result.status,
        "partial": result.partial,
        "to_date": to_date,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "issues": list(result.issues),
    }


def drawdown_analysis(
    returns: list[tuple[str, float]], first_date: str, episodes: int = DRAWDOWN_EPISODES
) -> dict[str, Any]:
    """Underwater series and the deepest peak-to-trough episodes.

    ``first_date`` is the NAV date the first return is measured from; it is the
    starting peak at wealth 1.0.
    """

    wealth = peak = 1.0
    peak_date = first_date
    series = [{"date": first_date, "drawdown": 0.0}]
    found: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for day, daily in returns:
        wealth *= 1.0 + daily
        if wealth >= peak:
            if current is not None:
                current["recovery_date"] = day
                found.append(current)
                current = None
            peak, peak_date = wealth, day
            series.append({"date": day, "drawdown": 0.0})
            continue
        drawdown = wealth / peak - 1.0
        series.append({"date": day, "drawdown": drawdown})
        if current is None:
            current = {"peak_date": peak_date, "trough_date": day, "depth": drawdown,
                       "recovery_date": None}
        elif drawdown < current["depth"]:
            current["trough_date"], current["depth"] = day, drawdown
    if current is not None:
        found.append(current)
    last_date = series[-1]["date"]
    for episode in found:
        peak_day = date.fromisoformat(episode["peak_date"])
        episode["days_to_trough"] = (date.fromisoformat(episode["trough_date"]) - peak_day).days
        recovery = episode["recovery_date"]
        episode["recovered"] = recovery is not None
        episode["days_to_recover"] = (
            (date.fromisoformat(recovery) - date.fromisoformat(episode["trough_date"])).days
            if recovery else None
        )
        episode["length_days"] = (date.fromisoformat(recovery or last_date) - peak_day).days
    deepest = sorted(found, key=lambda item: item["depth"])[:episodes]
    return {
        "series": series,
        "maximum_drawdown": min((item["depth"] for item in found), default=0.0),
        "current_drawdown": series[-1]["drawdown"],
        "episodes": deepest,
    }


def _skewness(values: list[float]) -> float | None:
    n = len(values)
    if n < 3:
        return None
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values)
    if deviation == 0:
        return None
    third = sum(((value - mean) / deviation) ** 3 for value in values)
    return n / ((n - 1) * (n - 2)) * third


def _excess_kurtosis(values: list[float]) -> float | None:
    n = len(values)
    if n < 4:
        return None
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values)
    if deviation == 0:
        return None
    fourth = sum(((value - mean) / deviation) ** 4 for value in values)
    return (n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) * fourth
            - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3)))


def return_statistics(
    returns: list[tuple[str, float]],
    first_date: str,
    months: list[dict[str, Any]],
    maximum_drawdown: float,
) -> dict[str, Any]:
    """Distribution and risk-adjusted statistics of the daily return series."""

    values = [value for _, value in returns]
    last_date = returns[-1][0] if returns else first_date
    total = math.prod(1.0 + value for value in values) - 1.0 if values else None
    annualized = _annualized(total, first_date, last_date)
    root_year = math.sqrt(TRADING_DAYS_PER_YEAR)
    mean = statistics.fmean(values) if values else None
    deviation = statistics.stdev(values) if len(values) >= 2 else None
    downside = (
        math.sqrt(sum(min(value, 0.0) ** 2 for value in values) / len(values)) if values else None
    )
    best = max(returns, key=lambda item: item[1]) if returns else None
    worst = min(returns, key=lambda item: item[1]) if returns else None
    measured = [month for month in months if month["twr"] is not None]
    positive = [month["twr"] for month in measured if month["twr"] > 0]
    negative = [month["twr"] for month in measured if month["twr"] < 0]
    best_month = max(measured, key=lambda month: month["twr"]) if measured else None
    worst_month = min(measured, key=lambda month: month["twr"]) if measured else None
    return {
        "period_start": first_date,
        "period_end": last_date,
        "return_observations": len(values),
        "total_return": total,
        "annualized_return": annualized,
        "annualized_volatility": deviation * root_year if deviation is not None else None,
        "downside_deviation": downside * root_year if downside is not None else None,
        "sortino_ratio": (
            mean / downside * root_year if mean is not None and downside not in (None, 0) else None
        ),
        "calmar_ratio": (
            annualized / abs(maximum_drawdown)
            if annualized is not None and maximum_drawdown < 0 else None
        ),
        "skewness": _skewness(values),
        "excess_kurtosis": _excess_kurtosis(values),
        "best_day": {"date": best[0], "return": best[1]} if best else None,
        "worst_day": {"date": worst[0], "return": worst[1]} if worst else None,
        "best_month": (
            {"month": best_month["month"], "return": best_month["twr"]} if best_month else None
        ),
        "worst_month": (
            {"month": worst_month["month"], "return": worst_month["twr"]} if worst_month else None
        ),
        "measured_months": len(measured),
        "positive_months": len(positive),
        "positive_month_ratio": len(positive) / len(measured) if measured else None,
        "average_gain_month": statistics.fmean(positive) if positive else None,
        "average_loss_month": statistics.fmean(negative) if negative else None,
        "gain_loss_ratio": (
            statistics.fmean(positive) / abs(statistics.fmean(negative))
            if positive and negative else None
        ),
    }


def rolling_series(
    returns: list[tuple[str, float]],
    first_date: str,
    *,
    volatility_window: int = ROLLING_VOLATILITY_OBSERVATIONS,
    return_days: int = ROLLING_RETURN_DAYS,
) -> list[dict[str, Any]]:
    """Rolling annualised volatility and trailing 12-month return per NAV date."""

    dates = [first_date] + [day for day, _ in returns]
    wealth = [1.0]
    for _, value in returns:
        wealth.append(wealth[-1] * (1.0 + value))
    values = [value for _, value in returns]
    root_year = math.sqrt(TRADING_DAYS_PER_YEAR)
    series = []
    for index in range(1, len(dates)):
        day = dates[index]
        window = values[max(0, index - volatility_window):index]
        volatility = (
            statistics.stdev(window) * root_year if len(window) == volatility_window else None
        )
        cutoff = (date.fromisoformat(day) - timedelta(days=return_days)).isoformat()
        base = bisect_right(dates, cutoff) - 1
        trailing = wealth[index] / wealth[base] - 1.0 if base >= 0 else None
        if volatility is None and trailing is None:
            continue
        series.append({"date": day, "volatility": volatility, "trailing_return": trailing})
    return series


def portfolio_analytics(
    nav_values: Mapping[object, object] | Iterable[tuple[object, object]],
    flow_values: Mapping[object, object] | Iterable[tuple[object, object]] | None,
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Monthly calendar, drawdowns, return statistics and rolling risk as JSON-ready data."""

    nav = validated_nav(nav_values)
    flows = validated_flows(flow_values)
    if as_of is not None:
        nav = {day: value for day, value in nav.items() if day <= as_of}
        flows = {day: value for day, value in flows.items() if day <= as_of}
    base = {"calculation_version": CALCULATION_VERSION, "methodology": METHODOLOGY}
    if len(nav) < 2:
        return {**base, "available": False, "effective_as_of": next(reversed(nav), None),
                "issues": ["At least two NAV observations are needed."]}
    first_date, effective = next(iter(nav)), next(reversed(nav))
    returns, failures = daily_return_series(nav, flows)
    if failures:
        return {**base, "available": False, "effective_as_of": effective, "issues": failures}
    calendar_rows = monthly_calendar(nav, flows, as_of or effective)
    months = [
        {"month": f"{row['year']}-{index + 1:02d}", "twr": cell["twr"]}
        for row in calendar_rows
        for index, cell in enumerate(row["months"])
        if cell is not None and cell["status"] in ("pass", "partial")
    ]
    drawdowns = drawdown_analysis(returns, first_date)
    return {
        **base,
        "available": True,
        "inception_date": first_date,
        "effective_as_of": effective,
        "statistics": return_statistics(
            returns, first_date, months, drawdowns["maximum_drawdown"]
        ),
        "monthly_calendar": calendar_rows,
        "drawdown": drawdowns,
        "rolling": rolling_series(returns, first_date),
        "issues": [],
    }


def holdings_concentration(view: Mapping[str, Any]) -> dict[str, Any]:
    """Top-weight, effective-number and currency concentration of a holdings view.

    ``view`` is the output of ``holdings_view.enrich_holdings``. Holdings without
    a weight (for example, those missing FX) are excluded and counted.
    """

    holdings = view.get("holdings") or []
    weighted = [row for row in holdings if row.get("weight") is not None]
    if not weighted or not view.get("weight_basis"):
        return {"available": False, "holdings_count": len(holdings),
                "reason": "No weighted holdings are available for this date."}
    weights = sorted((abs(float(row["weight"])) for row in weighted), reverse=True)
    gross = sum(weights)
    net = sum(float(row["weight"]) for row in weighted)
    herfindahl = sum((weight / gross) ** 2 for weight in weights) if gross else None
    currencies: dict[str, float] = defaultdict(float)
    for row in weighted:
        currencies[str(row.get("currency") or "Unknown").upper()] += float(row["weight"])
    largest = max(weighted, key=lambda row: abs(float(row["weight"])))
    return {
        "available": True,
        "weight_basis": view["weight_basis"],
        "holdings_count": len(holdings),
        "weighted_count": len(weighted),
        "largest": {"symbol": largest.get("symbol") or largest.get("conid"),
                    "description": largest.get("description"),
                    "weight": float(largest["weight"])},
        "top_1_weight": sum(weights[:1]),
        "top_5_weight": sum(weights[:5]),
        "top_10_weight": sum(weights[:10]),
        "gross_exposure": gross,
        "net_exposure": net,
        "herfindahl_index": herfindahl,
        "effective_holdings": 1.0 / herfindahl if herfindahl else None,
        "residual_weight": 1.0 - net if view["weight_basis"] == "nav" else None,
        "currency_exposure": [
            {"currency": currency, "weight": weight}
            for currency, weight in sorted(currencies.items(), key=lambda item: -abs(item[1]))
        ],
    }


__all__ = [
    "CALCULATION_VERSION",
    "PerformanceInputError",
    "daily_return_series",
    "drawdown_analysis",
    "holdings_concentration",
    "monthly_calendar",
    "portfolio_analytics",
    "return_statistics",
    "rolling_series",
]
