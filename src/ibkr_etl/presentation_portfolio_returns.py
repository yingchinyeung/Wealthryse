"""Portfolio-only returns for the generic monthly deck.

The deck's benchmark comparison uses dates both series share, so a market
holiday on the benchmark's exchange used to drop the portfolio's first or last
day from the deck's headline returns. Portfolio returns must instead come from
the full cash-flow-adjusted NAV history, exactly as the factsheet calculates
them. Benchmark-relative figures keep their common-date basis.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from .monthly_artifacts import _normalize_nav

VERSION = "presentation-portfolio-returns-v1"


def portfolio_return_model(snapshot: dict[str, Any]) -> dict[str, Any]:
    series = list((snapshot.get("performance") or {}).get("series") or [])
    normalized = _normalize_nav(series)
    if normalized[-1]["date"] != snapshot["data_as_of"]:
        raise ValueError("Performance series does not end on data_as_of")
    month_ends: dict[str, dict[str, Any]] = {}
    for row in normalized:
        month_ends[row["date"][:7]] = row
    month_rows = []
    previous = 100.0
    for key in sorted(month_ends):
        value = float(month_ends[key]["value"])
        month_rows.append({
            "date": month_ends[key]["date"],
            "portfolio": value,
            # The opening month runs from the first NAV (index 100).
            "portfolio_monthly_return": value / previous - 1.0,
        })
        previous = value
    return {
        "version": VERSION,
        "period_start": normalized[0]["date"],
        "period_end": normalized[-1]["date"],
        "total_return": float(normalized[-1]["value"]) / 100.0 - 1.0,
        "month_rows": month_rows,
    }


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit("Usage: python -m ibkr_etl.presentation_portfolio_returns snapshot.json")
    with open(args[0], encoding="utf-8") as stream:
        snapshot = json.load(stream)
    print(json.dumps(portfolio_return_model(snapshot), allow_nan=False))


if __name__ == "__main__":
    main()
