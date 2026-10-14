"""The figure set and the deterministic checks built on it. No model here.

The analyst's figure set is the only source of numbers the writer sees, and
every figure carries the tool result it came from. The reviewer's printed-
figure checks re-derive what the factsheet must show from the frozen
snapshot, using the factsheet renderer's own input builder, and compare it
with the text actually in the PDF.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence

# Wording that asserts a direction. A sentence using both kinds is not checked.
_RELATIVE_UP = re.compile(r"\b(outperform\w*|beat|ahead of|exceed\w*)\b", re.I)
_RELATIVE_DOWN = re.compile(r"\b(underperform\w*|lagg\w*|trail\w*|behind)\b", re.I)
_ABSOLUTE_UP = re.compile(r"\b(gain\w*|rose|rise[sn]?|advanc\w*|positive return)\b", re.I)
_ABSOLUTE_DOWN = re.compile(r"\b(fell|fall\w*|declin\w*|lost|loss\w*|negative return)\b", re.I)

RETURN_TOLERANCE = 0.001  # 10 basis points between the frozen snapshot and today's database

ToolCall = Callable[[str, dict[str, Any]], dict[str, Any]]


def previous_month_end(day: date) -> date:
    return day.replace(day=1) - timedelta(days=1)


def last_business_day(period_end: date) -> date:
    day = period_end
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def display(value: float | None, unit: str, currency: str = "") -> str:
    if value is None:
        return "unavailable"
    if unit == "percent":
        return f"{value * 100:.2f}%"
    if unit == "currency":
        return f"{currency} {value:,.0f}".strip()
    return f"{value:.2f}"


class FigureSet:
    """Collects figures with ids F1, F2, ... and the tool results behind them."""

    def __init__(self, period_end: str, currency: str = "") -> None:
        self.period_end = period_end
        self.currency = currency
        self.benchmark: str | None = None
        self.figures: list[dict[str, Any]] = []
        self.sources: list[dict[str, Any]] = []
        self.unavailable: list[str] = []
        self.inception_date: str | None = None

    def source(self, result: Mapping[str, Any]) -> int:
        self.sources.append({"tool": result.get("tool"), "as_of": result.get("as_of"),
                             "citations": result.get("citations", [])})
        return len(self.sources) - 1

    def add(self, key: str, label: str, value: Any, unit: str, source: int) -> None:
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            self.unavailable.append(label)
            return
        figure_id = f"F{len(self.figures) + 1}"
        self.figures.append({"id": figure_id, "key": key, "label": label, "value": float(value), "unit": unit,
                             "display": display(float(value), unit, self.currency), "source": source})

    def as_dict(self) -> dict[str, Any]:
        return {"period_end": self.period_end, "currency": self.currency, "benchmark": self.benchmark,
                "inception_date": self.inception_date, "figures": self.figures, "sources": self.sources,
                "unavailable": self.unavailable}


RANKS = {1: "largest", 2: "second-largest", 3: "third-largest"}


def build_figure_set(call: ToolCall, account_id: str, period_end: str) -> dict[str, Any]:
    """Month, year-to-date and since-inception returns, the benchmark difference, risk and contributors."""
    from ..tool_client import ToolCallFailed

    end = date.fromisoformat(period_end)
    month_start_base = previous_month_end(end).isoformat()
    figures = FigureSet(period_end)
    labels = {"Month to date": "month", "Year to date": "ytd", "Since inception": "inception"}
    names = {"month": "month", "ytd": "year-to-date", "inception": "since-inception"}

    try:
        compared = call("compare_to_benchmark", {"account_id": account_id, "as_of": period_end})
        figures.currency = compared["data"]["currency"]
        figures.benchmark = (compared["data"].get("benchmark") or {}).get("name")
        source = figures.source(compared)
        for row in compared["data"]["periods"]:
            key = labels.get(row.get("period"))
            if key is None:
                continue
            figures.add(f"{key}.portfolio_return", f"Portfolio {names[key]} return", row.get("portfolio_return"), "percent", source)
            figures.add(f"{key}.benchmark_return", f"Benchmark {names[key]} return", row.get("benchmark_return"), "percent", source)
            figures.add(f"{key}.excess_return", f"Difference from the benchmark, {names[key]}", row.get("excess_return"),
                        "percent", source)
    except ToolCallFailed:
        figures.unavailable.append("Benchmark comparison")
        performance = call("get_performance", {"account_id": account_id, "as_of": period_end})
        figures.currency = performance["data"]["currency"]
        source = figures.source(performance)
        for row in performance["data"]["periods"]:
            key = labels.get(row.get("label"))
            if key is not None:
                figures.add(f"{key}.portfolio_return", f"Portfolio {names[key]} return", row.get("twr"), "percent", source)

    performance = call("get_performance", {"account_id": account_id, "as_of": period_end})
    figures.inception_date = performance["data"].get("inception_date")
    source = figures.source(performance)
    figures.add("nav", "Net asset value at month end", performance["data"].get("latest_nav"), "currency", source)
    month = next((p for p in performance["data"]["periods"] if p.get("key") == "mtd"), {})
    figures.add("month.net_flows", "Net deposits and withdrawals in the month", month.get("net_flows"), "currency", source)

    try:
        risk = call("get_risk_metrics", {"account_id": account_id, "to_date": period_end})
        source = figures.source(risk)
        data = risk["data"]
        figures.add("risk.volatility", "Annualised volatility, last year", data.get("annualized_volatility"), "percent", source)
        figures.add("risk.max_drawdown", "Largest fall from a peak, last year", data.get("max_drawdown"), "percent", source)
        bench = data.get("benchmark") or {}
        figures.add("risk.beta", "Beta against the benchmark, last year", bench.get("beta"), "number", source)
        figures.add("risk.tracking_error", "Tracking error, last year", bench.get("tracking_error"), "percent", source)
    except ToolCallFailed:
        figures.unavailable.append("Risk measures")

    try:
        changes = call("get_holdings_changes", {"account_id": account_id, "from_date": month_start_base,
                                                 "to_date": period_end})
        source = figures.source(changes)
        moves = sorted(changes["data"]["changes"], key=lambda c: c.get("from_price") or 0.0)
        # Ranks in words: the writer copies labels, and the deck's commentary may not hold a digit.
        for n, row in enumerate([m for m in reversed(moves) if (m.get("from_price") or 0) > 0][:3], 1):
            figures.add(f"contributor.{n}", f"Price gain from {row['symbol']}, the {RANKS[n]} contributor",
                        row["from_price"], "currency", source)
        for n, row in enumerate([m for m in moves if (m.get("from_price") or 0) < 0][:3], 1):
            figures.add(f"detractor.{n}", f"Price loss from {row['symbol']}, the {RANKS[n]} detractor",
                        row["from_price"], "currency", source)
        figures.add("month.price_effect", "Change in holdings value from prices", changes["data"].get("from_price"),
                    "currency", source)
        figures.add("month.fx_effect", "Change in holdings value from currencies", changes["data"].get("from_fx"),
                    "currency", source)
    except ToolCallFailed:
        figures.unavailable.append("Holding contributions")

    try:
        sectors = call("get_sector_allocation", {"account_id": account_id, "as_of": period_end})
        source = figures.source(sectors)
        allocation = sorted(sectors["data"].get("allocation") or [], key=lambda r: -(r.get("weight") or 0))
        for n, row in enumerate(allocation[:3], 1):
            figures.add(f"sector.{n}", f"Weight of {row.get('sector')} (largest sector {n})", row.get("weight"),
                        "percent", source)
    except ToolCallFailed:
        figures.unavailable.append("Sector weights")
    return figures.as_dict()


def figure_lookup(figure_set: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in figure_set.get("figures", [])}


def direction_conflicts(items: Sequence[Mapping[str, Any]], figure_set: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Sentences whose wording points one way while every figure they cite points the other."""
    lookup = figure_lookup(figure_set)
    conflicts = []
    for item in items:
        text = str(item.get("text") or "")
        cited = [lookup[i] for i in item.get("figures", []) if i in lookup]
        for up, down, suffix, what in ((_RELATIVE_UP, _RELATIVE_DOWN, ".excess_return", "the benchmark difference"),
                                       (_ABSOLUTE_UP, _ABSOLUTE_DOWN, ".portfolio_return", "the portfolio return")):
            rising, falling = bool(up.search(text)), bool(down.search(text))
            values = [f["value"] for f in cited if f["key"].endswith(suffix)]
            if rising == falling or not values:
                continue
            if rising and all(v < 0 for v in values) or falling and all(v > 0 for v in values):
                shown = ", ".join(f["display"] for f in cited if f["key"].endswith(suffix))
                conflicts.append({"field": item.get("field"), "index": item.get("index"),
                                  "message": f"The wording says {'up' if rising else 'down'} but {what} it cites is {shown}."})
    return conflicts


# Printed-figure checks ----------------------------------------------------------


def factsheet_expectations(snapshot: dict[str, Any]) -> dict[str, Any]:
    """What the standard factsheet must print, from the renderer's own input builder."""
    from ibkr_etl.monthly_artifacts import (
        ALPHA_WORLD_SUBJECT_IDS, MonthlyArtifactError, _display_date, _number, _percent, factsheet_inputs)

    subject = str(snapshot.get("subject_id") or "")
    if subject in ALPHA_WORLD_SUBJECT_IDS:
        return {"skipped": "This account uses a special factsheet layout; its printed figures are checked by "
                           "the layout validator only."}
    try:
        inputs = factsheet_inputs(snapshot)
    except (MonthlyArtifactError, KeyError, TypeError, ValueError) as error:
        return {"error": f"The snapshot could not be turned into factsheet figures: {str(error)[:200]}"}
    gross = inputs.gross_market_value
    span = f"{_display_date(inputs.history_start_date, short=True)} to {_display_date(inputs.period_end, short=True)}"
    history = []
    for row in inputs.history:
        cells = [row["date"], f"{row['normalized_nav']:.2f}"]
        if row["monthly_return"] is not None:
            cells += [_percent(row["monthly_return"]), _percent(row["cumulative_return"])]
        history.append(cells)
    last = inputs.history[-1]
    return {
        "display_date": inputs.display_date,
        "page_1": {
            "Top holding weights": [f"{abs(_number(h['market_value_text'], field='weight')) / gross * 100:.2f}%"
                                    for h in inputs.top_holdings],
            "Sector weights": [f"{s['weight'] * 100:.2f}%" for s in inputs.sectors],
            "Historical performance rows": [cell for cells in history for cell in cells],
            "Trailing twelve-month return": ["N/A" if inputs.trailing_twelve_month_return is None
                                             else f"{inputs.trailing_twelve_month_return * 100:.2f} %"],
            "Report date": [inputs.display_date],
            "History heading": [f"Historical Performance from {span}"],
        },
        "page_2": {"Report date": [inputs.display_date], "Chart title": [f"Historical Performance ({span})"]},
        "month_return": last["monthly_return"],
        "cumulative_return": last["cumulative_return"],
        "history_start_date": inputs.history_start_date,
    }


def check_factsheet_text(expectations: Mapping[str, Any], pages: Sequence[str]) -> list[dict[str, Any]]:
    """Every expected printed string must appear on its page, exactly as the renderer formats it."""
    if "skipped" in expectations or "error" in expectations:
        return []
    problems = []
    if len(pages) != 2:
        problems.append({"page": None, "message": f"The factsheet has {len(pages)} pages; the approved layout has two."})
    for number, key in ((1, "page_1"), (2, "page_2")):
        text = pages[number - 1] if len(pages) >= number else ""
        runs = set(text.split("\n"))
        for label, wanted in expectations[key].items():
            missing = [w for w in wanted if w not in runs and w not in text]
            if missing:
                sample = ", ".join(missing[:4]) + (" and more" if len(missing) > 4 else "")
                problems.append({"page": number, "message": (
                    f"{label}: {len(missing)} of {len(wanted)} values are not printed as the frozen snapshot gives "
                    f"them (expected {sample}).")})
    return problems


def check_against_live(expectations: Mapping[str, Any], figure_set: Mapping[str, Any],
                       inception_date: str | None) -> list[str]:
    """The frozen snapshot's returns against today's database figures, within ten basis points."""
    if "page_1" not in expectations:
        return []
    by_key = {f["key"]: f for f in figure_set.get("figures", [])}
    problems = []
    pairs = [("month_return", "month.portfolio_return", "month return")]
    if inception_date and expectations.get("history_start_date") == inception_date:
        pairs.append(("cumulative_return", "inception.portfolio_return", "since-inception return"))
    for snapshot_key, figure_key, label in pairs:
        frozen, live = expectations.get(snapshot_key), by_key.get(figure_key)
        if frozen is None or live is None:
            continue
        if abs(float(frozen) - live["value"]) > RETURN_TOLERANCE:
            problems.append(f"The factsheet's {label} ({display(float(frozen), 'percent')}) differs from today's "
                            f"database figure ({live['display']}) by more than 0.10 percentage points.")
    return problems


def long_date(value: str) -> str:
    """The deck's own date format (en-GB long date, e.g. 31 August 2026)."""
    parsed = date.fromisoformat(value)
    return f"{parsed.day} {parsed.strftime('%B')} {parsed.year}"


def check_deck_text(subject_id: str, period_end: str, slides: Sequence[str]) -> list[dict[str, Any]]:
    from ibkr_etl.monthly_artifacts import ALPHA_WORLD_SUBJECT_IDS  # their decks use their own layout

    if subject_id in ALPHA_WORLD_SUBJECT_IDS:
        return []
    if not slides:
        return [{"page": None, "message": "The presentation has no slides."}]
    wanted = long_date(period_end)
    if not any(wanted in text for text in slides):
        return [{"page": 1, "message": f"No slide shows the report date {wanted}; the deck may be for another month."}]
    return []


# Commentary goes in the deck only; the factsheet never carries it (Ying, 14 October 2026). A subject listed
# here has a deck with no commentary line, so the team writes none for it. Alpha World T's deck
# (scripts/render_alpha_t_private_presentation.mjs) has carried the line since 14 October 2026 (Ying).
DECKS_WITHOUT_COMMENTARY: frozenset[str] = frozenset()


def factsheet_commentary(pages: Sequence[str], sentences: Sequence[str]) -> list[int]:
    """The factsheet pages that print commentary: the deck's prefix or any of the team's sentences."""
    wanted = [" ".join(s.split()) for s in sentences if s and s.strip()]
    found = []
    for number, text in enumerate(pages, 1):
        flat = " ".join(text.split())
        if "Portfolio analyst:" in flat or any(s in flat for s in wanted):
            found.append(number)
    return found


def deck_commentary(slides: Sequence[str]) -> list[dict[str, Any]]:
    """The model-written lines in the deck, which today's process prefixes with 'Portfolio analyst:'."""
    found = []
    for number, text in enumerate(slides, 1):
        for line in text.split("\n"):
            if line.startswith("Portfolio analyst:"):
                found.append({"slide": number, "text": line[len("Portfolio analyst:"):].strip()})
    return found
