"""Monthly reports on certified figures, with the automatic fallback (medallion upgrade U5,
"After month-end" item 2; docs/MEDALLION_CERTIFIED_FIGURES.md, "Monthly reports and Attribution").

One setting, MONTHLY_REPORTS_CERTIFIED, says what a month-end report does with the certified
figures when its snapshot is made (reporting_snapshot.build_month_end_payload, which every
freeze uses):

- off (the default; unset, empty or any other value): today's behaviour exactly. Nothing is
  read and nothing is logged.
- fallback: the report is made with today's code as before. The certified figures for the
  report's data date are read alongside, and every report field that has a certified version
  is compared with it. A certified value is used only when it is exactly today's value, so the
  report's bytes never change; a value within the certified tolerance counts as close, and
  today's value is kept, as it is for a different or missing one. One line,
  ``CERTIFIED_REPORTS {json}``, reports each report: the masked subject, counts and figure ids,
  never values.
- on: the certified value replaces today's in every field the mapping below writes, wherever a
  certified value exists. Not used before two clean month-ends.

The certified figures are read for exactly the report's data date (data_as_of, the last NAV
date on or before the month end), on the report's own connection inside a savepoint, so a
missing view or a failed read leaves the report as today's and never breaks the freeze.

Which report fields have a certified version (REPORT_FIELDS below; write=False means compared
only, never written, because the report works it out on another basis):

- portfolio_summary.latest_nav.total_text: overview.nav
- portfolio_summary.positions: overview.positions
- risk.metrics.<name>: risk.<figure> (certified_pages.RISK_FIELDS: the risk snapshot's 15 fields)
- benchmark_comparisons[].metrics.<name>: benchmark.<name> for that comparison's benchmark (the
  build certifies each subject's default benchmark, so another benchmark's are missing)
- monthly_reporting.portfolio_monthly_return, .equity_contribution, .residual_contribution:
  attribution.portfolio_return, .holdings_contribution, .residual_contribution (month_YYYY-MM)
- monthly_reporting.security_contributions[].contribution: attribution.holding_contribution
  (item: conid); .sector_contributions[].contribution: attribution.sector_contribution (item: sector)
- compared only: month_end_performance[].portfolio_monthly_return against
  performance.monthly_return (the report's is the ratio of the benchmark comparison's month-end
  levels, the certified one a time-weighted return over every NAV date);
  sector_allocation.gross_market_value and .allocation[].weight against holdings.gross_market_value
  and holdings.sector_allocation (the report converts with its own dated reporting FX).

The Alpha World and profiled design composites are made from their own approved HKD design, not
from today's website figures, so they are not compared (status not_applicable).
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import certified_figures as register
from .certified_pages import (ATTRIBUTION_TOTALS, BENCHMARK_FIELDS, RISK_FIELDS, VIEW, Slot, _key, _same)

SETTING = "MONTHLY_REPORTS_CERTIFIED"
MODES = ("off", "fallback", "on")
LOG_PREFIX = "CERTIFIED_REPORTS"
DESIGN_SUBJECTS = frozenset({"alpha-world-ms", "alpha-world-t"})
EQUAL, CLOSE, DIFFERENT, MISSING = "equal", "close", "different", "missing"
STATUSES = (EQUAL, CLOSE, DIFFERENT, MISSING)

READ_SQL = f"""
SELECT figure_id, period, item, benchmark_id, value, value_text, unit, status
FROM {VIEW}
WHERE tenant_id = ? AND subject_id = ? AND as_of_date = ? AND figure_id = ANY(?)
"""


def mode(environ: Mapping[str, str] | None = None) -> str:
    """off, fallback or on; anything else is off."""
    value = str((os.environ if environ is None else environ).get(SETTING, "")).strip().lower()
    return value if value in MODES else "off"


@dataclass
class ReportField:
    """One report value with a certified version: where it is and which certified figure it is."""

    field_id: str  # the report's field, list items as [] ("risk.metrics.sharpe_ratio")
    slot: Slot     # its place in the payload, the certified key and whether "on" writes it

    @property
    def figure_id(self) -> str:
        return self.slot.key[0]


def _benchmark_id(comparison: Mapping) -> str:
    benchmark = comparison.get("benchmark")
    return str(benchmark.get("benchmark_id") or "") if isinstance(benchmark, Mapping) else ""


def own_design(subject_id: str, payload: Mapping) -> bool:
    """A report made from its own approved design (Alpha World MS and T, profiled composites)."""
    return subject_id in DESIGN_SUBJECTS or payload.get("report_profile") is not None \
        or "methodology_identity" in payload


def report_fields(payload: Mapping) -> list[ReportField]:
    """Every value in a month-end payload that has a certified version (see the module docstring)."""
    fields: list[ReportField] = []

    def add(field_id: str, key: tuple, holder: Any, name: str, kind: str = "float", write: bool = True) -> None:
        if isinstance(holder, dict) and name in holder:
            fields.append(ReportField(field_id, Slot(key, holder, name, kind, write)))

    summary = payload.get("portfolio_summary") or {}
    if isinstance(summary, dict):
        add("portfolio_summary.latest_nav.total_text", _key("overview.nav"), summary.get("latest_nav"),
            "total_text", "decimal_text")
        add("portfolio_summary.positions", _key("overview.positions"), summary, "positions", "int")
    metrics = (payload.get("risk") or {}).get("metrics")
    for figure_id, name in RISK_FIELDS:
        add(f"risk.metrics.{name}", _key(figure_id), metrics, name)
    for comparison in payload.get("benchmark_comparisons") or []:
        if not isinstance(comparison, dict) or not comparison.get("metrics"):
            continue
        benchmark_id = _benchmark_id(comparison)
        for name in BENCHMARK_FIELDS:
            add(f"benchmark_comparisons[].metrics.{name}", _key(f"benchmark.{name}", benchmark_id=benchmark_id),
                comparison["metrics"], name)
    reporting = payload.get("monthly_reporting") or {}
    # The composites' attribution (no position coverage) is not certified.
    if isinstance(reporting, dict) and reporting.get("available") and "position_coverage" in reporting:
        period = f"month_{str(payload.get('period_end') or '')[:7]}"
        for figure_id, name in ATTRIBUTION_TOTALS:
            add(f"monthly_reporting.{name}", _key(figure_id, period), reporting, name)
        for row in reporting.get("security_contributions") or []:
            add("monthly_reporting.security_contributions[].contribution",
                _key("attribution.holding_contribution", period, row.get("conid")), row, "contribution")
        for row in reporting.get("sector_contributions") or []:
            add("monthly_reporting.sector_contributions[].contribution",
                _key("attribution.sector_contribution", period, row.get("sector")), row, "contribution")
    for point in payload.get("month_end_performance") or []:
        if isinstance(point, dict) and point.get("portfolio_monthly_return") is not None:
            add("month_end_performance[].portfolio_monthly_return",
                _key("performance.monthly_return", f"month_{str(point.get('date'))[:7]}"), point,
                "portfolio_monthly_return", write=False)
    sectors = payload.get("sector_allocation") or {}
    if isinstance(sectors, dict):
        add("sector_allocation.gross_market_value", _key("holdings.gross_market_value"), sectors,
            "gross_market_value", write=False)
        for row in sectors.get("allocation") or []:
            add("sector_allocation.allocation[].weight", _key("holdings.sector_allocation", item=row.get("sector")),
                row, "weight", write=False)
    return fields


def read_figures(connection, *, tenant_id: str, subject_id: str, as_of: str,
                 figure_ids: list[str]) -> dict[tuple, Any]:
    """The certified values on exactly ``as_of``, by key; an unavailable figure's value is None.
    Read inside a savepoint where the connection has one, so a failure leaves its transaction usable."""
    def run():
        return connection.execute(READ_SQL, (tenant_id, subject_id, as_of, figure_ids)).fetchall()

    transaction = getattr(connection, "transaction", None)
    if callable(transaction):
        with transaction():
            rows = run()
    else:
        rows = run()
    found = {}
    for row in rows:
        if row["status"] != "ok":
            value = None
        elif row["unit"] in ("date", "text"):
            value = row["value_text"]
        else:
            value = None if row["value"] is None else float(row["value"])
        found[_key(str(row["figure_id"]), str(row["period"]), str(row["item"]), str(row["benchmark_id"]))] = value
    return found


def status(field: ReportField, certified: Mapping[tuple, Any]) -> str:
    """equal (exactly), close (within the certified tolerance), different or missing."""
    if field.slot.key not in certified:
        return MISSING
    today, value = field.slot.get(), certified[field.slot.key]
    if today is None or value is None:
        return EQUAL if today is None and value is None else DIFFERENT
    unit = register.figure(field.figure_id).unit
    if unit in ("date", "text"):
        return EQUAL if _same(today, value, unit) else DIFFERENT
    if float(today) == float(value):
        return EQUAL
    return CLOSE if _same(today, value, unit) else DIFFERENT


def _number(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def compare(fields: list[ReportField], certified: Mapping[tuple, Any]) -> list[dict]:
    """One entry per field, with both values (for the private rehearsal file only)."""
    return [{"field_id": field.field_id, "figure_id": field.figure_id, "period": field.slot.key[1],
             "item": field.slot.key[2], "benchmark_id": field.slot.key[3], "write": field.slot.write,
             "status": status(field, certified), "today": _number(field.slot.get()),
             "certified": _number(certified.get(field.slot.key))}
            for field in fields]


def _counts(entries: list[dict]) -> dict:
    found = Counter(entry["status"] for entry in entries)
    counts = {"compared": len(entries), **{name: found.get(name, 0) for name in STATUSES}}
    for name in (CLOSE, DIFFERENT, MISSING):
        ids = sorted({entry["figure_id"] for entry in entries if entry["status"] == name})
        if ids:
            counts[f"{name}_ids"] = ids
    return counts


def _write(fields: list[ReportField], certified: Mapping[tuple, Any], selected: str) -> int:
    """Write certified values into the payload: in fallback only those exactly today's (so nothing
    changes), in on every one there is. Returns how many fields took the certified value."""
    planned, used = [], 0
    for field in fields:
        if not field.slot.write or field.slot.key not in certified:
            continue
        value = certified[field.slot.key]
        if selected == "fallback":
            used += status(field, certified) == EQUAL
            continue  # exactly today's value: writing it would change nothing
        if value is None:
            continue
        used += 1
        # Decimal text keeps today's spelling ("1250.50") when the number is the same.
        if not (field.slot.kind == "decimal_text" and field.slot.get() == float(value)):
            planned.append((field.slot, field.slot.converted(value)))
    for slot, value in planned:  # every conversion has worked before anything is written
        slot.holder[slot.field] = value
    return used


def _log(line: dict, out: Callable[[str], None] | None) -> None:
    text = LOG_PREFIX + " " + json.dumps(line, sort_keys=True, separators=(",", ":"))
    try:
        (out or (lambda value: print(value, flush=True)))(text)
    except Exception:  # noqa: BLE001 - logging must not fail the report
        pass


def apply_certified(connection, *, tenant_id: str, subject_id: str, payload: dict,
                    environ: Mapping[str, str] | None = None,
                    out: Callable[[str], None] | None = None) -> dict | None:
    """Compare (fallback) or replace (on) the payload's certified fields, as MONTHLY_REPORTS_CERTIFIED
    says. Returns None when off; otherwise the comparison, with the values, for the rehearsal tool.
    Logs one CERTIFIED_REPORTS line (counts and figure ids, masked subject)."""
    selected = mode(environ)
    if selected == "off":
        return None
    from .certified_build import mask

    line: dict[str, Any] = {"mode": selected, "subject": mask(subject_id)}
    result: dict[str, Any] = {"mode": selected, "fields": [], "used": 0}
    as_of = str(payload.get("data_as_of") or "")
    if own_design(subject_id, payload) or not as_of:
        line["status"] = result["status"] = "not_applicable"
        _log(line, out)
        return result
    fields = report_fields(payload)
    try:
        certified = read_figures(connection, tenant_id=tenant_id, subject_id=subject_id, as_of=as_of,
                                 figure_ids=sorted({field.figure_id for field in fields}))
    except Exception as error:  # noqa: BLE001 - the certified layer must never break a report
        line.update(status="error", error=type(error).__name__, compared=0)
        result.update(status="error", error=type(error).__name__)
        _log(line, out)
        return result
    entries = compare(fields, certified)
    written = [entry for entry in entries if entry["write"]]
    only = [entry for entry in entries if not entry["write"]]
    try:
        used = _write(fields, certified, selected)
    except (TypeError, ValueError) as error:  # a value that cannot be converted: today's report
        line.update(status="error", error=type(error).__name__)
        result.update(status="error", error=type(error).__name__, fields=entries)
        _log(line, out)
        return result
    counts = _counts(written)
    agreed = counts[DIFFERENT] == counts[MISSING] == 0 and (selected == "on" or counts[CLOSE] == 0)
    state = "none" if not certified else "match" if agreed else "differ"
    line.update(status=state, **counts, used=used, kept_today=len(written) - used,
                compared_only=_counts(only))
    result.update(status=state, fields=entries, used=used)
    _log(line, out)
    return result
