from __future__ import annotations

import bisect
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .db import is_postgres, transaction
from .reference_snapshot import load_reference_snapshot
from .fx_selection import latest_fx_quote


HKT = timezone(timedelta(hours=8), name="Asia/Hong_Kong")
CALCULATION_VERSION = "country-exclusion-cash-ledger-v2"
ALPHA_WORLD_CALCULATION_VERSION = "fixed-unit-composite-v2"
TAIWAN_COUNTRY_NAMES = {
    "taiwan",
    "taiwan, province of china",
    "taiwan province of china",
    "tw",
    "twn",
}
TAIWAN_EXCHANGES = {"TWSE", "TWO", "TPEX", "ROCO", "TAIFEX"}
ALLOWED_DERIVED_CASH_CURRENCIES = {"HKD", "USD"}
OPENING_CASH = {"HKD": 484_300.0, "USD": 0.0}
# Ledger reasons that mean a source trade is outside the filtered portfolio
# itself, as opposed to cash-policy reasons such as a non-permitted currency.
# The website's filtered trade history keys on these exact strings.
DUPLICATE_TRADE_REASON = "duplicate logical trade"
EXCLUDED_INSTRUMENT_TRADE_REASON_PREFIX = "trade belongs to excluded Taiwan instrument: "
EXCLUDED_CURRENCY_TRADE_REASON = "all legs of an excluded-currency trade are excluded"
_SOURCE_ACCOUNT_SUFFIX = re.compile(r"\s+from\s+U\d+\s*$", re.IGNORECASE)
_EXTERNAL_FLOW_PATTERN = re.compile(
    r"\b(DEPOSIT|WITHDRAW(?:AL)?|TRANSFER|WIRE|ACH|FPS|CONTRIBUTION|DISTRIBUTION)\b",
    re.IGNORECASE,
)


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class _FxRates:
    def __init__(self, rows: Iterable[Any], account_id: str, base_currency: str) -> None:
        self.account_id = account_id
        self.base_currency = base_currency
        self.series: dict[tuple[str, str], list[tuple[str, Any]]] = defaultdict(list)
        for row in rows:
            self.series[(row["from_currency"], row["to_currency"])].append(
                (row["rate_date"], row["rate_text"])
            )
        for values in self.series.values():
            values.sort(key=lambda item: item[0])

    def rate(self, currency: str | None, on_date: str) -> float | None:
        if not currency:
            return None
        if currency == self.base_currency:
            return 1.0
        values = self.series.get((currency, self.base_currency), [])
        if not values:
            return None
        quote = latest_fx_quote(values, on_date)
        return quote[0] if quote else None


class _CrossFxRates:
    """Tenant FX history with preferred-account and reciprocal fallbacks."""

    def __init__(self, rows: Iterable[Any]) -> None:
        self.series: dict[tuple[str, str, str], list[tuple[str, Any]]] = defaultdict(list)
        for row in rows:
            self.series[
                (row["account_id"], row["from_currency"], row["to_currency"])
            ].append((row["rate_date"], row["rate_text"]))
        for values in self.series.values():
            values.sort(key=lambda item: item[0])

    @staticmethod
    def _at_or_before(
        values: list[tuple[str, Any]], on_date: str
    ) -> tuple[float, str] | None:
        if not values:
            return None
        return latest_fx_quote(values, on_date)

    def rate(
        self,
        from_currency: str,
        to_currency: str,
        on_date: str,
        preferred_accounts: Iterable[str] = (),
    ) -> tuple[float, str]:
        source = from_currency.strip().upper()
        target = to_currency.strip().upper()
        if source == target:
            return 1.0, on_date
        ordered_accounts = list(dict.fromkeys(preferred_accounts))
        ordered_accounts.extend(
            account
            for account, _, _ in self.series
            if account not in ordered_accounts
        )
        for account in ordered_accounts:
            direct = self._at_or_before(
                self.series.get((account, source, target), []), on_date
            )
            if direct:
                return direct
            inverse = self._at_or_before(
                self.series.get((account, target, source), []), on_date
            )
            if inverse:
                return 1.0 / inverse[0], inverse[1]
        raise RuntimeError(
            f"No FX rate is available for {source}/{target} on or before {on_date}"
        )


def _value_at_or_before(
    series: list[tuple[str, float]], on_date: str, *, maximum_age_days: int | None = None
) -> tuple[float, str]:
    dates = [item[0] for item in series]
    index = bisect.bisect_right(dates, on_date) - 1
    if index < 0:
        raise RuntimeError(f"No observation is available on or before {on_date}")
    observation_date, value = series[index]
    if maximum_age_days is not None:
        age = (
            datetime.fromisoformat(on_date).date()
            - datetime.fromisoformat(observation_date).date()
        ).days
        if age > maximum_age_days:
            raise RuntimeError(
                f"Observation for {on_date} is stale: latest is {observation_date}"
            )
    return value, observation_date


def portfolio_exclusion_reason(
    row: Any,
    reference_data: dict[str, Any],
    *,
    excluded_countries: Iterable[str],
    excluded_exchanges: Iterable[str],
    excluded_currencies: Iterable[str],
) -> str | None:
    reference = reference_data.get("sectors", {}).get(row["conid"], {})
    country = str(reference.get("country") or "").strip().lower()
    exchange = str(row["listing_exchange"] or "").strip().upper()
    position_currency = str(row["currency"] or "").strip().upper()
    instrument_currency = str(row["instrument_currency"] or "").strip().upper()
    countries = {str(value).strip().lower() for value in excluded_countries}
    if "taiwan" in countries:
        countries.update(TAIWAN_COUNTRY_NAMES)
    exchanges = {str(value).strip().upper() for value in excluded_exchanges}
    currencies = {str(value).strip().upper() for value in excluded_currencies}
    if country in countries:
        return f"reference country={reference.get('country')}"
    if exchange in exchanges:
        return f"listing exchange={exchange}"
    matched = sorted({position_currency, instrument_currency}.intersection(currencies))
    if matched:
        return f"position or instrument currency={matched[0]}"
    if exchange == "CORPACT" and position_currency in currencies:
        return f"corporate-action position currency={position_currency}"
    return None


def taiwan_exclusion_reason(
    row: Any, reference_data: dict[str, Any]
) -> str | None:
    return portfolio_exclusion_reason(
        row,
        reference_data,
        excluded_countries=TAIWAN_COUNTRY_NAMES,
        excluded_exchanges=TAIWAN_EXCHANGES,
        excluded_currencies=("TWD",),
    )


def _instrument_exclusion_reason(
    conid: str | None,
    instruments: dict[str, Any],
    reference_data: dict[str, Any],
    *,
    excluded_countries: Iterable[str],
    excluded_exchanges: Iterable[str],
    excluded_currencies: Iterable[str],
) -> str | None:
    if not conid:
        return None
    instrument = instruments.get(conid)
    reference = reference_data.get("sectors", {}).get(conid, {})
    country = str(reference.get("country") or "").strip().lower()
    exchange = str(instrument["listing_exchange"] if instrument else "").strip().upper()
    currency = str(instrument["currency"] if instrument else "").strip().upper()
    symbol = str(instrument["symbol"] if instrument else "").strip().upper()
    countries = {str(value).strip().lower() for value in excluded_countries}
    if "taiwan" in countries:
        countries.update(TAIWAN_COUNTRY_NAMES)
    exchanges = {str(value).strip().upper() for value in excluded_exchanges}
    currencies = {str(value).strip().upper() for value in excluded_currencies}
    if country in countries:
        return f"reference country={reference.get('country')}"
    if exchange in exchanges:
        return f"listing exchange={exchange}"
    symbol_legs = set(symbol.split("."))
    matched = sorted(({currency} | symbol_legs).intersection(currencies))
    if matched:
        return f"instrument currency or FX leg={matched[0]}"
    return None


def _normalized_cash_description(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return _SOURCE_ACCOUNT_SUFFIX.sub("", text).strip().upper()


def _cash_dedupe_key(row: Any) -> tuple[str, str, str, str, str, str]:
    amount = _number(row["amount_text"])
    return (
        str(row["effective_date"] or ""),
        str(row["model_code"] or ""),
        str(row["conid"] or ""),
        str(row["currency"] or "").upper(),
        "" if amount is None else f"{amount:.10f}",
        _normalized_cash_description(row["description"]),
    )


def _is_excluded_currency_description(
    value: Any, excluded_currencies: Iterable[str]
) -> str | None:
    text = _normalized_cash_description(value)
    for currency in sorted({str(item).strip().upper() for item in excluded_currencies}):
        if re.search(rf"(?:^|[^A-Z]){re.escape(currency)}(?:[^A-Z]|$)", text):
            return currency
    return None


def _is_interest_activity(transaction_type: Any, description: Any) -> bool:
    return bool(
        re.search(
            r"\b(?:INTEREST|CREDIT\s+INT|DEBIT\s+INT)\b",
            f"{transaction_type or ''} {description or ''}",
            re.IGNORECASE,
        )
    )


def _is_external_flow(transaction_type: Any, description: Any) -> bool:
    text = f"{transaction_type or ''} {description or ''}"
    if re.search(r"\b(?:CREDIT|DEBIT)\s+INT\b", text, re.IGNORECASE):
        return False
    return bool(_EXTERNAL_FLOW_PATTERN.search(text))


def _build_cash_ledger(
    connection: Any,
    *,
    source_account_id: str,
    portfolio_id: str,
    history_start_date: str,
    base_currency: str,
    fx: _FxRates,
    instruments: dict[str, Any],
    reference_data: dict[str, Any],
    generated_at: str,
    opening_cash: dict[str, float] | None = None,
    allowed_cash_currencies: Iterable[str] = ALLOWED_DERIVED_CASH_CURRENCIES,
    excluded_countries: Iterable[str] = TAIWAN_COUNTRY_NAMES,
    excluded_exchanges: Iterable[str] = TAIWAN_EXCHANGES,
    excluded_currencies: Iterable[str] = ("TWD",),
    include_actual_interest: bool = True,
) -> list[dict[str, Any]]:
    """Build an auditable reconstructed ledger without copying source cash."""

    events: list[dict[str, Any]] = []
    opening_balances = dict(opening_cash or OPENING_CASH)
    allowed_currencies = {
        str(value).strip().upper() for value in allowed_cash_currencies
    }
    excluded_currency_set = {
        str(value).strip().upper() for value in excluded_currencies
    }

    def append_event(
        *,
        event_key: str,
        event_date: str,
        settle_date: str | None,
        source_type: str,
        source_key: str | None,
        conid: str | None,
        currency: str,
        amount: float,
        included: bool,
        external_flow: bool,
        classification: str,
        description: str,
        exclusion_reason: str | None = None,
    ) -> None:
        code = currency.strip().upper()
        rate = fx.rate(code, event_date) if code in allowed_currencies else None
        if included and rate is None:
            raise RuntimeError(
                f"No {code}/{base_currency} FX rate is available for included "
                f"cash event {event_key} on {event_date}"
            )
        events.append(
            {
                "portfolio_id": portfolio_id,
                "event_key": event_key,
                "source_account_id": source_account_id,
                "event_date": event_date,
                "settle_date": settle_date,
                "source_type": source_type,
                "source_key": source_key,
                "conid": conid,
                "currency": code,
                "amount": amount,
                "fx_rate_to_base": rate,
                "amount_base": amount * rate if rate is not None else None,
                "included": 1 if included else 0,
                "external_flow": 1 if external_flow and included else 0,
                "classification": classification,
                "description": description,
                "exclusion_reason": exclusion_reason,
                "generated_at": generated_at,
            }
        )

    for currency, amount in opening_balances.items():
        append_event(
            event_key=f"opening:{history_start_date}:{currency}",
            event_date=history_start_date,
            settle_date=history_start_date,
            source_type="opening_balance",
            source_key=None,
            conid=None,
            currency=currency,
            amount=amount,
            included=True,
            external_flow=False,
            classification="opening balance",
            description=(
                "Approved non-interest-bearing opening balance before activity on "
                f"{history_start_date}"
            ),
        )

    cash_rows = connection.execute(
        """
        SELECT source_key, transaction_id, event_datetime, model_code,
               effective_date, settle_date, transaction_type, description,
               amount_text, currency, conid
        FROM cash_transactions
        WHERE account_id = ? AND effective_date >= ?
        ORDER BY effective_date, source_key
        """,
        (source_account_id, history_start_date),
    ).fetchall()
    cash_rows = sorted(
        cash_rows,
        key=lambda row: (
            row["effective_date"] or "",
            -len(str(row["event_datetime"] or "")),
            row["source_key"] or "",
        ),
    )
    seen_cash: set[tuple[str, str, str, str, str, str]] = set()
    for row in cash_rows:
        currency = str(row["currency"] or "").strip().upper()
        amount = _number(row["amount_text"])
        conid = str(row["conid"] or "") or None
        instrument_reason = _instrument_exclusion_reason(
            conid,
            instruments,
            reference_data,
            excluded_countries=excluded_countries,
            excluded_exchanges=excluded_exchanges,
            excluded_currencies=excluded_currency_set,
        )
        dedupe_key = _cash_dedupe_key(row)
        duplicate = dedupe_key in seen_cash
        if not duplicate:
            seen_cash.add(dedupe_key)

        reason: str | None = None
        if duplicate:
            reason = "duplicate logical cash transaction"
        elif amount is None:
            reason = "missing or invalid cash amount"
        elif instrument_reason:
            reason = f"cash activity belongs to excluded Taiwan instrument: {instrument_reason}"
        elif currency not in allowed_currencies:
            reason = f"currency {currency or 'UNKNOWN'} is not permitted"
        elif excluded_description_currency := _is_excluded_currency_description(
            row["description"], excluded_currency_set
        ):
            reason = (
                "cash description is linked to excluded currency "
                f"{excluded_description_currency}"
            )
        elif not include_actual_interest and _is_interest_activity(
            row["transaction_type"], row["description"]
        ):
            reason = "actual posted interest is excluded by the approved cash policy"

        append_event(
            event_key=f"cash:{row['source_key']}",
            event_date=row["effective_date"],
            settle_date=row["settle_date"],
            source_type="cash_transaction",
            source_key=row["source_key"],
            conid=conid,
            currency=currency or "UNKNOWN",
            amount=amount or 0.0,
            included=reason is None,
            external_flow=_is_external_flow(
                row["transaction_type"], row["description"]
            ),
            classification=str(row["transaction_type"] or "cash activity"),
            description=str(row["description"] or ""),
            exclusion_reason=reason,
        )

    trade_rows = connection.execute(
        """
        SELECT t.source_key, t.conid, t.trade_date, t.settle_date, t.buy_sell,
               t.quantity_text, t.proceeds_text, t.commission_text,
               t.commission_currency, t.net_cash_text, t.currency,
               i.symbol, i.description, i.asset_category
        FROM trades AS t
        LEFT JOIN instruments AS i ON i.conid = t.conid
        WHERE t.account_id = ? AND t.trade_date >= ?
        ORDER BY t.trade_date, t.source_key
        """,
        (source_account_id, history_start_date),
    ).fetchall()
    seen_trades: set[tuple[str, str, str, str, str, str]] = set()
    for row in trade_rows:
        conid = str(row["conid"] or "") or None
        instrument_reason = _instrument_exclusion_reason(
            conid,
            instruments,
            reference_data,
            excluded_countries=excluded_countries,
            excluded_exchanges=excluded_exchanges,
            excluded_currencies=excluded_currency_set,
        )
        currency = str(row["currency"] or "").strip().upper()
        commission_currency = str(
            row["commission_currency"] or currency
        ).strip().upper()
        asset = str(row["asset_category"] or "").strip().upper()
        symbol = str(row["symbol"] or "").strip().upper()
        trade_key = (
            str(row["trade_date"] or ""),
            str(conid or ""),
            str(row["quantity_text"] or ""),
            str(row["proceeds_text"] or ""),
            str(row["commission_text"] or ""),
            str(row["net_cash_text"] or ""),
        )
        duplicate = trade_key in seen_trades
        if not duplicate:
            seen_trades.add(trade_key)
        excluded_pair = bool(
            set(symbol.split(".")).intersection(excluded_currency_set)
            or currency in excluded_currency_set
        )
        common_reason: str | None = None
        if duplicate:
            common_reason = DUPLICATE_TRADE_REASON
        elif instrument_reason:
            common_reason = EXCLUDED_INSTRUMENT_TRADE_REASON_PREFIX + instrument_reason
        elif excluded_pair:
            common_reason = EXCLUDED_CURRENCY_TRADE_REASON

        if asset == "CASH" and "." in symbol:
            base_code, quote_code = symbol.split(".", 1)
            quantity = _number(row["quantity_text"])
            proceeds = _number(row["proceeds_text"])
            pair_reason = common_reason
            if not {base_code, quote_code}.issubset(allowed_currencies):
                pair_reason = pair_reason or "FX pair contains a non-permitted currency"
            for leg_currency, leg_amount, suffix in (
                (base_code, quantity, "base"),
                (quote_code, None if proceeds is None else -proceeds, "quote"),
            ):
                reason = pair_reason
                if leg_amount is None:
                    reason = reason or "missing or invalid FX trade amount"
                append_event(
                    event_key=f"trade:{row['source_key']}:{suffix}",
                    event_date=row["trade_date"],
                    settle_date=row["settle_date"],
                    source_type="fx_trade",
                    source_key=row["source_key"],
                    conid=conid,
                    currency=leg_currency or "UNKNOWN",
                    amount=leg_amount or 0.0,
                    included=reason is None,
                    external_flow=False,
                    classification="FX trade",
                    description=f"{row['buy_sell'] or ''} {symbol}".strip(),
                    exclusion_reason=reason,
                )
            commission = _number(row["commission_text"])
            if commission not in (None, 0.0):
                reason = pair_reason
                if commission_currency not in allowed_currencies:
                    reason = reason or f"currency {commission_currency or 'UNKNOWN'} is not permitted"
                append_event(
                    event_key=f"trade:{row['source_key']}:commission",
                    event_date=row["trade_date"],
                    settle_date=row["settle_date"],
                    source_type="trade_commission",
                    source_key=row["source_key"],
                    conid=conid,
                    currency=commission_currency or "UNKNOWN",
                    amount=commission,
                    included=reason is None,
                    external_flow=False,
                    classification="commission",
                    description=f"Commission for {symbol}",
                    exclusion_reason=reason,
                )
            continue

        net_cash = _number(row["net_cash_text"])
        proceeds = _number(row["proceeds_text"])
        commission = _number(row["commission_text"])
        if net_cash is None:
            net_cash = None if proceeds is None else -proceeds
            if (
                net_cash is not None
                and commission is not None
                and commission_currency == currency
            ):
                net_cash += commission
        reason = common_reason
        if currency not in allowed_currencies:
            reason = reason or f"currency {currency or 'UNKNOWN'} is not permitted"
        if net_cash is None:
            reason = reason or "missing or invalid trade net cash"
        append_event(
            event_key=f"trade:{row['source_key']}:net",
            event_date=row["trade_date"],
            settle_date=row["settle_date"],
            source_type="security_trade",
            source_key=row["source_key"],
            conid=conid,
            currency=currency or "UNKNOWN",
            amount=net_cash or 0.0,
            included=reason is None,
            external_flow=False,
            classification="security trade net cash",
            description=f"{row['buy_sell'] or ''} {symbol or row['description'] or conid or ''}".strip(),
            exclusion_reason=reason,
        )
        if (
            commission not in (None, 0.0)
            and commission_currency != currency
        ):
            commission_reason = common_reason
            if commission_currency not in allowed_currencies:
                commission_reason = commission_reason or (
                    f"currency {commission_currency or 'UNKNOWN'} is not permitted"
                )
            append_event(
                event_key=f"trade:{row['source_key']}:commission",
                event_date=row["trade_date"],
                settle_date=row["settle_date"],
                source_type="trade_commission",
                source_key=row["source_key"],
                conid=conid,
                currency=commission_currency or "UNKNOWN",
                amount=commission,
                included=commission_reason is None,
                external_flow=False,
                classification="commission",
                description=f"Commission for {symbol or conid or 'trade'}",
                exclusion_reason=commission_reason,
            )

    return events


def _cash_balances_for_dates(
    events: list[dict[str, Any]],
    dates: list[str],
    fx: _FxRates,
    generated_at: str,
    *,
    opening_cash: dict[str, float] | None = None,
    allowed_cash_currencies: Iterable[str] = ALLOWED_DERIVED_CASH_CURRENCIES,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    included = sorted(
        (
            event
            for event in events
            if event["included"] and event["source_type"] != "opening_balance"
        ),
        key=lambda event: (event["event_date"], event["event_key"]),
    )
    running = dict(opening_cash or OPENING_CASH)
    allowed_currencies = {
        str(value).strip().upper() for value in allowed_cash_currencies
    }
    balances: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    event_index = 0
    previous_date: str | None = None
    for snapshot_date in sorted(dates):
        activity = {currency: 0.0 for currency in allowed_currencies}
        while event_index < len(included) and included[event_index]["event_date"] <= snapshot_date:
            event = included[event_index]
            if previous_date is None or event["event_date"] > previous_date:
                activity[event["currency"]] += event["amount"]
            event_index += 1
        total_base = 0.0
        for currency in sorted(allowed_currencies):
            opening = running[currency]
            closing = opening + activity[currency]
            rate = fx.rate(currency, snapshot_date)
            if rate is None:
                raise RuntimeError(
                    f"No {currency} FX rate is available for cash balance on {snapshot_date}"
                )
            base_value = closing * rate
            balances.append(
                {
                    "snapshot_date": snapshot_date,
                    "currency": currency,
                    "opening_balance": opening,
                    "activity": activity[currency],
                    "closing_balance": closing,
                    "fx_rate_to_base": rate,
                    "base_value": base_value,
                    "generated_at": generated_at,
                }
            )
            running[currency] = closing
            total_base += base_value
        totals[snapshot_date] = total_base
        previous_date = snapshot_date
    return balances, totals


def _adjusted_component(value: Any, excluded_value: float) -> float | None:
    number = _number(value)
    return None if number is None else number - excluded_value


def refresh_filtered_portfolio(
    connection: Any,
    reference_path: Path,
    *,
    contract: dict[str, Any] | None = None,
    portfolio_id: str = "hong-kong-china",
    portfolio_name: str = "China & Hong Kong",
    source_account_id: str = "U16088606",
    history_start_date: str = "2025-01-20",
    generated_at: str | None = None,
    expected_reference_sha256: str | None = None,
) -> dict[str, Any]:
    """Refresh a typed filtered portfolio from its source IBKR account.

    The portfolio keeps every non-Taiwan position, removes source-account cash,
    and substitutes an audited HKD/USD ledger beginning with HKD 484,300 and
    USD 0 before activity on 2025-01-20. Its NAV is recomputed for every reliable
    source date. It is stored separately from broker accounts so consolidated
    account totals are not double counted.
    """

    if contract is not None:
        portfolio_id = str(contract["subject_id"])
        portfolio_name = str(contract["display_name"])
        source_account_id = str(contract["source_account_id"])
        history_start_date = str(contract["history_start_date"])
    position_policy = dict((contract or {}).get("position_filter") or {})
    cash_policy = dict((contract or {}).get("cash_policy") or {})
    excluded_countries = position_policy.get("excluded_countries") or TAIWAN_COUNTRY_NAMES
    excluded_exchanges = position_policy.get("excluded_exchanges") or TAIWAN_EXCHANGES
    excluded_currencies = position_policy.get("excluded_currencies") or ("TWD",)
    opening_cash = dict(cash_policy.get("opening_cash") or OPENING_CASH)
    allowed_cash_currencies = (
        cash_policy.get("allowed_currencies") or ALLOWED_DERIVED_CASH_CURRENCIES
    )
    include_actual_interest = bool(
        cash_policy.get("include_actual_posted_interest", True)
    )
    generated_at = generated_at or datetime.now(HKT).isoformat(timespec="seconds")
    reference_data, reference_manifest = load_reference_snapshot(
        reference_path, expected_sha256=expected_reference_sha256
    )
    instruments = {
        row["conid"]: row
        for row in connection.execute(
            """
            SELECT conid, symbol, description, asset_category, currency,
                   listing_exchange
            FROM instruments
            """
        )
    }
    account = connection.execute(
        "SELECT account_id, base_currency FROM accounts WHERE account_id = ?",
        (source_account_id,),
    ).fetchone()
    if account is None:
        raise RuntimeError(
            f"Derived portfolio source account does not exist: {source_account_id}"
        )
    base_currency = account["base_currency"] or "USD"
    if contract is not None and str(contract["base_currency"]).upper() != str(base_currency).upper():
        raise RuntimeError(
            "Filtered portfolio base currency must match its physical source account"
        )

    position_rows = connection.execute(
        """
        SELECT p.account_id, p.snapshot_date, p.conid, p.model_code, p.currency,
               p.quantity_text, p.cost_basis_text, p.mark_price_text,
               p.market_value_text, p.unrealized_pnl_text,
               i.symbol, i.description, i.asset_category,
               i.currency AS instrument_currency, i.listing_exchange
        FROM position_snapshots AS p
        LEFT JOIN instruments AS i ON i.conid = p.conid
        WHERE p.account_id = ? AND p.snapshot_date >= ?
        ORDER BY p.snapshot_date, p.conid, p.model_code
        """,
        (source_account_id, history_start_date),
    ).fetchall()
    nav_rows = connection.execute(
        """
        SELECT account_id, snapshot_date, currency, total_nav, cash, stock,
               options, futures, bonds, accruals
        FROM v_daily_nav
        WHERE account_id = ? AND snapshot_date >= ?
        ORDER BY snapshot_date, currency
        """,
        (source_account_id, history_start_date),
    ).fetchall()
    if not nav_rows:
        raise RuntimeError(
            f"Derived portfolio source account has no daily NAV: {source_account_id}"
        )
    fx = _FxRates(
        connection.execute(
            """
            SELECT rate_date, from_currency, to_currency, rate_text
            FROM fx_rates
            WHERE account_id = ?
            ORDER BY from_currency, to_currency, rate_date
            """,
            (source_account_id,),
        ),
        source_account_id,
        base_currency,
    )

    included_rows: list[Any] = []
    excluded_rows: list[tuple[Any, str, float]] = []
    source_counts: dict[str, int] = defaultdict(int)
    included_counts: dict[str, int] = defaultdict(int)
    excluded_totals: dict[str, float] = defaultdict(float)
    excluded_counts: dict[str, int] = defaultdict(int)
    excluded_assets: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    excluded_conids: set[str] = set()

    for row in position_rows:
        snapshot_date = row["snapshot_date"]
        source_counts[snapshot_date] += 1
        reason = portfolio_exclusion_reason(
            row,
            reference_data,
            excluded_countries=excluded_countries,
            excluded_exchanges=excluded_exchanges,
            excluded_currencies=excluded_currencies,
        )
        if reason is None:
            included_rows.append(row)
            included_counts[snapshot_date] += 1
            continue
        market_value = _number(row["market_value_text"])
        rate = fx.rate(row["currency"], snapshot_date)
        if market_value is None:
            market_value = 0.0
        if rate is None:
            raise RuntimeError(
                "No source-account FX rate is available for excluded position "
                f"{row['conid']} ({row['currency']}) on {snapshot_date}"
            )
        market_value_base = market_value * rate
        excluded_rows.append((row, reason, market_value_base))
        excluded_conids.add(row["conid"])
        excluded_totals[snapshot_date] += market_value_base
        excluded_counts[snapshot_date] += 1
        asset = str(row["asset_category"] or "").strip().upper()
        if asset == "STK":
            component = "stock"
        elif asset in {"OPT", "WAR"}:
            component = "options"
        elif asset in {"FUT", "FOP"}:
            component = "futures"
        elif asset in {"BOND", "BILL"}:
            component = "bonds"
        else:
            component = "other"
        excluded_assets[snapshot_date][component] += market_value_base

    reliable_nav_rows = [
        row for row in nav_rows if source_counts.get(row["snapshot_date"], 0) > 0
    ]
    latest_source_nav_date = max(row["snapshot_date"] for row in nav_rows)
    if not source_counts.get(latest_source_nav_date, 0):
        raise RuntimeError(
            "The latest source NAV has no matching position snapshot; refusing "
            "to publish a stale country-filtered portfolio"
        )
    cash_events = _build_cash_ledger(
        connection,
        source_account_id=source_account_id,
        portfolio_id=portfolio_id,
        history_start_date=history_start_date,
        base_currency=base_currency,
        fx=fx,
        instruments=instruments,
        reference_data=reference_data,
        generated_at=generated_at,
        opening_cash=opening_cash,
        allowed_cash_currencies=allowed_cash_currencies,
        excluded_countries=excluded_countries,
        excluded_exchanges=excluded_exchanges,
        excluded_currencies=excluded_currencies,
        include_actual_interest=include_actual_interest,
    )
    cash_balances, cash_totals = _cash_balances_for_dates(
        cash_events,
        [row["snapshot_date"] for row in reliable_nav_rows],
        fx,
        generated_at,
        opening_cash=opening_cash,
        allowed_cash_currencies=allowed_cash_currencies,
    )

    stored_definition = contract or {
        "type": "exclude_company_country_with_cash_ledger",
        "excluded_country": "Taiwan",
        "country_signals": [
            "reference country",
            "Taiwan listing exchange",
            "TWD instrument or position currency",
        ],
        "cash_treatment": (
            "replace source cash with an HKD/USD ledger; HKD 484300 and USD 0 "
            "opening balances before activity on 2025-01-20; no assumed interest"
        ),
        "opening_cash": OPENING_CASH,
        "allowed_cash_currencies": sorted(ALLOWED_DERIVED_CASH_CURRENCIES),
        "actual_posted_interest": "included when denominated in HKD or USD",
        "twd_treatment": "all TWD positions, cash activity and FX legs excluded",
        "source_account_id": source_account_id,
        "history_start_date": history_start_date,
    }
    postgres = is_postgres(connection)
    conflict_target = "tenant_id, portfolio_id" if postgres else "portfolio_id"

    with transaction(connection):
        existing = connection.execute(
            "SELECT created_at FROM derived_portfolios WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else generated_at
        connection.execute(
            f"""
            INSERT INTO derived_portfolios(
                portfolio_id, name, source_account_id, base_currency,
                exclusion_rule_json, calculation_version, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT({conflict_target}) DO UPDATE SET
                name = excluded.name,
                source_account_id = excluded.source_account_id,
                base_currency = excluded.base_currency,
                exclusion_rule_json = excluded.exclusion_rule_json,
                calculation_version = excluded.calculation_version,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                portfolio_id,
                portfolio_name,
                source_account_id,
                base_currency,
                json.dumps(stored_definition, sort_keys=True, separators=(",", ":")),
                CALCULATION_VERSION,
                created_at,
                generated_at,
            ),
        )
        for table in (
            "derived_portfolio_nav",
            "derived_portfolio_positions",
            "derived_portfolio_exclusions",
            "derived_portfolio_cash_ledger",
            "derived_portfolio_cash_balances",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE portfolio_id = ?", (portfolio_id,)
            )

        for row in included_rows:
            connection.execute(
                """
                INSERT INTO derived_portfolio_positions(
                    portfolio_id, source_account_id, snapshot_date, conid,
                    model_code, currency, quantity_text, cost_basis_text,
                    mark_price_text, market_value_text, unrealized_pnl_text,
                    generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    source_account_id,
                    row["snapshot_date"],
                    row["conid"],
                    row["model_code"],
                    row["currency"],
                    row["quantity_text"],
                    row["cost_basis_text"],
                    row["mark_price_text"],
                    row["market_value_text"],
                    row["unrealized_pnl_text"],
                    generated_at,
                ),
            )

        for row, reason, market_value_base in excluded_rows:
            connection.execute(
                """
                INSERT INTO derived_portfolio_exclusions(
                    portfolio_id, source_account_id, snapshot_date, conid,
                    model_code, symbol, description, currency, listing_exchange,
                    exclusion_reason, quantity_text, market_value_text,
                    market_value_base, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    source_account_id,
                    row["snapshot_date"],
                    row["conid"],
                    row["model_code"],
                    row["symbol"],
                    row["description"],
                    row["currency"],
                    row["listing_exchange"],
                    reason,
                    row["quantity_text"],
                    row["market_value_text"],
                    market_value_base,
                    generated_at,
                ),
            )

        for event in cash_events:
            connection.execute(
                """
                INSERT INTO derived_portfolio_cash_ledger(
                    portfolio_id, event_key, source_account_id, event_date,
                    settle_date, source_type, source_key, conid, currency,
                    amount, fx_rate_to_base, amount_base, included, external_flow,
                    classification, description, exclusion_reason, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    event["event_key"],
                    source_account_id,
                    event["event_date"],
                    event["settle_date"],
                    event["source_type"],
                    event["source_key"],
                    event["conid"],
                    event["currency"],
                    event["amount"],
                    event["fx_rate_to_base"],
                    event["amount_base"],
                    event["included"],
                    event["external_flow"],
                    event["classification"],
                    event["description"],
                    event["exclusion_reason"],
                    generated_at,
                ),
            )

        for balance in cash_balances:
            connection.execute(
                """
                INSERT INTO derived_portfolio_cash_balances(
                    portfolio_id, snapshot_date, currency, opening_balance,
                    activity, closing_balance, fx_rate_to_base, base_value,
                    generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    balance["snapshot_date"],
                    balance["currency"],
                    balance["opening_balance"],
                    balance["activity"],
                    balance["closing_balance"],
                    balance["fx_rate_to_base"],
                    balance["base_value"],
                    generated_at,
                ),
            )

        for row in reliable_nav_rows:
            snapshot_date = row["snapshot_date"]
            excluded = excluded_totals[snapshot_date]
            assets = excluded_assets[snapshot_date]
            source_total = _number(row["total_nav"])
            source_cash = _number(row["cash"])
            if source_total is None or source_cash is None:
                raise RuntimeError(
                    f"Source total NAV or cash is missing on {snapshot_date}; "
                    "cannot replace source cash safely"
                )
            derived_cash = cash_totals[snapshot_date]
            connection.execute(
                """
                INSERT INTO derived_portfolio_nav(
                    portfolio_id, snapshot_date, currency, total_nav, cash,
                    stock, options, futures, bonds, accruals, source_total_nav,
                    excluded_market_value_base, source_position_count,
                    included_position_count, excluded_position_count, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    snapshot_date,
                    row["currency"],
                    source_total - excluded - source_cash + derived_cash,
                    derived_cash,
                    _adjusted_component(row["stock"], assets["stock"]),
                    _adjusted_component(row["options"], assets["options"]),
                    _adjusted_component(row["futures"], assets["futures"]),
                    _adjusted_component(row["bonds"], assets["bonds"]),
                    _number(row["accruals"]),
                    source_total,
                    excluded,
                    source_counts[snapshot_date],
                    included_counts[snapshot_date],
                    excluded_counts[snapshot_date],
                    generated_at,
                ),
            )

    latest_date = max(row["snapshot_date"] for row in reliable_nav_rows)
    latest_balances = {
        row["currency"]: row["closing_balance"]
        for row in cash_balances
        if row["snapshot_date"] == latest_date
    }
    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio_name,
        "source_account_id": source_account_id,
        "history_start_date": history_start_date,
        "base_currency": base_currency,
        "calculation_version": CALCULATION_VERSION,
        "reference_input": reference_manifest,
        "nav_dates": len(reliable_nav_rows),
        "position_rows": len(included_rows),
        "exclusion_rows": len(excluded_rows),
        "excluded_conids": len(excluded_conids),
        "latest_date": latest_date,
        "latest_excluded_positions": excluded_counts[latest_date],
        "latest_excluded_market_value_base": excluded_totals[latest_date],
        "cash_ledger_events": len(cash_events),
        "included_cash_events": sum(event["included"] for event in cash_events),
        "latest_cash_balances": latest_balances,
        "latest_cash_base": cash_totals[latest_date],
        "generated_at": generated_at,
    }


def refresh_hong_kong_china_portfolio(
    connection: Any,
    reference_path: Path,
    *,
    portfolio_id: str = "hong-kong-china",
    portfolio_name: str = "China & Hong Kong",
    source_account_id: str = "U16088606",
    history_start_date: str = "2025-01-20",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper for the approved reference portfolio."""

    return refresh_filtered_portfolio(
        connection,
        reference_path,
        portfolio_id=portfolio_id,
        portfolio_name=portfolio_name,
        source_account_id=source_account_id,
        history_start_date=history_start_date,
        generated_at=generated_at,
    )


def _refresh_alpha_world_portfolio(
    connection: Any,
    *,
    portfolio_id: str,
    portfolio_name: str,
    normalization_date: str,
    primary_component_id: str,
    primary_component_name: str,
    primary_component_type: str,
    primary_source_kind: str,
    secondary_account_id: str,
    metadata_source_account_id: str,
    constant_cash: float,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Refresh one fixed-unit Alpha World composite from normalized sleeves."""

    generated_at = generated_at or datetime.now(HKT).isoformat(timespec="seconds")
    secondary_account = connection.execute(
        "SELECT account_id, base_currency FROM accounts WHERE account_id = ?",
        (secondary_account_id,),
    ).fetchone()
    if secondary_account is None:
        raise RuntimeError(
            f"{portfolio_name} account sleeve does not exist: {secondary_account_id}"
        )
    if primary_source_kind == "derived_portfolio":
        primary_definition = connection.execute(
            """
            SELECT base_currency
            FROM derived_portfolios
            WHERE portfolio_id = ? AND status = 'active'
            """,
            (primary_component_id,),
        ).fetchone()
        if primary_definition is None:
            raise RuntimeError(
                f"{portfolio_name} portfolio sleeve does not exist: {primary_component_id}"
            )
        base_currency = str(primary_definition["base_currency"] or "USD").upper()
        primary_rows = connection.execute(
            """
            SELECT snapshot_date, total_nav
            FROM derived_portfolio_nav
            WHERE portfolio_id = ? AND snapshot_date >= ? AND currency = ?
            ORDER BY snapshot_date
            """,
            (primary_component_id, normalization_date, base_currency),
        ).fetchall()
    elif primary_source_kind == "account":
        primary_account = connection.execute(
            "SELECT account_id, base_currency FROM accounts WHERE account_id = ?",
            (primary_component_id,),
        ).fetchone()
        if primary_account is None:
            raise RuntimeError(
                f"{portfolio_name} account sleeve does not exist: {primary_component_id}"
            )
        base_currency = str(primary_account["base_currency"] or "USD").upper()
        primary_rows = connection.execute(
            """
            SELECT snapshot_date, total_nav
            FROM v_daily_nav
            WHERE account_id = ? AND snapshot_date >= ? AND currency = ?
            ORDER BY snapshot_date
            """,
            (primary_component_id, normalization_date, base_currency),
        ).fetchall()
    else:
        raise ValueError(f"Unsupported primary source kind: {primary_source_kind}")

    secondary_currency = str(
        secondary_account["base_currency"] or base_currency
    ).upper()
    secondary_rows = connection.execute(
        """
        SELECT snapshot_date, total_nav
        FROM v_daily_nav
        WHERE account_id = ? AND snapshot_date >= ? AND currency = ?
        ORDER BY snapshot_date
        """,
        (secondary_account_id, normalization_date, secondary_currency),
    ).fetchall()
    primary_values = {
        row["snapshot_date"]: _number(row["total_nav"])
        for row in primary_rows
        if _number(row["total_nav"]) is not None
    }
    secondary_values = {
        row["snapshot_date"]: _number(row["total_nav"])
        for row in secondary_rows
        if _number(row["total_nav"]) is not None
    }
    target_dates = sorted(set(primary_values).intersection(secondary_values))
    target_dates = [value for value in target_dates if value >= normalization_date]
    if normalization_date not in target_dates:
        raise RuntimeError(
            f"{portfolio_name} requires both portfolio NAV inputs on the common "
            f"normalization date {normalization_date}"
        )

    market_series: dict[str, list[tuple[str, float]]] = {}
    market_currency: dict[str, str] = {}
    for instrument_id in ("hang-seng-price-index", "spy-settlement"):
        rows = connection.execute(
            """
            SELECT price_date, price, currency
            FROM market_data_prices
            WHERE instrument_id = ?
            ORDER BY price_date
            """,
            (instrument_id,),
        ).fetchall()
        series = [
            (row["price_date"], float(row["price"]))
            for row in rows
            if _number(row["price"]) is not None
        ]
        if not series:
            raise RuntimeError(f"No market prices are stored for {instrument_id}")
        market_series[instrument_id] = series
        market_currency[instrument_id] = str(rows[-1]["currency"]).upper()

    fx = _CrossFxRates(
        connection.execute(
            """
            SELECT account_id, rate_date, from_currency, to_currency, rate_text
            FROM fx_rates
            ORDER BY account_id, from_currency, to_currency, rate_date
            """
        )
    )
    coefficients = {
        primary_component_id: 0.91,
        secondary_account_id: 0.45,
        "hang-seng-price-index": -0.49,
        "spy-settlement": -0.45,
    }
    component_names = {
        primary_component_id: primary_component_name,
        secondary_account_id: f"IBKR account {secondary_account_id}",
        "hang-seng-price-index": "Hang Seng Index price index",
        "spy-settlement": "SPY settlement price (unadjusted close)",
        "constant-cash": "Non-interest-bearing cash",
    }
    component_types = {
        primary_component_id: primary_component_type,
        secondary_account_id: "account",
        "hang-seng-price-index": "index",
        "spy-settlement": "etf",
        "constant-cash": "cash",
    }

    def inputs_for(day: str) -> dict[str, tuple[float, str, str]]:
        primary = float(primary_values[day])
        secondary = float(secondary_values[day])
        hsi, hsi_date = _value_at_or_before(
            market_series["hang-seng-price-index"], day, maximum_age_days=7
        )
        spy, spy_date = _value_at_or_before(
            market_series["spy-settlement"], day, maximum_age_days=7
        )
        return {
            primary_component_id: (primary, base_currency, day),
            secondary_account_id: (secondary, secondary_currency, day),
            "hang-seng-price-index": (
                hsi,
                market_currency["hang-seng-price-index"],
                hsi_date,
            ),
            "spy-settlement": (
                spy,
                market_currency["spy-settlement"],
                spy_date,
            ),
        }

    base_inputs = inputs_for(normalization_date)
    base_values: dict[str, float] = {}
    for component_id, (source_value, source_currency, _) in base_inputs.items():
        rate, _ = fx.rate(
            source_currency,
            base_currency,
            normalization_date,
            preferred_accounts=(primary_component_id, secondary_account_id),
        )
        base_value = source_value * rate
        if base_value == 0:
            raise RuntimeError(f"Zero normalization value for {component_id}")
        base_values[component_id] = base_value

    calculated: list[dict[str, Any]] = []
    for day in target_dates:
        components: list[dict[str, Any]] = []
        for component_id, (source_value, source_currency, observation_date) in inputs_for(day).items():
            rate, _ = fx.rate(
                source_currency,
                base_currency,
                day,
                preferred_accounts=(primary_component_id, secondary_account_id),
            )
            base_value = source_value * rate
            normalized_value = 100.0 * base_value / base_values[component_id]
            coefficient = coefficients[component_id]
            components.append(
                {
                    "component_id": component_id,
                    "component_name": component_names[component_id],
                    "component_type": component_types[component_id],
                    "direction": "long" if coefficient > 0 else "short",
                    "coefficient": coefficient,
                    "source_currency": source_currency,
                    "source_value": source_value,
                    "fx_rate_to_base": rate,
                    "base_value": base_value,
                    "normalized_value": normalized_value,
                    "contribution": coefficient * normalized_value,
                    "observation_date": observation_date,
                }
            )
        components.append(
            {
                "component_id": "constant-cash",
                "component_name": component_names["constant-cash"],
                "component_type": "cash",
                "direction": "cash",
                "coefficient": constant_cash,
                "source_currency": base_currency,
                "source_value": constant_cash,
                "fx_rate_to_base": 1.0,
                "base_value": constant_cash,
                "normalized_value": 1.0,
                "contribution": constant_cash,
                "observation_date": day,
            }
        )
        calculated.append(
            {
                "date": day,
                "total_nav": sum(item["contribution"] for item in components),
                "components": components,
            }
        )

    definition = {
        "type": "fixed_unit_composite",
        "formula": (
            f"58 + 0.91*N({primary_component_name}) + 0.45*N({secondary_account_id}) "
            "- 0.49*N(Hang Seng Index) - 0.45*N(SPY)"
        ),
        "normalization_date": normalization_date,
        "normalization_base": 100,
        "base_currency": base_currency,
        "constant_cash": constant_cash,
        "cash_interest": 0,
        "rebalancing": "fixed units; no daily rebalancing",
        "calendar_alignment": "last market observation on or before each common IBKR NAV date",
        "hang_seng_series": "Yahoo ^HSI unadjusted price-index close",
        "spy_series": "Yahoo SPY unadjusted close used as settlement-price proxy",
        "coefficients": coefficients,
        "primary_source_kind": primary_source_kind,
    }
    postgres = is_postgres(connection)
    conflict_target = "tenant_id, portfolio_id" if postgres else "portfolio_id"
    with transaction(connection):
        existing = connection.execute(
            "SELECT created_at FROM derived_portfolios WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else generated_at
        connection.execute(
            f"""
            INSERT INTO derived_portfolios(
                portfolio_id, name, source_account_id, base_currency,
                exclusion_rule_json, calculation_version, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT({conflict_target}) DO UPDATE SET
                name = excluded.name,
                source_account_id = excluded.source_account_id,
                base_currency = excluded.base_currency,
                exclusion_rule_json = excluded.exclusion_rule_json,
                calculation_version = excluded.calculation_version,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                portfolio_id,
                portfolio_name,
                metadata_source_account_id,
                base_currency,
                json.dumps(definition, sort_keys=True, separators=(",", ":")),
                ALPHA_WORLD_CALCULATION_VERSION,
                created_at,
                generated_at,
            ),
        )
        connection.execute(
            "DELETE FROM synthetic_portfolio_components WHERE portfolio_id = ?",
            (portfolio_id,),
        )
        for table in (
            "derived_portfolio_nav",
            "derived_portfolio_positions",
            "derived_portfolio_exclusions",
            "derived_portfolio_cash_ledger",
            "derived_portfolio_cash_balances",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE portfolio_id = ?", (portfolio_id,)
            )
        for row in calculated:
            connection.execute(
                """
                INSERT INTO derived_portfolio_nav(
                    portfolio_id, snapshot_date, currency, total_nav, cash,
                    stock, options, futures, bonds, accruals, source_total_nav,
                    excluded_market_value_base, source_position_count,
                    included_position_count, excluded_position_count, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 0, 5, 5, 0, ?)
                """,
                (
                    portfolio_id,
                    row["date"],
                    base_currency,
                    row["total_nav"],
                    constant_cash,
                    row["total_nav"] - constant_cash,
                    generated_at,
                ),
            )
            for component in row["components"]:
                connection.execute(
                    """
                    INSERT INTO synthetic_portfolio_components(
                        portfolio_id, snapshot_date, component_id, component_name,
                        component_type, direction, coefficient, source_currency,
                        base_currency, source_value, fx_rate_to_base, base_value,
                        normalized_value, contribution, observation_date, generated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        portfolio_id,
                        row["date"],
                        component["component_id"],
                        component["component_name"],
                        component["component_type"],
                        component["direction"],
                        component["coefficient"],
                        component["source_currency"],
                        base_currency,
                        component["source_value"],
                        component["fx_rate_to_base"],
                        component["base_value"],
                        component["normalized_value"],
                        component["contribution"],
                        component["observation_date"],
                        generated_at,
                    ),
                )

    latest = calculated[-1]
    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio_name,
        "calculation_version": ALPHA_WORLD_CALCULATION_VERSION,
        "normalization_date": normalization_date,
        "base_currency": base_currency,
        "nav_dates": len(calculated),
        "first_date": calculated[0]["date"],
        "latest_date": latest["date"],
        "latest_nav": latest["total_nav"],
        "latest_components": {
            component["component_id"]: {
                "observation_date": component["observation_date"],
                "normalized_value": component["normalized_value"],
                "contribution": component["contribution"],
            }
            for component in latest["components"]
        },
        "generated_at": generated_at,
    }


def _flow_adjusted_nav(
    connection: Any, subject_id: str, values: dict[str, float], *, subject_type: str
) -> dict[str, float]:
    """Return a NAV path with external deposits and withdrawals removed.

    The first date keeps its NAV. Each later date grows by the daily Modified
    Dietz return the website uses, r = (V1 - V0 - F) / (V0 + F / 2), with F the
    net external flow dated after the previous NAV and up to this one.
    """

    if len(values) < 2:
        return dict(values)
    from .provisional_benchmark import _external_flows

    flows = _external_flows(connection, subject_id, subject_type=subject_type)
    dates = sorted(values)
    # Scale the raw NAV by a factor that only changes on flow days, so a sleeve
    # without external flows keeps exactly its raw NAV.
    factor = 1.0
    adjusted = {dates[0]: values[dates[0]]}
    for previous, current in zip(dates, dates[1:]):
        flow = sum(amount for day, amount in flows.items() if previous < day <= current)
        if flow:
            denominator = values[previous] + 0.5 * flow
            if denominator <= 0 or values[current] <= 0:
                raise ValueError(
                    f"Cash-flow-adjusted return is invalid for {subject_id} on {current}"
                )
            daily_return = (values[current] - values[previous] - flow) / denominator
            factor = adjusted[previous] * (1.0 + daily_return) / values[current]
        adjusted[current] = values[current] * factor
    return adjusted


def refresh_fixed_unit_composite(
    connection: Any,
    contract: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate one canonical fixed-unit Studio contract.

    Account and portfolio sleeves define the common daily calendar. Benchmark
    observations may carry only up to the approved number of days. Every sleeve
    is converted to the reporting currency before it is normalized to 100.
    """

    generated_at = generated_at or datetime.now(HKT).isoformat(timespec="seconds")
    portfolio_id = str(contract["subject_id"])
    reference_hkd = portfolio_id in ('alpha-world-ms', 'alpha-world-t')
    reference_label = 'MS' if portfolio_id == 'alpha-world-ms' else 'T'
    if reference_hkd:
        from .subject_onboarding import validate_reference_runtime_contract, configuration_sha256
        validate_reference_runtime_contract(portfolio_id,contract,configuration_sha256(contract))
    portfolio_name = str(contract["display_name"])
    base_currency = str(contract["base_currency"]).upper()
    normalization_date = str(contract["normalization_date"])
    constant_cash = float(contract["constant_cash"])
    maximum_age_days = int(contract.get("market_carry_days", 7))
    components = [dict(item) for item in contract["components"]]
    nav_series: dict[str, dict[str, float]] = {}
    # Account and portfolio sleeves are normalized on a deposit-neutral NAV so
    # external cash flows do not show up as sleeve performance.
    adjusted_series: dict[str, dict[str, float]] = {}
    market_series: dict[str, list[tuple[str, float]]] = {}
    actual_currencies: dict[str, str] = {}
    calendar_sets: list[set[str]] = []
    preferred_accounts: list[str] = []

    for component in components:
        component_id = str(component["component_id"])
        component_type = str(component["component_type"])
        if component_type == "account":
            account = connection.execute(
                "SELECT account_id, base_currency FROM accounts WHERE account_id = ?",
                (component_id,),
            ).fetchone()
            if account is None:
                raise RuntimeError(f"Composite account sleeve does not exist: {component_id}")
            currency = str(account["base_currency"] or component["source_currency"]).upper()
            rows = connection.execute(
                """
                SELECT snapshot_date, total_nav FROM v_daily_nav
                WHERE account_id = ? AND snapshot_date >= ? AND currency = ?
                ORDER BY snapshot_date
                """,
                (component_id, normalization_date, currency),
            ).fetchall()
            if portfolio_id == 'alpha-world-t' and any(
                    _number(row['total_nav']) is None or float(row['total_nav']) <= 0 for row in rows):
                raise ValueError('T source NAV contains invalid observations; do not omit source dates')
            values = {
                str(row["snapshot_date"]): float(row["total_nav"])
                for row in rows
                if _number(row["total_nav"]) is not None
            }
            preferred_accounts.append(component_id)
            nav_series[component_id] = values
            adjusted_series[component_id] = _flow_adjusted_nav(connection, component_id, values, subject_type="account")
            calendar_sets.append(set(values))
            actual_currencies[component_id] = currency
        elif component_type == "portfolio":
            portfolio = connection.execute(
                """
                SELECT base_currency FROM derived_portfolios
                WHERE portfolio_id = ? AND status = 'active'
                """,
                (component_id,),
            ).fetchone()
            if portfolio is None:
                raise RuntimeError(f"Composite portfolio sleeve does not exist: {component_id}")
            currency = str(portfolio["base_currency"] or component["source_currency"]).upper()
            rows = connection.execute(
                """
                SELECT snapshot_date, total_nav FROM derived_portfolio_nav
                WHERE portfolio_id = ? AND snapshot_date >= ? AND currency = ?
                ORDER BY snapshot_date
                """,
                (component_id, normalization_date, currency),
            ).fetchall()
            values = {
                str(row["snapshot_date"]): float(row["total_nav"])
                for row in rows
                if _number(row["total_nav"]) is not None
            }
            nav_series[component_id] = values
            adjusted_series[component_id] = _flow_adjusted_nav(connection, component_id, values, subject_type="filtered_portfolio")
            calendar_sets.append(set(values))
            actual_currencies[component_id] = currency
        elif component_type == "benchmark":
            rows = connection.execute(
                """
                SELECT price_date, price, currency FROM market_data_prices
                WHERE instrument_id = ? ORDER BY price_date
                """,
                (component_id,),
            ).fetchall()
            values = [
                (str(row["price_date"]), float(row["price"]))
                for row in rows
                if _number(row["price"]) is not None
            ]
            if portfolio_id == 'alpha-world-t' and any(
                    _number(row['price']) is None or str(row['currency']).upper() != str(component['source_currency']).upper()
                    for row in rows):
                raise ValueError('T market prices contain invalid values or mixed currencies')
            if not values:
                raise RuntimeError(f"No market prices are stored for {component_id}")
            market_series[component_id] = values
            actual_currencies[component_id] = str(rows[-1]["currency"]).upper()
        else:
            raise RuntimeError(f"Unsupported composite sleeve: {component_type}")
        declared_currency = str(component["source_currency"]).upper()
        if actual_currencies[component_id] != declared_currency:
            raise RuntimeError(
                f"Currency drift for {component_id}: expected {declared_currency}, "
                f"received {actual_currencies[component_id]}"
            )

    if not calendar_sets:
        raise RuntimeError("A composite requires an account or portfolio calendar sleeve")
    target_dates = sorted(set.intersection(*calendar_sets))
    target_dates = [value for value in target_dates if value >= normalization_date]
    if normalization_date not in target_dates:
        raise RuntimeError(
            f"Every account and portfolio sleeve must have NAV on {normalization_date}"
        )
    fx = _CrossFxRates(
        connection.execute(
            """
            SELECT account_id, rate_date, from_currency, to_currency, rate_text
            FROM fx_rates ORDER BY account_id, from_currency, to_currency, rate_date
            """
        )
    )

    def inputs_for(day: str) -> dict[str, tuple[float, str, str]]:
        values: dict[str, tuple[float, str, str]] = {}
        for component in components:
            component_id = str(component["component_id"])
            if component["component_type"] == "benchmark":
                value, observed = _value_at_or_before(
                    market_series[component_id], day, maximum_age_days=maximum_age_days
                )
            else:
                value, observed = nav_series[component_id][day], day
            values[component_id] = (
                float(value), actual_currencies[component_id], observed
            )
        return values

    def performance_value(component_id: str, value: float, day: str) -> float:
        adjusted = adjusted_series.get(component_id)
        return value if adjusted is None else adjusted[day]

    base_values: dict[str, float] = {}
    for component_id, (value, currency, _) in inputs_for(normalization_date).items():
        value = performance_value(component_id, value, normalization_date)
        rate, fx_date = fx.rate(
            currency, base_currency, normalization_date,
            preferred_accounts=preferred_accounts,
        )
        if reference_hkd and (datetime.fromisoformat(normalization_date)-datetime.fromisoformat(fx_date)).days>4:
            raise ValueError(f'{reference_label} inception FX exceeds its validated four-calendar-day carry')
        converted = value * rate
        if converted == 0 or (reference_hkd and (not math.isfinite(converted) or converted<=0)):
            raise RuntimeError(f"Zero normalization value for {component_id}")
        base_values[component_id] = converted

    calculated: list[dict[str, Any]] = []
    definitions = {str(row["component_id"]): row for row in components}
    for day in target_dates:
        rows: list[dict[str, Any]] = []
        for component_id, (value, currency, observed) in inputs_for(day).items():
            component = definitions[component_id]
            rate, fx_date = fx.rate(
                currency, base_currency, day, preferred_accounts=preferred_accounts
            )
            if reference_hkd and (datetime.fromisoformat(day)-datetime.fromisoformat(fx_date)).days>4:
                raise ValueError(f'{reference_label} FX exceeds its validated four-calendar-day carry')
            converted = value * rate
            if reference_hkd and (not math.isfinite(converted) or converted<=0):
                raise ValueError(f'{reference_label} requires positive finite converted source values')
            normalized = (
                100.0 * performance_value(component_id, value, day) * rate
                / base_values[component_id]
            )
            coefficient = float(component["coefficient"])
            rows.append(
                {
                    "component_id": component_id,
                    "component_name": component["component_name"],
                    "component_type": component["component_type"],
                    "direction": component["direction"],
                    "coefficient": coefficient,
                    "source_currency": currency,
                    "source_value": value,
                    "fx_rate_to_base": rate,
                    "base_value": converted,
                    "normalized_value": normalized,
                    "contribution": coefficient * normalized,
                    "observation_date": observed,
                }
            )
        rows.append(
            {
                "component_id": "constant-cash",
                "component_name": "Non-interest-bearing cash",
                "component_type": "cash",
                "direction": "cash",
                "coefficient": constant_cash,
                "source_currency": base_currency,
                "source_value": constant_cash,
                "fx_rate_to_base": 1.0,
                "base_value": constant_cash,
                "normalized_value": 1.0,
                "contribution": constant_cash,
                "observation_date": day,
            }
        )
        calculated.append(
            {"date": day, "total_nav": sum(row["contribution"] for row in rows), "components": rows}
        )

    if reference_hkd and any(not math.isfinite(r['total_nav']) or r['total_nav']<=0 for r in calculated):
        raise ValueError(f'{reference_label} requires positive finite derived NAV values')
    postgres = is_postgres(connection)
    conflict_target = "tenant_id, portfolio_id" if postgres else "portfolio_id"
    metadata_source = next(
        (str(row["component_id"]) for row in components if row["component_type"] == "account"),
        str(components[0]["component_id"]),
    )
    with transaction(connection):
        existing = connection.execute(
            "SELECT created_at FROM derived_portfolios WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else generated_at
        connection.execute(
            f"""
            INSERT INTO derived_portfolios(
                portfolio_id, name, source_account_id, base_currency,
                exclusion_rule_json, calculation_version, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT({conflict_target}) DO UPDATE SET
                name = excluded.name, source_account_id = excluded.source_account_id,
                base_currency = excluded.base_currency,
                exclusion_rule_json = excluded.exclusion_rule_json,
                calculation_version = excluded.calculation_version,
                status = excluded.status, updated_at = excluded.updated_at
            """,
            (
                portfolio_id, portfolio_name, metadata_source, base_currency,
                json.dumps(contract, sort_keys=True, separators=(",", ":")),
                "fixed-unit-composite-studio-v1", created_at, generated_at,
            ),
        )
        connection.execute(
            "DELETE FROM synthetic_portfolio_components WHERE portfolio_id = ?",
            (portfolio_id,),
        )
        for table in (
            "derived_portfolio_nav", "derived_portfolio_positions",
            "derived_portfolio_exclusions", "derived_portfolio_cash_ledger",
            "derived_portfolio_cash_balances",
        ):
            if table == 'derived_portfolio_nav':
                # Retain historical currency versions for the coordinated
                # cutover/rollback; only this recalculable currency is replaced.
                connection.execute('DELETE FROM derived_portfolio_nav WHERE portfolio_id = ? AND currency = ?',
                    (portfolio_id,base_currency))
            else:
                connection.execute(f"DELETE FROM {table} WHERE portfolio_id = ?", (portfolio_id,))
        for row in calculated:
            connection.execute(
                """
                INSERT INTO derived_portfolio_nav(
                    portfolio_id, snapshot_date, currency, total_nav, cash,
                    stock, options, futures, bonds, accruals, source_total_nav,
                    excluded_market_value_base, source_position_count,
                    included_position_count, excluded_position_count, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 0, ?, ?, 0, ?)
                """,
                (
                    portfolio_id, row["date"], base_currency, row["total_nav"],
                    constant_cash, row["total_nav"] - constant_cash,
                    len(components) + 1, len(components) + 1, generated_at,
                ),
            )
            for component in row["components"]:
                connection.execute(
                    """
                    INSERT INTO synthetic_portfolio_components(
                        portfolio_id, snapshot_date, component_id, component_name,
                        component_type, direction, coefficient, source_currency,
                        base_currency, source_value, fx_rate_to_base, base_value,
                        normalized_value, contribution, observation_date, generated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        portfolio_id, row["date"], component["component_id"],
                        component["component_name"], component["component_type"],
                        component["direction"], component["coefficient"],
                        component["source_currency"], base_currency,
                        component["source_value"], component["fx_rate_to_base"],
                        component["base_value"], component["normalized_value"],
                        component["contribution"], component["observation_date"], generated_at,
                    ),
                )
    latest = calculated[-1]
    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio_name,
        "calculation_version": "fixed-unit-composite-studio-v1",
        "normalization_date": normalization_date,
        "base_currency": base_currency,
        "nav_dates": len(calculated),
        "first_date": calculated[0]["date"],
        "latest_date": latest["date"],
        "latest_nav": latest["total_nav"],
        "generated_at": generated_at,
    }


def refresh_alpha_world_ms_portfolio(
    connection: Any,
    *,
    portfolio_id: str = "alpha-world-ms",
    portfolio_name: str = "Alpha World MS",
    normalization_date: str = "2025-01-20",
    hong_kong_china_portfolio_id: str = "hong-kong-china",
    source_account_id: str = "U16088571",
    constant_cash: float = 58.0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Use the same approved HKD interpreter as the scheduled typed worker."""
    from .subject_onboarding import approved_reference_definitions
    contract = approved_reference_definitions()['alpha-world-ms']
    if (portfolio_id,portfolio_name,normalization_date,hong_kong_china_portfolio_id,
            source_account_id,constant_cash) != ('alpha-world-ms','Alpha World MS',
            '2025-01-20','hong-kong-china','U16088571',58.0):
        raise ValueError('MS overrides require a separately approved typed contract')
    return refresh_fixed_unit_composite(connection,contract,generated_at=generated_at)


def refresh_alpha_world_t_portfolio(
    connection: Any,
    *,
    portfolio_id: str = "alpha-world-t",
    portfolio_name: str = "Alpha World T",
    normalization_date: str = "2025-01-20",
    primary_account_id: str = "U16088606",
    secondary_account_id: str = "U16088571",
    constant_cash: float = 58.0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate the approved HKD contract, retaining the full Greater China NAV."""
    from .subject_onboarding import approved_reference_definitions
    contract = approved_reference_definitions()['alpha-world-t']
    if (portfolio_id,portfolio_name,normalization_date,primary_account_id,
            secondary_account_id,constant_cash) != ('alpha-world-t','Alpha World T',
            '2025-01-20','U16088606','U16088571',58.0):
        raise ValueError('T overrides require a separately approved typed contract')
    return refresh_fixed_unit_composite(connection,contract,generated_at=generated_at)
