"""The agents' figure tools on certified figures, with the automatic fallback.

AGENT_CERTIFIED_FIGURE_TOOLS (off | fallback | on; ibkr_etl.certified_tool_figures has the rules)
decides, on the tool server, whether get_performance, compare_to_benchmark, get_risk_metrics,
get_nav_history and get_sector_allocation read agent.certified_figures alongside their own
figures. Off, the default, they read nothing and answer exactly as before.

In fallback each figure below that has a certified version with the same definition is compared
with it; the certified value is used only when the two are equal, and is then listed in the
tool's ``certified`` block, so the agent can cite it [F#] (only for figures whose page is on, as
get_certified_figures serves them). Otherwise today's value stays and one log line counts it.

The figures compared ("slots"), each at the NAV date the tool answers for:

- get_performance: latest_nav -> overview.nav; for every period the website shows a return for
  (MTD, QTD, YTD, each calendar year, since inception): twr -> overview.twr, irr_annualized ->
  overview.irr_annualised, twr_annualized -> overview.twr_annualised (calendar years and since
  inception), net_flows -> overview.net_external_flows (accounts with investor flows).
- compare_to_benchmark: each period's portfolio_return -> overview.twr.
- get_nav_history: from inception only, each point's value -> overview.nav and
  cumulative_return -> overview.twr since_inception on that point's date (all are compared and
  used; only the last point is listed).
- get_risk_metrics: over the whole history only (from_date on or before the first valuation):
  total_return -> overview.twr since_inception, max_drawdown -> stats.maximum_drawdown,
  best_day / worst_day -> stats.best_day / stats.worst_day, best_month / worst_month ->
  stats.best_month / stats.worst_month.
- get_sector_allocation: each sector's weight -> holdings.sector_allocation (a model
  portfolio's sleeve types as the website names them, "Equity Fund" for equity_fund).

Not compared, because the tool's definition differs from the certified figure's (kept as
today, and never logged):

- get_risk_metrics' default one-year window: its total return starts from the last valuation
  before the same calendar date a year earlier, stats.trailing_12m_return from the last NAV at
  least 365 days earlier; its volatility, downside deviation and benchmark statistics are
  annualised by valuations a year, the certified ones by sqrt(252); its annualized_return uses
  365.25-day years, overview.twr_annualised 365; positive_months is a count, stats.positive_months
  a share; its beta, correlation and tracking error use benchmark prices in the benchmark's own
  currency on valuation days, the certified benchmark.* figures the website's comparison series
  in the reporting currency.
- compare_to_benchmark's benchmark_return and excess_return: the benchmark's own-currency price
  return over each period; the certified benchmark.* figures cover the comparison's whole span.
- get_nav_history's benchmark_cumulative_return: as above.
- get_holdings' weight: |value| / gross holdings; holdings.holding_weight is value / NAV.
- get_performance's description promises 1Y, which it does not return; there is no certified
  one-year figure either, so nothing changes (the description stays as it is).
"""
from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import date
from typing import Any, Callable, Mapping

from ibkr_etl import certified_tool_figures as core
from ibkr_etl.certified_tool_figures import Slot

from . import certified_tools

LOGGER = logging.getLogger("wealthryse_agent.tools")
READ_TIMEOUT_MS = 2000
SETTING = core.SETTING

READ_SQL = """
SELECT figure_id, period, item, benchmark_id, as_of_date, value, value_text, unit, currency, status,
       definition_id, definition_version, calculation_version, build_id, published_at
FROM certified_figures
WHERE subject_id = ? AND figure_id = ANY(?) AND as_of_date = ANY(?::date[]) AND benchmark_id = ANY(?)
"""


def mode() -> str:
    return core.mode()


def reader(connection, account_id: str) -> core.Reader:
    """Reads agent.certified_figures inside the tool's own read-only transaction, in a savepoint
    with a short statement timeout, so a slow or missing view never breaks the tool."""
    def read(figure_ids: list[str], dates: list[str], benchmark_ids: list[str]) -> dict:
        savepoint = getattr(connection, "transaction", None)
        with savepoint() if savepoint else nullcontext():
            previous = connection.execute("SELECT current_setting('statement_timeout') AS t").fetchone()["t"]
            connection.execute("SELECT set_config('statement_timeout', ?, true)", (f"{READ_TIMEOUT_MS}ms",))
            rows = connection.execute(READ_SQL, (account_id, list(figure_ids), list(dates),
                                                 ["", *benchmark_ids])).fetchall()
            connection.execute("SELECT set_config('statement_timeout', ?, true)", (str(previous),))
        return {core.key_of(row): row for row in rows}
    return read


def apply(connection, tool: str, account_id: str, result: dict[str, Any], build_slots: Callable[[], list[Slot]],
          refresh: Callable[[dict[str, Any]], None] | None = None,
          read: core.Reader | None = None) -> dict[str, Any]:
    """``result`` as the mode says: unchanged when off (nothing is read or built)."""
    mode_now = core.mode()
    if mode_now == "off":
        return result
    try:
        slots = build_slots()
    except Exception as error:  # a slot that cannot be built leaves today's answer
        core._log(LOGGER, logging.WARNING, tool=tool, mode=mode_now, subject=core._mask(account_id),
                  status="error", error=type(error).__name__, compared=0)
        return result
    if not slots:
        return result
    served = certified_tools.served()
    for slot in slots:
        slot.attach = slot.attach and slot.figure_id in served
    core.settle(tool, account_id, result, slots, read or reader(connection, account_id), mode_now, LOGGER)
    if refresh is not None:
        refresh(result)
    return result


# --- Slots ----------------------------------------------------------------------------------

def _good(period: Mapping[str, Any]) -> bool:
    return period.get("status") not in ("fail", "empty")


def performance_slots(result: Mapping[str, Any]) -> list[Slot]:
    as_of, data = result.get("as_of"), result["data"]
    if not as_of:
        return []
    slots = []
    if data.get("latest_nav") is not None:
        slots.append(Slot("overview.nav", as_of, data, "latest_nav", "/data/latest_nav"))
    for index, period in enumerate(data.get("periods") or []):
        key = str(period.get("key") or "")
        if not key or not _good(period):
            continue
        at = f"/data/periods/{index}"
        slots += [Slot("overview.twr", as_of, period, "twr", f"{at}/twr", period=key),
                  Slot("overview.irr_annualised", as_of, period, "irr_annualized", f"{at}/irr_annualized", period=key)]
        if key.startswith("year_") or key == "since_inception":
            slots.append(Slot("overview.twr_annualised", as_of, period, "twr_annualized", f"{at}/twr_annualized",
                              period=key))
        if data.get("flows_included"):
            slots.append(Slot("overview.net_external_flows", as_of, period, "net_flows", f"{at}/net_flows",
                              period=key))
    return slots


def benchmark_period_slots(result: Mapping[str, Any], periods: list[Mapping[str, Any]]) -> list[Slot]:
    """compare_to_benchmark's rows name their period by label; the period results give the key."""
    as_of = result.get("as_of")
    keys = {str(p.get("label")): str(p.get("key")) for p in periods if _good(p)}
    slots = []
    for index, row in enumerate(result["data"].get("periods") or []):
        key = keys.get(str(row.get("period")))
        if as_of and key:
            slots.append(Slot("overview.twr", as_of, row, "portfolio_return", f"/data/periods/{index}/portfolio_return",
                              period=key))
    return slots


def refresh_excess(result: dict[str, Any]) -> None:
    """Excess return follows the portfolio return used (identical when nothing changed)."""
    for row in result["data"].get("periods") or []:
        if row.get("benchmark_return") is not None and row.get("portfolio_return") is not None:
            row["excess_return"] = row["portfolio_return"] - row["benchmark_return"]


def nav_history_slots(result: Mapping[str, Any], from_inception: bool) -> list[Slot]:
    data = result["data"]
    points = data.get("points") or []
    if not from_inception or not points:
        return []
    slots, last = [], len(points) - 1
    for index, point in enumerate(points):
        day = str(point.get("date") or "")
        if not day:
            continue
        at = f"/data/points/{index}"
        slots.append(Slot("overview.nav", day, point, "value", f"{at}/value", attach=index == last))
        if index:  # the first point is the base: no return yet
            slots.append(Slot("overview.twr", day, point, "cumulative_return", f"{at}/cumulative_return",
                              period="since_inception", attach=index == last))
    return slots


def risk_slots(result: Mapping[str, Any], whole_history: bool) -> list[Slot]:
    as_of, data = result.get("as_of"), result["data"]
    if not whole_history or not as_of:
        return []
    slots = [Slot("overview.twr", as_of, data, "total_return", "/data/total_return", period="since_inception"),
             Slot("stats.maximum_drawdown", as_of, data, "max_drawdown", "/data/max_drawdown")]
    for figure_id, name in (("stats.best_day", "best_day"), ("stats.worst_day", "worst_day"),
                            ("stats.best_month", "best_month"), ("stats.worst_month", "worst_month")):
        entry = data.get(name)
        if isinstance(entry, dict) and "return" in entry:
            slots.append(Slot(figure_id, as_of, entry, "return", f"/data/{name}/return"))
    return slots


def refresh_risk(result: dict[str, Any]) -> None:
    """The annualised return follows the total return used (identical when nothing changed)."""
    data = result["data"]
    if data.get("annualized_return") is None or data.get("total_return") is None:
        return
    days = abs((date.fromisoformat(str(data["to_date"])) - date.fromisoformat(str(data["from_date"]))).days)
    years = max(days / 365.25, 1 / 365.25)
    data["annualized_return"] = (1.0 + data["total_return"]) ** (1.0 / years) - 1.0


def sector_label(sector: Any, composite: bool) -> str:
    """The website names a model portfolio's sleeve types in title case (ibkr_etl.agent_tools)."""
    return str(sector).replace("_", " ").title() if composite else str(sector)


def sector_slots(result: Mapping[str, Any], nav_date: str, composite: bool) -> list[Slot]:
    """Sector weights are certified on the NAV date the page asks for, whichever day the holdings are from."""
    return [Slot("holdings.sector_allocation", nav_date, row, "weight", f"/data/allocation/{index}/weight",
                 item=sector_label(row["sector"], composite))
            for index, row in enumerate(result["data"].get("allocation") or [])
            if isinstance(row, dict) and row.get("sector") not in (None, "")]
