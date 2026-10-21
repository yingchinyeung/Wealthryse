"""The agents' figure tools on certified figures, with the automatic fallback (medallion U5,
docs/MEDALLION_CERTIFIED_FIGURES.md, "After month-end" item 3, brought forward to October).

One setting, AGENT_CERTIFIED_FIGURE_TOOLS, read where the tools run: on the agent tool server for
the agents' figure tools (wealthryse_agent.tools) and on the website for the website analyst's
(ibkr_etl.agent_tools, only through ibkr_etl.ai_analyst):

- off (the default, and any value not listed here): today's figures exactly. Nothing is read.
- fallback: every figure a tool works out that has a certified version with the same definition
  (a "slot") is compared with the published certified figure for the same subject, date, period,
  item and benchmark. When they are equal, within the tolerances the certified pages already use
  (ratios and numbers relative 1e-9, money to the cent, dates and text exactly, both blank
  counts as equal), the certified value is used; the agents' tools also list it in the answer's
  ``certified`` block, so an agent can cite it as [F#] (the website analyst's model sees only
  sanitised fields and gets no block). When they differ, or no certified figure is published,
  today's value stays, and one ``CERTIFIED_FIGURE_TOOLS {json}`` log line for the call gives the
  counts by figure id: never values, never account numbers (subjects masked as
  certified_build.mask does).
- on: the certified value wherever one is published, even when it differs (still logged);
  today's value where none is. Not used before two clean month-ends.

Where a tool's definition differs from the certified figure's (a one-year window, another
annualisation, the benchmark's own currency, another weight basis), there is no slot: the two
would never be equal, so the tool keeps today's value and nothing is logged. The tool modules
list each such case next to their slots.

The month-end snapshot builder (reporting_snapshot.py) never reaches this: ibkr_etl.agent_tools
applies it only when its caller passes ``certified_mode``, and only the website analyst does.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import logging
import math
import os
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping

from . import certified_figures as register

SETTING = "AGENT_CERTIFIED_FIGURE_TOOLS"
MODES = ("off", "fallback", "on")
LOG_PREFIX = "CERTIFIED_FIGURE_TOOLS"
# The website analyst's reader: its own short-lived, read-only connection with a statement timeout,
# as the certified pages read (a model call follows, so it may take a little longer than a page).
WEB_VIEW = "gold.v_certified_figures"
WEB_READ_TIMEOUT_MS = 1000
WEB_CONNECT_TIMEOUT_S = 2
WEB_LOGGER = logging.getLogger("uvicorn.error.certified_figures")
NOTE = ("These figures are certified: the stored, checked figures the website shows, with the same value as "
        "this tool's. Cite each one you quote by its ref, e.g. [F1], not by this tool call.")
OUTCOMES = ("equal", "different", "missing", "error")
# Where the fallback kept today's value although a certified figure exists for it (they differ,
# or the certified figures could not be read): {JSON pointer of the value: "fallback"}, beside
# the tool's own fields, for a reader (such as a supervising agent) to see which values are the
# old calculation. Absent when nothing fell back. A value with no certified figure published is
# not listed (the log line counts those).
SOURCE_FIELD = "figures_source"
FALLBACK = "fallback"

Key = tuple[str, str, str, str, str]  # figure_id, period, item, benchmark_id, as_of_date


def mode(environ: Mapping[str, str] | None = None) -> str:
    """off, fallback or on; anything else, or nothing, is off."""
    environ = os.environ if environ is None else environ
    value = str(environ.get(SETTING, "")).strip().lower()
    return value if value in MODES else "off"


@dataclass
class Slot:
    """One figure in a tool's answer that has a certified version with the same definition."""

    figure_id: str
    as_of: str
    holder: dict
    field: str
    at: str  # JSON pointer of the value in the tool's answer, such as /data/periods/2/twr
    period: str = ""
    item: str = ""
    benchmark_id: str = ""
    attach: bool = True  # list it in the answer's certified block (False: compare and use only)

    @property
    def key(self) -> Key:
        return (self.figure_id, self.period or "", self.item or "", self.benchmark_id or "", str(self.as_of)[:10])

    def today(self) -> Any:
        raw = self.holder.get(self.field)
        if raw is None or raw == "":
            return None
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None


def key_of(row: Mapping[str, Any]) -> Key:
    day = row["as_of_date"]
    day = day.isoformat() if hasattr(day, "isoformat") else str(day)
    return (str(row["figure_id"]), str(row["period"] or ""), str(row["item"] or ""), str(row["benchmark_id"] or ""),
            day[:10])


def certified_value(row: Mapping[str, Any]) -> Any:
    if row["status"] != "ok":
        return None
    if row["unit"] in ("date", "text"):
        return row["value_text"]
    return None if row["value"] is None else float(row["value"])


def same(today: Any, certified: Any, unit: str) -> bool:
    """The certified pages' own comparison (certified_pages._same): the release report's tolerances."""
    from .certified_pages import _same

    return _same(today, certified, unit)


def display(value: Any, unit: str, currency: str | None) -> str | None:
    """As get_certified_figures shows a value."""
    if value is None:
        return None
    if unit in ("date", "text"):
        return str(value)
    number = float(value)
    if unit == "ratio":
        return f"{number * 100:.2f}%"
    if unit == "money":
        return f"{number:,.2f}" + (f" {currency}" if currency else "")
    return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def attached(slot: Slot, row: Mapping[str, Any]) -> dict[str, Any]:
    """The certified figure as the answer lists it: what [F#] citations and traces need."""
    figure = register.figure(slot.figure_id)
    value = certified_value(row)
    currency = row.get("currency") or None
    return {
        "figure_id": figure.id, "label": figure.label, "value": value, "unit": row["unit"],
        **({"currency": currency} if currency else {}),
        "display": display(value, row["unit"], currency),
        "as_of_date": slot.key[4],
        **({"period_key": slot.period} if slot.period else {}),
        **({"item": slot.item} if slot.item else {}),
        **({"benchmark_id": slot.benchmark_id} if slot.benchmark_id else {}),
        "at": slot.at,
        "definition": {"id": row.get("definition_id") or figure.definition_id,
                       "version": row.get("definition_version") or figure.definition_version, "link": figure.link},
        "lineage": {"build_id": _iso(row.get("build_id"))},
    }


# --- Counting, for the comparison on a restored copy -----------------------------------------

_OBSERVERS: list[Callable[[str, str, str, str], None]] = []


@contextmanager
def observe(pointers: dict | None = None) -> Iterator[Counter]:
    """Counts each slot's outcome as (tool, figure_id, outcome) while the block runs; ``pointers``,
    when given, collects {(tool, JSON pointer): figure_id} for every slot compared."""
    counts: Counter = Counter()

    def note(tool: str, figure_id: str, outcome: str, at: str) -> None:
        counts[(tool, figure_id, outcome)] += 1
        if pointers is not None:
            pointers[(tool, at)] = figure_id

    _OBSERVERS.append(note)
    try:
        yield counts
    finally:
        _OBSERVERS.remove(note)


def _noted(tool: str, slot: "Slot", outcome: str) -> None:
    for note in list(_OBSERVERS):
        note(tool, slot.figure_id, outcome, slot.at)


def _mask(subject: str) -> str:
    from .certified_build import mask

    return mask(subject)


def _log(logger: logging.Logger, level: int, **line: Any) -> None:
    logger.log(level, "%s %s", LOG_PREFIX, json.dumps(line, sort_keys=True, separators=(",", ":")))


# --- The fallback itself ------------------------------------------------------------------------

Reader = Callable[[list[str], list[str], list[str]], Mapping[Key, Mapping[str, Any]]]


def settle(tool: str, subject: str, answer: dict, slots: list[Slot], read: Reader, mode_now: str,
           logger: logging.Logger, block: Callable[[dict], dict] | None = None) -> dict:
    """Compare each slot with its certified figure; use, attach and log as the mode says.

    ``read(figure_ids, dates, benchmark_ids)`` returns the published rows by key_of(row). A
    failing read changes nothing: today's figures, and one log line. Returns ``answer``, with
    the used values written in place and, when any figure was used, a ``certified`` block
    (``block`` says where it goes; by default the answer's top level)."""
    if mode_now not in ("fallback", "on") or not slots:
        return answer
    started = time.perf_counter()
    try:
        found = read(sorted({slot.figure_id for slot in slots}), sorted({slot.key[4] for slot in slots}),
                     sorted({slot.benchmark_id for slot in slots if slot.benchmark_id}))
    except Exception as error:  # the certified layer must never take a tool down
        for slot in slots:
            _noted(tool, slot, "error")
        answer[SOURCE_FIELD] = {slot.at: FALLBACK for slot in slots}
        _log(logger, logging.WARNING, tool=tool, mode=mode_now, subject=_mask(subject), status="error",
             error=type(error).__name__, compared=len(slots),
             read_ms=round((time.perf_counter() - started) * 1000, 1))
        return answer
    read_ms = round((time.perf_counter() - started) * 1000, 1)
    differ, missing, writes, figures = Counter(), Counter(), [], []
    fallback: dict[str, str] = {}
    used = 0
    for slot in slots:
        unit = register.figure(slot.figure_id).unit
        row = found.get(slot.key)
        if row is None:
            outcome = "missing"
            missing[slot.figure_id] += 1
        else:
            outcome = "equal" if same(slot.today(), certified_value(row), unit) else "different"
            if outcome == "different":
                differ[slot.figure_id] += 1
        _noted(tool, slot, outcome)
        if outcome == "different" and mode_now != "on":
            fallback[slot.at] = FALLBACK
        if row is None or (outcome == "different" and mode_now != "on"):
            continue
        used += 1
        value = certified_value(row)
        if not (outcome == "equal" and slot.today() == value):  # exactly equal keeps today's spelling
            writes.append((slot, value))
        if slot.attach and row["status"] == "ok":
            figures.append(attached(slot, row))
    for slot, value in writes:
        slot.holder[slot.field] = value
    if fallback:
        answer[SOURCE_FIELD] = fallback
    if figures:
        (block(answer) if block else answer)["certified"] = {"figures": figures, "note": NOTE}
    if differ or missing:
        _log(logger, logging.WARNING if differ else logging.INFO, tool=tool, mode=mode_now, subject=_mask(subject),
             status="differ" if differ else "missing", compared=len(slots), used=used,
             differ=dict(sorted(differ.items())), missing=dict(sorted(missing.items())), read_ms=read_ms)
    return answer


# --- The website analyst's tools (ibkr_etl.agent_tools) ---------------------------------------
#
# Slots, and the cases that have none:
# - compare_with_benchmarks: data.metrics' eight comparison figures -> benchmark.<name> for that
#   benchmark (the certified build stores only the benchmark the Performance page shows; another
#   one counts as missing).
# - get_risk_metrics: data.metrics -> risk.* as the Risk snapshot reads them
#   (certified_pages.RISK_FIELDS): the same benchmark-dated, sqrt(252) definitions.
# - get_sector_allocation: data.allocation[].weight -> holdings.sector_allocation per sector.
# - get_holdings has no weights (it returns IBKR's rows), get_performance_series no returns (NAV
#   records only), get_portfolio_summary and get_data_freshness are not figure tools here: no slots.


def _web_slots(tool: str, as_of: str | None, data: dict) -> list[Slot]:
    from .certified_pages import BENCHMARK_FIELDS, RISK_FIELDS

    if not as_of:
        return []
    metrics = data.get("metrics")
    if tool == "compare_with_benchmarks" and isinstance(metrics, dict):
        benchmark = data.get("benchmark") or {}
        benchmark_id = str(benchmark.get("benchmark_id") or "") if isinstance(benchmark, Mapping) else ""
        if not benchmark_id:
            return []
        return [Slot(f"benchmark.{name}", as_of, metrics, name, f"/data/metrics/{name}", benchmark_id=benchmark_id)
                for name in BENCHMARK_FIELDS if name in metrics]
    if tool == "get_risk_metrics" and isinstance(metrics, dict):
        return [Slot(figure_id, as_of, metrics, name, f"/data/metrics/{name}")
                for figure_id, name in RISK_FIELDS if name in metrics]
    if tool == "get_sector_allocation":
        return [Slot("holdings.sector_allocation", as_of, row, "weight", f"/data/allocation/{index}/weight",
                     item=str(row["sector"]))
                for index, row in enumerate(data.get("allocation") or [])
                if isinstance(row, dict) and row.get("sector") not in (None, "")]
    return []


WEB_READ_SQL = f"""
SELECT figure_id, period, item, benchmark_id, as_of_date, value, value_text, unit, currency, status,
       definition_id, definition_version, calculation_version, build_id, published_at
FROM {WEB_VIEW}
WHERE tenant_id = %(tenant)s AND subject_id = %(subject)s AND figure_id = ANY(%(figures)s)
  AND as_of_date = ANY(%(dates)s::date[]) AND benchmark_id = ANY(%(benchmarks)s)
"""


@contextmanager
def _web_connection(tenant_id: str):
    """A read-only connection of its own with a short statement timeout, as the certified pages use."""
    import psycopg
    from psycopg.rows import dict_row

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The website database connection is not configured")
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=WEB_CONNECT_TIMEOUT_S,
                         options=f"-c statement_timeout={WEB_READ_TIMEOUT_MS} "
                                 f"-c lock_timeout={WEB_READ_TIMEOUT_MS}") as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        yield connection


def web_reader(tenant_id: str, subject_id: str) -> Reader:
    def read(figure_ids: list[str], dates: list[str], benchmark_ids: list[str]) -> dict[Key, Mapping]:
        with _web_connection(tenant_id) as connection:
            rows = connection.execute(WEB_READ_SQL, {
                "tenant": tenant_id, "subject": subject_id, "figures": figure_ids, "dates": dates,
                "benchmarks": ["", *benchmark_ids]}).fetchall()
        return {key_of(row): row for row in rows}
    return read


def apply_to_result(result: Any, *, tenant_id: str, mode_now: str, reader: Reader | None = None) -> Any:
    """A website tool's AgentToolResult with certified figures read alongside, as the mode says.

    Works on a copy of the result's data; returns the result unchanged when nothing is certified."""
    if mode_now not in ("fallback", "on"):
        return result
    data = copy.deepcopy(dict(result.data or {}))
    slots = _web_slots(result.tool_name, result.as_of, data)
    if not slots:
        return result
    for slot in slots:
        slot.attach = False  # the analyst's model sees only sanitised fields and cites no [F#]: no block
    read = reader or web_reader(tenant_id, result.subject_id)
    settle(result.tool_name, result.subject_id, data, slots, read, mode_now, WEB_LOGGER)
    if data == dict(result.data or {}):
        return result
    return dataclasses.replace(result, data=data)
