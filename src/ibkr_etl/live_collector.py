"""Read-only live IBKR collector for the gateway VM.

Connects to IB Gateway over the TWS API with ``readonly=True`` and writes the
live_ tables from migration 063: account values, positions, quotes, today's
executions, contract details and daily bars. It never places, modifies or
cancels orders; the gateway itself also runs with the Read-Only API setting.

The Flex statement stays the book of record. These rows feed the website's
live view and, later, the market data jobs.

Run as ``python -m ibkr_etl.live_collector`` with the environment described
in :meth:`CollectorSettings.from_environment`. ib_async is imported only when
the collector runs, so the rest of the package does not depend on it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import signal
import sys
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

COLLECTOR_VERSION = "live-collector/1"
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")

# Gateway notices that are not problems: market data and HMDS farm status.
INFORMATIONAL_CODES = frozenset({2104, 2106, 2107, 2108, 2119, 2158})
# Lost and restored connectivity between the gateway and IBKR's servers.
CONNECTIVITY_LOST_CODES = frozenset({1100, 2110})
CONNECTIVITY_RESTORED_CODES = frozenset({1101, 1102})

QUOTED_SEC_TYPES = frozenset({"STK", "OPT", "FUT", "FOP", "WAR", "IND"})
BAR_SEC_TYPES = frozenset({"STK", "IND"})
UNSET_DOUBLE = sys.float_info.max
UNSET_INTEGER = 2**31 - 1

SNAPSHOT_SECONDS_OPEN = 60
SNAPSHOT_SECONDS_CLOSED = 300
CONTRACT_REFRESH = timedelta(days=7)
BAR_REQUEST_SPACING_SECONDS = 11  # IBKR allows 60 historical requests per 10 minutes.
BAR_RUN_TIMES_HKT = (time(6, 30), time(16, 45))  # after the US and the Hong Kong close
PURGE_TIME_HKT = time(3, 0)
SNAPSHOT_RETENTION = timedelta(days=30)
EXECUTION_RETENTION = timedelta(days=366)

_ACCOUNT_ID = re.compile(r"^[A-Z]{1,3}[0-9]{1,12}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_BENCHMARK = re.compile(r"^(IND|STK):([A-Z0-9.&-]{1,12}):([A-Z0-9.]{1,12}):([A-Z]{3})$")

LOGGER = logging.getLogger("ibkr_etl.live_collector")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Benchmark:
    sec_type: str
    symbol: str
    exchange: str
    currency: str


@dataclass(frozen=True)
class CollectorSettings:
    gateway_host: str
    gateway_port: int
    client_id: int
    account_ids: tuple[str, ...]
    tenant_id: str
    database_url: str = field(repr=False)
    market_data_type: int = 3
    status_file: Path | None = None
    benchmarks: tuple[Benchmark, ...] = ()
    max_market_data_lines: int = 90

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "CollectorSettings":
        """Read the collector's settings; the database URL comes from a file.

        IB_GATEWAY_HOST, IB_GATEWAY_PORT, IB_CLIENT_ID (default 17),
        LIVE_ACCOUNT_IDS (comma separated), PORTFOLIO_TENANT_ID,
        LIVE_DATABASE_URL_FILE, LIVE_MARKET_DATA_TYPE (1 live, 3 delayed;
        default 3, which still returns live prices where a subscription exists),
        LIVE_STATUS_FILE, LIVE_BENCHMARKS (SECTYPE:SYMBOL:EXCHANGE:CURRENCY,
        comma separated) and LIVE_MAX_MARKET_DATA_LINES (default 90 of the
        100 lines every IBKR account has).
        """
        env = os.environ if env is None else env
        host = env.get("IB_GATEWAY_HOST", "").strip()
        if not host:
            raise ValueError("IB_GATEWAY_HOST is required")
        port = _bounded_int(env.get("IB_GATEWAY_PORT", ""), "IB_GATEWAY_PORT", 1, 65535)
        client_id = _bounded_int(env.get("IB_CLIENT_ID", "17"), "IB_CLIENT_ID", 1, 999)
        accounts = tuple(
            part.strip() for part in env.get("LIVE_ACCOUNT_IDS", "").split(",") if part.strip()
        )
        if not accounts or any(not _ACCOUNT_ID.match(account) for account in accounts):
            raise ValueError("LIVE_ACCOUNT_IDS must list IBKR account IDs")
        tenant_id = str(uuid.UUID(env.get("PORTFOLIO_TENANT_ID", "").strip()))
        url_file = env.get("LIVE_DATABASE_URL_FILE", "").strip()
        if not url_file:
            raise ValueError("LIVE_DATABASE_URL_FILE is required")
        database_url = Path(url_file).read_text(encoding="utf-8").strip()
        if not database_url.startswith(("postgres://", "postgresql://")):
            raise ValueError("The live database URL must be a PostgreSQL URL")
        market_data_type = _bounded_int(
            env.get("LIVE_MARKET_DATA_TYPE", "3"), "LIVE_MARKET_DATA_TYPE", 1, 4
        )
        status_file = env.get("LIVE_STATUS_FILE", "").strip()
        benchmarks = tuple(
            parse_benchmark(part.strip())
            for part in env.get(
                "LIVE_BENCHMARKS", "IND:SPX:CBOE:USD,IND:COMP:NASDAQ:USD"
            ).split(",")
            if part.strip()
        )
        max_lines = _bounded_int(
            env.get("LIVE_MAX_MARKET_DATA_LINES", "90"), "LIVE_MAX_MARKET_DATA_LINES", 1, 1000
        )
        return cls(
            gateway_host=host,
            gateway_port=port,
            client_id=client_id,
            account_ids=accounts,
            tenant_id=tenant_id,
            database_url=database_url,
            market_data_type=market_data_type,
            status_file=Path(status_file) if status_file else None,
            benchmarks=benchmarks,
            max_market_data_lines=max_lines,
        )


def _bounded_int(raw: str, name: str, low: int, high: int) -> int:
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise ValueError(f"{name} must be a whole number") from None
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def parse_benchmark(text: str) -> Benchmark:
    match = _BENCHMARK.match(text)
    if not match:
        raise ValueError("LIVE_BENCHMARKS entries look like IND:SPX:CBOE:USD")
    return Benchmark(*match.groups())


# --- Values from the API -------------------------------------------------


def number(value: Any) -> float | None:
    """A finite float, or None for IBKR's unset markers, NaN and blanks."""
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    if result == UNSET_DOUBLE or result == UNSET_INTEGER:
        return None
    return result


def multiplier(contract: Any) -> float:
    value = number(getattr(contract, "multiplier", "") or None)
    return value if value and value > 0 else 1.0


def quote_price(ticker: Any) -> float | None:
    """Last trade, else the bid/ask midpoint, else the prior close."""
    last = number(getattr(ticker, "last", None))
    if last is not None and last > 0:
        return last
    bid = number(getattr(ticker, "bid", None))
    ask = number(getattr(ticker, "ask", None))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2
    close = number(getattr(ticker, "close", None))
    return close if close is not None and close > 0 else None


def account_row(
    account_id: str,
    summary: Iterable[Any],
    pnl: Any | None,
    observed_at: datetime,
) -> dict[str, Any] | None:
    """One live_account_values row from account summary values and reqPnL.

    Returns None when the summary has no net liquidation value yet, so a
    half-loaded account is never written as a snapshot.
    """
    net_liquidation = None
    base_currency = None
    total_cash = None
    cash: dict[str, float] = {}
    for value in summary:
        if getattr(value, "account", account_id) != account_id:
            continue
        tag = getattr(value, "tag", "")
        currency = getattr(value, "currency", "") or ""
        amount = number(getattr(value, "value", None))
        if tag == "NetLiquidation" and _CURRENCY.match(currency):
            net_liquidation, base_currency = amount, currency
        elif tag == "TotalCashValue" and currency != "BASE":
            total_cash = amount
        elif tag == "CashBalance" and _CURRENCY.match(currency) and amount is not None:
            cash[currency] = amount
    if net_liquidation is None or base_currency is None:
        return None
    return {
        "account_id": account_id,
        "observed_at": observed_at,
        "base_currency": base_currency,
        "net_liquidation": net_liquidation,
        "total_cash": total_cash,
        "daily_pnl": number(getattr(pnl, "dailyPnL", None)) if pnl else None,
        "unrealized_pnl": number(getattr(pnl, "unrealizedPnL", None)) if pnl else None,
        "realized_pnl": number(getattr(pnl, "realizedPnL", None)) if pnl else None,
        "cash_by_currency": dict(sorted(cash.items())),
    }


def position_rows(
    account_id: str,
    positions: Iterable[Any],
    tickers: Mapping[str, Any],
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """live_positions rows in the instrument's currency.

    IBKR's average cost includes the contract multiplier; it is stored per
    unit so it compares with the price. Market value, unrealized P&L and the
    change since the prior close come from the live quote, so they are blank
    for an instrument without market data.
    """
    rows = []
    for position in positions:
        if getattr(position, "account", "") != account_id:
            continue
        quantity = number(getattr(position, "position", None))
        contract = getattr(position, "contract", None)
        conid = str(getattr(contract, "conId", "") or "")
        currency = getattr(contract, "currency", "") or ""
        if not quantity or not conid.isdigit() or not _CURRENCY.match(currency):
            continue
        scale = multiplier(contract)
        total_cost = number(getattr(position, "avgCost", None))
        average_cost = total_cost / scale if total_cost is not None else None
        ticker = tickers.get(conid)
        price = quote_price(ticker) if ticker is not None else None
        prior_close = number(getattr(ticker, "close", None)) if ticker is not None else None
        market_value = quantity * price * scale if price is not None else None
        rows.append({
            "account_id": account_id,
            "observed_at": observed_at,
            "conid": conid,
            "symbol": getattr(contract, "symbol", "") or getattr(contract, "localSymbol", "") or conid,
            "sec_type": getattr(contract, "secType", "") or "UNKNOWN",
            "currency": currency,
            "primary_exchange": getattr(contract, "primaryExchange", "") or None,
            "quantity": quantity,
            "average_cost": average_cost,
            "market_price": price,
            "market_value": market_value,
            "unrealized_pnl": (
                market_value - quantity * total_cost
                if market_value is not None and total_cost is not None else None
            ),
            "daily_pnl": (
                quantity * (price - prior_close) * scale
                if price is not None and prior_close is not None and prior_close > 0 else None
            ),
        })
    return rows


def quote_row(conid: str, contract: Any, ticker: Any, observed_at: datetime) -> dict[str, Any] | None:
    last = number(getattr(ticker, "last", None))
    bid = number(getattr(ticker, "bid", None))
    ask = number(getattr(ticker, "ask", None))
    close = number(getattr(ticker, "close", None))
    data_type = getattr(ticker, "marketDataType", None)
    currency = getattr(contract, "currency", "") or ""
    if all(value is None for value in (last, bid, ask, close)):
        return None
    if data_type not in (1, 2, 3, 4) or not _CURRENCY.match(currency):
        return None
    return {
        "conid": conid,
        "observed_at": observed_at,
        "symbol": getattr(contract, "symbol", "") or conid,
        "currency": currency,
        "last_price": last,
        "bid_price": bid,
        "ask_price": ask,
        "prior_close": close,
        "market_data_type": data_type,
    }


def execution_row(fill: Any, account_ids: Sequence[str], seen_at: datetime) -> dict[str, Any] | None:
    execution = getattr(fill, "execution", None)
    contract = getattr(fill, "contract", None)
    report = getattr(fill, "commissionReport", None)
    account_id = getattr(execution, "acctNumber", "")
    exec_id = getattr(execution, "execId", "") or ""
    side = {"BOT": "BUY", "SLD": "SELL"}.get(getattr(execution, "side", ""))
    quantity = number(getattr(execution, "shares", None))
    price = number(getattr(execution, "price", None))
    conid = str(getattr(contract, "conId", "") or "")
    currency = getattr(contract, "currency", "") or ""
    executed_at = getattr(execution, "time", None)
    if (
        account_id not in account_ids or not exec_id or len(exec_id) > 64 or side is None
        or not quantity or quantity <= 0 or price is None or not conid.isdigit()
        or not _CURRENCY.match(currency) or not isinstance(executed_at, datetime)
        or executed_at.year < 2000
    ):
        return None
    if executed_at.tzinfo is None:
        executed_at = executed_at.replace(tzinfo=timezone.utc)
    commission_currency = getattr(report, "currency", "") or ""
    has_commission = bool(_CURRENCY.match(commission_currency))
    return {
        "exec_id": exec_id,
        "account_id": account_id,
        "executed_at": executed_at,
        "conid": conid,
        "symbol": getattr(contract, "symbol", "") or conid,
        "sec_type": getattr(contract, "secType", "") or "UNKNOWN",
        "currency": currency,
        "side": side,
        "quantity": quantity,
        "price": price,
        "commission": number(getattr(report, "commission", None)) if has_commission else None,
        "commission_currency": commission_currency if has_commission else None,
        "realized_pnl": number(getattr(report, "realizedPNL", None)) if has_commission else None,
        "seen_at": seen_at,
    }


def contract_row(details: Any, is_benchmark: bool, refreshed_at: datetime) -> dict[str, Any] | None:
    contract = getattr(details, "contract", None)
    conid = str(getattr(contract, "conId", "") or "")
    currency = getattr(contract, "currency", "") or ""
    if not conid.isdigit() or not _CURRENCY.match(currency):
        return None
    return {
        "conid": conid,
        "symbol": getattr(contract, "symbol", "") or conid,
        "local_symbol": getattr(contract, "localSymbol", "") or None,
        "sec_type": getattr(contract, "secType", "") or "UNKNOWN",
        "currency": currency,
        "primary_exchange": getattr(contract, "primaryExchange", "") or None,
        "long_name": getattr(details, "longName", "") or None,
        "industry": getattr(details, "industry", "") or None,
        "category": getattr(details, "category", "") or None,
        "subcategory": getattr(details, "subcategory", "") or None,
        "is_benchmark": is_benchmark,
        "refreshed_at": refreshed_at,
    }


def bar_rows(
    conid: str, currency: str, what_to_show: str, bars: Iterable[Any], fetched_at: datetime
) -> list[dict[str, Any]]:
    rows = []
    for bar in bars:
        bar_date = getattr(bar, "date", None)
        if isinstance(bar_date, datetime):
            bar_date = bar_date.date()
        close = number(getattr(bar, "close", None))
        if not isinstance(bar_date, date) or close is None or close <= 0:
            continue
        volume = number(getattr(bar, "volume", None))
        rows.append({
            "conid": conid,
            "what_to_show": what_to_show,
            "bar_date": bar_date,
            "open_price": _positive(getattr(bar, "open", None)),
            "high_price": _positive(getattr(bar, "high", None)),
            "low_price": _positive(getattr(bar, "low", None)),
            "close_price": close,
            "volume": volume if volume is not None and volume >= 0 else None,
            "currency": currency,
            "fetched_at": fetched_at,
        })
    return rows


def _positive(value: Any) -> float | None:
    result = number(value)
    return result if result is not None and result > 0 else None


# --- Schedule -------------------------------------------------------------


def markets_open(now: datetime) -> bool:
    """True during Hong Kong or US regular trading hours on a weekday.

    Holidays are not modelled; on a holiday the collector simply records
    unchanged prices every minute.
    """
    hk = now.astimezone(HONG_KONG)
    if hk.weekday() < 5 and time(9, 30) <= hk.time() <= time(16, 10):
        return True
    ny = now.astimezone(NEW_YORK)
    return ny.weekday() < 5 and time(9, 30) <= ny.time() <= time(16, 0)


def snapshot_interval(now: datetime) -> int:
    return SNAPSHOT_SECONDS_OPEN if markets_open(now) else SNAPSHOT_SECONDS_CLOSED


def latest_slot(now: datetime, slots: Sequence[time]) -> datetime:
    """The most recent Hong Kong time slot at or before ``now``."""
    hk = now.astimezone(HONG_KONG)
    candidates = []
    for day_offset in (0, 1):
        day = hk.date() - timedelta(days=day_offset)
        for slot in slots:
            moment = datetime.combine(day, slot, tzinfo=HONG_KONG)
            if moment <= hk:
                candidates.append(moment)
    return max(candidates).astimezone(timezone.utc)


def bar_what_to_show(sec_type: str) -> str:
    # Adjusted closes exist for stocks and ETFs only.
    return "TRADES" if sec_type == "IND" else "ADJUSTED_LAST"


# --- Storage --------------------------------------------------------------


class LiveStore:
    """Writes the live_ tables as the collector's role, one transaction per call.

    The collector calls it from worker threads so a slow database never stalls
    the gateway connection; the lock keeps each call's transaction whole.
    """

    def __init__(self, database_url: str, tenant_id: str, connect: Callable[..., Any] | None = None):
        self._database_url = database_url
        self._tenant_id = tenant_id
        self._connect = connect or self._default_connect
        self._connection = None
        self._lock = threading.RLock()

    def _default_connect(self):
        import psycopg

        from .db import PostgresConnection, _hybrid_row_factory

        connection = PostgresConnection(psycopg.connect(
            self._database_url, connect_timeout=10, row_factory=_hybrid_row_factory
        ))
        connection.execute("SELECT set_config('app.tenant_id', ?, false)", (self._tenant_id,))
        connection.commit()
        return connection

    def _conn(self):
        if self._connection is None:
            self._connection = self._connect()
        return self._connection

    def _run(self, work: Callable[[Any], Any]) -> Any:
        with self._lock:
            connection = self._conn()
            try:
                result = work(connection)
                connection.commit()
                return result
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
                # A broken connection is replaced on the next call.
                self.close()
                raise

    def _write(self, statements: Iterable[tuple[str, Sequence[Any]]]) -> None:
        def work(connection):
            for sql, parameters in statements:
                connection.execute(sql, parameters)

        self._run(work)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:
                    pass
                self._connection = None

    def write_status(self, row: Mapping[str, Any]) -> None:
        self._write([(
            """
            INSERT INTO live_gateway_status(
                observed_at, gateway_state, last_message_at, market_data_lines_in_use,
                market_data_type, last_error_code, last_error_message, collector_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, observed_at) DO NOTHING
            """,
            (
                row["observed_at"], row["gateway_state"], row["last_message_at"],
                row["market_data_lines_in_use"], row["market_data_type"],
                row["last_error_code"], row["last_error_message"], COLLECTOR_VERSION,
            ),
        )])

    def write_snapshot(self, account: Mapping[str, Any], positions: Sequence[Mapping[str, Any]]) -> None:
        """The account row and all its positions in one transaction."""
        statements = [(
            """
            INSERT INTO live_account_values(
                account_id, observed_at, base_currency, net_liquidation, total_cash,
                daily_pnl, unrealized_pnl, realized_pnl, cash_by_currency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS jsonb))
            """,
            (
                account["account_id"], account["observed_at"], account["base_currency"],
                account["net_liquidation"], account["total_cash"], account["daily_pnl"],
                account["unrealized_pnl"], account["realized_pnl"],
                json.dumps(account["cash_by_currency"], sort_keys=True),
            ),
        )]
        for row in positions:
            statements.append((
                """
                INSERT INTO live_positions(
                    account_id, observed_at, conid, symbol, sec_type, currency,
                    primary_exchange, quantity, average_cost, market_price, market_value,
                    unrealized_pnl, daily_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["account_id"], row["observed_at"], row["conid"], row["symbol"],
                    row["sec_type"], row["currency"], row["primary_exchange"], row["quantity"],
                    row["average_cost"], row["market_price"], row["market_value"],
                    row["unrealized_pnl"], row["daily_pnl"],
                ),
            ))
        self._write(statements)

    def write_quotes(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        self._write([(
            """
            INSERT INTO live_quotes(
                conid, observed_at, symbol, currency, last_price, bid_price, ask_price,
                prior_close, market_data_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, conid, observed_at) DO NOTHING
            """,
            (
                row["conid"], row["observed_at"], row["symbol"], row["currency"],
                row["last_price"], row["bid_price"], row["ask_price"], row["prior_close"],
                row["market_data_type"],
            ),
        ) for row in rows])

    def write_executions(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        # The commission report can arrive after the execution; a later
        # sighting fills it in without moving first_seen_at.
        self._write([(
            """
            INSERT INTO live_executions(
                exec_id, account_id, executed_at, conid, symbol, sec_type, currency, side,
                quantity, price, commission, commission_currency, realized_pnl,
                first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, exec_id) DO UPDATE SET
                commission = COALESCE(excluded.commission, live_executions.commission),
                commission_currency = COALESCE(
                    excluded.commission_currency, live_executions.commission_currency
                ),
                realized_pnl = COALESCE(excluded.realized_pnl, live_executions.realized_pnl),
                updated_at = excluded.updated_at
            WHERE (excluded.commission IS NOT NULL AND live_executions.commission IS NULL)
               OR (excluded.realized_pnl IS NOT NULL AND live_executions.realized_pnl IS NULL)
            """,
            (
                row["exec_id"], row["account_id"], row["executed_at"], row["conid"],
                row["symbol"], row["sec_type"], row["currency"], row["side"], row["quantity"],
                row["price"], row["commission"], row["commission_currency"],
                row["realized_pnl"], row["seen_at"], row["seen_at"],
            ),
        ) for row in rows])

    def write_contracts(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        self._write([(
            """
            INSERT INTO live_contracts(
                conid, symbol, local_symbol, sec_type, currency, primary_exchange,
                long_name, industry, category, subcategory, is_benchmark, refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, conid) DO UPDATE SET
                symbol = excluded.symbol, local_symbol = excluded.local_symbol,
                sec_type = excluded.sec_type, currency = excluded.currency,
                primary_exchange = excluded.primary_exchange, long_name = excluded.long_name,
                industry = excluded.industry, category = excluded.category,
                subcategory = excluded.subcategory,
                is_benchmark = live_contracts.is_benchmark OR excluded.is_benchmark,
                refreshed_at = excluded.refreshed_at
            """,
            (
                row["conid"], row["symbol"], row["local_symbol"], row["sec_type"],
                row["currency"], row["primary_exchange"], row["long_name"], row["industry"],
                row["category"], row["subcategory"], row["is_benchmark"], row["refreshed_at"],
            ),
        ) for row in rows])

    def write_bars(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        self._write([(
            """
            INSERT INTO live_daily_bars(
                conid, what_to_show, bar_date, open_price, high_price, low_price,
                close_price, volume, currency, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, conid, what_to_show, bar_date) DO UPDATE SET
                open_price = excluded.open_price, high_price = excluded.high_price,
                low_price = excluded.low_price, close_price = excluded.close_price,
                volume = excluded.volume, currency = excluded.currency,
                fetched_at = excluded.fetched_at
            """,
            (
                row["conid"], row["what_to_show"], row["bar_date"], row["open_price"],
                row["high_price"], row["low_price"], row["close_price"], row["volume"],
                row["currency"], row["fetched_at"],
            ),
        ) for row in rows])

    def latest_bar_dates(self) -> dict[tuple[str, str], date]:
        rows = self._run(lambda connection: connection.execute(
            "SELECT conid, what_to_show, max(bar_date) FROM live_daily_bars GROUP BY conid, what_to_show"
        ).fetchall())
        return {(row[0], row[1]): row[2] for row in rows}

    def purge(self, now: datetime) -> None:
        snapshot_cutoff = now - SNAPSHOT_RETENTION
        self._write([
            # live_positions rows go with their account snapshot (ON DELETE CASCADE).
            ("DELETE FROM live_account_values WHERE observed_at < ?", (snapshot_cutoff,)),
            ("DELETE FROM live_quotes WHERE observed_at < ?", (snapshot_cutoff,)),
            ("DELETE FROM live_gateway_status WHERE observed_at < ?", (snapshot_cutoff,)),
            ("DELETE FROM live_executions WHERE executed_at < ?", (now - EXECUTION_RETENTION,)),
        ])


# --- Collector ------------------------------------------------------------


def _default_contract_factory(**fields: Any) -> Any:
    from ib_async import Contract

    return Contract(**fields)


def write_status_file(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace the status file the VM's re-login check reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds") if moment else None


class LiveCollector:
    """One loop that keeps the live tables current. Never trades."""

    def __init__(
        self,
        settings: CollectorSettings,
        ib: Any,
        store: Any,
        *,
        clock: Callable[[], datetime] = utc_now,
        contract_factory: Callable[..., Any] = _default_contract_factory,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.ib = ib
        self.store = store
        self.clock = clock
        self.contract = contract_factory
        self.sleep = sleep
        self.server_connected = False
        self.last_message_at: datetime | None = None
        self.last_error: tuple[int, str] | None = None
        self.subscribed = False
        self.tickers: dict[str, Any] = {}
        self.contracts: dict[str, Any] = {}  # conid -> qualified Contract
        self.benchmark_conids: set[str] = set()
        self.last_snapshot_at: datetime | None = None
        self.last_contract_refresh: datetime | None = None
        self.last_bar_slot: datetime | None = None
        self.last_purge_slot: datetime | None = None
        self.last_quotes: dict[str, tuple] = {}
        self.missing_accounts: tuple[str, ...] = ()
        self.bars_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._next_connect_at: datetime | None = None
        self._connect_failures = 0
        ib.errorEvent += self._on_error
        ib.disconnectedEvent += self._on_disconnected
        ib.updateEvent += self._on_update

    # Events -----------------------------------------------------------

    def _on_error(self, req_id: int, code: int, message: str, contract: Any = None) -> None:
        if code in INFORMATIONAL_CODES:
            return
        if code in CONNECTIVITY_LOST_CODES:
            self.server_connected = False
        elif code in CONNECTIVITY_RESTORED_CODES:
            self.server_connected = True
        self.last_error = (int(code), str(message)[:300])
        LOGGER.warning("gateway error %s: %s", code, str(message)[:300])

    def _on_disconnected(self) -> None:
        self.server_connected = False
        self.subscribed = False
        self.tickers.clear()
        LOGGER.warning("disconnected from IB Gateway")

    def _on_update(self) -> None:
        self.last_message_at = self.clock()

    def stop(self) -> None:
        self._stopping.set()

    # Connection -------------------------------------------------------

    def gateway_state(self) -> str:
        return "connected" if self.ib.isConnected() and self.server_connected else "disconnected"

    async def ensure_connected(self, now: datetime) -> bool:
        if self.ib.isConnected():
            return True
        if self._next_connect_at and now < self._next_connect_at:
            return False
        try:
            await self.ib.connectAsync(
                self.settings.gateway_host,
                self.settings.gateway_port,
                clientId=self.settings.client_id,
                timeout=20,
                readonly=True,
            )
        except Exception as error:
            self._connect_failures += 1
            delay = min(30 * 2 ** (self._connect_failures - 1), 300)
            self._next_connect_at = now + timedelta(seconds=delay)
            LOGGER.warning("connect failed (%s); next try in %ss", type(error).__name__, delay)
            return False
        self._connect_failures = 0
        self._next_connect_at = None
        self.server_connected = True
        self.last_message_at = self.clock()
        self.ib.RequestTimeout = 30
        managed = set(self.ib.managedAccounts())
        self.missing_accounts = tuple(a for a in self.settings.account_ids if a not in managed)
        if self.missing_accounts:
            self.last_error = (0, "Configured accounts not on this login: " + ",".join(self.missing_accounts))
            LOGGER.error("configured accounts missing from the login: %s", ",".join(self.missing_accounts))
        LOGGER.info("connected to IB Gateway (read-only)")
        return True

    async def subscribe(self, now: datetime) -> None:
        """Market data type, account P&L and quotes for holdings and benchmarks."""
        self.ib.reqMarketDataType(self.settings.market_data_type)
        subscribed_pnl = {pnl.account for pnl in self.ib.pnl()}
        for account in self.settings.account_ids:
            if account not in self.missing_accounts and account not in subscribed_pnl:
                self.ib.reqPnL(account)
        await self.ib.accountSummaryAsync()
        await self.refresh_contracts(now)
        self.sync_market_data()
        self.subscribed = True

    def held_contracts(self) -> dict[str, Any]:
        held = {}
        for position in self.ib.positions():
            if getattr(position, "account", "") not in self.settings.account_ids:
                continue
            if not number(getattr(position, "position", None)):
                continue
            contract = position.contract
            held[str(contract.conId)] = contract
        return held

    async def refresh_contracts(self, now: datetime, conids: Iterable[str] | None = None) -> None:
        """Contract details for holdings and benchmarks (weekly, and for new holdings)."""
        rows = []
        targets: list[tuple[Any, bool]] = []
        if conids is None:
            targets.extend(
                (self.contract(secType=b.sec_type, symbol=b.symbol, exchange=b.exchange, currency=b.currency), True)
                for b in self.settings.benchmarks
            )
            wanted = list(self.held_contracts())
        else:
            wanted = list(conids)
        targets.extend((self.contract(conId=int(conid)), False) for conid in wanted)
        for request, is_benchmark in targets:
            try:
                details = await self.ib.reqContractDetailsAsync(request)
            except Exception as error:
                LOGGER.warning("contract details failed (%s)", type(error).__name__)
                continue
            if not details:
                continue
            row = contract_row(details[0], is_benchmark, now)
            if row is None:
                continue
            rows.append(row)
            self.contracts[row["conid"]] = details[0].contract
            if is_benchmark:
                self.benchmark_conids.add(row["conid"])
        await self._store("write_contracts", rows)
        if conids is None:
            self.last_contract_refresh = now

    def desired_quotes(self) -> list[str]:
        """Benchmarks first, then holdings by cost, within the line budget."""
        held = self.held_contracts()
        weight: dict[str, float] = {}
        for position in self.ib.positions():
            conid = str(position.contract.conId)
            cost = abs((number(position.position) or 0) * (number(position.avgCost) or 0))
            weight[conid] = weight.get(conid, 0.0) + cost
        ordered = sorted(self.benchmark_conids) + sorted(
            (c for c in held if c not in self.benchmark_conids), key=lambda c: -weight.get(c, 0.0)
        )
        quotable = [
            conid for conid in ordered
            if getattr(self.contracts.get(conid) or held.get(conid), "secType", "") in QUOTED_SEC_TYPES
        ]
        return quotable[: self.settings.max_market_data_lines]

    def sync_market_data(self) -> None:
        desired = self.desired_quotes()
        for conid in list(self.tickers):
            if conid not in desired:
                self.ib.cancelMktData(self.tickers.pop(conid).contract)
        for conid in desired:
            if conid in self.tickers:
                continue
            known = self.contracts.get(conid)
            if known is None:
                continue
            sec_type = getattr(known, "secType", "")
            exchange = (
                getattr(known, "exchange", "") if sec_type in {"IND", "FUT", "FOP"} else "SMART"
            ) or getattr(known, "primaryExchange", "") or "SMART"
            request = self.contract(conId=int(conid), exchange=exchange)
            self.tickers[conid] = self.ib.reqMktData(request, genericTickList="", snapshot=False)

    # Steps ------------------------------------------------------------

    def status_row(self, now: datetime) -> dict[str, Any]:
        code, message = self.last_error or (None, None)
        return {
            "observed_at": now,
            "gateway_state": self.gateway_state(),
            "last_message_at": self.last_message_at,
            "market_data_lines_in_use": len(self.tickers),
            "market_data_type": self.settings.market_data_type,
            "last_error_code": code,
            "last_error_message": message,
        }

    async def snapshot(self, now: datetime) -> None:
        held = self.held_contracts()
        new = [conid for conid in held if conid not in self.contracts]
        if new:
            await self.refresh_contracts(now, new)
        self.sync_market_data()
        summary = await self.ib.accountSummaryAsync()
        positions = self.ib.positions()
        pnl_by_account = {pnl.account: pnl for pnl in self.ib.pnl()}
        for account in self.settings.account_ids:
            if account in self.missing_accounts:
                continue
            row = account_row(account, summary, pnl_by_account.get(account), now)
            if row is None:
                LOGGER.info("account %s has no net liquidation value yet", account)
                continue
            await self._store("write_snapshot", row, position_rows(account, positions, self.tickers, now))
        quotes = []
        for conid, ticker in self.tickers.items():
            row = quote_row(conid, self.contracts.get(conid) or ticker.contract, ticker, now)
            if row is None:
                continue
            key = (row["last_price"], row["bid_price"], row["ask_price"], row["prior_close"], row["market_data_type"])
            if self.last_quotes.get(conid) == key:
                continue
            quotes.append(row)
            self.last_quotes[conid] = key
        await self._store("write_quotes", quotes)
        fills = await self.ib.reqExecutionsAsync()
        executions = [
            row for row in (execution_row(fill, self.settings.account_ids, now) for fill in fills) if row
        ]
        await self._store("write_executions", executions)
        self.last_snapshot_at = now

    async def fetch_bars(self, now: datetime) -> None:
        """Daily bars for holdings and benchmarks: a year at first, then the last two weeks.

        On Sundays the adjusted series is fetched in full again, so earlier
        closes pick up dividend and split adjustments.
        """
        latest = await self._store("latest_bar_dates")
        full_refresh = now.astimezone(HONG_KONG).weekday() == 6
        held = self.held_contracts()
        targets = sorted(
            conid for conid, contract in self.contracts.items()
            if getattr(contract, "secType", "") in BAR_SEC_TYPES
            and (conid in self.benchmark_conids or conid in held)
        )
        for index, conid in enumerate(targets):
            contract = self.contracts[conid]
            what_to_show = bar_what_to_show(contract.secType)
            have = latest.get((conid, what_to_show))
            duration = "1 Y" if have is None or (full_refresh and what_to_show == "ADJUSTED_LAST") else "14 D"
            exchange = getattr(contract, "exchange", "") if contract.secType == "IND" else "SMART"
            request = self.contract(conId=int(conid), exchange=exchange or "SMART")
            if index:
                await self.sleep(BAR_REQUEST_SPACING_SECONDS)
            try:
                bars = await self.ib.reqHistoricalDataAsync(
                    request, endDateTime="", durationStr=duration, barSizeSetting="1 day",
                    whatToShow=what_to_show, useRTH=True, formatDate=1,
                )
            except Exception as error:
                LOGGER.warning("bars failed for %s (%s)", conid, type(error).__name__)
                continue
            await self._store("write_bars", bar_rows(conid, contract.currency, what_to_show, bars or [], now))

    async def tick(self) -> None:
        """One pass of the loop; each step's failure is logged and retried next pass."""
        now = self.clock()
        connected = await self.ensure_connected(now)
        # The VM's re-login check reads this file; it never waits on the database.
        self.write_status_file(now)
        if connected and not self.subscribed:
            await self._step("subscribe", self.subscribe(now))
        if connected and self.subscribed:
            due = self.last_snapshot_at is None or (
                now - self.last_snapshot_at
            ).total_seconds() >= snapshot_interval(now) - 1
            if due:
                await self._step("snapshot", self.snapshot(now))
            if self.last_contract_refresh is None or now - self.last_contract_refresh >= CONTRACT_REFRESH:
                await self._step("contracts", self.refresh_contracts(now))
            slot = latest_slot(now, BAR_RUN_TIMES_HKT)
            if self.last_bar_slot != slot and (self.bars_task is None or self.bars_task.done()):
                # Paced bar requests take minutes; the minute loop carries on meanwhile.
                self.last_bar_slot = slot
                self.bars_task = asyncio.create_task(self._step("bars", self.fetch_bars(now)))
        purge_slot = latest_slot(now, (PURGE_TIME_HKT,))
        if self.last_purge_slot != purge_slot:
            self.last_purge_slot = purge_slot
            await self._step("purge", self._store("purge", now))
        await self._step("status", self._store("write_status", self.status_row(now)))

    def write_status_file(self, now: datetime) -> None:
        if not self.settings.status_file:
            return
        try:
            write_status_file(self.settings.status_file, {
                "gateway_state": self.gateway_state(),
                "observed_at": _iso(now),
                "last_message_at": _iso(self.last_message_at),
            })
        except OSError as error:
            LOGGER.warning("status file not written (%s)", type(error).__name__)

    async def _store(self, method: str, *args: Any) -> Any:
        return await asyncio.to_thread(getattr(self.store, method), *args)

    async def drain(self) -> None:
        """Wait for a running bar fetch (used when stopping and by tests)."""
        if self.bars_task is not None:
            await self.bars_task

    async def _step(self, name: str, awaitable: Any) -> None:
        try:
            await awaitable
        except Exception as error:
            LOGGER.warning("%s failed (%s)", name, type(error).__name__)

    async def run(self) -> None:
        while not self._stopping.is_set():
            await self.tick()
            now = self.clock()
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=60 - now.second - now.microsecond / 1_000_000
                )
            except asyncio.TimeoutError:
                pass
        if self.bars_task is not None and not self.bars_task.done():
            self.bars_task.cancel()
        if self.ib.isConnected():
            self.ib.disconnect()
        self.store.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = CollectorSettings.from_environment()
    from ib_async import IB

    collector = LiveCollector(settings, IB(), LiveStore(settings.database_url, settings.tenant_id))

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, collector.stop)
        await collector.run()

    asyncio.run(runner())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
