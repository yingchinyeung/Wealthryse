"""An IBKR account's NAV on one day, rebuilt from silver positions, cash and FX, against IBKR's own total.

IBKR's daily NAV (the TOTAL row of nav_snapshots, from the statement's EquitySummaryByReportDateInBase)
is the figure the platform shows. This rebuild is the independent check that the silver positions, cash
and FX of the same day add up to it:

    positions at IBKR's mark value, each in the base currency at IBKR's FX rate
  + cash, each currency in the base currency at IBKR's FX rate
  + IBKR's accruals
  = IBKR's total

Cash comes from the Cash Report. When the statement breaks cash down by currency, those rows are used.
Some Flex queries send only the base-currency total (the row stored with currency BASE_SUMMARY, already
in the base currency); that row is then the account's cash. Counting neither, as the rebuild once did,
treats a margin loan or a cash balance as zero and shows a gap that is not in the data.

Shared by the daily quality check (layer_quality, value.nav_rebuild) and the onboarding reconciliation
agent (wealthryse_agent.onboarding_reconcile). Arithmetic is Decimal on the stored text.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

CURRENCY = re.compile(r"^[A-Z]{3}$")
BASE_SUMMARY = "BASE_SUMMARY"
POSITION_FIELDS = ("stock_text", "options_text", "futures_text", "bonds_text")
CENT = Decimal("0.01")


def decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def fx_rate(currency: str, base: str, cash: Iterable[Mapping[str, Any]],
            fx_rates: Iterable[Mapping[str, Any]]) -> Decimal | None:
    """IBKR's rate from currency to base on the day: the Cash Report's rate, else the statement's rates."""
    if currency == base:
        return Decimal(1)
    for row in cash:
        if row.get("currency") == currency and (rate := decimal(row.get("fx_rate_to_base_text"))):
            return rate
    for row in fx_rates:
        rate = decimal(row.get("rate_text"))
        if not rate:
            continue
        if (row.get("from_currency"), row.get("to_currency")) == (currency, base):
            return rate
        if (row.get("from_currency"), row.get("to_currency")) == (base, currency):
            return Decimal(1) / rate
    return None


def rebuild(total: Mapping[str, Any], positions: Iterable[Mapping[str, Any]], cash: Iterable[Mapping[str, Any]],
            fx_rates: Iterable[Mapping[str, Any]], base: str) -> dict[str, Any] | None:
    """One day's rebuilt NAV against IBKR's TOTAL row. None when IBKR gave no total."""
    broker = decimal(total.get("total_text"))
    if broker is None:
        return None
    cash, fx_rates = list(cash), list(fx_rates)
    missing_fx: set[str] = set()
    missing_price: list[dict[str, Any]] = []
    position_value = Decimal(0)
    for row in positions:
        quantity, value = decimal(row.get("quantity_text")), decimal(row.get("market_value_text"))
        if not quantity:
            continue
        if value is None or not decimal(row.get("mark_price_text")):
            missing_price.append({"conid": row.get("conid"), "quantity": str(quantity)})
            continue
        rate = fx_rate(str(row.get("currency") or base), base, cash, fx_rates)
        if rate is None:
            missing_fx.add(str(row.get("currency")))
            continue
        position_value += value * rate
    by_currency = [r for r in cash if CURRENCY.match(str(r.get("currency") or ""))
                   and decimal(r.get("ending_cash_text")) is not None]
    summary = next((decimal(r.get("ending_cash_text")) for r in cash
                    if str(r.get("currency") or "").upper() == BASE_SUMMARY
                    and decimal(r.get("ending_cash_text")) is not None), None)
    cash_value = Decimal(0)
    if by_currency:
        cash_source = "currencies"
        for row in by_currency:
            currency = str(row["currency"])
            rate = fx_rate(currency, base, cash, fx_rates)
            if rate is None:
                missing_fx.add(currency)
                continue
            cash_value += decimal(row["ending_cash_text"]) * rate
    elif summary is not None:
        cash_source = "base_summary"
        cash_value = summary
    else:
        cash_source = "none"
    accruals = decimal(total.get("accruals_text")) or Decimal(0)
    rebuilt = position_value + cash_value + accruals
    return {"broker_nav": broker, "rebuilt_nav": rebuilt, "gap": rebuilt - broker,
            "positions_value": position_value, "cash_value": cash_value, "accruals": accruals,
            "cash_source": cash_source,
            "broker_positions_value": sum((decimal(total.get(k)) or Decimal(0) for k in POSITION_FIELDS),
                                          Decimal(0)),
            "broker_cash": decimal(total.get("cash_text")),
            "missing_fx": sorted(missing_fx), "missing_price": missing_price}


def basis_points(amount: Decimal, of: Decimal) -> float | None:
    """amount as basis points (0.01%) of `of`, to two places; the check reports gaps this way, never amounts."""
    if not of:
        return None
    return float((amount / of * 10000).quantize(CENT))
