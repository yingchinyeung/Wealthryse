"""The agents' certified figures tools (medallion upgrade U5, build plan part 7).

get_certified_figures and trace_figure read the stored, checked figures every website page
reads (agent.certified_figures, migrations/agent/017_certified_figures.sql) instead of working
figures out again. list_certified_figures lists the register and reads no client data, so it
sits with the guide tools.

All of it is off unless AGENT_CERTIFIED_FIGURES=on, and even then a figure is served only
once its own website page is on in CERTIFIED_FIGURES_PAGES (set to the website's value on the
tool server), so an agent never quotes a certified figure the website does not show yet. The
month-end commentary job (month_end_drafter) never gets them before 8 Nov: its drafts must
match the frozen month-end reports. The register is read through ibkr_etl.certified_figures.
"""
from __future__ import annotations

import os
from typing import Any, Mapping

from ibkr_etl import certified_figures as register

AGENT_SETTING = register.AGENT_SETTING
PAGES_SETTING = register.PAGES_SETTING
GET = "get_certified_figures"
TRACE = "trace_figure"
LIST = "list_certified_figures"
# The month-end commentary job keeps its own tool list until 8 Nov (open question 12).
NOT_FOR_JOBS = frozenset({"month_end_drafter"})
MAX_FIGURE_IDS = 25
MAX_ROWS = 200
PUBLISH_GATE = "shadow"  # the publish gate's verdict is recorded, not binding, until 8 Nov
# The tool that still works a page's figures out, for a figure whose page has not moved.
STILL_ANSWERED_BY = {
    "overview": "get_performance", "holdings": "get_holdings", "cash": "get_data_freshness",
    "performance": "compare_to_benchmark", "risk-snapshot": "get_risk_metrics",
    "risk-statistics": "get_risk_metrics", "combined-holdings": "get_holdings",
}
_ON = ("on", "true", "1", "yes")


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    """AGENT_CERTIFIED_FIGURES=on shows the tools; anything else, or nothing, hides them."""
    environ = os.environ if environ is None else environ
    return str(environ.get(AGENT_SETTING, "")).strip().lower() in _ON


def served(environ: Mapping[str, str] | None = None) -> dict[str, register.Figure]:
    """The figures the tools may serve: none when off; else those whose page is on."""
    environ = os.environ if environ is None else environ
    if not enabled(environ):
        return {}
    return {item.id: item for item in register.served_figures(register.page_modes(environ.get(PAGES_SETTING)))}


# ---- get_certified_figures ----

def _display(value: Any, unit: str, currency: str | None) -> str | None:
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


def _wanted(arguments: Mapping[str, Any], allowed: Mapping[str, register.Figure], error) -> list[register.Figure]:
    ids, page = arguments.get("figures"), arguments.get("page")
    if bool(ids) == bool(page):
        raise error("invalid_arguments", "Give either figures (1 to 25 figure ids) or page, not both.", 400)
    if page:
        if page not in register.pages():
            raise error("invalid_arguments", "page must be one of: " + ", ".join(register.pages()) + ".", 400)
        if register.page_modes(os.environ.get(PAGES_SETTING)).get(page) != "on" or not allowed:
            raise error("page_not_moved", f"The {page} page does not show certified figures yet. Use "
                        f"{STILL_ANSWERED_BY.get(page, 'the existing tools')} for its figures.", 409)
        return [item for item in register.figures_for_page(page) if item.id in allowed]
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_FIGURE_IDS or not all(isinstance(x, str) for x in ids):
        raise error("invalid_arguments", f"figures must be a list of 1 to {MAX_FIGURE_IDS} figure ids.", 400)
    found = []
    for figure_id in dict.fromkeys(ids):
        try:
            item = register.figure(figure_id)
        except register.UnknownFigure as unknown:
            hint = f" Closest: {', '.join(unknown.closest)}." if unknown.closest else ""
            raise error("unknown_figure", f"There is no certified figure called {figure_id}.{hint} "
                        f"list_certified_figures lists them.", 404) from None
        if item.id not in allowed:
            raise error("page_not_moved", f"{item.id} is not certified on the website yet. Use "
                        f"{STILL_ANSWERED_BY.get(item.page, 'the existing tools')} for it.", 409)
        found.append(item)
    return found


def _as_of(connection, account_id: str, ids: list[str], requested: str | None, error) -> str:
    row = connection.execute(
        "SELECT MAX(as_of_date) AS d FROM certified_figures WHERE subject_id = ? AND figure_id = ANY(?) "
        "AND (?::date IS NULL OR as_of_date <= ?::date)", (account_id, list(ids), requested, requested)).fetchone()
    if row is None or row["d"] is None:
        raise error("no_certified_figures", "No certified figure is published on or before that date. The build "
                    "publishes after the daily data steps, by about 10:00 UTC.", 404)
    return _iso(row["d"])


def _behind_reason(connection, published_at: Any) -> str:
    rows = connection.execute("SELECT status, started_at FROM certified_builds").fetchall()
    later = [row for row in rows if published_at is None or row["started_at"] > published_at]
    if any(row["status"] == "held" for row in later):
        return "The newest build was held for review, so these are the last published figures."
    return "The figures for the newer date are not published yet."


def get_certified_figures(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    from .tools import ToolError, _benchmark_mapping, _day, _latest_nav_date, _subject

    allowed = served()
    account_id = str(arguments["account_id"])
    wanted = _wanted(arguments, allowed, ToolError)
    subject = _subject(connection, account_id)
    requested = _day(arguments["as_of"], "as_of") if arguments.get("as_of") else None
    period = str(arguments.get("period") or "")
    if period and not any(register.period_allowed(item, period) for item in wanted):
        raise ToolError("invalid_arguments", f"None of these figures has the period {period}.", 400)
    ids = [item.id for item in wanted]
    as_of = _as_of(connection, account_id, ids, requested, ToolError)
    benchmark = arguments.get("benchmark_id")
    if not benchmark and any(item.per_benchmark for item in wanted):
        mapping = _benchmark_mapping(connection, account_id, as_of)
        benchmark = mapping["benchmark_id"] if mapping else None
    rows = connection.execute(
        """
        SELECT figure_id, period, item, benchmark_id, as_of_date, value, value_text, unit, currency, status, reason,
               period_start, period_end, observations, definition_id, definition_version, calculation_version,
               source_table, source_key, build_id, lineage_run_id, build_status, published_at
        FROM certified_figures
        WHERE subject_id = ? AND figure_id = ANY(?) AND as_of_date = ?::date
          AND (?::text = '' OR period IN ('', ?)) AND (?::text IS NULL OR benchmark_id IN ('', ?))
        ORDER BY figure_id, period, item, benchmark_id
        """, (account_id, ids, as_of, period, period, benchmark, benchmark)).fetchall()
    try:
        latest = _latest_nav_date(connection, account_id, None)
    except ToolError:
        latest = as_of
    newest_publication = max((row["published_at"] for row in rows if row["published_at"] is not None), default=None)
    reason = _behind_reason(connection, newest_publication) if latest > as_of else None
    freshness = register.freshness(as_of, latest, reason=reason)
    by_id = {item.id: item for item in wanted}
    figures, unavailable, seen = [], [], set()
    for row in rows:
        item = by_id[row["figure_id"]]
        seen.add(item.id)
        if row["status"] != "ok":
            unavailable.append({"figure_id": item.id, "period": row["period"] or None, "item": row["item"] or None,
                                "reason": row["reason"] or "Not available for this date."})
            continue
        if len(figures) >= MAX_ROWS:
            continue
        value = row["value_text"] if row["unit"] in ("date", "text") else row["value"]
        figures.append({
            "ref": f"F{len(figures) + 1}",
            "figure_id": item.id,
            "label": item.label,
            "value": value,
            "unit": row["unit"],
            **({"currency": row["currency"]} if row["currency"] else {}),
            "display": _display(value, row["unit"], row["currency"]),
            "as_of_date": _iso(row["as_of_date"]),
            **({"period_key": row["period"]} if row["period"] else {}),
            **({"item": row["item"]} if row["item"] else {}),
            **({"benchmark_id": row["benchmark_id"]} if row["benchmark_id"] else {}),
            "period": {"start": _iso(row["period_start"]), "end": _iso(row["period_end"]),
                       "observations": row["observations"], **({"basis": item.basis} if item.basis else {})},
            "definition": {"id": row["definition_id"], "version": row["definition_version"], "name": item.guide,
                           "link": item.link,
                           "what_it_tells_you": _meaning(item.guide)},
            "calculation_version": row["calculation_version"],
            "freshness": freshness,
            "lineage": {"build_id": _iso(row["build_id"]), "run_id": _iso(row["lineage_run_id"]),
                        "source": {"table": row["source_table"], "key": row["source_key"]}},
            "build": {"status": row["build_status"], "published_at": _iso(row["published_at"]),
                      "publish_gate": PUBLISH_GATE},
        })
    unavailable += [{"figure_id": figure_id, "reason": f"No certified figure for {as_of}."}
                    for figure_id in ids if figure_id not in seen and arguments.get("figures")]
    result = {
        "as_of": as_of,
        "subject": {"id": account_id, "type": subject["subject_type"],
                    "reporting_currency": subject["reporting_currency"] or subject["base_currency"]},
        "figures": figures,
        "unavailable": unavailable,
        "citations": [{"source": "certified_figures", "account_id": account_id, "as_of": as_of,
                       "build_ids": sorted({figure["lineage"]["build_id"] for figure in figures})}],
    }
    if len(rows) - len(unavailable) > MAX_ROWS:
        result["truncated"] = f"Only the first {MAX_ROWS} figures are returned; ask for fewer figure ids."
    return result


def _meaning(guide_name: str) -> str | None:
    from ibkr_etl import metrics_guide

    try:
        return metrics_guide.definition(guide_name).meaning
    except (KeyError, ValueError):
        return None


# ---- trace_figure ----

def trace_figure(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    from .tools import ToolError, _day

    allowed = served()
    account_id = str(arguments["account_id"])
    figure_id = str(arguments["figure_id"])
    [item] = _wanted({"figures": [figure_id]}, allowed, ToolError)
    requested = _day(arguments["as_of"], "as_of") if arguments.get("as_of") else None
    as_of = _as_of(connection, account_id, [item.id], requested, ToolError)
    period, part, benchmark = (str(arguments.get(name) or "") for name in ("period", "item", "benchmark_id"))
    row = connection.execute(
        "SELECT agent.certified_figure_trace(?, ?, ?, ?, ?, ?::date) AS trace",
        (account_id, item.id, period, part, benchmark, as_of)).fetchone()
    trace = row["trace"] if row else None
    if not trace:
        raise ToolError("figure_not_found", f"No published {item.id} for {as_of} with that period, item and "
                        "benchmark. get_certified_figures shows which exist.", 404)
    return {
        "as_of": as_of,
        "data": {"figure_id": item.id, "label": item.label, "as_of_date": as_of,
                 **{name: value for name, value in (("period", period), ("item", part), ("benchmark_id", benchmark))
                    if value},
                 **trace,
                 "how_to_read": "runs go from the build back to the raw files; depth 0 wrote the figure. A step "
                                "whose basis is dataset-time is inferred from timing: the latest run that wrote "
                                "that table before the next step started."},
        "citations": [{"source": "certified_figures", "account_id": account_id, "as_of": as_of,
                       "figure_id": item.id}],
    }


# ---- list_certified_figures (no client data) ----

LIST_SPEC = {
    "name": LIST,
    "description": "The certified figures: each figure id with its label, unit, page, periods, Metrics guide link, "
                   "and whether get_certified_figures serves it now (only once its website page shows certified "
                   "figures). No account data.",
    "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
        "page": {"type": "string", "description": "only this page's figures: " + ", ".join(sorted(STILL_ANSWERED_BY))}},
        "required": []},
}


def list_certified_figures(arguments: Mapping[str, Any]) -> dict[str, Any]:
    page = arguments.get("page")
    if page and page not in register.pages():
        raise ValueError("page must be one of: " + ", ".join(register.pages()))
    allowed = served()
    rows = [{"figure_id": item.id, "label": item.label, "unit": item.unit, "page": item.page,
             "kind": item.kind, **({"periods": list(item.periods)} if item.periods else {}),
             **({"item": item.item} if item.item else {}), **({"per_benchmark": True} if item.per_benchmark else {}),
             **({"basis": item.basis} if item.basis else {}), "link": item.link, "served": item.id in allowed}
            for item in register.figures().values() if not page or item.page == page]
    return {"tool": LIST, "account_id": None, "as_of": None,
            "data": {"figures": rows, "served": sum(1 for row in rows if row["served"]),
                     "note": "get_certified_figures serves a figure only once its page shows certified figures."},
            "citations": [{"source": "certified_figures_register"}]}
