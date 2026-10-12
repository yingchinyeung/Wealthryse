"""Read-only portfolio tools.

Every tool runs inside ``database.scoped`` with exactly one account visible,
uses fixed parameterised SQL against the agent views, and reuses the
platform's own calculation code so its figures match the website. A tool
never writes, never takes SQL from its caller, and fails closed (raises
ToolError) instead of guessing when data is missing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
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


def _days_between(a: str, b: str) -> int:
    return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)


def compare_to_benchmark(connection, arguments: Mapping[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account_id"])
    subject = _subject(connection, account_id)
    currency = _reporting_currency(subject)
    as_of = _latest_nav_date(connection, account_id, arguments.get("as_of") and _day(arguments["as_of"], "as_of"))
    requested = arguments.get("benchmark_id")
    mapping = connection.execute(
        """
        SELECT b.benchmark_id, b.name, b.symbol, b.currency, m.mapping_type, m.reporting_currency
        FROM portfolio_benchmark_mapping m JOIN benchmark_master b ON b.benchmark_id = m.benchmark_id
        WHERE m.subject_id = ? AND b.active AND m.effective_from <= ? AND (m.effective_to IS NULL OR m.effective_to >= ?)
          AND (?::text IS NULL OR b.benchmark_id = ?)
        ORDER BY CASE m.mapping_type WHEN 'primary' THEN 0 ELSE 1 END, b.name LIMIT 1
        """,
        (account_id, as_of, as_of, requested, requested),
    ).fetchone()
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


# Registry ------------------------------------------------------------------

def _schema(properties: Mapping[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties), "required": required, "additionalProperties": False}


_ACCOUNT = {"type": "string", "description": "account_id from list_accounts"}
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
