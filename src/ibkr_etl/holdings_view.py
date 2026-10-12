"""Enrich physical and filtered holdings for the website holdings views.

Pure functions only: callers read rows and dated FX history, this module adds
cost, P&L, reporting-currency values, weights and sectors without mutating
broker evidence. Missing or unusable FX never raises; the affected row keeps
its native figures, leaves reporting fields null and is flagged.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .fx_selection import InvalidFxObservation, latest_fx_quote

HOLDINGS_VIEW_METHOD = "holdings-view-v1"
UNCLASSIFIED_SECTOR = "Unclassified"
WEIGHT_BASIS_NAV = "nav"
WEIGHT_BASIS_GROSS = "gross_holdings"


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _text(value: Decimal | None) -> str | None:
    """Plain decimal text without exponent or trailing conversion zeros."""
    return None if value is None else format(value.normalize(), "f")


def _ratio(numerator: Decimal | None, denominator: Decimal | None) -> float | None:
    if numerator is None or denominator in (None, Decimal(0)):
        return None
    return float(numerator / denominator)


def fx_history(fx_rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], list[tuple[str, Any]]]:
    """Group fx_rates rows by currency pair in ascending date order."""
    history: dict[tuple[str, str], list[tuple[str, Any]]] = defaultdict(list)
    for row in fx_rows:
        key = (str(row["from_currency"]).upper(), str(row["to_currency"]).upper())
        history[key].append((str(row["rate_date"]), row["rate_text"]))
    for values in history.values():
        values.sort(key=lambda item: item[0])
    return dict(history)


def reporting_fx(history: Mapping[tuple[str, str], list], native: str, target: str,
                 on_date: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return (fx evidence, flag) using direct-then-reciprocal dated lookup.

    Mirrors ``reporting_currency_holdings.normalize_report_holdings``; the only
    difference is that absent or invalid FX is reported as a flag instead of
    an exception, so one row cannot take the holdings page down.
    """
    native, target = str(native or "").upper(), str(target or "").upper()
    if len(native) != 3 or not native.isalpha():
        return None, "unknown_currency"
    if native == target:
        return {"from": native, "to": target, "rate": "1", "date": str(on_date),
                "direction": "identity"}, None
    try:
        quote = latest_fx_quote(history.get((native, target), []), on_date)
        direction = "direct"
        if quote is None:
            quote = latest_fx_quote(history.get((target, native), []), on_date)
            direction = "reciprocal"
    except InvalidFxObservation:
        return None, "fx_invalid"
    if quote is None:
        return None, "fx_missing"
    rate = Decimal(str(quote[0]))
    if direction == "reciprocal":
        rate = Decimal(1) / rate
    return {"from": native, "to": target, "rate": str(rate), "date": quote[1],
            "direction": direction}, None


def enrich_holding(row: Mapping[str, Any], *, reporting_currency: str,
                   history: Mapping[tuple[str, str], list],
                   fx_unavailable_reason: str | None = None) -> dict[str, Any]:
    """Return a copy of one holding with native P&L and reporting values."""
    result = dict(row)
    flags: list[str] = []
    target = str(reporting_currency).upper()
    market_value = _decimal(row.get("market_value_text"))
    cost_basis = _decimal(row.get("cost_basis_text"))
    pnl = _decimal(row.get("unrealized_pnl_text"))
    pnl_source = "reported" if pnl is not None else None
    if pnl is None and market_value is not None and cost_basis is not None:
        pnl, pnl_source = market_value - cost_basis, "derived"
    if market_value is None:
        flags.append("missing_market_value")
    if cost_basis is None:
        flags.append("missing_cost_basis")
    if fx_unavailable_reason:
        fx, fx_flag = None, fx_unavailable_reason
    else:
        fx, fx_flag = reporting_fx(history, row.get("currency") or "", target,
                                   str(row["snapshot_date"]))
    if fx_flag:
        flags.append(fx_flag)
    rate = _decimal(fx["rate"]) if fx else None
    convert = (lambda value: None if value is None or rate is None else value * rate)
    result.update(
        sector=str(row.get("sector") or UNCLASSIFIED_SECTOR),
        cost_basis_text=row.get("cost_basis_text"),
        unrealized_pnl_text=_text(pnl),
        unrealized_pnl_source=pnl_source,
        unrealized_pnl_pct=_ratio(pnl, abs(cost_basis) if cost_basis is not None else None),
        reporting_currency=target,
        reporting_fx=fx,
        reporting_market_value_text=_text(convert(market_value)),
        reporting_cost_basis_text=_text(convert(cost_basis)),
        reporting_unrealized_pnl_text=_text(convert(pnl)),
        weight=None,
        fx_missing=fx is None,
        flags=flags,
    )
    return result


def apply_weights(rows: list[dict[str, Any]], nav_total: object = None) -> dict[str, Any]:
    """Set each row's weight in place; return the basis actually used."""
    values = [_decimal(row.get("reporting_market_value_text")) for row in rows]
    gross = sum((abs(value) for value in values if value is not None), Decimal(0))
    nav = _decimal(nav_total)
    if nav is not None and nav != 0:
        basis, denominator = WEIGHT_BASIS_NAV, nav
    elif gross != 0:
        basis, denominator = WEIGHT_BASIS_GROSS, gross
    else:
        basis, denominator = None, None
    for row, value in zip(rows, values):
        row["weight"] = _ratio(value, denominator)
    return {
        "weight_basis": basis,
        "weight_denominator_text": _text(denominator),
        "nav_text": _text(nav),
        "gross_holdings_text": _text(gross),
    }


def enrich_holdings(rows: Iterable[Mapping[str, Any]], *, reporting_currency: str,
                    fx_rows: Iterable[Mapping[str, Any]] = (), nav_total: object = None,
                    fx_unavailable_reason: str | None = None) -> dict[str, Any]:
    """Enrich one subject's holdings and compute weights.

    ``nav_total`` is the subject NAV in the reporting currency on the holdings
    date; when it is absent or zero, weights fall back to gross holdings value
    and ``weight_basis`` says so.
    """
    history = fx_history(fx_rows)
    enriched = [enrich_holding(row, reporting_currency=reporting_currency, history=history,
                               fx_unavailable_reason=fx_unavailable_reason) for row in rows]
    summary = apply_weights(enriched, nav_total)
    missing = sorted({str(row.get("conid")) for row in enriched if row["fx_missing"]})
    return {
        "holdings": enriched,
        "reporting_currency": str(reporting_currency).upper(),
        "valuation_method": HOLDINGS_VIEW_METHOD,
        "fx_missing_conids": missing,
        **summary,
    }


def combine_holdings(accounts: Iterable[Mapping[str, Any]], *, currency: str) -> dict[str, Any]:
    """Aggregate already-enriched account holdings by conid.

    Each account mapping carries ``account_id``, optional ``display_name`` and
    ``holdings`` enriched into ``currency``. Rows whose FX is missing still add
    their quantity but make the aggregate value ``incomplete``. Weights use
    the gross value of all converted holdings, because cash and other NAV
    components are not aggregated here.
    """
    target = str(currency).upper()
    combined: dict[str, dict[str, Any]] = {}
    for account in accounts:
        for row in account["holdings"]:
            if row.get("reporting_currency") != target:
                raise ValueError("Holding was enriched into a different currency")
            conid = str(row["conid"])
            entry = combined.setdefault(conid, {
                "conid": conid, "symbol": row.get("symbol"), "description": row.get("description"),
                "sector": row.get("sector") or UNCLASSIFIED_SECTOR, "native_currencies": set(),
                "quantity": Decimal(0), "value": Decimal(0), "cost": Decimal(0), "pnl": Decimal(0),
                "value_complete": True, "cost_complete": True, "pnl_complete": True,
                "accounts": [],
            })
            if row.get("currency"):
                entry["native_currencies"].add(str(row["currency"]).upper())
            for key, field in (("quantity", "quantity_text"), ("value", "reporting_market_value_text"),
                               ("cost", "reporting_cost_basis_text"), ("pnl", "reporting_unrealized_pnl_text")):
                number = _decimal(row.get(field))
                if number is None:
                    if key != "quantity":
                        entry[f"{key}_complete"] = False
                    continue
                entry[key] += number
            entry["accounts"].append({
                "account_id": account["account_id"],
                "display_name": account.get("display_name") or account["account_id"],
                "snapshot_date": row.get("snapshot_date"),
                "currency": row.get("currency"),
                "quantity_text": row.get("quantity_text"),
                "market_value_text": row.get("market_value_text"),
                "cost_basis_text": row.get("cost_basis_text"),
                "unrealized_pnl_text": row.get("unrealized_pnl_text"),
                "reporting_market_value_text": row.get("reporting_market_value_text"),
                "reporting_cost_basis_text": row.get("reporting_cost_basis_text"),
                "reporting_unrealized_pnl_text": row.get("reporting_unrealized_pnl_text"),
                "reporting_fx": row.get("reporting_fx"),
                "fx_missing": bool(row.get("fx_missing")),
                "flags": list(row.get("flags") or []),
            })
    gross = sum((abs(entry["value"]) for entry in combined.values()), Decimal(0))
    holdings = []
    for entry in combined.values():
        value = entry["value"]
        cost = entry["cost"] if entry["cost_complete"] else None
        pnl = entry["pnl"] if entry["pnl_complete"] else None
        holdings.append({
            "conid": entry["conid"], "symbol": entry["symbol"], "description": entry["description"],
            "sector": entry["sector"], "native_currencies": sorted(entry["native_currencies"]),
            "quantity_text": _text(entry["quantity"]),
            "reporting_currency": target,
            "reporting_market_value_text": _text(value),
            "reporting_cost_basis_text": _text(cost),
            "reporting_unrealized_pnl_text": _text(pnl),
            "unrealized_pnl_pct": _ratio(pnl, abs(cost) if cost is not None else None),
            "weight": _ratio(value, gross),
            "incomplete": not entry["value_complete"],
            "account_count": len(entry["accounts"]),
            "accounts": entry["accounts"],
        })
    holdings.sort(key=lambda item: abs(Decimal(item["reporting_market_value_text"])), reverse=True)
    return {
        "currency": target,
        "weight_basis": WEIGHT_BASIS_GROSS,
        "gross_holdings_text": _text(gross),
        "incomplete": any(item["incomplete"] for item in holdings),
        "holdings": holdings,
    }
