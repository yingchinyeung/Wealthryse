"""Read-only portfolio tools.

Every tool runs inside ``database.scoped`` with exactly one account visible,
uses fixed parameterised SQL against the agent views, and reuses the
platform's own calculation code so its figures match the website. A tool
never writes, never takes SQL from its caller, and fails closed (raises
ToolError) instead of guessing when data is missing.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping

from ibkr_etl.fx_selection import latest_fx_quote
from ibkr_etl.performance_periods import CALCULATION_VERSION as PERFORMANCE_VERSION
from ibkr_etl.performance_periods import PerformanceInputError, performance_periods
from ibkr_etl.reporting_currency_holdings import normalize_collected_report_holdings
from ibkr_etl.sector_classification import YAHOO_PROVIDER, aggregate_sector_allocation
from ibkr_etl.subject_read_contract import ACTIVE_NAV_CURRENCY_SQL, synthetic_exposure


class ToolError(Exception):
    """A safe, user-presentable failure. Never carries database text or payloads."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any]
    run: Callable[[Any, Mapping[str, Any]], dict[str, Any]]
    needs_account: bool = True
    # When set, only these scheduled jobs may call it; people (and so website chat) never see it.
    only_jobs: frozenset[str] = frozenset()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _day(value: Any, name: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as error:
        raise ToolError("invalid_arguments", f"{name} must be a date in YYYY-MM-DD form.", 400) from error


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _citation(source: str, account_id: str | None, **extra: Any) -> dict[str, Any]:
    return {"source": source, "account_id": account_id, **{k: v for k, v in extra.items() if v is not None}}


def _subject(connection, account_id: str) -> Mapping[str, Any]:
    row = connection.execute(
        "SELECT subject_id, subject_type, display_name, base_currency, reporting_currency, "
        "history_start_date, source_account_id FROM platform_subjects WHERE subject_id = ?",
        (account_id,),
    ).fetchone()
    if row is None:
        raise ToolError("account_not_found", "This account is not available.", 404)
    return row


def _reporting_currency(subject: Mapping[str, Any]) -> str:
    currency = str(subject["reporting_currency"] or subject["base_currency"] or "").upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ToolError("reporting_currency_unavailable", "The account has no validated reporting currency.")
    return currency


def _latest_nav_date(connection, account_id: str, as_of: str | None) -> str:
    row = connection.execute(
        f"SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE subject_id = ? "
        f"AND currency = {ACTIVE_NAV_CURRENCY_SQL} AND (?::text IS NULL OR snapshot_date <= ?)",
        (account_id, as_of, as_of),
    ).fetchone()
    if row is None or row["latest"] is None:
        raise ToolError("no_valuation", "No valuation is available on or before that date.", 404)
    return str(row["latest"])


def _position_rows(connection, subject: Mapping[str, Any], as_of: str) -> tuple[str | None, list[dict[str, Any]], bool]:
    """Latest holdings on or before ``as_of`` with sector fields, native currency."""
    account_id = str(subject["subject_id"])
    if str(subject["subject_type"]) == "composite_portfolio":
        currency = _reporting_currency(subject)
        snapshot = connection.execute(
            "SELECT MAX(snapshot_date) AS latest FROM synthetic_portfolio_components "
            "WHERE portfolio_id = ? AND snapshot_date <= ? AND base_currency = ?",
            (account_id, as_of, currency),
        ).fetchone()["latest"]
        if snapshot is None:
            return None, [], True
        rows = connection.execute(
            """
            SELECT snapshot_date, component_id AS conid, component_id AS symbol,
                   component_name AS description, component_type AS asset_category,
                   base_currency AS currency, coefficient, source_currency, source_value,
                   fx_rate_to_base, base_value, normalized_value,
                   contribution::text AS market_value_text, direction, observation_date
            FROM synthetic_portfolio_components
            WHERE portfolio_id = ? AND snapshot_date = ? AND base_currency = ?
            ORDER BY ABS(contribution) DESC
            """,
            (account_id, snapshot, currency),
        ).fetchall()
        try:
            return str(snapshot), [synthetic_exposure(row) for row in rows], True
        except ValueError as error:
            raise ToolError("composite_inconsistent", "The model portfolio's components do not reconcile.") from error
    snapshot = connection.execute(
        "SELECT MAX(snapshot_date) AS latest FROM subject_position_series WHERE subject_id = ? AND snapshot_date <= ?",
        (account_id, as_of),
    ).fetchone()["latest"]
    if snapshot is None:
        return None, [], False
    rows = connection.execute(
        """
        SELECT p.snapshot_date, p.conid, i.symbol, i.description, i.asset_category, i.listing_exchange,
               p.currency, p.quantity_text, p.mark_price_text, p.market_value_text,
               p.cost_basis_text, p.unrealized_pnl_text,
               c.sector, c.industry, c.exchange, c.checked_at, c.quality_status, c.license_status
        FROM subject_position_series p
        LEFT JOIN instruments i ON i.conid = p.conid
        LEFT JOIN instrument_classifications c ON c.conid = p.conid AND c.provider = ?
        WHERE p.subject_id = ? AND p.snapshot_date = ?
          AND COALESCE(NULLIF(p.quantity_text, ''), '0')::numeric <> 0
        ORDER BY ABS(COALESCE(NULLIF(p.market_value_text, ''), '0')::numeric) DESC
        """,
        (YAHOO_PROVIDER, account_id, snapshot),
    ).fetchall()
    return str(snapshot), [dict(row) for row in rows], False


def _valued_holdings(connection, subject: Mapping[str, Any], as_of: str) -> tuple[str | None, list[dict[str, Any]], dict[str, Any], bool]:
    """Holdings converted to the reporting currency with the platform's dated-FX rule."""
    snapshot, rows, composite = _position_rows(connection, subject, as_of)
    if not rows:
        return snapshot, [], {}, composite
    currency = _reporting_currency(subject)
    if composite:
        total = sum(abs(float(r["market_value_text"])) for r in rows) or 0.0
        sectors: dict[str, float] = {}
        for r in rows:
            sectors[str(r["asset_category"])] = sectors.get(str(r["asset_category"]), 0.0) + abs(float(r["market_value_text"]))
        allocation = [
            {"sector": k, "market_value": v, "weight": v / total if total else None, "holdings": 1}
            for k, v in sorted(sectors.items(), key=lambda kv: kv[1], reverse=True)
        ]
        return snapshot, rows, {"currency": currency, "allocation": allocation, "gross_market_value": total,
                                "classification_basis": "composite sleeve type", "available": True}, True
    sector = aggregate_sector_allocation(rows)
    try:
        normalized = normalize_collected_report_holdings(connection, str(subject["subject_id"]), currency, {
            "holdings": {"as_of": snapshot, "data": {"holdings": rows}},
            "sector_allocation": {"data": sector},
        })
    except ValueError as error:
        raise ToolError("reporting_fx_unavailable",
                        "Some holdings have no dated exchange rate into the reporting currency.") from error
    return snapshot, normalized["holdings"]["data"]["holdings"], normalized["sector_allocation"]["data"], False


# Tools ---------------------------------------------------------------------

def list_accounts(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT subject_id, display_name, subject_type, reporting_currency, base_currency, history_start_date "
        "FROM platform_subjects ORDER BY subject_id"
    ).fetchall()
    accounts = [
        {"account_id": r["subject_id"], "name": r["display_name"], "type": r["subject_type"],
         "reporting_currency": r["reporting_currency"] or r["base_currency"],
         "history_start_date": str(r["history_start_date"]) if r["history_start_date"] else None}
        for r in rows
    ]
    return {"data": {"accounts": accounts}, "as_of": None, "citations": [_citation("platform_subjects", None)]}


def get_holdings(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    limit = int(arguments.get("limit") or 25)
    if not 1 <= limit <= 200:
        raise ToolError("invalid_arguments", "limit must be between 1 and 200.", 400)
    subject = _subject(connection, account_id)
    as_of = _latest_nav_date(connection, account_id, arguments.get("as_of") and _day(arguments["as_of"], "as_of"))
    snapshot, holdings, sector, composite = _valued_holdings(connection, subject, as_of)
    if snapshot is None:
        raise ToolError("no_holdings", "No holdings are recorded on or before that date.", 404)
    currency = _reporting_currency(subject)
    gross = sum(abs(float(h["market_value_text"])) for h in holdings)
    items = []
    for h in holdings[:limit]:
        value = float(h["market_value_text"])
        items.append({
            "symbol": h.get("symbol") or h.get("conid"),
            "description": h.get("description"),
            "sector": h.get("sector") or (h.get("asset_category") if composite else None),
            "native_currency": h.get("native_currency") or h.get("currency"),
            "quantity": None if composite else _float(h.get("quantity_text")),
            "market_value": value,
            "native_market_value": _float(h.get("native_market_value_text") or h.get("market_value_text")),
            "unrealized_pnl_native": None if composite else _float(h.get("unrealized_pnl_text")),
            "weight": abs(value) / gross if gross else None,
        })
    return {
        "as_of": snapshot,
        "data": {"currency": currency, "holdings_date": snapshot, "positions": len(holdings),
                 "returned": len(items), "gross_market_value": gross, "holdings": items,
                 "valuation": "composite exposure" if composite else "dated-reporting-fx-v1"},
        "citations": [_citation("synthetic_portfolio_components" if composite else "subject_position_series",
                                account_id, date=snapshot), _citation("fx_rates", account_id, date=snapshot)],
    }


def _fx_rate(history: dict[tuple[str, str], list[tuple[str, str]]], native: str, target: str, day: str) -> float:
    if native == target:
        return 1.0
    quote = latest_fx_quote(history.get((native, target), []), day)
    if quote is not None:
        return float(quote[0])
    quote = latest_fx_quote(history.get((target, native), []), day)
    if quote is not None:
        return 1.0 / float(quote[0])
    raise ToolError("reporting_fx_unavailable", f"No {native}/{target} exchange rate on or before {day}.")


def get_holdings_changes(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Split each holding's change in reporting-currency value into trades, price and FX.

    For quantity q, native value per unit u and FX rate f at the two dates:
      trade = (q2 - q1) * u2 * f2,  price = q1 * (u2 - u1) * f2,  fx = q1 * u1 * (f2 - f1)
    which add up exactly to V2 - V1. A position that closed is all trade.
    """
    account_id = str(arguments["account_id"])
    start, end = _day(arguments["from_date"], "from_date"), _day(arguments["to_date"], "to_date")
    if start >= end:
        raise ToolError("invalid_arguments", "from_date must be before to_date.", 400)
    subject = _subject(connection, account_id)
    if str(subject["subject_type"]) == "composite_portfolio":
        raise ToolError("not_supported", "Model portfolios have no traded positions to compare.")
    currency = _reporting_currency(subject)
    d1, rows1, _ = _position_rows(connection, subject, start)
    d2, rows2, _ = _position_rows(connection, subject, end)
    if d1 is None or d2 is None:
        raise ToolError("no_holdings", "Holdings are not recorded for both dates.", 404)
    fx_account = str(subject["source_account_id"] or account_id)
    history: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for r in connection.execute(
        "SELECT rate_date, from_currency, to_currency, rate_text FROM fx_rates WHERE account_id = ? "
        "AND rate_date <= ? ORDER BY from_currency, to_currency, rate_date", (fx_account, d2),
    ).fetchall():
        history.setdefault((str(r["from_currency"]).upper(), str(r["to_currency"]).upper()), []).append(
            (str(r["rate_date"]), r["rate_text"]))
    before = {str(r["conid"]): r for r in rows1}
    after = {str(r["conid"]): r for r in rows2}
    changes, totals = [], {"trade": 0.0, "price": 0.0, "fx": 0.0, "start_value": 0.0, "end_value": 0.0}
    for conid in sorted(set(before) | set(after)):
        a, b = before.get(conid), after.get(conid)
        row = b or a
        native = str(row["currency"]).upper()
        q1 = _float(a["quantity_text"]) if a else 0.0
        q2 = _float(b["quantity_text"]) if b else 0.0
        m1 = _float(a["market_value_text"]) if a else 0.0
        m2 = _float(b["market_value_text"]) if b else 0.0
        if None in (q1, q2, m1, m2):
            raise ToolError("invalid_position", "A position has a missing quantity or value.")
        f1 = _fx_rate(history, native, currency, d1) if a else 0.0
        f2 = _fx_rate(history, native, currency, d2) if b else 0.0
        v1, v2 = m1 * f1, m2 * f2
        if b is None:
            trade, price, fx = -v1, 0.0, 0.0
        elif a is None:
            trade, price, fx = v2, 0.0, 0.0
        else:
            u1, u2 = m1 / q1, m2 / q2
            trade, price, fx = (q2 - q1) * u2 * f2, q1 * (u2 - u1) * f2, q1 * u1 * (f2 - f1)
        status = "opened" if a is None else "closed" if b is None else (
            "added" if q2 > q1 else "reduced" if q2 < q1 else "held")
        changes.append({"symbol": row.get("symbol") or conid, "native_currency": native, "status": status,
                        "quantity_start": q1, "quantity_end": q2, "value_start": v1, "value_end": v2,
                        "change": v2 - v1, "from_trades": trade, "from_price": price, "from_fx": fx})
        for key, value in (("trade", trade), ("price", price), ("fx", fx), ("start_value", v1), ("end_value", v2)):
            totals[key] += value
    changes.sort(key=lambda c: abs(c["change"]), reverse=True)
    return {
        "as_of": d2,
        "data": {"currency": currency, "start_date": d1, "end_date": d2,
                 "total_change": totals["end_value"] - totals["start_value"],
                 "from_trades": totals["trade"], "from_price": totals["price"], "from_fx": totals["fx"],
                 "holdings_value_start": totals["start_value"], "holdings_value_end": totals["end_value"],
                 "changes": changes[:50], "positions_compared": len(changes),
                 "note": "Holdings only; cash and accruals are excluded."},
        "citations": [_citation("subject_position_series", account_id, date=d1),
                      _citation("subject_position_series", account_id, date=d2),
                      _citation("fx_rates", fx_account, date=d2)],
    }


def _nav_and_flows(connection, subject: Mapping[str, Any], through: str) -> tuple[list[tuple[str, str]], dict[str, float] | None]:
    account_id = str(subject["subject_id"])
    start = str(subject["history_start_date"] or "1900-01-01")
    nav = [(str(r["snapshot_date"]), r["total_text"]) for r in connection.execute(
        f"SELECT snapshot_date, total_text FROM subject_nav_series WHERE subject_id = ? AND snapshot_date >= ? "
        f"AND snapshot_date <= ? AND currency = {ACTIVE_NAV_CURRENCY_SQL} ORDER BY snapshot_date",
        (account_id, start, through),
    ).fetchall() if r["total_text"] not in (None, "")]
    subject_type = str(subject["subject_type"])
    if subject_type == "composite_portfolio":
        return nav, None
    if str(subject["base_currency"] or "").upper() != _reporting_currency(subject):
        raise ToolError("flow_currency_mismatch",
                        "Cash flows are recorded in a different currency from the reporting currency.")
    from ibkr_etl.provisional_benchmark import _external_flows

    try:
        return nav, _external_flows(connection, account_id, subject_type=subject_type)
    except RuntimeError as error:
        raise ToolError("flows_unavailable", "External cash flows could not be read for this account.") from error


def get_performance(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    subject = _subject(connection, account_id)
    as_of = _latest_nav_date(connection, account_id, arguments.get("as_of") and _day(arguments["as_of"], "as_of"))
    nav, flows = _nav_and_flows(connection, subject, as_of)
    custom_start = arguments.get("start_date") and _day(arguments["start_date"], "start_date")
    custom_end = arguments.get("end_date") and _day(arguments["end_date"], "end_date")
    try:
        result = performance_periods(nav, flows, as_of=as_of, custom_start=custom_start, custom_end=custom_end)
    except PerformanceInputError as error:
        raise ToolError("performance_unavailable", "Performance could not be calculated from the recorded values.") from error
    keep = ("key", "label", "start_date", "end_date", "status", "partial", "start_value", "end_value",
            "twr", "twr_annualized", "irr_annualized", "net_flows", "issues")
    periods = [{k: p.get(k) for k in keep} for p in result["periods"]]
    custom = {k: result["custom"].get(k) for k in keep} if result.get("custom") else None
    return {
        "as_of": result["effective_as_of"],
        "data": {"currency": _reporting_currency(subject), "inception_date": result["inception_date"],
                 "latest_nav": _float(nav[-1][1]) if nav else None, "periods": periods, "custom_period": custom,
                 "method": "time-weighted return (twr) and money-weighted IRR; returns are fractions (0.05 = 5%)",
                 "flows_included": flows is not None, "calculation_version": PERFORMANCE_VERSION},
        "citations": [_citation("subject_nav_series", account_id, through=as_of)]
                     + ([_citation("external_flows", account_id, through=as_of)] if flows is not None else []),
    }


def _benchmark_level(connection, benchmark_id: str, day: str) -> tuple[str, float] | None:
    row = connection.execute(
        "SELECT observation_date, level FROM benchmark_prices WHERE benchmark_id = ? AND observation_date <= ? "
        "AND COALESCE(quality_status, 'pass') <> 'fail' ORDER BY observation_date DESC LIMIT 1",
        (benchmark_id, day),
    ).fetchone()
    if row is None or _float(row["level"]) is None:
        return None
    return str(row["observation_date"]), float(row["level"])


BENCHMARK_GAP_DAYS = 7


def _benchmark_mapping(connection, account_id: str, as_of: str, requested: str | None = None) -> Mapping[str, Any] | None:
    return connection.execute(
        """
        SELECT b.benchmark_id, b.name, b.symbol, b.currency, m.mapping_type, m.reporting_currency
        FROM portfolio_benchmark_mapping m JOIN benchmark_master b ON b.benchmark_id = m.benchmark_id
        WHERE m.subject_id = ? AND b.active AND m.effective_from <= ? AND (m.effective_to IS NULL OR m.effective_to >= ?)
          AND (?::text IS NULL OR b.benchmark_id = ?)
        ORDER BY CASE m.mapping_type WHEN 'primary' THEN 0 ELSE 1 END, b.name LIMIT 1
        """,
        (account_id, as_of, as_of, requested, requested),
    ).fetchone()


def _days_between(a: str, b: str) -> int:
    return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)


def compare_to_benchmark(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    subject = _subject(connection, account_id)
    currency = _reporting_currency(subject)
    as_of = _latest_nav_date(connection, account_id, arguments.get("as_of") and _day(arguments["as_of"], "as_of"))
    requested = arguments.get("benchmark_id")
    mapping = _benchmark_mapping(connection, account_id, as_of, requested)
    if mapping is None:
        raise ToolError("benchmark_unavailable", "No benchmark is configured for this account on that date.", 404)
    if str(mapping["currency"] or "").upper() != currency:
        raise ToolError("benchmark_currency_mismatch",
                        f"The benchmark is priced in {mapping['currency']}, not the account's {currency}.")
    nav, flows = _nav_and_flows(connection, subject, as_of)
    try:
        periods = performance_periods(nav, flows, as_of=as_of)["periods"]
    except PerformanceInputError as error:
        raise ToolError("performance_unavailable", "Performance could not be calculated from the recorded values.") from error
    rows = []
    for p in periods:
        if p.get("twr") is None or not p.get("start_date") or not p.get("end_date"):
            continue
        first = _benchmark_level(connection, str(mapping["benchmark_id"]), str(p["start_date"]))
        last = _benchmark_level(connection, str(mapping["benchmark_id"]), str(p["end_date"]))
        # A level more than a week older than the period date would compare different windows.
        if first and _days_between(first[0], str(p["start_date"])) > BENCHMARK_GAP_DAYS:
            first = None
        if last and _days_between(last[0], str(p["end_date"])) > BENCHMARK_GAP_DAYS:
            last = None
        bench = (last[1] / first[1] - 1.0) if first and last and first[1] > 0 else None
        rows.append({"period": p["label"], "start_date": p["start_date"], "end_date": p["end_date"],
                     "portfolio_return": p["twr"], "benchmark_return": bench,
                     "excess_return": (p["twr"] - bench) if bench is not None else None,
                     "benchmark_dates": [first[0], last[0]] if first and last else None,
                     "note": None if bench is not None else "No benchmark level within a week of the period dates."})
    return {
        "as_of": as_of,
        "data": {"currency": currency, "benchmark": {"id": mapping["benchmark_id"], "name": mapping["name"],
                 "symbol": mapping["symbol"], "role": mapping["mapping_type"]}, "periods": rows,
                 "method": "portfolio time-weighted return vs benchmark price return over the same dates; fractions"},
        "citations": [_citation("subject_nav_series", account_id, through=as_of),
                      _citation("benchmark_prices", None, benchmark_id=mapping["benchmark_id"], through=as_of)],
    }


def get_sector_allocation(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    subject = _subject(connection, account_id)
    as_of = _latest_nav_date(connection, account_id, arguments.get("as_of") and _day(arguments["as_of"], "as_of"))
    snapshot, _, sector, composite = _valued_holdings(connection, subject, as_of)
    if snapshot is None:
        raise ToolError("no_holdings", "No holdings are recorded on or before that date.", 404)
    return {
        "as_of": snapshot,
        "data": {"currency": _reporting_currency(subject), "holdings_date": snapshot,
                 "allocation": sector.get("allocation", []),
                 "gross_market_value": sector.get("gross_market_value"),
                 "classification_coverage": (sector.get("classification_coverage_pct") or 0) / 100
                 if not composite else 1.0,
                 "unclassified_symbols": sector.get("unclassified_symbols", []),
                 "basis": "composite sleeve type" if composite else "Yahoo Finance sector classification"},
        "citations": [_citation("subject_position_series", account_id, date=snapshot),
                      _citation("instrument_classifications", None, provider=YAHOO_PROVIDER)],
    }


def get_cash_flows(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    start, end = _day(arguments["from_date"], "from_date"), _day(arguments["to_date"], "to_date")
    if start > end:
        raise ToolError("invalid_arguments", "from_date must not be after to_date.", 400)
    subject = _subject(connection, account_id)
    _, flows = _nav_and_flows(connection, subject, end)
    if flows is None:
        return {"as_of": end, "data": {"flows": [], "note": "Model portfolios have no investor deposits or withdrawals."},
                "citations": [_citation("platform_subjects", account_id)]}
    items = [{"date": d, "amount": round(v, 2)} for d, v in sorted(flows.items()) if start <= d <= end and v]
    inflow = sum(i["amount"] for i in items if i["amount"] > 0)
    outflow = sum(i["amount"] for i in items if i["amount"] < 0)
    return {
        "as_of": end,
        "data": {"currency": _reporting_currency(subject), "from_date": start, "to_date": end,
                 "deposits": inflow, "withdrawals": outflow, "net": inflow + outflow, "flows": items[:200]},
        "citations": [_citation("external_flows", account_id, from_date=start, to_date=end)],
    }


def get_data_freshness(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    subject = _subject(connection, account_id)
    nav = connection.execute(
        f"SELECT MAX(snapshot_date) AS d FROM subject_nav_series WHERE subject_id = ? AND currency = {ACTIVE_NAV_CURRENCY_SQL}",
        (account_id,)).fetchone()["d"]
    table = "synthetic_portfolio_components" if str(subject["subject_type"]) == "composite_portfolio" else "subject_position_series"
    column = "portfolio_id" if table == "synthetic_portfolio_components" else "subject_id"
    positions = connection.execute(f"SELECT MAX(snapshot_date) AS d FROM {table} WHERE {column} = ?", (account_id,)).fetchone()["d"]
    benchmarks = [{"benchmark_id": r["benchmark_id"], "latest": str(r["d"]) if r["d"] else None} for r in connection.execute(
        "SELECT m.benchmark_id, MAX(p.observation_date) AS d FROM portfolio_benchmark_mapping m "
        "LEFT JOIN benchmark_prices p ON p.benchmark_id = m.benchmark_id WHERE m.subject_id = ? GROUP BY m.benchmark_id",
        (account_id,)).fetchall()]
    run = connection.execute(
        "SELECT MAX(completed_at) AS d FROM ingestion_runs WHERE status IN ('success', 'succeeded', 'completed')"
    ).fetchone()["d"]
    today = date.today()
    lag = (today - date.fromisoformat(str(nav))).days if nav else None
    return {
        "as_of": str(nav) if nav else None,
        "data": {"latest_valuation_date": str(nav) if nav else None, "latest_holdings_date": str(positions) if positions else None,
                 "benchmarks": benchmarks, "last_successful_import": str(run) if run else None,
                 "days_since_valuation": lag, "checked_on": today.isoformat()},
        "citations": [_citation("subject_nav_series", account_id), _citation(table, account_id),
                      _citation("ingestion_runs", None)],
    }


# Time series and risk ---------------------------------------------------------

MAX_SERIES_POINTS = 400
MIN_RISK_OBSERVATIONS = 20


def _series_inputs(connection, subject: Mapping[str, Any], through: str) -> tuple[dict[str, float], dict[str, float]]:
    from ibkr_etl.performance_periods import validated_flows, validated_nav

    nav, flows = _nav_and_flows(connection, subject, through)
    try:
        return validated_nav(nav), validated_flows(flows or {})
    except PerformanceInputError as error:
        raise ToolError("performance_unavailable", "The recorded values could not be read as a series.") from error


def daily_returns(nav: Mapping[str, float], flows: Mapping[str, float], dates: list[str]) -> list[tuple[str, float]]:
    """Modified Dietz return between consecutive observations, as the website and get_performance use."""
    from bisect import bisect_right

    flow_days = sorted(flows)
    out = []
    for previous, current in zip(dates, dates[1:]):
        external = sum(flows[d] for d in flow_days[bisect_right(flow_days, previous):bisect_right(flow_days, current)])
        denominator = nav[previous] + 0.5 * external
        daily = (nav[current] - nav[previous] - external) / denominator if denominator > 0 else float("nan")
        if not math.isfinite(daily) or daily <= -1:
            raise ToolError("performance_unavailable", f"The return on {current} could not be calculated.")
        out.append((current, daily))
    return out


def _window(nav: Mapping[str, float], start: str | None, end: str) -> list[str]:
    """Observation dates from the last one before ``start`` (the base) through ``end``."""
    dates = [d for d in nav if d <= end]
    if start:
        before = [d for d in dates if d < start]
        dates = before[-1:] + [d for d in dates if d >= start]
    if len(dates) < 2:
        raise ToolError("no_valuation", "There are not enough valuations in that period.", 404)
    return dates


def _growth(returns: list[tuple[str, float]], base_day: str) -> list[tuple[str, float]]:
    growth, out = 1.0, [(base_day, 1.0)]
    for day, r in returns:
        growth *= 1.0 + r
        out.append((day, growth))
    return out


def drawdown(index: list[tuple[str, float]]) -> dict[str, Any]:
    peak_day, peak, worst = index[0][0], index[0][1], {"max_drawdown": 0.0, "peak_date": None, "trough_date": None}
    for day, level in index:
        if level > peak:
            peak_day, peak = day, level
        fall = level / peak - 1.0
        if fall < worst["max_drawdown"]:
            worst = {"max_drawdown": fall, "peak_date": peak_day, "trough_date": day}
    recovery = None
    if worst["trough_date"]:
        peak_level = dict(index)[worst["peak_date"]]
        recovery = next((d for d, level in index if d > worst["trough_date"] and level >= peak_level), None)
    return {**worst, "recovery_date": recovery}


def monthly_returns(returns: list[tuple[str, float]]) -> list[dict[str, Any]]:
    months: dict[str, float] = {}
    for day, r in returns:
        months[day[:7]] = (1.0 + months.get(day[:7], 0.0)) * (1.0 + r) - 1.0
    return [{"month": m, "return": v} for m, v in months.items()]


def _sample(dates: list[str], frequency: str) -> list[str]:
    if frequency == "daily":
        return dates
    key = (lambda d: d[:7]) if frequency == "monthly" else (lambda d: date.fromisoformat(d).isocalendar()[:2])
    last: dict[Any, str] = {}
    for d in dates:
        last[key(d)] = d
    return sorted(set(last.values()) | {dates[0], dates[-1]})


class _BenchmarkLevels:
    def __init__(self, connection, benchmark_id: str, start: str, end: str) -> None:
        rows = connection.execute(
            "SELECT observation_date, level FROM benchmark_prices WHERE benchmark_id = ? AND observation_date <= ? "
            "AND observation_date >= ? AND COALESCE(quality_status, 'pass') <> 'fail' ORDER BY observation_date",
            (benchmark_id, end, (date.fromisoformat(start) - timedelta(days=BENCHMARK_GAP_DAYS)).isoformat()),
        ).fetchall()
        points = [(str(r["observation_date"]), _float(r["level"])) for r in rows]
        self.points = [(d, v) for d, v in points if v is not None and v > 0]
        self.days = [d for d, _ in self.points]

    def level(self, day: str) -> float | None:
        from bisect import bisect_right

        i = bisect_right(self.days, day) - 1
        if i < 0 or _days_between(self.days[i], day) > BENCHMARK_GAP_DAYS:
            return None
        return self.points[i][1]


def _benchmark_for(connection, subject: Mapping[str, Any], as_of: str, requested: str | None = None):
    mapping = _benchmark_mapping(connection, str(subject["subject_id"]), as_of, requested)
    if mapping is None or str(mapping["currency"] or "").upper() != _reporting_currency(subject):
        return None
    return mapping


def get_nav_history(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    subject = _subject(connection, account_id)
    end = _latest_nav_date(connection, account_id, arguments.get("to_date") and _day(arguments["to_date"], "to_date"))
    start = arguments.get("from_date") and _day(arguments["from_date"], "from_date")
    frequency = str(arguments.get("frequency") or "monthly")
    if frequency not in ("daily", "weekly", "monthly"):
        raise ToolError("invalid_arguments", "frequency must be daily, weekly or monthly.", 400)
    nav, flows = _series_inputs(connection, subject, end)
    dates = _window(nav, start, end)
    index = dict(_growth(daily_returns(nav, flows, dates), dates[0]))
    for candidate in ("daily", "weekly", "monthly"):
        if ("daily", "weekly", "monthly").index(candidate) < ("daily", "weekly", "monthly").index(frequency):
            continue
        sampled = _sample(dates, candidate)
        if len(sampled) <= MAX_SERIES_POINTS:
            frequency = candidate
            break
    else:
        raise ToolError("too_many_points", "That period is too long to return; choose a shorter one.", 400)
    mapping = _benchmark_for(connection, subject, end)
    levels = _BenchmarkLevels(connection, str(mapping["benchmark_id"]), dates[0], end) if mapping else None
    base_level = levels.level(dates[0]) if levels else None
    flow_days = sorted(flows)
    points, previous = [], None
    for d in sampled:
        level = levels.level(d) if levels and base_level else None
        points.append({
            "date": d, "value": nav[d], "cumulative_return": index[d] - 1.0,
            "net_flows": round(sum(v for f, v in flows.items() if (previous is None or f > previous) and f <= d
                                   and (previous is not None or f > dates[0])), 2) if flow_days else 0.0,
            "benchmark_cumulative_return": (level / base_level - 1.0) if level else None,
        })
        previous = d
    return {
        "as_of": end,
        "data": {"currency": _reporting_currency(subject), "from_date": dates[0], "to_date": end, "frequency": frequency,
                 "benchmark": {"id": mapping["benchmark_id"], "name": mapping["name"]} if mapping else None,
                 "points": points,
                 "method": "value is the recorded NAV; cumulative_return is time-weighted from the first date "
                           "(flows removed); benchmark_cumulative_return is its price return; fractions"},
        "citations": [_citation("subject_nav_series", account_id, from_date=dates[0], to_date=end)]
                     + ([_citation("benchmark_prices", None, benchmark_id=mapping["benchmark_id"], through=end)] if mapping else []),
    }


def _stats(values: list[float]) -> tuple[float, float]:
    mean = math.fsum(values) / len(values)
    return mean, math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1))


def get_risk_metrics(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    subject = _subject(connection, account_id)
    end = _latest_nav_date(connection, account_id, arguments.get("to_date") and _day(arguments["to_date"], "to_date"))
    start = (arguments.get("from_date") and _day(arguments["from_date"], "from_date")) or \
        (date.fromisoformat(end).replace(year=date.fromisoformat(end).year - 1)
         if not end.endswith("02-29") else date.fromisoformat(end).replace(day=28, year=date.fromisoformat(end).year - 1)).isoformat()
    nav, flows = _series_inputs(connection, subject, end)
    dates = _window(nav, start, end)
    returns = daily_returns(nav, flows, dates)
    if len(returns) < MIN_RISK_OBSERVATIONS:
        raise ToolError("not_enough_history", f"Risk measures need at least {MIN_RISK_OBSERVATIONS} valuations in the period.", 404)
    years = max(_days_between(dates[0], dates[-1]) / 365.25, 1 / 365.25)
    per_year = len(returns) / years
    values = [r for _, r in returns]
    mean, sd = _stats(values)
    index = _growth(returns, dates[0])
    total = index[-1][1] - 1.0
    months = monthly_returns(returns)
    best_day, worst_day = max(returns, key=lambda x: x[1]), min(returns, key=lambda x: x[1])
    data: dict[str, Any] = {
        "currency": _reporting_currency(subject), "from_date": dates[0], "to_date": dates[-1],
        "observations": len(returns), "observations_per_year": per_year,
        "total_return": total,
        "annualized_return": (1.0 + total) ** (1.0 / years) - 1.0 if years >= 1 else None,
        "annualized_volatility": sd * math.sqrt(per_year),
        "downside_deviation": math.sqrt(math.fsum(min(v, 0.0) ** 2 for v in values) / len(values)) * math.sqrt(per_year),
        **drawdown(index),
        "best_day": {"date": best_day[0], "return": best_day[1]},
        "worst_day": {"date": worst_day[0], "return": worst_day[1]},
        "positive_days": sum(1 for v in values if v > 0),
        "monthly_returns": months[-60:],
        "best_month": max(months, key=lambda m: m["return"]),
        "worst_month": min(months, key=lambda m: m["return"]),
        "positive_months": sum(1 for m in months if m["return"] > 0),
        "benchmark": None,
        "method": "returns between valuations (Modified Dietz, flows removed); volatility and tracking error "
                  "annualised by valuations per year; the first and last month may be partial; fractions",
    }
    mapping = _benchmark_for(connection, subject, end, arguments.get("benchmark_id"))
    citations = [_citation("subject_nav_series", account_id, from_date=dates[0], to_date=dates[-1])]
    if mapping:
        levels = _BenchmarkLevels(connection, str(mapping["benchmark_id"]), dates[0], dates[-1])
        pairs = []
        for (previous, current), (_, r) in zip(zip(dates, dates[1:]), returns):
            a, b = levels.level(previous), levels.level(current)
            if a and b:
                pairs.append((r, b / a - 1.0))
        bench: dict[str, Any] = {"id": mapping["benchmark_id"], "name": mapping["name"], "matched_observations": len(pairs)}
        first, last = levels.level(dates[0]), levels.level(dates[-1])
        bench["total_return"] = last / first - 1.0 if first and last else None
        if len(pairs) >= MIN_RISK_OBSERVATIONS:
            p, b = [x for x, _ in pairs], [y for _, y in pairs]
            pm, ps = _stats(p)
            bm, bs = _stats(b)
            cov = math.fsum((x - pm) * (y - bm) for x, y in pairs) / (len(pairs) - 1)
            _, te = _stats([x - y for x, y in pairs])
            bench.update({"annualized_volatility": bs * math.sqrt(per_year),
                          "beta": cov / bs ** 2 if bs > 0 else None,
                          "correlation": cov / (ps * bs) if ps > 0 and bs > 0 else None,
                          "tracking_error": te * math.sqrt(per_year)})
        else:
            bench["note"] = "Too few matching benchmark prices for beta, correlation or tracking error."
        data["benchmark"] = bench
        citations.append(_citation("benchmark_prices", None, benchmark_id=mapping["benchmark_id"], through=dates[-1]))
    return {"as_of": dates[-1], "data": data, "citations": citations}


# Month-end package (the scheduled month-end team only) -------------------------


def _uuid(value: Any, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as error:
        raise ToolError("invalid_arguments", f"{name} must be an id from get_month_end_package.", 400) from error


def get_month_end_package(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    period_end = _day(arguments["period_end"], "period_end")
    rows = connection.execute(
        "SELECT snapshot_id::text AS snapshot_id, snapshot_sha256, quality_status, publication_allowed, report_type, "
        "generation_run_id, status, output_reference, output_sha256, completed_at FROM month_end_packages "
        "WHERE subject_id = ? AND period_end = ? ORDER BY generation_run_id DESC",
        (account_id, period_end),
    ).fetchall()
    if not rows:
        raise ToolError("no_package", "No month-end package has been built for that month yet.", 404)
    newest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        newest.setdefault(str(row["report_type"]), row)
    head = newest.get("factsheet") or next(iter(newest.values()))
    files = [{"report_type": kind, "generation_run_id": int(row["generation_run_id"]), "status": row["status"],
              "snapshot_id": row["snapshot_id"], "output_reference": row["output_reference"],
              "output_sha256": row["output_sha256"], "completed_at": row["completed_at"]}
             for kind, row in sorted(newest.items())]
    return {
        "as_of": period_end,
        "data": {"period_end": period_end, "snapshot_id": head["snapshot_id"], "snapshot_sha256": head["snapshot_sha256"],
                 "quality_status": head["quality_status"], "publication_allowed": bool(head["publication_allowed"]),
                 "files": files},
        "citations": [_citation("report_generation_runs", account_id, period_end=period_end),
                      _citation("reporting_snapshots", account_id, snapshot_id=head["snapshot_id"])],
    }


def get_month_end_snapshot(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    snapshot_id = _uuid(arguments["snapshot_id"], "snapshot_id")
    row = connection.execute(
        "SELECT period_end, snapshot_sha256, payload_json FROM month_end_snapshots "
        "WHERE subject_id = ? AND snapshot_id = ?::uuid", (account_id, snapshot_id),
    ).fetchone()
    if row is None or not row["payload_json"]:
        raise ToolError("no_snapshot", "That month-end snapshot is not available for this account.", 404)
    # The same integrity rule the package job applies before rendering.
    if hashlib.sha256(str(row["payload_json"]).encode("utf-8")).hexdigest() != row["snapshot_sha256"]:
        raise ToolError("snapshot_integrity_failed", "The month-end snapshot does not match its recorded hash.")
    try:
        payload = json.loads(row["payload_json"])
    except ValueError as error:
        raise ToolError("snapshot_unreadable", "The month-end snapshot could not be read.") from error
    return {
        "as_of": str(row["period_end"]),
        "data": {"snapshot_id": snapshot_id, "snapshot_sha256": row["snapshot_sha256"],
                 "period_end": str(row["period_end"]), "payload": payload},
        "citations": [_citation("reporting_snapshots", account_id, snapshot_id=snapshot_id)],
    }


def list_month_end_packages(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    rows = connection.execute(
        "SELECT period_end, report_type, status, MAX(generation_run_id) AS generation_run_id FROM month_end_packages "
        "WHERE subject_id = ? GROUP BY period_end, report_type, status ORDER BY period_end", (account_id,),
    ).fetchall()
    months: dict[str, dict[str, Any]] = {}
    for row in rows:
        month = months.setdefault(str(row["period_end"]), {"period_end": str(row["period_end"]), "files": {}})
        current = month["files"].get(row["report_type"])
        if current is None or int(row["generation_run_id"]) > current["generation_run_id"]:
            month["files"][row["report_type"]] = {"status": row["status"], "generation_run_id": int(row["generation_run_id"])}
    return {"as_of": None, "data": {"months": list(months.values())},
            "citations": [_citation("report_generation_runs", account_id)]}


def list_onboarding_report_cases(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Composite cases in stage 3 whose historical reports are rendered, with the files to review."""
    rows = connection.execute(
        "SELECT case_id::text AS case_id, subject_id, display_name, description, report_style, reporting_currency, "
        "history_start_date, reports_evidence_json, reports_checked_at FROM onboarding_report_cases ORDER BY case_id"
    ).fetchall()
    cases = []
    for row in rows:
        try:
            evidence = json.loads(row["reports_evidence_json"] or "{}")
        except ValueError as error:
            raise ToolError("evidence_unreadable", "A case's rendered-reports record could not be read.") from error
        cases.append({"case_id": row["case_id"], "subject_id": row["subject_id"], "display_name": row["display_name"],
                      "description": row["description"], "report_style": row["report_style"],
                      "reporting_currency": row["reporting_currency"],
                      "history_start_date": row["history_start_date"], "reports_rendered": evidence,
                      "reports_checked_at": row["reports_checked_at"]})
    return {"as_of": None, "data": {"cases": cases},
            "citations": [_citation("broker_onboarding_checks", None, check_id="reports_rendered")]}


def list_onboarding_intake_cases(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Cases before approval 1, with the terms the owner entered, for the intake agent to draft against."""
    rows = connection.execute(
        "SELECT case_id::text AS case_id, source_kind, subject_id, display_name, description, reporting_currency, "
        "history_start_date, stage FROM onboarding_intake_cases ORDER BY case_id"
    ).fetchall()
    cases = [{key: row[key] for key in ("case_id", "source_kind", "subject_id", "display_name", "description",
                                        "reporting_currency", "history_start_date", "stage")} for row in rows]
    return {"as_of": None, "data": {"cases": cases}, "citations": [_citation("broker_onboarding_cases", None)]}


# Onboarding: broker accounts in stage 2 --------------------------------------------
# Values stay as the loaded text, so the agent does its arithmetic in Decimal on exactly what was stored.

_CASE_ID = {"type": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"}
_BROKER_CASE_FIELDS = ("case_id", "subject_id", "broker", "broker_account_id", "display_name", "reporting_currency",
                       "history_start_date", "base_currency")


def _broker_case(connection, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    row = connection.execute(
        "SELECT case_id::text AS case_id, subject_id, broker, broker_account_id, display_name, reporting_currency, "
        "history_start_date, base_currency FROM onboarding_broker_cases WHERE case_id::text = ?",
        (str(arguments["case_id"]),),
    ).fetchone()
    if row is None:
        raise ToolError("no_case", "That case is not a broker account waiting in load and reconcile.", 404)
    return row


def list_onboarding_broker_cases(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """IBKR broker-account cases in stage 2 (load and reconcile)."""
    rows = connection.execute(
        "SELECT case_id::text AS case_id, subject_id, broker, broker_account_id, display_name, reporting_currency, "
        "history_start_date, base_currency FROM onboarding_broker_cases ORDER BY case_id"
    ).fetchall()
    return {"as_of": None, "data": {"cases": [{k: row[k] for k in _BROKER_CASE_FIELDS} for row in rows]},
            "citations": [_citation("broker_onboarding_cases", None)]}


def get_onboarding_broker_overview(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The broker's daily NAV, how much activity is loaded, and every security the account has held."""
    case = _broker_case(connection, arguments)
    account = str(case["broker_account_id"])
    nav = connection.execute(
        "SELECT snapshot_date, currency, total_text, cash_text, stock_text, options_text, futures_text, bonds_text, "
        "accruals_text FROM onboarding_nav WHERE account_id = ? AND asset_class = 'TOTAL' ORDER BY snapshot_date",
        (account,),
    ).fetchall()
    activity = connection.execute(
        "SELECT kind, COUNT(*) AS count, MIN(activity_date) AS first, MAX(activity_date) AS last "
        "FROM onboarding_activity WHERE account_id = ? GROUP BY kind ORDER BY kind",
        (account,),
    ).fetchall()
    held = connection.execute(
        "SELECT p.conid, MIN(p.snapshot_date) AS first_held, MAX(p.snapshot_date) AS last_held, MAX(p.currency) AS "
        "position_currency, i.symbol, i.description, i.asset_category, i.currency, i.listing_exchange, i.isin "
        "FROM onboarding_positions p LEFT JOIN onboarding_instruments i ON i.conid = p.conid "
        "WHERE p.account_id = ? GROUP BY p.conid, i.symbol, i.description, i.asset_category, i.currency, "
        "i.listing_exchange, i.isin ORDER BY p.conid",
        (account,),
    ).fetchall()
    wanted = {str(r["conid"]) for r in held} | {str(r["isin"]) for r in held if r["isin"]} | \
        {str(r["symbol"]) for r in held if r["symbol"]}
    identifiers = [dict(row) for row in connection.execute(
        "SELECT scheme, identifier, security_key FROM security_identifiers WHERE identifier = ANY(?) "
        "ORDER BY scheme, identifier", (sorted(wanted),)).fetchall()] if wanted else []
    return {"as_of": nav[-1]["snapshot_date"] if nav else None,
            "data": {"case": {k: case[k] for k in _BROKER_CASE_FIELDS}, "nav": [dict(r) for r in nav],
                     "activity": [dict(r) for r in activity], "securities": [dict(r) for r in held],
                     "security_identifiers": identifiers},
            "citations": [_citation("nav_snapshots", account), _citation("position_snapshots", account),
                          _citation("trades", account), _citation("cash_transactions", account),
                          _citation("security_identifiers", None)]}


def get_onboarding_broker_day(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Everything needed to rebuild one day's NAV: positions, cash, FX, the broker's NAV and nearby events."""
    case = _broker_case(connection, arguments)
    account, day = str(case["broker_account_id"]), _day(arguments["date"], "date")
    near = [(date.fromisoformat(day) + timedelta(days=offset)).isoformat() for offset in (-5, 5)]

    def query(sql: str, params: tuple) -> list[dict[str, Any]]:
        return [dict(r) for r in connection.execute(sql, params).fetchall()]

    return {"as_of": day, "data": {
        "date": day,
        "nav": query("SELECT currency, asset_class, total_text, cash_text, stock_text, options_text, futures_text, "
                     "bonds_text, accruals_text FROM onboarding_nav WHERE account_id = ? AND snapshot_date = ? "
                     "ORDER BY asset_class", (account, day)),
        "positions": query("SELECT conid, currency, quantity_text, mark_price_text, market_value_text "
                           "FROM onboarding_positions WHERE account_id = ? AND snapshot_date = ? ORDER BY conid",
                           (account, day)),
        "cash": query("SELECT currency, ending_cash_text, fx_rate_to_base_text FROM onboarding_cash "
                      "WHERE account_id = ? AND snapshot_date = ? ORDER BY currency", (account, day)),
        "fx_rates": query("SELECT from_currency, to_currency, rate_text FROM onboarding_fx_rates "
                          "WHERE account_id = ? AND rate_date = ? ORDER BY from_currency, to_currency", (account, day)),
        "corporate_actions": query("SELECT effective_date, action_type, description, conid, quantity_text, amount_text, "
                                   "currency FROM onboarding_corporate_actions WHERE account_id = ? "
                                   "AND effective_date BETWEEN ? AND ? ORDER BY effective_date, conid",
                                   (account, near[0], near[1])),
        "activity": query("SELECT kind, activity_date, settle_date, activity_type, conid, amount_text, currency "
                          "FROM onboarding_activity WHERE account_id = ? AND (activity_date BETWEEN ? AND ? "
                          "OR settle_date BETWEEN ? AND ?) ORDER BY activity_date LIMIT 200",
                          (account, near[0], near[1], near[0], near[1])),
    }, "citations": [_citation("nav_snapshots", account, date=day), _citation("position_snapshots", account, date=day),
                     _citation("cash_snapshots", account, date=day)]}


def list_live_onboarding_cases(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Cases live and waiting for their first month-end, with dates and statuses only."""
    rows = connection.execute(
        "SELECT case_id::text AS case_id, subject_id, display_name, source_kind, history_start_date, went_live_at, "
        "latest_data_date, latest_assurance_date, latest_assurance_status, month_team_has_access, "
        "first_month_end_status FROM onboarding_live_cases ORDER BY case_id"
    ).fetchall()
    cases = [{key: (str(value) if key in ("went_live_at", "latest_assurance_date") and value is not None else value)
              for key, value in dict(row).items()} for row in rows]
    return {"as_of": None, "data": {"cases": cases}, "citations": [_citation("broker_onboarding_cases", None)]}


def list_broker_logins(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The tenant's IBKR logins with their latest check: statuses, dates and counts, never the token."""
    rows = connection.execute(
        "SELECT connection_id::text AS connection_id, display_name, connection_status, credential_expires_on, "
        "flex_source_id::text AS flex_source_id, source_status, credential_status, blocked_until, "
        "requires_manual_reset, checks_requested, latest_request_id::text AS latest_request_id, latest_status, "
        "latest_error_phase, latest_error_code, latest_created_at, latest_completed_at, latest_account_count, "
        "accounts_found FROM broker_logins ORDER BY display_name, connection_id"
    ).fetchall()
    stamps = ("credential_expires_on", "blocked_until", "latest_created_at", "latest_completed_at")
    logins = [{key: (str(value) if key in stamps and value is not None else value)
               for key, value in dict(row).items()} for row in rows]
    return {"as_of": None, "data": {"logins": logins}, "citations": [_citation("broker_connections", None)]}


# Operations: the administrator's incidents and the platform's recent runs ------------------


def list_open_incidents(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Open and acknowledged incidents, with the administrator's own evidence."""
    rows = connection.execute(
        "SELECT incident_id, incident_key, check_id, severity, status, description, evidence_json, first_seen_at, "
        "last_seen_at, occurrence_count FROM open_incidents ORDER BY incident_id"
    ).fetchall()
    incidents = []
    for row in rows:
        item = {key: row[key] for key in ("incident_id", "incident_key", "check_id", "severity", "status",
                                          "description", "first_seen_at", "last_seen_at", "occurrence_count")}
        try:
            item["evidence"] = json.loads(row["evidence_json"] or "{}")
        except ValueError:
            item["evidence"] = {"unreadable": True}
        incidents.append(item)
    return {"as_of": None, "data": {"incidents": incidents}, "citations": [_citation("system_incidents", None)]}


def get_operations_history(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Import runs over 14 days and the administrator's runs and actions over 7 days, newest first."""
    def rows(sql: str) -> list[dict[str, Any]]:
        return [dict(row) for row in connection.execute(sql).fetchall()]

    data = {
        "ingestion_runs": rows("SELECT run_id, run_type, started_at, completed_at, status, row_count, error_message "
                               "FROM recent_ingestion_runs ORDER BY started_at DESC LIMIT 200"),
        "admin_runs": rows("SELECT agent_run_id, started_at, completed_at, status, finding_count, action_count, "
                           "summary, error_code FROM recent_admin_runs ORDER BY started_at DESC LIMIT 100"),
        "admin_actions": rows("SELECT action_id, incident_id, action_type, target_job, status, reason, created_at, "
                              "executed_at, error_code FROM recent_admin_actions ORDER BY created_at DESC LIMIT 50"),
    }
    return {"as_of": None, "data": data, "citations": [_citation("ingestion_runs", None),
                                                       _citation("autonomous_agent_runs", None)]}


# Registry ------------------------------------------------------------------


def _schema(properties: Mapping[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties), "required": required, "additionalProperties": False}


_ACCOUNT = {"type": "string", "description": "account_id from list_accounts"}
MONTH_TEAM = frozenset({"month_team"})
ONBOARDING = frozenset({"onboarding"})
OPERATIONS = frozenset({"operations"})
_DATE = {"type": "string", "description": "YYYY-MM-DD"}
_AS_OF = {"type": "string", "description": "YYYY-MM-DD; omit for the latest valuation"}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("list_accounts", "List the accounts and portfolios the person may see.", _schema({}, []), list_accounts, False),
    ToolSpec("get_holdings", "Largest holdings with reporting-currency values and weights.",
             _schema({"account_id": _ACCOUNT, "as_of": _AS_OF, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
                     ["account_id"]), get_holdings),
    ToolSpec("get_holdings_changes", "Change in holdings value between two dates, split into trades, price moves and FX.",
             _schema({"account_id": _ACCOUNT, "from_date": _DATE, "to_date": _DATE}, ["account_id", "from_date", "to_date"]),
             get_holdings_changes),
    ToolSpec("get_performance", "Time-weighted and money-weighted returns for standard periods (MTD, QTD, YTD, 1Y, since inception) and an optional custom period.",
             _schema({"account_id": _ACCOUNT, "as_of": _AS_OF, "start_date": _DATE, "end_date": _DATE}, ["account_id"]),
             get_performance),
    ToolSpec("compare_to_benchmark", "Portfolio return against its configured benchmark for the standard periods, with excess return.",
             _schema({"account_id": _ACCOUNT, "as_of": _AS_OF, "benchmark_id": {"type": "string"}}, ["account_id"]),
             compare_to_benchmark),
    ToolSpec("get_sector_allocation", "Sector weights of current holdings in the reporting currency.",
             _schema({"account_id": _ACCOUNT, "as_of": _AS_OF}, ["account_id"]), get_sector_allocation),
    ToolSpec("get_cash_flows", "Investor deposits and withdrawals between two dates.",
             _schema({"account_id": _ACCOUNT, "from_date": _DATE, "to_date": _DATE}, ["account_id", "from_date", "to_date"]),
             get_cash_flows),
    ToolSpec("get_data_freshness", "How recent the account's valuation, holdings, benchmark and import data are.",
             _schema({"account_id": _ACCOUNT}, ["account_id"]), get_data_freshness),
    ToolSpec("get_nav_history", "Account value and cumulative time-weighted return over time, with the benchmark's "
             "cumulative return, for charts. Up to 400 points; long periods are thinned to weekly or monthly.",
             _schema({"account_id": _ACCOUNT, "from_date": {**_DATE, "description": "YYYY-MM-DD; omit for inception"},
                      "to_date": _AS_OF, "frequency": {"type": "string", "description": "daily, weekly or monthly (default)"}},
                     ["account_id"]), get_nav_history),
    ToolSpec("get_risk_metrics", "Risk and return statistics for a period (default the last year): volatility, "
             "downside deviation, maximum drawdown with dates, best and worst day and month, monthly returns, and "
             "beta, correlation and tracking error against the benchmark.",
             _schema({"account_id": _ACCOUNT, "from_date": {**_DATE, "description": "YYYY-MM-DD; omit for one year"},
                      "to_date": _AS_OF, "benchmark_id": {"type": "string"}}, ["account_id"]), get_risk_metrics),
    ToolSpec("get_month_end_package", "The month-end factsheet and presentation built for a month, with their "
             "snapshot, status, file references and hashes.",
             _schema({"account_id": _ACCOUNT, "period_end": _DATE}, ["account_id", "period_end"]),
             get_month_end_package, only_jobs=MONTH_TEAM),
    ToolSpec("get_month_end_snapshot", "The frozen month-end snapshot a package was rendered from.",
             _schema({"account_id": _ACCOUNT, "snapshot_id": {"type": "string"}}, ["account_id", "snapshot_id"]),
             get_month_end_snapshot, only_jobs=MONTH_TEAM),
    ToolSpec("list_month_end_packages", "Every month with a built month-end package, and each file's status.",
             _schema({"account_id": _ACCOUNT}, ["account_id"]), list_month_end_packages, only_jobs=MONTH_TEAM),
    ToolSpec("list_onboarding_report_cases", "Composite onboarding cases whose historical reports are rendered and "
             "waiting for the agent review, with each report's month, file, hash and private storage path.",
             _schema({}, []), list_onboarding_report_cases, False, only_jobs=ONBOARDING),
    ToolSpec("list_onboarding_intake_cases", "Onboarding cases waiting for approval 1 (the account terms), with "
             "the terms entered on the case, for the intake agent to draft from the client's documents.",
             _schema({}, []), list_onboarding_intake_cases, False, only_jobs=ONBOARDING),
    ToolSpec("list_onboarding_broker_cases", "IBKR broker-account onboarding cases waiting in load and reconcile.",
             _schema({}, []), list_onboarding_broker_cases, False, only_jobs=ONBOARDING),
    ToolSpec("get_onboarding_broker_overview", "A stage 2 broker case's daily NAV from the broker, how many trades "
             "and cash transactions are loaded, and every security it has held with its identifiers.",
             _schema({"case_id": _CASE_ID}, ["case_id"]), get_onboarding_broker_overview, False, only_jobs=ONBOARDING),
    ToolSpec("get_onboarding_broker_day", "One day of a stage 2 broker case: positions, cash, FX rates, the broker's "
             "NAV, and corporate actions and transactions within five days.",
             _schema({"case_id": _CASE_ID, "date": _DATE}, ["case_id", "date"]), get_onboarding_broker_day, False,
             only_jobs=ONBOARDING),
    ToolSpec("list_live_onboarding_cases", "Onboarding cases that went live and wait for their first month-end, "
             "with the go-live time, latest data and assurance dates, and whether the month-end team has access.",
             _schema({}, []), list_live_onboarding_cases, False, only_jobs=ONBOARDING),
    ToolSpec("list_broker_logins", "The IBKR logins added for onboarding, with the latest check of each: its "
             "status, IBKR's error code if it failed, and how many accounts it found.", _schema({}, []),
             list_broker_logins, False, only_jobs=ONBOARDING),
    ToolSpec("list_open_incidents", "The autonomous administrator's open and acknowledged incidents, with its "
             "evidence, for the operations agent to investigate.", _schema({}, []), list_open_incidents, False,
             only_jobs=OPERATIONS),
    ToolSpec("get_operations_history", "Import runs over the last 14 days and the autonomous administrator's runs "
             "and actions over the last 7 days, newest first.", _schema({}, []), get_operations_history, False,
             only_jobs=OPERATIONS),
)
REGISTRY = {tool.name: tool for tool in TOOLS}


def validate_arguments(spec: ToolSpec, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        raise ToolError("invalid_arguments", "Arguments must be an object.", 400)
    properties = spec.parameters["properties"]
    unexpected = set(arguments) - set(properties)
    if unexpected:
        raise ToolError("invalid_arguments", "Unsupported arguments: " + ", ".join(sorted(unexpected)), 400)
    clean = {k: v for k, v in arguments.items() if v not in (None, "")}
    for name in spec.parameters["required"]:
        if name not in clean:
            raise ToolError("invalid_arguments", f"{name} is required.", 400)
    for name, value in clean.items():
        kind = properties[name]["type"]
        if kind == "string" and (not isinstance(value, str) or len(value) > 64):
            raise ToolError("invalid_arguments", f"{name} must be a short string.", 400)
        if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ToolError("invalid_arguments", f"{name} must be a whole number.", 400)
    return clean


def json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def run_tool(connection, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError("unknown_tool", "That tool does not exist.", 404)
    clean = validate_arguments(spec, arguments)
    result = spec.run(connection, clean)
    return json_safe({"tool": name, "account_id": clean.get("account_id"), "generated_at": _now(), **result})
