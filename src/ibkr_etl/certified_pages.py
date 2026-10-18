"""Certified figures on the website pages (medallion upgrade U5, part 5).

docs/MEDALLION_CERTIFIED_FIGURES.md explains the layer. One setting,
CERTIFIED_FIGURES_PAGES (for example "overview=on,holdings=on,performance=shadow"),
puts each of the seven pages in one of three modes. A page not listed is off.

- off: today's behaviour exactly. serve() only calls the page's own builder: no query,
  no log line.
- shadow: the page shows today's figures. The certified ones are read alongside and one
  line, ``CERTIFIED_FIGURES {json}``, says which figure ids differ, with counts only:
  never values, dates of holdings or account numbers.
- on: the page shows the certified figures and its answer gains a ``certified`` block
  (as-of date, build, freshness). When the newest figures are held back it shows the last
  published ones with a note (open question 14). When the read fails it shows today's.

The reader runs only after the page's own access check, for one subject, and reads
gold.v_certified_figures only, on its own short-lived connection with a short statement
timeout, so a slow or missing view never slows or breaks the page.

Each answer keeps its keys and shapes: certified values are written into the fields the
page's JavaScript already reads, and only the ``certified`` block is added. Fields that are
not certified figures (dates, opening values, charts, records) stay as today's builder made
them, for the same date.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping

from . import certified_figures as register

# uvicorn's logger is the one the website's container logs show at INFO.
LOGGER = logging.getLogger("uvicorn.error.certified_figures")
LOG_PREFIX = "CERTIFIED_FIGURES"
VIEW = "gold.v_certified_figures"
READ_TIMEOUT_MS = 300
CONNECT_TIMEOUT_S = 2
GUIDE_LINK = "/metrics-guide"
# The release data report's tolerances: ratios and numbers relative 1e-9, money to the cent.
RELATIVE_TOLERANCE = 1e-9
ABSOLUTE_TOLERANCE = 1e-12
MONEY_TOLERANCE = 0.005
# A page that shows another page's figures is on only once that page is on.
BORROWS = {"combined-holdings": "holdings"}

Key = tuple[str, str, str, str]  # figure_id, period, item, benchmark_id


# --- What each answer carries -------------------------------------------------------------


@dataclass
class Slot:
    """One certified figure's place in a page's answer."""

    key: Key
    holder: dict
    field: str
    kind: str  # "float", "decimal_text", "int", "date" or "text"
    write: bool = True

    def get(self) -> Any:
        raw = self.holder.get(self.field)
        if raw is None or raw == "":
            return None
        if self.kind in ("float", "decimal_text", "int"):
            try:
                number = float(raw)
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) else None
        return str(raw)[:10] if self.kind == "date" else str(raw)

    def converted(self, value: Any) -> Any:
        if value is None:
            return None
        if self.kind == "float":
            return float(value)
        if self.kind == "int":
            return int(round(float(value)))
        if self.kind == "decimal_text":
            return _decimal_text(value)
        return str(value)[:10] if self.kind == "date" else str(value)


def _decimal_text(value: Any) -> str:
    """Plain decimal text, as holdings_view writes it (no exponent, no trailing zeros)."""
    try:
        number = Decimal(repr(float(value)))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("not a number") from None
    return format(number.normalize(), "f")


def _day(value: Any) -> str | None:
    return None if value in (None, "") else str(value)[:10]


def _key(figure_id: str, period: str = "", item: object = "", benchmark_id: str = "") -> Key:
    return (figure_id, period or "", "" if item is None else str(item), benchmark_id or "")


def _summary_slots(payload: Mapping) -> list[Slot]:
    slots = []
    latest = payload.get("latest_nav")
    if isinstance(latest, dict):
        slots += [
            Slot(_key("overview.nav"), latest, "total_text", "decimal_text"),
            Slot(_key("overview.reporting_currency"), latest, "currency", "text"),
            Slot(_key("overview.as_of"), latest, "snapshot_date", "date"),
            Slot(_key("overview.positions"), payload, "current_positions", "int"),
        ]
    coverage = payload.get("nav_coverage")
    if isinstance(coverage, dict) and latest:
        slots.append(Slot(_key("overview.observations"), coverage, "observations", "int"))
    return slots


def _period_slots(payload: Mapping) -> list[Slot]:
    fields = (("overview.twr", "twr"), ("overview.twr_annualised", "twr_annualized"),
              ("overview.irr_annualised", "irr_annualized"),
              ("overview.net_external_flows", "net_flows"))
    slots = []
    for period in payload.get("periods") or []:
        for figure_id, name in fields:
            if register.period_allowed(register.figure(figure_id), period["key"]):
                slots.append(Slot(_key(figure_id, period["key"]), period, name, "float"))
    custom, as_of = payload.get("custom"), payload.get("as_of")
    # The Overview asks for the custom period from the as-of date to itself: the day change.
    if isinstance(custom, dict) and as_of and custom.get("requested_start") == custom.get("requested_end") == as_of:
        slots.append(Slot(_key("overview.day_change"), custom, "twr", "float"))
    return slots


def _account_entry_slots(entry: Mapping) -> list[Slot]:
    return [
        Slot(_key("overview.nav"), entry, "nav", "float"),
        Slot(_key("overview.as_of"), entry, "nav_date", "date"),
        Slot(_key("overview.reporting_currency"), entry, "currency", "text"),
        Slot(_key("overview.twr", "mtd"), entry, "mtd", "float"),
        Slot(_key("overview.twr", "ytd"), entry, "ytd", "float"),
    ]


HOLDING_FIELDS = (
    ("holdings.quantity", "quantity_text", "decimal_text"),
    ("holdings.market_value", "market_value_text", "decimal_text"),
    ("holdings.value_in_reporting_currency", "reporting_market_value_text", "decimal_text"),
    ("holdings.cost_basis", "cost_basis_text", "decimal_text"),
    ("holdings.unrealized_pnl", "unrealized_pnl_text", "decimal_text"),
    ("holdings.pnl_pct", "unrealized_pnl_pct", "float"),
    ("holdings.holding_weight", "weight", "float"),
)


def _holdings_slots(payload: Mapping) -> list[Slot]:
    if "valuation_method" not in payload:  # composite sleeves have no certified holdings
        return []
    slots = []
    for row in payload.get("holdings") or []:
        for figure_id, name, kind in HOLDING_FIELDS:
            slots.append(Slot(_key(figure_id, item=row.get("conid")), row, name, kind))
    if payload.get("holdings"):
        slots.append(Slot(_key("holdings.gross_market_value"), payload, "gross_holdings_text", "decimal_text"))
    concentration = payload.get("concentration")
    if isinstance(concentration, dict) and concentration.get("available"):
        if isinstance(concentration.get("largest"), dict):
            slots.append(Slot(_key("holdings.largest_holding"), concentration["largest"], "weight", "float"))
        for figure_id, name in (("holdings.top_5_weight", "top_5_weight"),
                                ("holdings.top_10_weight", "top_10_weight"),
                                ("holdings.effective_holdings", "effective_holdings")):
            slots.append(Slot(_key(figure_id), concentration, name, "float"))
        for row in concentration.get("currency_exposure") or []:
            slots.append(Slot(_key("holdings.currency_exposure", item=row.get("currency")), row, "weight", "float"))
    return slots


def _sector_slots(payload: Mapping) -> list[Slot]:
    return [Slot(_key("holdings.sector_allocation", item=row.get("sector")), row, "weight", "float")
            for row in payload.get("allocation") or [] if isinstance(row, dict)]


def _cash_slots(payload: Mapping) -> list[Slot]:
    if "model_cash" in payload:  # the composites' model cash is not a guide figure
        return []
    slots = []
    for row in payload.get("balances") or []:
        slots += [Slot(_key("cash.ending_cash", item=row.get("currency")), row, "ending_cash_text", "decimal_text"),
                  Slot(_key("cash.settled_cash", item=row.get("currency")), row, "settled_cash_text", "decimal_text")]
    return slots


STATISTICS_FIELDS = (
    ("stats.sortino_ratio", "sortino_ratio"), ("stats.calmar_ratio", "calmar_ratio"),
    ("stats.annualized_volatility", "annualized_volatility"),
    ("stats.downside_deviation", "downside_deviation"), ("stats.skewness", "skewness"),
    ("stats.excess_kurtosis", "excess_kurtosis"), ("stats.positive_months", "positive_month_ratio"),
    ("stats.average_gain_month", "average_gain_month"), ("stats.average_loss_month", "average_loss_month"),
    ("stats.gain_loss_ratio", "gain_loss_ratio"),
)


def _analytics_slots(payload: Mapping) -> list[Slot]:
    if not payload.get("available"):
        return []
    slots = []
    for row in payload.get("monthly_calendar") or []:
        for index, cell in enumerate(row.get("months") or []):
            if isinstance(cell, dict):
                slots.append(Slot(_key("performance.monthly_return", f"month_{row['year']}-{index + 1:02d}"),
                                  cell, "twr", "float"))
        if isinstance(row.get("year_return"), dict):
            slots.append(Slot(_key("performance.monthly_return", f"year_{row['year']}"),
                              row["year_return"], "twr", "float"))
    stats = payload.get("statistics") or {}
    for figure_id, name in STATISTICS_FIELDS:
        slots.append(Slot(_key(figure_id), stats, name, "float"))
    for figure_id, name in (("stats.best_day", "best_day"), ("stats.worst_day", "worst_day"),
                            ("stats.best_month", "best_month"), ("stats.worst_month", "worst_month")):
        if isinstance(stats.get(name), dict):
            slots.append(Slot(_key(figure_id), stats[name], "return", "float"))
    drawdown = payload.get("drawdown") or {}
    slots += [Slot(_key("stats.maximum_drawdown"), drawdown, "maximum_drawdown", "float"),
              Slot(_key("stats.current_drawdown"), drawdown, "current_drawdown", "float")]
    rolling = payload.get("rolling") or []
    # The chart stays as today; its last point is the certified trailing return, compared only.
    if rolling and rolling[-1].get("date") == payload.get("effective_as_of"):
        slots.append(Slot(_key("stats.trailing_12m_return"), rolling[-1], "trailing_return", "float", write=False))
    return slots


BENCHMARK_FIELDS = ("portfolio_total_return", "benchmark_total_return", "excess_total_return",
                    "tracking_error", "information_ratio", "beta", "correlation", "relative_win_rate")


def _benchmark_id(payload: Mapping) -> str:
    benchmark = payload.get("benchmark")
    return str(benchmark.get("benchmark_id") or "") if isinstance(benchmark, Mapping) else ""


def _benchmark_slots(payload: Mapping) -> list[Slot]:
    metrics = payload.get("metrics")
    if not payload.get("available") or not isinstance(metrics, dict):
        return []
    benchmark_id = _benchmark_id(payload)
    return [Slot(_key(f"benchmark.{name}", benchmark_id=benchmark_id), metrics, name, "float")
            for name in BENCHMARK_FIELDS]


RISK_FIELDS = (
    ("risk.total_return", "total_return"), ("risk.annualized_return", "annualized_return"),
    ("risk.annualized_volatility", "annualized_volatility"), ("risk.risk_free_rate", "risk_free_annual_rate"),
    ("risk.sharpe_ratio", "sharpe_ratio"), ("risk.expected_max_drawdown_1y", "expected_maximum_drawdown_1y"),
    ("risk.actual_max_drawdown_1y", "actual_maximum_drawdown_1y"),
    ("risk.tail_max_drawdown_1y", "tail_maximum_drawdown_1y"),
    ("risk.crisis_max_drawdown_2008", "crisis_maximum_drawdown"), ("risk.var_95_daily", "var_95_daily"),
    ("risk.etl_95_daily", "cvar_95_daily"), ("risk.etl_95_daily", "daily_expected_tail_loss"),
    ("risk.win_rate", "win_rate"), ("risk.blue_mean_daily_return", "blue_mean_daily_return"),
    ("risk.maximum_drawdown", "maximum_drawdown"),
)


def _risk_slots(payload: Mapping) -> list[Slot]:
    metrics = payload.get("metrics")
    if payload.get("cache_status") != "hit" or not isinstance(metrics, dict) or not metrics:
        return []
    return [Slot(_key(figure_id), metrics, name, "float")
            for figure_id, name in RISK_FIELDS if name in metrics]


def _risk_detail_slots(payload: Mapping) -> list[Slot]:
    if payload.get("cache_status") != "hit":
        return []
    slots = []
    contributors = payload.get("expected_tail_loss_contributors")
    if isinstance(contributors, dict):
        for side in ("top", "bottom"):
            for row in contributors.get(side) or []:
                slots.append(Slot(_key("risk.etl_contribution", item=row.get("conid")), row,
                                  "expected_tail_loss_contribution", "float"))
        if "residual_contribution" in contributors:
            slots.append(Slot(_key("risk.etl_residual_contribution"), contributors,
                              "residual_contribution", "float"))
    scenarios = payload.get("crisis_scenarios")
    if isinstance(scenarios, dict):
        for row in scenarios.get("scenarios") or []:
            slots.append(Slot(_key("risk.crisis_scenario_max_drawdown", item=row.get("scenario_id")), row,
                              "maximum_drawdown", "float"))
    return slots


def _account_records_slots(payload: Mapping) -> list[Slot]:
    """One account's holding records before Combined holdings adds them up."""
    slots = []
    for row in payload.get("rows") or []:
        for figure_id, name in (("holdings.quantity", "quantity_text"), ("holdings.market_value", "market_value_text"),
                                ("holdings.cost_basis", "cost_basis_text")):
            slots.append(Slot(_key(figure_id, item=row.get("conid")), row, name, "decimal_text"))
        # IBKR's unrealized P&L when it sent one; otherwise today's sum works it out from the
        # (certified) market value and cost basis, as holdings_view does.
        if row.get("unrealized_pnl_text") not in (None, ""):
            slots.append(Slot(_key("holdings.unrealized_pnl", item=row.get("conid")), row,
                              "unrealized_pnl_text", "decimal_text"))
    return slots


@dataclass(frozen=True)
class Group:
    page: str
    figure_ids: tuple[str, ...]


@dataclass(frozen=True)
class Endpoint:
    groups: tuple[Group, ...]
    slots: Callable[[Mapping], list[Slot]]
    effective: Callable[[Mapping], str | None]
    benchmark: Callable[[Mapping], str] | None = None
    # When figures are behind, only these keys come from the rebuilt answer (None: all).
    rebuilt_keys: tuple[str, ...] | None = None
    # Figures a page shows only some of (top and bottom 5): certified rows it does not
    # show are not counted as extra.
    partial: frozenset[str] = frozenset()


OVERVIEW_SUMMARY = ("overview.as_of", "overview.nav", "overview.positions",
                    "overview.reporting_currency", "overview.observations")
OVERVIEW_PERIODS = ("overview.twr", "overview.twr_annualised", "overview.irr_annualised",
                    "overview.net_external_flows", "overview.day_change")
HOLDINGS_FIGURES = tuple(figure_id for figure_id, _, _ in HOLDING_FIELDS) + (
    "holdings.gross_market_value", "holdings.largest_holding", "holdings.top_5_weight",
    "holdings.top_10_weight", "holdings.effective_holdings", "holdings.currency_exposure")
STATISTICS_FIGURES = tuple(figure_id for figure_id, _ in STATISTICS_FIELDS) + (
    "stats.best_day", "stats.worst_day", "stats.best_month", "stats.worst_month",
    "stats.maximum_drawdown", "stats.current_drawdown", "stats.trailing_12m_return")
RISK_FIGURES = tuple(dict.fromkeys(figure_id for figure_id, _ in RISK_FIELDS))

ENDPOINTS: dict[str, Endpoint] = {
    "accounts-overview": Endpoint(
        (Group("overview", ("overview.nav", "overview.as_of", "overview.reporting_currency", "overview.twr")),),
        _account_entry_slots, lambda payload: _day(payload.get("nav_date"))),
    "summary": Endpoint((Group("overview", OVERVIEW_SUMMARY),), _summary_slots,
                        lambda payload: _day(payload.get("effective_as_of"))),
    "performance-periods": Endpoint((Group("overview", OVERVIEW_PERIODS),), _period_slots,
                                    lambda payload: _day(payload.get("effective_as_of"))),
    "holdings": Endpoint((Group("holdings", HOLDINGS_FIGURES),), _holdings_slots,
                         lambda payload: _day(payload.get("snapshot_date"))),
    "sectors": Endpoint((Group("holdings", ("holdings.sector_allocation",)),), _sector_slots,
                        lambda payload: _day(payload.get("position_date"))),
    "cash": Endpoint((Group("cash", ("cash.ending_cash", "cash.settled_cash")),), _cash_slots,
                     lambda payload: _day(payload.get("balances_date")),
                     rebuilt_keys=("balances_date", "balances")),
    "analytics": Endpoint((Group("performance", ("performance.monthly_return",)),
                           Group("risk-statistics", STATISTICS_FIGURES)),
                          _analytics_slots, lambda payload: _day(payload.get("effective_as_of"))),
    "benchmark-comparison": Endpoint(
        (Group("performance", tuple(f"benchmark.{name}" for name in BENCHMARK_FIELDS)),), _benchmark_slots,
        lambda payload: _day((payload.get("metrics") or {}).get("period_end")) if payload.get("available") else None,
        benchmark=_benchmark_id),
    "risk": Endpoint((Group("risk-snapshot", RISK_FIGURES),), _risk_slots,
                     lambda payload: _day(payload.get("as_of"))),
    "risk-details": Endpoint(
        (Group("risk-snapshot", ("risk.etl_contribution", "risk.etl_residual_contribution",
                                 "risk.crisis_scenario_max_drawdown")),),
        _risk_detail_slots, lambda payload: _day(payload.get("as_of")),
        partial=frozenset({"risk.etl_contribution"})),
    "combined-holdings": Endpoint(
        (Group("combined-holdings", ("holdings.quantity", "holdings.market_value", "holdings.cost_basis",
                                     "holdings.unrealized_pnl")),),
        _account_records_slots, lambda payload: _day(payload.get("snapshot_date"))),
}


# --- The setting --------------------------------------------------------------------------


def page_modes(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Each page's mode, with a borrowing page held at shadow until the page it borrows from is on."""
    environ = os.environ if environ is None else environ
    modes = register.page_modes(environ.get(register.PAGES_SETTING, ""))
    for page, owner in BORROWS.items():
        if modes.get(page) == "on" and modes.get(owner) != "on":
            modes[page] = "shadow"
    return modes


def _active_groups(endpoint: Endpoint, environ: Mapping[str, str] | None) -> list[tuple[Group, str]]:
    text = (os.environ if environ is None else environ).get(register.PAGES_SETTING, "")
    if not str(text).strip():
        return []
    modes = page_modes(environ)
    return [(group, modes[group.page]) for group in endpoint.groups if modes.get(group.page, "off") != "off"]


# --- Reading the view ---------------------------------------------------------------------


@dataclass
class Certified:
    """The newest published figures for one subject and group, on one as-of date."""

    as_of: str
    values: dict[Key, Any] = field(default_factory=dict)
    build_id: str | None = None
    published_at: str | None = None


READ_SQL = f"""
SELECT figure_id, period, item, benchmark_id, as_of_date, value, value_text, unit, status,
       build_id, published_at
FROM {VIEW}
WHERE tenant_id = %(tenant)s AND subject_id = %(subject)s
  AND figure_id = ANY(%(figures)s) AND benchmark_id = ANY(%(benchmarks)s)
  AND as_of_date = (
      SELECT MAX(as_of_date) FROM {VIEW}
      WHERE tenant_id = %(tenant)s AND subject_id = %(subject)s
        AND figure_id = ANY(%(figures)s) AND benchmark_id = ANY(%(benchmarks)s)
        AND (%(as_of)s::date IS NULL OR as_of_date <= %(as_of)s::date))
"""


def read_certified(connection, *, tenant_id: str, subject_id: str, figure_ids: Iterable[str],
                   as_of: str | None = None, benchmark_id: str = "") -> Certified | None:
    """The newest published figures on or before ``as_of``; None when there are none."""
    rows = connection.execute(READ_SQL, {
        "tenant": tenant_id, "subject": subject_id, "figures": list(figure_ids),
        "benchmarks": ["", benchmark_id] if benchmark_id else [""], "as_of": as_of,
    }).fetchall()
    if not rows:
        return None
    certified = Certified(as_of=_day(rows[0]["as_of_date"]))
    newest = None
    for row in rows:
        if row["status"] != "ok":
            value = None
        elif row["unit"] in ("date", "text"):
            value = row["value_text"]
        else:
            value = None if row["value"] is None else float(row["value"])
        certified.values[_key(row["figure_id"], row["period"], row["item"], row["benchmark_id"])] = value
        if row["published_at"] is not None and (newest is None or row["published_at"] > newest["published_at"]):
            newest = row
    if newest is not None:
        certified.build_id = str(newest["build_id"])
        certified.published_at = newest["published_at"].isoformat() if hasattr(newest["published_at"], "isoformat") else str(newest["published_at"])
    return certified


@contextmanager
def _connect(tenant_id: str):
    """A read-only connection of its own, with a short statement timeout."""
    import psycopg
    from psycopg.rows import dict_row

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The website database connection is not configured")
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT_S,
                         options=f"-c statement_timeout={READ_TIMEOUT_MS} -c lock_timeout={READ_TIMEOUT_MS}") as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        yield connection


def _read_groups(groups: list[Group], *, tenant_id: str, subject_id: str, as_of: str | None,
                 benchmark_id: str) -> dict[str, Certified | None]:
    with _connect(tenant_id) as connection:
        return {group.page: read_certified(connection, tenant_id=tenant_id, subject_id=subject_id,
                                           figure_ids=group.figure_ids, as_of=as_of, benchmark_id=benchmark_id)
                for group in groups}


# --- Comparing and showing ----------------------------------------------------------------


def _same(today: Any, certified: Any, unit: str) -> bool:
    if today is None or certified is None:
        return today is None and certified is None
    if unit in ("date", "text"):
        return str(today)[:10] == str(certified)[:10] if unit == "date" else str(today) == str(certified)
    today, certified = float(today), float(certified)
    if unit == "money":
        return abs(today - certified) <= MONEY_TOLERANCE
    return math.isclose(today, certified, rel_tol=RELATIVE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE)


def _compare(slots: list[Slot], certified: Certified, partial: frozenset[str]) -> dict:
    today = {slot.key: slot.get() for slot in slots}
    differ, missing, extra = Counter(), Counter(), Counter()
    for key, value in today.items():
        if key not in certified.values:
            missing[key[0]] += 1
        elif not _same(value, certified.values[key], register.figure(key[0]).unit):
            differ[key[0]] += 1
    shown = {key[0] for key in today} - partial
    for key in certified.values:
        if key[0] in shown and key not in today:
            extra[key[0]] += 1
    return {"status": "differ" if differ or missing or extra else "match", "compared": len(today),
            "differ": dict(sorted(differ.items())), "missing": dict(sorted(missing.items())),
            "extra": dict(sorted(extra.items()))}


def _log(endpoint: str, page: str, mode: str, status: str, **details: Any) -> None:
    line = {"endpoint": endpoint, "page": page, "mode": mode, "status": status, **details}
    LOGGER.info("%s %s", LOG_PREFIX, json.dumps(line, sort_keys=True, separators=(",", ":")))


def _label(day: str) -> str:
    value = date.fromisoformat(day)
    return f"{value.day} {value:%b %Y}"


def _block(used: list[tuple[Group, Certified]], latest: str | None, count: int) -> dict:
    as_of = min(certified.as_of for _, certified in used)
    newest = max((certified for _, certified in used), key=lambda item: item.published_at or "")
    freshness = register.freshness(as_of, latest)
    behind = freshness["status"] != "fresh"
    return {
        "as_of": as_of,
        "label": f"Certified · as of {_label(as_of)}",
        "note": f"Not yet updated for {_label(latest)}" if behind and latest else None,
        "freshness": freshness,
        "pages": [group.page for group, _ in used],
        "figures": count,
        "build_id": newest.build_id,
        "published_at": newest.published_at,
        "link": GUIDE_LINK,
    }


def combined_block(blocks: list[dict]) -> dict:
    """One certified block for Combined holdings, from each account's: the oldest as-of date
    and the furthest behind, so the line never claims more than the stalest account."""
    order = {"fresh": 0, "behind": 1, "late": 2}
    stalest = min(blocks, key=lambda block: block["as_of"])
    worst = max(blocks, key=lambda block: (order.get(block["freshness"]["status"], 2),
                                           block["freshness"]["business_days_behind"]))
    newest = max(blocks, key=lambda block: block.get("published_at") or "")
    note = next((block["note"] for block in blocks if block.get("note")), None)
    return {**stalest, "note": note, "freshness": worst["freshness"], "accounts": len(blocks),
            "figures": sum(block["figures"] for block in blocks), "build_id": newest.get("build_id"),
            "published_at": newest.get("published_at")}


def _overlay(spec: Endpoint, payload: dict, used: list[tuple[Group, Certified]]) -> int:
    """Write certified values into the answer's own fields; returns how many were written."""
    planned, count = [], 0
    for slot in spec.slots(payload):
        if not slot.write:
            continue
        for group, certified in used:
            if slot.key[0] in group.figure_ids and slot.key in certified.values:
                value = certified.values[slot.key]
                count += 1
                # Decimal text keeps today's spelling ("1250.50") when the number is the same.
                if not (slot.kind == "decimal_text" and value is not None and slot.get() == float(value)):
                    planned.append((slot, slot.converted(value)))
    for slot, value in planned:  # every conversion has worked before anything is written
        slot.holder[slot.field] = value
    return count


def serve(endpoint: str, *, tenant_id: str, subject_id: str, requested_as_of: str | None,
          build: Callable[[str | None], dict], environ: Mapping[str, str] | None = None) -> dict:
    """The page's answer: today's, or with certified figures read alongside or shown.

    ``build(day)`` is the page's own builder for an as-of date. Call this only after the
    page's access check has passed for ``subject_id``.
    """
    spec = ENDPOINTS[endpoint]
    groups = _active_groups(spec, environ)
    if not groups:
        return build(requested_as_of)
    payload = build(requested_as_of)
    try:
        return _with_certified(endpoint, spec, groups, payload, tenant_id=tenant_id, subject_id=subject_id,
                               requested_as_of=requested_as_of, build=build)
    except Exception as error:  # the certified layer must never take a page down
        for group, mode in groups:
            _log(endpoint, group.page, mode, "error", error=type(error).__name__)
        return payload


def _with_certified(endpoint: str, spec: Endpoint, groups: list[tuple[Group, str]], payload: dict, *,
                    tenant_id: str, subject_id: str, requested_as_of: str | None,
                    build: Callable[[str | None], dict]) -> dict:
    slots = spec.slots(payload)
    shown = {slot.key[0] for slot in slots}
    groups = [(group, mode) for group, mode in groups if shown & set(group.figure_ids)]
    if not groups:  # nothing on this answer is a certified figure (an empty or composite answer)
        return payload
    latest = spec.effective(payload)
    benchmark_id = spec.benchmark(payload) if spec.benchmark else ""
    started = time.perf_counter()
    try:
        reads = _read_groups([group for group, _ in groups], tenant_id=tenant_id, subject_id=subject_id,
                             as_of=requested_as_of, benchmark_id=benchmark_id)
    except Exception as error:  # a slow, missing or failing view: today's figures
        read_ms = round((time.perf_counter() - started) * 1000, 1)
        for group, mode in groups:
            _log(endpoint, group.page, mode, "error", error=type(error).__name__, read_ms=read_ms)
        return payload
    read_ms = round((time.perf_counter() - started) * 1000, 1)
    used = []
    for group, mode in groups:
        certified = reads[group.page]
        group_slots = [slot for slot in slots if slot.key[0] in group.figure_ids]
        if certified is None:
            _log(endpoint, group.page, mode, "none", compared=0, read_ms=read_ms)
        elif latest and certified.as_of < latest:
            behind = register.freshness(certified.as_of, latest)["business_days_behind"]
            if mode == "shadow":
                _log(endpoint, group.page, mode, "behind", business_days_behind=behind, read_ms=read_ms)
            else:
                used.append((group, certified))
        elif mode == "shadow":
            _log(endpoint, group.page, mode, read_ms=read_ms, **_compare(group_slots, certified, spec.partial))
        else:
            used.append((group, certified))
    if not used:
        return payload
    return _show(endpoint, spec, payload, used, latest=latest, tenant_id=tenant_id, subject_id=subject_id,
                 requested_as_of=requested_as_of, build=build, benchmark_id=benchmark_id)


def _show(endpoint: str, spec: Endpoint, payload: dict, used: list[tuple[Group, Certified]], *,
          latest: str | None, tenant_id: str, subject_id: str, requested_as_of: str | None,
          build: Callable[[str | None], dict], benchmark_id: str) -> dict:
    """Today's answer for the certified date, with the certified values written into it."""
    day = min(certified.as_of for _, certified in used)
    answer = payload
    if latest and day < latest:
        # Held back or missing: the last published figures, with today's other fields for that date.
        rebuilt = build(day)
        if spec.rebuilt_keys is None:
            answer = rebuilt
            if "requested_as_of" in payload:
                answer["requested_as_of"] = payload["requested_as_of"]
        else:
            answer = {**payload, **{key: rebuilt[key] for key in spec.rebuilt_keys if key in rebuilt}}
        on_date = spec.effective(answer)
        stale = [group for group, certified in used if on_date and certified.as_of != on_date]
        if stale:
            again = _read_groups(stale, tenant_id=tenant_id, subject_id=subject_id, as_of=on_date,
                                 benchmark_id=benchmark_id)
            used = [(group, again.get(group.page) if group in stale else certified) for group, certified in used]
            used = [(group, certified) for group, certified in used
                    if certified is not None and certified.as_of == on_date]
        for group, certified in used:
            _log(endpoint, group.page, "on", "behind",
                 business_days_behind=register.freshness(certified.as_of, latest)["business_days_behind"])
        if not used:
            return payload
    count = _overlay(spec, answer, used)
    if not count:
        return payload
    answer["certified"] = _block(used, latest, count)
    return answer
