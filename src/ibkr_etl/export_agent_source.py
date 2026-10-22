"""The export agent's reads of the platform database, as the signed-in website user.

Rows come from ``subject_exports`` (the Exports tab's own reads). The checks'
book reads are separate queries over the same tenant-scoped subject views:
the account's value on a day, its positions and cash valued with the dated
FX history the monthly reports use, and its cash balances and activity.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, ContextManager, Mapping

from .export_agent import Books, planner_facts
from .fx_selection import InvalidFxObservation, latest_fx_quote
from .subject_exports import subject_export


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


class PlatformExportSource:
    def __init__(self, *, database: Callable[[], ContextManager[Any]], tenant_id: str, subject_id: str,
                 access: Mapping[str, Any], cash_filter: Callable, trade_filter: Callable) -> None:
        self.database = database
        self.tenant_id = tenant_id
        self.subject_id = subject_id
        self.access = access
        self.cash_filter = cash_filter
        self.trade_filter = trade_filter
        self.subject_type = str(access.get("subject_type") or "physical_account")
        self.currency = str(access["reporting_currency"]).upper()

    @property
    def display_name(self) -> str:
        return str(self.access.get("display_name") or self.subject_id)

    def facts(self, today: str) -> dict[str, Any]:
        with self.database() as connection:
            latest = connection.execute(
                "SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id = %s AND subject_id = %s "
                "AND currency = %s", (self.tenant_id, self.subject_id, self.currency)).fetchone()["latest"]
            types = connection.execute(
                "SELECT DISTINCT transaction_type FROM subject_cash_activity_series WHERE tenant_id = %s "
                "AND subject_id = %s AND included ORDER BY transaction_type LIMIT 60",
                (self.tenant_id, self.subject_id)).fetchall()
            currencies = connection.execute(
                "SELECT DISTINCT currency FROM subject_cash_activity_series WHERE tenant_id = %s AND subject_id = %s "
                "UNION SELECT DISTINCT currency FROM subject_position_series WHERE tenant_id = %s AND subject_id = %s "
                "ORDER BY 1 LIMIT 40", (self.tenant_id, self.subject_id, self.tenant_id, self.subject_id)).fetchall()
        return planner_facts(
            account_id=self.subject_id, display_name=self.display_name, subject_type=self.subject_type,
            reporting_currency=self.currency, history_start_date=str(self.access["history_start_date"]),
            latest_date=str(latest) if latest else None,
            transaction_types=[str(r["transaction_type"]) for r in types if r["transaction_type"]],
            currencies=[str(r["currency"]) for r in currencies if r["currency"]], today=today)

    def fetch(self, item: Mapping[str, Any]):
        with self.database() as connection:
            columns, rows, _, _ = subject_export(
                connection, tenant_id=self.tenant_id, subject_id=self.subject_id, access=self.access,
                dataset=item["dataset"], requested_as_of=item["to_date"], from_date=item.get("from_date"),
                symbol=item.get("symbol"), side=item.get("side"), cash_filter=self.cash_filter,
                trade_filter=self.trade_filter)
        return columns, rows

    # Book reads for the checks ----------------------------------------------------------------

    def books(self) -> Books:
        return Books(nav_on=self.nav_on, holdings_value=self.holdings_value, cash_roll=self.cash_roll)

    def nav_on(self, day: str) -> Decimal | None:
        with self.database() as connection:
            row = connection.execute(
                "SELECT total_text FROM subject_nav_series WHERE tenant_id = %s AND subject_id = %s AND currency = %s "
                "AND snapshot_date = %s", (self.tenant_id, self.subject_id, self.currency, day)).fetchone()
        return _decimal(row["total_text"]) if row else None

    def _fx_account(self, connection) -> str | None:
        row = connection.execute(
            "SELECT subject_type, source_account_id FROM platform_subjects WHERE tenant_id = %s AND subject_id = %s",
            (self.tenant_id, self.subject_id)).fetchone()
        if row is None:
            return None
        if row["source_account_id"]:
            return str(row["source_account_id"])
        if row["subject_type"] == "filtered_portfolio":
            definition = connection.execute(
                "SELECT configuration_json ->> 'source_account_id' AS source_account_id "
                "FROM subject_calculation_definitions WHERE tenant_id = %s AND subject_id = %s "
                "AND subject_type = 'filtered_portfolio' AND approval_status IN ('validated', 'approved') "
                "ORDER BY definition_version DESC, created_at DESC LIMIT 1", (self.tenant_id, self.subject_id)).fetchone()
            return str(definition["source_account_id"]) if definition and definition["source_account_id"] else None
        return self.subject_id

    def holdings_value(self, day: str) -> Mapping[str, Any]:
        """Positions and cash on ``day`` in the reporting currency, beside the account's value."""
        nav = self.nav_on(day)
        if nav is None:
            return {"reason": f"The books have no account value on {day} to compare with."}
        with self.database() as connection:
            positions = connection.execute(
                "SELECT currency, market_value_text FROM subject_position_series WHERE tenant_id = %s "
                "AND subject_id = %s AND snapshot_date = %s AND COALESCE(NULLIF(quantity_text, ''), '0')::numeric <> 0",
                (self.tenant_id, self.subject_id, day)).fetchall()
            cash = connection.execute(
                "SELECT currency, ending_cash_text FROM subject_cash_balance_series WHERE tenant_id = %s "
                "AND subject_id = %s AND snapshot_date = %s AND currency <> 'BASE_SUMMARY'",
                (self.tenant_id, self.subject_id, day)).fetchall()
            fx_account = self._fx_account(connection)
            fx_rows = [] if fx_account is None else connection.execute(
                "SELECT rate_date, from_currency, to_currency, rate_text FROM fx_rates WHERE account_id = %s "
                "AND rate_date <= %s ORDER BY from_currency, to_currency, rate_date", (fx_account, day)).fetchall()
        if not cash:
            return {"reason": f"The books have no cash balances on {day}."}
        history: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for row in fx_rows:
            history[(str(row["from_currency"]).upper(), str(row["to_currency"]).upper())].append(
                (str(row["rate_date"]), row["rate_text"]))

        def value(currency: str, number: Decimal) -> Decimal | None:
            native = currency.upper()
            if native == self.currency:
                return number
            try:
                quote = latest_fx_quote(history.get((native, self.currency), []), day)
                if quote is not None:
                    return number * Decimal(str(quote[0]))
                quote = latest_fx_quote(history.get((self.currency, native), []), day)
            except InvalidFxObservation:
                return None
            return None if quote is None else number / Decimal(str(quote[0]))

        totals = {"positions": Decimal(0), "cash": Decimal(0)}
        for kind, rows, column in (("positions", positions, "market_value_text"), ("cash", cash, "ending_cash_text")):
            for row in rows:
                number = _decimal(row[column])
                if number is None:
                    return {"reason": f"A {kind} value on {day} is not a number."}
                converted = value(str(row["currency"]), number)
                if converted is None:
                    return {"reason": f"There is no {row['currency']} to {self.currency} exchange rate on or before {day}."}
                totals[kind] += converted
        return {"nav": nav, "positions": totals["positions"], "cash": totals["cash"], "currency": self.currency,
                "date": day}

    def cash_roll(self, from_day: str, to_day: str) -> Mapping[str, Any]:
        """Opening and closing cash by currency, with the activity and trade cash between them."""
        before = (date.fromisoformat(from_day) - timedelta(days=1)).isoformat()
        with self.database() as connection:
            def balances(day: str) -> dict[str, Decimal] | None:
                latest = connection.execute(
                    "SELECT MAX(snapshot_date) AS d FROM subject_cash_balance_series WHERE tenant_id = %s "
                    "AND subject_id = %s AND snapshot_date <= %s", (self.tenant_id, self.subject_id, day)).fetchone()["d"]
                if latest is None:
                    return None
                rows = connection.execute(
                    "SELECT currency, ending_cash_text FROM subject_cash_balance_series WHERE tenant_id = %s "
                    "AND subject_id = %s AND snapshot_date = %s AND currency <> 'BASE_SUMMARY'",
                    (self.tenant_id, self.subject_id, latest)).fetchall()
                return {str(r["currency"]).upper(): _decimal(r["ending_cash_text"]) or Decimal(0) for r in rows}

            opening, closing = balances(before), balances(to_day)
            activity = connection.execute(
                "SELECT currency, amount_text FROM subject_cash_activity_series WHERE tenant_id = %s AND subject_id = %s "
                "AND included AND effective_date BETWEEN %s AND %s", (self.tenant_id, self.subject_id, from_day, to_day)
            ).fetchall()
            trades = connection.execute(
                "SELECT t.currency, t.net_cash_text FROM trades t JOIN accounts a ON a.account_id = t.account_id "
                "WHERE a.tenant_id = %s AND t.account_id = %s AND t.trade_date BETWEEN %s AND %s",
                (self.tenant_id, self.subject_id, from_day, to_day)).fetchall()
        if opening is None or closing is None:
            return {"reason": "The books have no cash balance before the start of this period."}
        result: dict[str, dict[str, Decimal]] = {}
        zero = {"opening": Decimal(0), "activity": Decimal(0), "trades": Decimal(0), "closing": Decimal(0)}
        for currency in set(opening) | set(closing):
            result[currency] = {**zero, "opening": opening.get(currency, Decimal(0)),
                                "closing": closing.get(currency, Decimal(0))}
        for rows, key, column in ((activity, "activity", "amount_text"), (trades, "trades", "net_cash_text")):
            for row in rows:
                currency = str(row["currency"] or "").upper()
                result.setdefault(currency, dict(zero))[key] += _decimal(row[column]) or Decimal(0)
        return result
