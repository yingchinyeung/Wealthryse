"""Database writer for the live_ tables (migration 063) and the market-hours rule.

These tables and the website's live view (off by default, per account's live_view switch)
outlive the retired TWS gateway: a later source, such as the IBKR Web API over OAuth
(ibkr_web_oauth.py), can fill them through LiveStore. COLLECTOR_VERSION keeps the value
the status rows were written with.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

COLLECTOR_VERSION = "live-collector/1"
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")
SNAPSHOT_RETENTION = timedelta(days=30)
EXECUTION_RETENTION = timedelta(days=366)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def markets_open(now: datetime) -> bool:
    """True during Hong Kong or US regular trading hours on a weekday.

    Holidays are not modelled.
    """
    hk = now.astimezone(HONG_KONG)
    if hk.weekday() < 5 and time(9, 30) <= hk.time() <= time(16, 10):
        return True
    ny = now.astimezone(NEW_YORK)
    return ny.weekday() < 5 and time(9, 30) <= ny.time() <= time(16, 0)



class LiveStore:
    """Writes the live_ tables as the ibkr_live_collector role, one transaction per call.

    Safe to call from worker threads: the lock keeps each call's transaction whole.
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


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds") if moment else None
