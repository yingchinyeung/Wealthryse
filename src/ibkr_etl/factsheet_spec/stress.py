"""Synthetic snapshots built to break factsheet layouts, for the checker's stress suite.

Each case is a realistic account snapshot pushed to one edge a real account will
reach sooner or later: a December inception, five years of history, a fivefold
rise, a long losing run, 40-character holding names, a dozen sectors, a single
holding. Every spec, built-in or drafted by the agents, must render each case and
pass its generated checks before anyone is asked to approve it. The data is
deterministic and marked synthetic, so nothing here can be mistaken for a client's.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

SECTORS = (
    ("North America", "Technology", "Software"), ("North America", "Healthcare", "Biotechnology"),
    ("Hong Kong", "Financial Services", "Banks"), ("China", "Consumer Cyclical", "Internet Retail"),
    ("Taiwan", "Technology", "Semiconductors"), ("North America", "Communication Services", "Entertainment"),
    ("Europe", "Industrials", "Aerospace & Defense"), ("China", "Real Estate", "Property Development"),
    ("Hong Kong", "Utilities", "Utilities - Regulated Electric"), ("North America", "Energy", "Oil & Gas Integrated"),
    ("Asia Pacific", "Basic Materials", "Specialty Chemicals"), ("Latin America", "Consumer Defensive", "Beverages"),
)
LONG_NAMES = (
    "Consolidated Semiconductor Manufacturing International Holdings",
    "Pan-Pacific Biotechnology Research & Therapeutics Group",
    "Greater Bay Area Infrastructure Development Corporation",
    "Transcontinental Communication Services & Media Enterprises",
    "First Hong Kong Mortgage & Commercial Banking Association",
)


@dataclass(frozen=True)
class StressCase:
    case_id: str
    label: str
    build: Callable[[str], dict[str, Any]]


def _sessions(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def snapshot(*, subject_id: str = "STRESS-TEST", currency: str = "USD", start: date = date(2025, 1, 20),
             end: date = date(2026, 9, 30), growth: float = 0.12, wobble: float = 0.01,
             names: tuple[str, ...] | None = None, sectors: int = 5, holdings: int = 5,
             composite: bool = False) -> dict[str, Any]:
    """An account snapshot for ``start`` to ``end``; ``growth`` is the total change in NAV over the history."""
    sessions = _sessions(start, end)
    count = len(sessions)
    series = []
    for i, day in enumerate(sessions):
        trend = (1.0 + growth) ** (i / max(1, count - 1))
        nav = 100000.0 * trend * (1.0 + wobble * math.sin(i / 3.0))
        series.append({"date": day.isoformat(), "currency": currency, "nav": round(nav, 2)})
    last = series[-1]
    period_end = _month_end(end.year, end.month)
    names = names or tuple(f"Holding {chr(65 + i)} Inc" for i in range(holdings))
    rows, classifications = [], []
    total = 0.0
    for i in range(holdings):
        region, sector, sub = SECTORS[i % max(1, sectors)]
        value = round(last["nav"] * (0.4 / (i + 1)), 2)
        total += value
        conid = str(1000 + i)
        rows.append({"conid": conid, "description": names[i % len(names)].upper(),
                     "symbol": f"S{i:02d}", "currency": currency, "market_value_text": f"{value:.2f}",
                     **({"asset_category": "account", "direction": "long" if i % 2 == 0 else "short"}
                        if composite else {})})
        classifications.append({"conid": conid, "symbol": f"S{i:02d}", "region": region, "sector": sector,
                                "sub_sector": sub, "market_value": value})
    for row in classifications:
        row["weight"] = row["market_value"] / total
    month_ends, seen = [], {}
    for row in series:
        seen[row["date"][:7]] = row
    first_nav = series[0]["nav"]
    for key in sorted(seen):
        month_ends.append({"date": seen[key]["date"], "portfolio": seen[key]["nav"] / first_nav * 100.0})
    result = {
        "synthetic_data": True,
        "subject_id": subject_id, "reporting_currency": currency,
        "period_start": series[0]["date"], "period_end": period_end.isoformat(), "data_as_of": last["date"],
        "portfolio_summary": {"latest_nav": {"total_text": f"{last['nav']:.2f}"}, "positions": holdings},
        "performance": {"series": series},
        "holdings": {"holdings": rows},
        "sector_allocation": {"gross_market_value": total, "classifications": classifications,
                              **({"classification_basis": "composite sleeve type"} if composite else {})},
        "month_end_performance": month_ends,
        "risk": {"metrics": {"annualized_return": 0.0712, "annualized_volatility": 0.1522, "sharpe_ratio": 0.94,
                             "maximum_drawdown": -0.1236, "var_95_daily": -0.0135, "cvar_95_daily": -0.0195,
                             "win_rate": 0.5263, "total_return": growth, "risk_free_annual_rate": 0.0265},
                 "risk_free": {"annual_rate": 0.0265}},
    }
    return result


CASES = (
    StressCase("baseline", "A typical 20-month history", lambda c: snapshot(currency=c)),
    StressCase("december_inception", "History that starts in December",
               lambda c: snapshot(currency=c, start=date(2024, 12, 2))),
    StressCase("five_years", "Five years of month-ends",
               lambda c: snapshot(currency=c, start=date(2021, 9, 1))),
    StressCase("fivefold", "NAV rises fivefold", lambda c: snapshot(currency=c, growth=4.0)),
    StressCase("losing_run", "NAV falls 45% with no recovery", lambda c: snapshot(currency=c, growth=-0.45)),
    StressCase("first_month", "Only one month of history",
               lambda c: snapshot(currency=c, start=date(2026, 9, 1))),
    StressCase("long_names", "40-character holding names", lambda c: snapshot(currency=c, names=LONG_NAMES)),
    StressCase("many_sectors", "Twelve sectors across eight regions",
               lambda c: snapshot(currency=c, holdings=12, sectors=12)),
    StressCase("single_holding", "One holding", lambda c: snapshot(currency=c, holdings=1, sectors=1)),
)


def cases_for(spec: dict[str, Any]) -> list[StressCase]:
    """The stress cases a design's inputs can read (MS and T read their own methodology inputs)."""
    return list(CASES) if spec.get("inputs") == "account" else []


def run(spec: dict[str, Any], *, template_path, example_path=None) -> list[dict[str, Any]]:
    """Render every case to memory; each result says pass, warn or fail and why."""
    from .engine import render_preview
    outcomes = []
    currency = spec["currencies"][0]
    for case in cases_for(spec):
        pdf, result = render_preview(spec, case.build(currency), template_path=template_path,
                                     example_path=example_path)
        if "error" in result:
            outcomes.append({"case": case.case_id, "label": case.label, "status": "fail", "detail": result["error"]})
            continue
        report = result["checks"]
        warnings = [c["detail"] for c in report["checks"] if c["status"] == "warn"]
        outcomes.append({"case": case.case_id, "label": case.label, "status": report["status"],
                         "detail": "; ".join(warnings) if warnings else "Rendered and passed every check.",
                         "pdf_bytes": len(pdf)})
    return outcomes
