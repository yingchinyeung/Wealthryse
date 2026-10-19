"""Relate a number that doesn't add up to a market event in the news. Code only, no model.

A numeric issue is anything an agent finds that a market change could explain: a broker's NAV that
no longer matches the rebuild from listed prices, a missing price, a jump in a price or a cost. It
names the markets involved and the window in which the problem began: from the last day that was
still right to the first day that was wrong.

An event is what the market news watch kept from the news (``market_news.py``): a market, a kind
of change, the day it takes effect, and the sentence in the source that says so. An event explains
an issue when the market matches, the kind of change can move that kind of number, and the event
takes effect inside the window (a one-day event, such as a closure, on a day the issue covers).

The KRX case that this was built for: from 14 Sep 2026 Kiwoom's per-stock prices for past days
stopped matching the official 15:30 closes behind its total, because they became the 20:00
after-market price; that was the day KRX started continuous after-market trading. The issue's window
is 11 Sep (last right) to 14 Sep (first wrong); the event takes effect on 14 Sep, so it is linked
as starting the same day.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from . import markets

# Kinds of market change the watch keeps.
EVENT_KINDS = (
    "trading_hours",  # sessions added, extended or shortened (after-market, night trading)
    "closing_price",  # how the official close or the valuation price is set (auction, combined venues)
    "new_venue",  # a new trading venue for the same shares (Nextrade, an ATS)
    "closure",  # a holiday, an emergency or weather closure, a half day
    "halt",  # a market-wide or index halt, a circuit breaker
    "price_limit",  # daily price limits, tick sizes
    "tax_or_fee",  # transaction tax, stamp duty, exchange fees
    "settlement",  # settlement cycle (T+1), settlement holidays
    "index_change",  # index method, rebalance, constituent change
    "currency",  # capital controls, FX trading hours, a peg
    "other_rule",  # any other rule that changes prices, costs or dates
)
# One-day events: the issue must cover the day. Others start a change that lasts.
ONE_DAY = frozenset({"closure", "halt"})

ISSUE_KINDS = ("valuation_gap", "missing_price", "price_jump", "cost_change", "date_gap")
RELEVANT: dict[str, tuple[str, ...]] = {
    "valuation_gap": ("trading_hours", "closing_price", "new_venue", "closure", "halt", "currency", "other_rule"),
    "missing_price": ("closure", "halt", "trading_hours", "new_venue", "other_rule"),
    "price_jump": ("halt", "price_limit", "index_change", "currency", "closing_price", "other_rule"),
    "cost_change": ("tax_or_fee", "settlement", "other_rule"),
    "date_gap": ("closure", "settlement", "halt", "other_rule"),
}
# The longest window an issue may give: wider than this and a match says little.
MAX_WINDOW_DAYS = 45
# A lasting change announced for a day may be felt a day or two later in a broker's data.
LAG_DAYS = 2


@dataclass(frozen=True)
class Issue:
    kind: str
    markets: frozenset[str]
    first_wrong: date  # the first day the number is wrong
    last_right: date | None = None  # the last day before it that was right, if known
    last_wrong: date | None = None  # the last wrong day seen, for one-day events
    subject_id: str | None = None
    ref: str | None = None  # where it was found, e.g. "onboarding:<case>:nav_reconciled"

    def window(self) -> tuple[date, date]:
        start = (self.last_right + timedelta(days=1)) if self.last_right else self.first_wrong
        start = max(start, self.first_wrong - timedelta(days=MAX_WINDOW_DAYS))
        return start, self.first_wrong


@dataclass(frozen=True)
class Link:
    event: Mapping[str, Any]
    strength: str  # same_day, in_window or covers_day
    days_before: int  # days from the event to the first wrong day
    issue: Issue = field(compare=False)

    def as_dict(self) -> dict[str, Any]:
        e = self.event
        return {"event_id": str(e.get("event_id") or ""), "market": e["market"], "kind": e["kind"],
                "effective_date": str(e["effective_date"]), "summary": e.get("summary"),
                "source_url": e.get("source_url"), "source_id": e.get("source_id"),
                "published_at": str(e.get("published_at") or "") or None,
                "strength": self.strength, "days_before": self.days_before,
                "wording": wording(self)}


def _day(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def link(issue: Issue, events: Iterable[Mapping[str, Any]]) -> list[Link]:
    """The events that could explain the issue, strongest first."""
    if issue.kind not in RELEVANT:
        raise ValueError(f"unknown issue kind {issue.kind}")
    wanted = markets.related(issue.markets)
    start, first_wrong = issue.window()
    last_wrong = issue.last_wrong or first_wrong
    found: list[Link] = []
    for event in events:
        if event.get("market") not in wanted or event.get("kind") not in RELEVANT[issue.kind]:
            continue
        effective = _day(event["effective_date"])
        if event["kind"] in ONE_DAY:
            ends = _day(event.get("end_date") or effective)
            if effective <= last_wrong and ends >= start:
                found.append(Link(event, "covers_day", (first_wrong - effective).days, issue))
            continue
        if start - timedelta(days=LAG_DAYS) <= effective <= first_wrong:
            strength = "same_day" if effective == first_wrong else "in_window"
            found.append(Link(event, strength, (first_wrong - effective).days, issue))
    order = {"same_day": 0, "covers_day": 1, "in_window": 2}
    found.sort(key=lambda l: (order[l.strength], l.days_before, 0 if l.event.get("official") else 1))
    # One link per market and kind: the strongest source.
    kept, seen = [], set()
    for item in found:
        key = (item.event["market"], item.event["kind"], str(item.event["effective_date"]))
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return kept


def _date_text(day: date) -> str:
    return f"{day.day} {day:%b} {day.year}"


def wording(item: Link) -> str:
    """One plain sentence, from the event's own stored facts."""
    e = item.event
    market = markets.BY_CODE.get(str(e["market"]))
    name = market.name if market else str(e["market"])
    when = _date_text(_day(e["effective_date"]))
    summary = str(e.get("summary") or e["kind"].replace("_", " ")).rstrip(".")
    host = ""
    if e.get("source_url"):
        from urllib.parse import urlsplit

        host = f" (source: {urlsplit(str(e['source_url'])).hostname})"
    if item.strength == "same_day":
        lead = f"This starts on {when}, the day of a {name} change: {summary}"
    elif item.strength == "covers_day":
        lead = f"On {when}, {name}: {summary}"
    else:
        start, _ = item.issue.window()
        lead = (f"It began between {_date_text(start)} and {_date_text(item.issue.first_wrong)}; on {when}, "
                f"{name}: {summary}")
    return lead + host + "."
