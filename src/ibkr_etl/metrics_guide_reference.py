"""An independent second implementation of the Metrics guide's formulas, to check the website against.

Written from the technical formulas in the guide (``metrics_guide``), not from the website's code,
and sharing none of it: the daily return, wealth index, quantile, moments and drawdown are all
recomputed here. ``conformance`` runs both on the same NAV and flows and lists every figure where
they disagree, so a code change that drifts from what the guide promises is caught.

Pure functions, no database. The guide_check job feeds it an account's recorded values through the
``get_guide_conformance`` tool, and the risk watch's inbox shows the result.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date
from typing import Any, Mapping, Sequence

REFERENCE_VERSION = "metrics-guide-reference-v1"
YEAR_SESSIONS = 252  # the guide's annualisation convention
# Same formula, same inputs: anything beyond float noise is a real difference.
ABSOLUTE_TOLERANCE = 1e-9
RELATIVE_TOLERANCE = 1e-6

# (guide definition, key in this module's results, where the website's figure comes from)
CHECKS = (
    ("Total return", "total_return", "statistics"),
    ("TWR annualised", "annualized_twr", "statistics"),
    ("Annualized return", "annualized_return", "risk"),
    ("Annualized volatility", "annualized_volatility", "statistics"),
    ("Downside deviation", "downside_deviation", "statistics"),
    ("Sortino ratio", "sortino_ratio", "statistics"),
    ("Calmar ratio", "calmar_ratio", "statistics"),
    ("Skewness", "skewness", "statistics"),
    ("Excess kurtosis", "excess_kurtosis", "statistics"),
    ("Best and worst day", "best_day", "statistics"),
    ("Best and worst day", "worst_day", "statistics"),
    ("Maximum drawdown (since inception)", "maximum_drawdown", "drawdown"),
    ("Current drawdown", "current_drawdown", "drawdown"),
    ("Win rate", "win_rate", "risk"),
    ("VaR 95% — daily", "var_95_daily", "risk"),
    ("Daily Expected Tail Loss (95%)", "cvar_95_daily", "risk"),
)
# The analyst's get_risk_metrics figures checked against the guide over the analyst's own window.
# (guide definition, key here, key in get_risk_metrics)
AGENT_CHECKS = (
    ("Total return", "total_return", "total_return"),
    ("Annualized volatility", "annualized_volatility", "annualized_volatility"),
    ("Downside deviation", "downside_deviation", "downside_deviation"),
    ("Maximum drawdown (since inception)", "maximum_drawdown", "max_drawdown"),
)


def daily_returns(nav: Mapping[str, float], flows: Mapping[str, float]) -> list[tuple[str, float]]:
    """rₜ = (Vₜ − Vₜ₋₁ − Fₜ) ÷ (Vₜ₋₁ + 0.5 × Fₜ), F summed after the prior date through the current date."""

    days = sorted(nav)
    flow_days = sorted(flows)
    out = []
    for before, now in zip(days, days[1:]):
        external = math.fsum(flows[d] for d in flow_days[bisect_right(flow_days, before):bisect_right(flow_days, now)])
        start = nav[before] + 0.5 * external
        if start <= 0:
            raise ValueError(f"non-positive starting value on {now}")
        out.append((now, (nav[now] - nav[before] - external) / start))
    return out


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _sample_sd(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    m = _mean(values)
    return math.sqrt(math.fsum((v - m) ** 2 for v in values) / (len(values) - 1))


def _percentile(values: Sequence[float], p: float) -> float:
    """Linearly interpolated sample quantile (position (N − 1) × p)."""

    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = int(math.floor(position))
    fraction = position - low
    if low + 1 >= len(ordered):
        return ordered[low]
    return ordered[low] * (1 - fraction) + ordered[low + 1] * fraction


def statistics(returns: Sequence[tuple[str, float]], first_date: str) -> dict[str, Any]:
    """Every figure in ``CHECKS`` from the guide's formulas."""

    values = [r for _, r in returns]
    n = len(values)
    if n < 2:
        raise ValueError("at least two daily returns are needed")
    wealth, peak, worst, path = 1.0, 1.0, 0.0, []
    for value in values:
        wealth *= 1 + value
        peak = max(peak, wealth)
        path.append(wealth / peak - 1)
        worst = min(worst, path[-1])
    total = wealth - 1
    days = (date.fromisoformat(returns[-1][0]) - date.fromisoformat(first_date)).days
    # Risk snapshot: "Annualized return", 365.25-day years. Statistics and Calmar: "TWR annualised",
    # 365-day years and only from a year of history.
    annualized = (1 + total) ** (365.25 / days) - 1 if days > 0 and total > -1 else None
    twr_annualised = (1 + total) ** (365 / days) - 1 if days >= 365 and total > -1 else None
    root = math.sqrt(YEAR_SESSIONS)
    m, sd = _mean(values), _sample_sd(values)
    downside = math.sqrt(math.fsum(min(v, 0.0) ** 2 for v in values) / n)
    skew = kurt = None
    if sd:
        z = [(v - m) / sd for v in values]
        if n >= 3:
            skew = n / ((n - 1) * (n - 2)) * math.fsum(x ** 3 for x in z)
        if n >= 4:
            kurt = (n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) * math.fsum(x ** 4 for x in z)
                    - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3)))
    fifth = _percentile(values, 0.05)
    tail = [v for v in values if v <= fifth]
    best = max(returns, key=lambda item: item[1])
    worst_day = min(returns, key=lambda item: item[1])
    return {
        "observations": n,
        "period_start": first_date,
        "period_end": returns[-1][0],
        "total_return": total,
        "annualized_return": annualized,
        "annualized_twr": twr_annualised,
        "annualized_volatility": sd * root if sd is not None else None,
        "downside_deviation": downside * root,
        "sortino_ratio": m / downside * root if downside else None,
        "calmar_ratio": twr_annualised / abs(worst) if twr_annualised is not None and worst < 0 else None,
        "skewness": skew,
        "excess_kurtosis": kurt,
        "best_day": best[1],
        "best_day_date": best[0],
        "worst_day": worst_day[1],
        "worst_day_date": worst_day[0],
        "maximum_drawdown": worst,
        "current_drawdown": path[-1],
        "win_rate": sum(1 for v in values if v > 0) / n,
        "var_95_daily": max(0.0, -fifth),
        "cvar_95_daily": max(0.0, -_mean(tail)),
    }


def website_figures(nav: Mapping[str, float], flows: Mapping[str, float]) -> dict[str, dict[str, Any]]:
    """The same figures as the website computes them, grouped by where they come from."""

    from .benchmark_analytics import ComparisonRow, portfolio_risk_metrics
    from .portfolio_analytics import portfolio_analytics

    analytics = portfolio_analytics(nav, flows)
    if not analytics.get("available"):
        raise ValueError("; ".join(analytics.get("issues") or ["the website could not analyse this series"]))
    stats = dict(analytics["statistics"])
    stats["annualized_twr"] = stats.get("annualized_return")
    stats["best_day"] = (stats.get("best_day") or {}).get("return")
    stats["worst_day"] = (stats.get("worst_day") or {}).get("return")
    days = sorted(nav)
    from .portfolio_analytics import daily_return_series

    returns, _ = daily_return_series(dict(sorted(nav.items())), dict(sorted(flows.items())))
    rows = [ComparisonRow(days[0], nav[days[0]], 1.0, 1.0, 1.0, None, None, None)]
    rows += [ComparisonRow(day, nav[day], 1.0, 1.0, 1.0, r, None, None) for day, r in returns]
    return {"statistics": stats, "drawdown": analytics["drawdown"], "risk": portfolio_risk_metrics(rows)}


def _agrees(expected: float | None, actual: float | None) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    return abs(expected - actual) <= max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * abs(expected))


def _check(metric: str, key: str, guide_value: Any, shown: Any, source: str) -> dict[str, Any]:
    guide_value = float(guide_value) if isinstance(guide_value, (int, float)) else None
    shown = float(shown) if isinstance(shown, (int, float)) and not isinstance(shown, bool) else None
    status = "match" if _agrees(guide_value, shown) else "mismatch"
    difference = shown - guide_value if guide_value is not None and shown is not None else None
    return {"metric": metric, "figure": key, "source": source, "guide_formula_value": guide_value,
            "shown_value": shown, "difference": difference, "status": status}


def conformance(nav: Mapping[str, float], flows: Mapping[str, float],
                agent_risk: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Compare the website (and, if given, the analyst's get_risk_metrics) with the guide's formulas."""

    days = sorted(nav)
    reference = statistics(daily_returns(nav, flows), days[0])
    website = website_figures(nav, flows)
    checks = [_check(metric, key, reference[key], website[group].get(key), f"website {group}")
              for metric, key, group in CHECKS]
    if agent_risk:
        start, end = agent_risk.get("from_date"), agent_risk.get("to_date")
        window = {d: v for d, v in nav.items() if start <= d <= end} if start and end else {}
        if len(window) >= 3:
            window_flows = {d: v for d, v in flows.items() if start < d <= end}
            agent_reference = statistics(daily_returns(window, window_flows), min(window))
            checks += [_check(metric, key, agent_reference[key], agent_risk.get(agent_key), "analyst get_risk_metrics")
                       for metric, key, agent_key in AGENT_CHECKS]
    mismatches = [c for c in checks if c["status"] == "mismatch"]
    return {
        "reference_version": REFERENCE_VERSION,
        "period_start": days[0],
        "period_end": days[-1],
        "observations": reference["observations"],
        "checked": len(checks),
        "mismatches": len(mismatches),
        "checks": checks,
        "method": "Each figure is recomputed from the Metrics guide's technical formula by a separate "
                  "implementation, on the same recorded values and flows, and compared with the website's own "
                  f"figure (tolerance {RELATIVE_TOLERANCE:g} relative). The website's Risk tab measures on "
                  "benchmark-aligned dates; here both sides use every recorded valuation date.",
    }
