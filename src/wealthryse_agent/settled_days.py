"""Which dates a scheduled job may treat as final, shared by the jobs and the event dispatcher.

The jobs that never redo a date (holdings explainer, risk watch, weekly brief, export packs) and the
month-end commentary job's start read the tool server's job-only ``get_settled_dates``. A Kiwoom
account's newest sessions are provisional there (``settled_through`` is older than the latest
date); every other account's latest date is final. A tool server without that tool (an older
release) answers through ``get_data_freshness``, and the jobs then behave as they did before.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Mapping

from .tool_client import ToolCallFailed

TOOL = "get_settled_dates"
# The month-end commentary job's window (infra/agent-apps.bicep: the 2nd to the 7th): events start it
# in these days only, and its scheduled runs stay as the backup.
MONTH_END_DAYS = range(2, 8)


def read_dates(call: Callable[[str, dict], dict], account_id: str) -> dict[str, Any]:
    """The account's dates: settled ones when the tool server has them, otherwise plain freshness."""
    try:
        return dict(call(TOOL, {"account_id": account_id})["data"])
    except ToolCallFailed:
        return dict(call("get_data_freshness", {"account_id": account_id})["data"])


def settled(dates: Mapping[str, Any], key: str) -> str | None:
    """``dates[key]`` (latest_valuation_date or latest_holdings_date), no later than the settled date.

    None when the account has no such date, or none of its dates is settled yet.
    """
    value = dates.get(key)
    if not value:
        return None
    if "settled_through" not in dates:
        return str(value)  # an older tool server: every date counts as final, as before
    through = dates.get("settled_through")
    if not through:
        return None
    return min(str(value), str(through))


def last_business_day(period_end: date) -> date:
    day = period_end
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def month_end(today: date) -> date:
    """The month the month-end commentary job works on today: the one before today's."""
    return today.replace(day=1) - timedelta(days=1)


def month_final(dates: Mapping[str, Any], period_end: date) -> bool:
    """Whether the month's data are in and final: its valuations and holdings reach the month's last
    business day, on settled dates (a Kiwoom month waits for the next trading day's close, and U.S.
    iStrategy's for its last sessions to settle), the same test the month-end team's data agent makes."""
    due = last_business_day(period_end).isoformat()
    valuation, holdings = settled(dates, "latest_valuation_date"), settled(dates, "latest_holdings_date")
    return bool(valuation and holdings and valuation >= due and holdings >= due)
