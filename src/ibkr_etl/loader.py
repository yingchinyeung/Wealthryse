"""Load a parsed Flex report into flex_rows and the normalized tables.

The optional legacy-key step (IBKR_FLEX_LEGACY_KEY_COMPAT) must not run while
account-sync imports the same accounts. With it, bronze (flex_rows) keeps the
parser's key while silver keeps the legacy key, so S6's replay will list those
rows as rekeyed.

The optional FX event step (IBKR_FX_EVENT_KEY, review item U8) needs the FX
step's migration (silver.fx_transactions.event_key). With it, an FX row IBKR
re-sends with restated amounts updates the row for the same FX event instead of
adding a second one. Without it, nothing here reads or writes event_key.

The Transfers section goes to silver.transfers (migration 084) as well as to
flex_rows, once that table exists; a database without it keeps Transfers in
flex_rows only, as before.
"""
from __future__ import annotations

import os
import sqlite3
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .parser import FlexRow, ParsedReport
from .util import canonical_json, decimal_text, parse_date_text, pick, sha256_text, utc_now_iso


# Off unless set: reuse the key of an existing event row that has identical raw
# IBKR attributes, so rows imported by another path are updated, not duplicated.
LEGACY_KEY_COMPAT_ENV = "IBKR_FLEX_LEGACY_KEY_COMPAT"
# Off unless set: one fx_transactions row per FX event (docs/MEDALLION_AS_KNOWN_ON.md).
FX_EVENT_KEY_ENV = "IBKR_FX_EVENT_KEY"
# An FX row's identity without the two amounts IBKR restates (proceeds, realised P&L).
FX_EVENT_FIELDS = ("account_id", "event_datetime", "report_date", "functional_currency", "fx_currency",
                   "activity_description", "model_code", "quantity_text", "cost_text")
FX_COLUMNS = ("account_id", "source_key", "event_datetime", "activity_description", "model_code",
              "report_date", "functional_currency", "fx_currency", "quantity_text", "proceeds_text",
              "cost_text", "realized_pnl_text", "row_sha256", "last_seen_run_id")


def legacy_key_compat_enabled() -> bool:
    return os.environ.get(LEGACY_KEY_COMPAT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def fx_event_key_enabled() -> bool:
    return os.environ.get(FX_EVENT_KEY_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def fx_event_identity(values: Mapping[str, Any]) -> str:
    """sha256 of an FX row's identity, from its silver column values. Empty and NULL count the same."""
    return sha256_text(canonical_json({name: "" if values.get(name) is None else str(values[name])
                                       for name in FX_EVENT_FIELDS}))


def fx_event_key(identity: str, occurrence: int) -> str:
    """The event key: the identity plus the row's place among identical-looking rows in one statement."""
    return f"fx-event:v1:{identity}:{occurrence}"


def fx_transaction_values(row: FlexRow, run_id: int) -> dict[str, Any]:
    """The fx_transactions columns the loader writes for one Flex record."""
    a = row.attributes
    return dict(zip(FX_COLUMNS, (
        row.account_id, row.source_key, pick(a, "dateTime"),
        pick(a, "activityDescription"), pick(a, "model", "modelCode"), row.report_date,
        pick(a, "functionalCurrency"), pick(a, "fxCurrency"),
        decimal_text(pick(a, "quantity")), decimal_text(pick(a, "proceeds")),
        decimal_text(pick(a, "cost")), decimal_text(pick(a, "realizedPL")),
        row.row_sha256, run_id,
    )))


def _legacy_key(connection, table: str, row: FlexRow, unavailable: set[tuple[str, str]]) -> str | None:
    if connection.execute(
        f"SELECT 1 FROM {table} WHERE account_id = ? AND source_key = ?",
        (row.account_id, row.source_key),
    ).fetchone() is not None:
        return None
    matches = connection.execute(
        f"SELECT source_key FROM {table} WHERE account_id = ? AND row_sha256 = ? AND source_key <> ?",
        (row.account_id, row.row_sha256, row.source_key),
    ).fetchall()
    if len(matches) != 1:
        return None  # none, or ambiguous: keep the parser's key
    legacy = str(matches[0][0])
    return None if (row.account_id, legacy) in unavailable else legacy


def _resolve_conid(connection: sqlite3.Connection, row: FlexRow) -> str | None:
    if row.conid:
        return row.conid
    a = row.attributes
    symbol = pick(a, "symbol")
    if not symbol:
        return None
    candidates = connection.execute(
        """
        SELECT conid, expiry, strike_text, put_call, listing_exchange
        FROM instruments
        WHERE symbol = ?
          AND COALESCE(currency, '') = COALESCE(?, '')
          AND COALESCE(asset_category, '') = COALESCE(?, '')
        """,
        (symbol, row.currency or "", pick(a, "assetCategory", "assetClass")),
    ).fetchall()
    expiry = pick(a, "expiry")
    strike = decimal_text(pick(a, "strike")) or ""
    put_call = pick(a, "putCall")
    exchange = pick(a, "listingExchange", "exchange")
    exact = [
        candidate for candidate in candidates
        if (candidate[1] or "") == expiry
        and (candidate[2] or "") == strike
        and (candidate[3] or "") == put_call
        and (not exchange or (candidate[4] or "") == exchange)
    ]
    if len(exact) == 1:
        return str(exact[0][0])
    if len(candidates) == 1:
        return str(candidates[0][0])

    identity = {
        "symbol": symbol,
        "currency": row.currency or "",
        "asset_category": pick(a, "assetCategory", "assetClass"),
        "expiry": expiry,
        "strike": strike,
        "put_call": put_call,
        "listing_exchange": exchange,
    }
    synthetic_conid = f"FLEX-{sha256_text(canonical_json(identity))[:24]}"
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO instruments(
            conid, symbol, description, asset_category, currency,
            listing_exchange, underlying_conid, expiry, strike_text, put_call,
            multiplier_text, isin, first_seen_at, last_seen_at, source_json
        ) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, '', ?, ?, ?)
        ON CONFLICT(conid) DO UPDATE SET last_seen_at = excluded.last_seen_at
        """,
        (
            synthetic_conid, symbol, pick(a, "description"),
            pick(a, "assetCategory", "assetClass"), row.currency or "", exchange,
            expiry, strike or None, put_call, decimal_text(pick(a, "multiplier")),
            now, now, canonical_json({"synthetic": True, **identity}),
        ),
    )
    return synthetic_conid


def _sum_decimal_attributes(attributes: dict[str, str], *fields: str) -> str | None:
    values = [decimal_text(pick(attributes, field)) for field in fields]
    present = [Decimal(value) for value in values if value is not None]
    return format(sum(present, Decimal("0")), "f") if present else None


NAV_PARTS = (
    "cash", "stock", "options", "bonds", "commodities",
    "cfdUnrealizedPl", "forexCfdUnrealizedPl", "cgtWithholdingAccruals",
    "dividendAccruals", "interestAccruals", "marginFinancingChargeAccruals",
)


def nav_total(attributes: Mapping[str, str], record_type: str) -> str | None:
    """The NAV total stored for a statement row: IBKR's total field, else, for an
    EquitySummaryByReportDate row without one (as some Flex queries send it), the sum of its parts."""
    total = decimal_text(pick(attributes, "total", "endingValue", "netLiquidationValue", "nav"))
    if total is None and "equitysummarybyreportdate" in record_type.lower():
        total = _sum_decimal_attributes(dict(attributes), *NAV_PARTS)
    return total


def _upsert_account(connection: sqlite3.Connection, row: FlexRow) -> None:
    attrs = row.attributes
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO accounts(
            account_id, alias, base_currency, account_type, ib_entity,
            first_seen_at, last_seen_at, source_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            alias = COALESCE(NULLIF(excluded.alias, ''), accounts.alias),
            base_currency = COALESCE(NULLIF(excluded.base_currency, ''), accounts.base_currency),
            account_type = COALESCE(NULLIF(excluded.account_type, ''), accounts.account_type),
            ib_entity = COALESCE(NULLIF(excluded.ib_entity, ''), accounts.ib_entity),
            last_seen_at = excluded.last_seen_at,
            source_json = excluded.source_json
        """,
        (
            row.account_id,
            pick(attrs, "accountAlias", "alias"),
            pick(attrs, "baseCurrency", "currency"),
            pick(attrs, "accountType", "type"),
            pick(attrs, "ibEntity"),
            now,
            now,
            canonical_json(attrs),
        ),
    )


def _upsert_instrument(connection: sqlite3.Connection, row: FlexRow) -> None:
    if not row.conid:
        return
    attrs = row.attributes
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO instruments(
            conid, symbol, description, asset_category, currency,
            listing_exchange, underlying_conid, expiry, strike_text, put_call,
            multiplier_text, isin, first_seen_at, last_seen_at, source_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(conid) DO UPDATE SET
            symbol = COALESCE(NULLIF(excluded.symbol, ''), instruments.symbol),
            description = COALESCE(NULLIF(excluded.description, ''), instruments.description),
            asset_category = COALESCE(NULLIF(excluded.asset_category, ''), instruments.asset_category),
            currency = COALESCE(NULLIF(excluded.currency, ''), instruments.currency),
            listing_exchange = COALESCE(NULLIF(excluded.listing_exchange, ''), instruments.listing_exchange),
            underlying_conid = COALESCE(NULLIF(excluded.underlying_conid, ''), instruments.underlying_conid),
            expiry = COALESCE(NULLIF(excluded.expiry, ''), instruments.expiry),
            strike_text = COALESCE(NULLIF(excluded.strike_text, ''), instruments.strike_text),
            put_call = COALESCE(NULLIF(excluded.put_call, ''), instruments.put_call),
            multiplier_text = COALESCE(NULLIF(excluded.multiplier_text, ''), instruments.multiplier_text),
            isin = COALESCE(NULLIF(excluded.isin, ''), instruments.isin),
            last_seen_at = excluded.last_seen_at,
            source_json = excluded.source_json
        """,
        (
            row.conid,
            pick(attrs, "symbol"),
            pick(attrs, "description"),
            pick(attrs, "assetCategory", "assetClass"),
            pick(attrs, "currency"),
            pick(attrs, "listingExchange", "exchange"),
            pick(attrs, "underlyingConid"),
            pick(attrs, "expiry"),
            decimal_text(pick(attrs, "strike")),
            pick(attrs, "putCall"),
            decimal_text(pick(attrs, "multiplier")),
            pick(attrs, "isin", "securityID"),
            now,
            now,
            canonical_json(attrs),
        ),
    )


def _upsert_flex_row(connection: sqlite3.Connection, row: FlexRow, run_id: int) -> None:
    connection.execute(
        """
        INSERT INTO flex_rows(
            account_id, section, record_type, source_key, report_date, conid,
            currency, row_sha256, data_json, first_seen_run_id, last_seen_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, section, record_type, source_key) DO UPDATE SET
            report_date = excluded.report_date,
            conid = excluded.conid,
            currency = excluded.currency,
            row_sha256 = excluded.row_sha256,
            data_json = excluded.data_json,
            last_seen_run_id = excluded.last_seen_run_id
        """,
        (
            row.account_id, row.section, row.record_type, row.source_key,
            row.report_date, row.conid, row.currency, row.row_sha256,
            canonical_json(row.attributes), run_id, run_id,
        ),
    )


def _upsert_trade(connection: sqlite3.Connection, row: FlexRow, run_id: int) -> None:
    a = row.attributes
    conid = _resolve_conid(connection, row)
    connection.execute(
        """
        INSERT INTO trades VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(account_id, source_key) DO UPDATE SET
            execution_id=excluded.execution_id, trade_id=excluded.trade_id,
            conid=excluded.conid, trade_date=excluded.trade_date,
            trade_time=excluded.trade_time, settle_date=excluded.settle_date,
            buy_sell=excluded.buy_sell, quantity_text=excluded.quantity_text,
            trade_price_text=excluded.trade_price_text, proceeds_text=excluded.proceeds_text,
            commission_text=excluded.commission_text,
            commission_currency=excluded.commission_currency,
            net_cash_text=excluded.net_cash_text,
            realized_pnl_text=excluded.realized_pnl_text, currency=excluded.currency,
            order_reference=excluded.order_reference, model_code=excluded.model_code,
            row_sha256=excluded.row_sha256, last_seen_run_id=excluded.last_seen_run_id
        """,
        (
            row.account_id, row.source_key, pick(a, "ibExecID"), pick(a, "tradeID"),
            conid, parse_date_text(pick(a, "tradeDate")) or row.report_date,
            pick(a, "tradeTime", "dateTime"),
            parse_date_text(pick(a, "settleDateTarget", "settleDate")), pick(a, "buySell", "side"),
            decimal_text(pick(a, "quantity")), decimal_text(pick(a, "tradePrice", "price")),
            decimal_text(pick(a, "proceeds", "tradeMoney")),
            decimal_text(pick(a, "ibCommission", "commission")),
            pick(a, "ibCommissionCurrency", "commissionCurrency"),
            decimal_text(pick(a, "netCash")), decimal_text(pick(a, "fifoPnlRealized", "realizedPL")),
            row.currency, pick(a, "orderReference", "orderRef"), pick(a, "model", "modelCode"),
            row.row_sha256, run_id,
        ),
    )


def _upsert_cash_transaction(connection: sqlite3.Connection, row: FlexRow, run_id: int) -> None:
    a = row.attributes
    connection.execute(
        """
        INSERT INTO cash_transactions(
            account_id, source_key, transaction_id, event_datetime, model_code,
            effective_date, settle_date, transaction_type, description, amount_text,
            currency, conid, row_sha256, last_seen_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, source_key) DO UPDATE SET
            transaction_id=excluded.transaction_id, event_datetime=excluded.event_datetime,
            model_code=excluded.model_code, effective_date=excluded.effective_date,
            settle_date=excluded.settle_date, transaction_type=excluded.transaction_type,
            description=excluded.description, amount_text=excluded.amount_text,
            currency=excluded.currency, conid=excluded.conid,
            row_sha256=excluded.row_sha256, last_seen_run_id=excluded.last_seen_run_id
        """,
        (
            row.account_id, row.source_key, pick(a, "transactionID"), pick(a, "dateTime"),
            pick(a, "model", "modelCode"), row.report_date,
            parse_date_text(pick(a, "settleDate")), pick(a, "type", "transactionType"),
            pick(a, "description"), decimal_text(pick(a, "amount")), row.currency,
            row.conid, row.row_sha256, run_id,
        ),
    )


TRANSFERS_TABLE = "transfers"
TRANSFER_COLUMNS = ("account_id", "source_key", "flex_account_id", "transaction_id", "transfer_date", "report_date",
                    "asset_category", "transfer_type", "direction", "description", "conid", "currency",
                    "quantity_text", "cash_transfer_text", "position_amount_text", "position_amount_in_base_text",
                    "row_sha256", "last_seen_run_id")


def has_transfers_table(connection) -> bool:
    """Whether silver.transfers (migration 084) is there to write; SQLite has it from 001_init.sql."""
    if getattr(connection, "backend", "sqlite") == "postgresql":
        return connection.execute("SELECT to_regclass(?)", (TRANSFERS_TABLE,)).fetchone()[0] is not None
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?", (TRANSFERS_TABLE,)
    ).fetchone() is not None


def transfer_values(row: FlexRow, run_id: int) -> dict[str, Any]:
    """A Transfers record as silver.transfers holds it: IBKR's own accountId (a consolidated copy carries
    "-"), dates as ISO text, amounts as decimal text, under the same key and digest as its flex_rows row."""
    a = row.attributes

    def text(*names: str) -> str | None:
        return pick(a, *names) or None

    return {
        "account_id": row.account_id, "source_key": row.source_key,
        "flex_account_id": text("accountId"), "transaction_id": text("transactionID"),
        "transfer_date": parse_date_text(pick(a, "date")), "report_date": parse_date_text(pick(a, "reportDate")),
        "asset_category": text("assetCategory"), "transfer_type": text("type"), "direction": text("direction"),
        "description": text("description"), "conid": row.conid, "currency": text("currency"),
        "quantity_text": decimal_text(pick(a, "quantity")),
        "cash_transfer_text": decimal_text(pick(a, "cashTransfer")),
        "position_amount_text": decimal_text(pick(a, "positionAmount")),
        "position_amount_in_base_text": decimal_text(pick(a, "positionAmountInBase")),
        "row_sha256": row.row_sha256, "last_seen_run_id": run_id,
    }


def upsert_transfer(connection, row: FlexRow, run_id: int) -> None:
    values = transfer_values(row, run_id)
    updates = ", ".join(f"{column}=excluded.{column}" for column in TRANSFER_COLUMNS[2:])
    connection.execute(
        f"INSERT INTO transfers({', '.join(TRANSFER_COLUMNS)}) VALUES ({', '.join('?' for _ in TRANSFER_COLUMNS)}) "
        f"ON CONFLICT(account_id, source_key) DO UPDATE SET {updates}",
        tuple(values[column] for column in TRANSFER_COLUMNS),
    )


def _upsert_corporate_action(connection: sqlite3.Connection, row: FlexRow, run_id: int) -> None:
    a = row.attributes
    connection.execute(
        """
        INSERT INTO corporate_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, source_key) DO UPDATE SET
            action_id=excluded.action_id, effective_date=excluded.effective_date,
            action_type=excluded.action_type, description=excluded.description,
            quantity_text=excluded.quantity_text, amount_text=excluded.amount_text,
            currency=excluded.currency, conid=excluded.conid,
            row_sha256=excluded.row_sha256, last_seen_run_id=excluded.last_seen_run_id
        """,
        (
            row.account_id, row.source_key, pick(a, "actionID"), row.report_date,
            pick(a, "type", "actionType", "code"), pick(a, "description"),
            decimal_text(pick(a, "quantity")), decimal_text(pick(a, "amount", "proceeds")),
            row.currency, row.conid, row.row_sha256, run_id,
        ),
    )


def _upsert_position(connection: sqlite3.Connection, row: FlexRow, run_id: int) -> None:
    conid = _resolve_conid(connection, row)
    if not row.report_date or not conid:
        return
    a = row.attributes
    connection.execute(
        """
        INSERT INTO position_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, snapshot_date, conid, model_code) DO UPDATE SET
            currency=excluded.currency, quantity_text=excluded.quantity_text,
            cost_basis_text=excluded.cost_basis_text, mark_price_text=excluded.mark_price_text,
            market_value_text=excluded.market_value_text,
            unrealized_pnl_text=excluded.unrealized_pnl_text,
            row_sha256=excluded.row_sha256, last_seen_run_id=excluded.last_seen_run_id
        """,
        (
            row.account_id, row.report_date, conid, pick(a, "model", "modelCode"),
            row.currency, decimal_text(pick(a, "position", "quantity")),
            decimal_text(pick(a, "costBasisMoney", "costBasis")),
            decimal_text(pick(a, "markPrice", "closePrice")),
            decimal_text(pick(a, "positionValue", "marketValue")),
            decimal_text(pick(a, "fifoPnlUnrealized", "unrealizedPL")),
            row.row_sha256, run_id,
        ),
    )


def _upsert_cash_snapshot(connection: sqlite3.Connection, row: FlexRow, run_id: int) -> None:
    if not row.report_date or not row.currency:
        return
    a = row.attributes
    connection.execute(
        """
        INSERT INTO cash_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, snapshot_date, currency) DO UPDATE SET
            ending_cash_text=excluded.ending_cash_text,
            settled_cash_text=excluded.settled_cash_text,
            fx_rate_to_base_text=excluded.fx_rate_to_base_text,
            row_sha256=excluded.row_sha256, last_seen_run_id=excluded.last_seen_run_id
        """,
        (
            row.account_id, row.report_date, row.currency,
            decimal_text(pick(a, "endingCash", "cashBalance")),
            decimal_text(pick(a, "endingSettledCash", "settledCash")),
            decimal_text(pick(a, "fxRateToBase", "exchangeRate")),
            row.row_sha256, run_id,
        ),
    )


def _upsert_nav(connection: sqlite3.Connection, row: FlexRow, run_id: int) -> None:
    if not row.report_date:
        return
    a = row.attributes
    currency = row.currency or pick(a, "baseCurrency", default="BASE")
    asset_class = pick(a, "assetClass", "assetCategory", default="TOTAL")
    accruals = _sum_decimal_attributes(
        a,
        "cfdUnrealizedPl", "forexCfdUnrealizedPl", "cgtWithholdingAccruals",
        "dividendAccruals", "interestAccruals", "marginFinancingChargeAccruals",
    )
    total = nav_total(a, row.record_type)
    connection.execute(
        """
        INSERT INTO nav_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, snapshot_date, currency, asset_class) DO UPDATE SET
            total_text=excluded.total_text, cash_text=excluded.cash_text,
            stock_text=excluded.stock_text, options_text=excluded.options_text,
            futures_text=excluded.futures_text, bonds_text=excluded.bonds_text,
            accruals_text=excluded.accruals_text, row_sha256=excluded.row_sha256,
            last_seen_run_id=excluded.last_seen_run_id
        """,
        (
            row.account_id, row.report_date, currency, asset_class,
            total,
            decimal_text(pick(a, "cash")), decimal_text(pick(a, "stock")),
            decimal_text(pick(a, "options", "option")), decimal_text(pick(a, "futures", "future")),
            decimal_text(pick(a, "bonds", "bond")),
            decimal_text(pick(a, "accruals")) or accruals,
            row.row_sha256, run_id,
        ),
    )


def _upsert_fx_transaction(connection: sqlite3.Connection, row: FlexRow, run_id: int) -> None:
    a = row.attributes
    connection.execute(
        """
        INSERT INTO fx_transactions(
            account_id, source_key, event_datetime, activity_description, model_code,
            report_date, functional_currency, fx_currency, quantity_text, proceeds_text,
            cost_text, realized_pnl_text, row_sha256, last_seen_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, source_key) DO UPDATE SET
            event_datetime=excluded.event_datetime,
            activity_description=excluded.activity_description,
            model_code=excluded.model_code, report_date=excluded.report_date,
            functional_currency=excluded.functional_currency,
            fx_currency=excluded.fx_currency,
            quantity_text=excluded.quantity_text,
            proceeds_text=excluded.proceeds_text,
            cost_text=excluded.cost_text,
            realized_pnl_text=excluded.realized_pnl_text,
            row_sha256=excluded.row_sha256,
            last_seen_run_id=excluded.last_seen_run_id
        """,
        (
            row.account_id, row.source_key, pick(a, "dateTime"),
            pick(a, "activityDescription"), pick(a, "model", "modelCode"), row.report_date,
            pick(a, "functionalCurrency"), pick(a, "fxCurrency"),
            decimal_text(pick(a, "quantity")), decimal_text(pick(a, "proceeds")),
            decimal_text(pick(a, "cost")), decimal_text(pick(a, "realizedPL")),
            row.row_sha256, run_id,
        ),
    )


def _require_event_key(connection) -> None:
    try:
        connection.execute("SELECT event_key FROM fx_transactions WHERE 1 = 0").fetchall()
    except Exception as error:  # noqa: BLE001 - any driver's "no such column"
        raise RuntimeError(f"{FX_EVENT_KEY_ENV} is on but fx_transactions has no event_key column: apply the "
                           f"FX step's migration first, or turn {FX_EVENT_KEY_ENV} off") from error


def _upsert_fx_event(connection, row: FlexRow, run_id: int, occurrences: Counter) -> int:
    """With IBKR_FX_EVENT_KEY: a re-sent FX event with new amounts updates its row. 1 when it did."""
    values = fx_transaction_values(row, run_id)
    identity = fx_event_identity(values)
    occurrences[(row.account_id, identity)] += 1
    event_key = fx_event_key(identity, occurrences[(row.account_id, identity)])
    same_record = connection.execute(
        "SELECT event_key FROM fx_transactions WHERE account_id = ? AND source_key = ?",
        (row.account_id, row.source_key),
    ).fetchone()
    holder = connection.execute(
        "SELECT source_key FROM fx_transactions WHERE account_id = ? AND event_key = ?",
        (row.account_id, event_key),
    ).fetchone()
    names = [name for name in FX_COLUMNS if name != "account_id"]
    if same_record is None and holder is not None:
        # The same FX event under a new content key: restated amounts. One row, new values;
        # source_key now names the bronze record of this latest version.
        connection.execute(
            f"UPDATE fx_transactions SET {', '.join(f'{name} = ?' for name in names)} "
            "WHERE account_id = ? AND source_key = ?",
            (*(values[name] for name in names), row.account_id, holder[0]),
        )
        return 1
    # A new event, or the same record again as today. Claim the event key only while it is free.
    claim = event_key if holder is None or holder[0] == row.source_key else None
    connection.execute(
        f"""
        INSERT INTO fx_transactions({', '.join(FX_COLUMNS)}, event_key)
        VALUES ({', '.join('?' for _ in FX_COLUMNS)}, ?)
        ON CONFLICT(account_id, source_key) DO UPDATE SET
            {', '.join(f'{name}=excluded.{name}' for name in names if name != 'source_key')},
            event_key=COALESCE(fx_transactions.event_key, excluded.event_key)
        """,
        (*(values[name] for name in FX_COLUMNS), claim),
    )
    return 0


def _upsert_fx_rate(connection: sqlite3.Connection, row: FlexRow, run_id: int) -> None:
    if not row.report_date:
        return
    a = row.attributes
    from_currency = pick(a, "fromCurrency", "currency")
    to_currency = pick(a, "toCurrency", "baseCurrency", default="BASE")
    rate = decimal_text(pick(a, "rate", "fxRateToBase", "exchangeRate"))
    if not from_currency or not rate:
        return
    connection.execute(
        """
        INSERT INTO fx_rates VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, rate_date, from_currency, to_currency) DO UPDATE SET
            rate_text=excluded.rate_text, row_sha256=excluded.row_sha256,
            last_seen_run_id=excluded.last_seen_run_id
        """,
        (row.account_id, row.report_date, from_currency, to_currency, rate, row.row_sha256, run_id),
    )


def load_report(
    connection: sqlite3.Connection,
    report: ParsedReport,
    run_id: int,
    *,
    legacy_key_compat: bool | None = None,
    fx_event_keys: bool | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    if legacy_key_compat is None:
        legacy_key_compat = legacy_key_compat_enabled()
    if fx_event_keys is None:
        fx_event_keys = fx_event_key_enabled()
    occurrences: Counter | None = None
    restated = 0
    if fx_event_keys:
        _require_event_key(connection)
        occurrences = Counter()
    # A legacy key is never taken over if this report uses it, or twice.
    unavailable = {(row.account_id, row.source_key) for row in report.rows} if legacy_key_compat else set()
    reused = 0

    def compatible(table: str, row: FlexRow) -> FlexRow:
        nonlocal reused
        if not legacy_key_compat:
            return row
        legacy = _legacy_key(connection, table, row, unavailable)
        if legacy is None:
            return row
        unavailable.add((row.account_id, legacy))
        reused += 1
        return replace(row, source_key=legacy)

    transfers = any(row.section == "Transfers" for row in report.rows) and has_transfers_table(connection)

    # Instrument details can appear after trades in a Flex document. Load all
    # dimensions first so security resolution is independent of XML section order.
    for row in report.rows:
        if row.account_id != "UNKNOWN" and row.record_type.lower() in {
            "flexstatement", "accountinformation"
        }:
            _upsert_account(connection, row)
        _upsert_instrument(connection, row)

    for row in report.rows:
        counts[row.record_type] = counts.get(row.record_type, 0) + 1
        _upsert_flex_row(connection, row, run_id)

        if transfers and row.section == "Transfers":
            upsert_transfer(connection, row, run_id)

        kind = row.record_type.lower()
        section = row.section.lower()
        if kind == "trade" or section == "trades" and "trade" in kind:
            _upsert_trade(connection, compatible("trades", row), run_id)
        elif "cashtransaction" in kind:
            _upsert_cash_transaction(connection, compatible("cash_transactions", row), run_id)
        elif "corporateaction" in kind:
            _upsert_corporate_action(connection, compatible("corporate_actions", row), run_id)
        elif "openposition" in kind:
            _upsert_position(connection, row, run_id)
        elif "cashreportcurrency" in kind or kind == "cashreport":
            _upsert_cash_snapshot(connection, row, run_id)
        elif "netassetvalue" in kind or "equitysummarybyreportdate" in kind:
            _upsert_nav(connection, row, run_id)
        elif "conversionrate" in kind or kind in {"fxrate", "currencyconversionrate"}:
            _upsert_fx_rate(connection, row, run_id)
        elif kind == "fxtransaction":
            if occurrences is None:
                _upsert_fx_transaction(connection, compatible("fx_transactions", row), run_id)
            else:
                restated += _upsert_fx_event(connection, compatible("fx_transactions", row), run_id, occurrences)
    if reused:
        counts["legacy_source_keys_reused"] = reused
    if restated:
        counts["fx_events_restated"] = restated
    return counts
