from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID


EXPORT_QUERIES: tuple[tuple[str, str], ...] = (
    ("tenants", "SELECT * FROM tenants ORDER BY tenant_id"),
    ("ingestion_runs", "SELECT * FROM ingestion_runs ORDER BY run_id"),
    ("raw_reports", "SELECT * FROM raw_reports ORDER BY raw_report_id"),
    ("accounts", "SELECT * FROM accounts ORDER BY account_id"),
    (
        "instruments",
        """
        SELECT i.*
        FROM instruments i
        WHERE i.conid IN (
            SELECT conid FROM flex_rows WHERE conid IS NOT NULL
            UNION SELECT conid FROM trades WHERE conid IS NOT NULL
            UNION SELECT conid FROM cash_transactions WHERE conid IS NOT NULL
            UNION SELECT conid FROM corporate_actions WHERE conid IS NOT NULL
            UNION SELECT conid FROM position_snapshots WHERE conid IS NOT NULL
        )
        ORDER BY i.conid
        """,
    ),
    ("flex_rows", "SELECT * FROM flex_rows ORDER BY flex_row_id"),
    ("trades", "SELECT * FROM trades ORDER BY trade_date, trade_time, source_key"),
    (
        "cash_transactions",
        "SELECT * FROM cash_transactions ORDER BY effective_date, source_key",
    ),
    (
        "corporate_actions",
        "SELECT * FROM corporate_actions ORDER BY effective_date, source_key",
    ),
    (
        "position_snapshots",
        "SELECT * FROM position_snapshots ORDER BY snapshot_date, account_id, conid",
    ),
    (
        "cash_snapshots",
        "SELECT * FROM cash_snapshots ORDER BY snapshot_date, account_id, currency",
    ),
    (
        "nav_snapshots",
        "SELECT * FROM nav_snapshots ORDER BY snapshot_date, account_id, currency, asset_class",
    ),
    (
        "fx_rates",
        "SELECT * FROM fx_rates ORDER BY rate_date, account_id, from_currency, to_currency",
    ),
    (
        "fx_transactions",
        "SELECT * FROM fx_transactions ORDER BY report_date, account_id, source_key",
    ),
    (
        "analytics_daily",
        "SELECT * FROM analytics_daily ORDER BY as_of_date, account_id, calculation_version",
    ),
    ("report_runs", "SELECT * FROM report_runs ORDER BY report_run_id"),
    (
        "derived_portfolios",
        "SELECT * FROM derived_portfolios ORDER BY name, portfolio_id",
    ),
    (
        "derived_portfolio_nav",
        "SELECT * FROM derived_portfolio_nav ORDER BY portfolio_id, snapshot_date, currency",
    ),
    (
        "derived_portfolio_positions",
        "SELECT * FROM derived_portfolio_positions ORDER BY portfolio_id, snapshot_date, conid, model_code",
    ),
    (
        "derived_portfolio_exclusions",
        "SELECT * FROM derived_portfolio_exclusions ORDER BY portfolio_id, snapshot_date, conid, model_code",
    ),
    (
        "derived_portfolio_cash_ledger",
        "SELECT * FROM derived_portfolio_cash_ledger ORDER BY portfolio_id, event_date, event_key",
    ),
    (
        "derived_portfolio_cash_balances",
        "SELECT * FROM derived_portfolio_cash_balances ORDER BY portfolio_id, snapshot_date, currency",
    ),
    (
        "market_data_prices",
        "SELECT * FROM market_data_prices ORDER BY instrument_id, price_date, price_type",
    ),
    (
        "synthetic_portfolio_components",
        "SELECT * FROM synthetic_portfolio_components ORDER BY portfolio_id, snapshot_date, component_id",
    ),
    (
        "v_latest_positions",
        "SELECT * FROM v_latest_positions ORDER BY account_id, conid, model_code",
    ),
    (
        "v_daily_nav",
        "SELECT * FROM v_daily_nav ORDER BY snapshot_date, account_id, currency",
    ),
)


def _column_name(description: Any) -> str:
    name = getattr(description, "name", None)
    return str(name if name is not None else description[0])


def _sqlite_type(values: Iterable[Any]) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, int):
            return "INTEGER"
        if isinstance(value, (float, Decimal)):
            return "REAL"
        if isinstance(value, (bytes, bytearray, memoryview)):
            return "BLOB"
        return "TEXT"
    return "TEXT"


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bytes)):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, (datetime, date, UUID)):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def export_query(
    source_connection: Any,
    target_connection: sqlite3.Connection,
    table_name: str,
    sql: str,
) -> int:
    cursor = source_connection.execute(sql)
    rows = cursor.fetchall()
    columns = [_column_name(item) for item in (cursor.description or ())]
    if not columns:
        raise RuntimeError(f"Export query for {table_name} returned no columns")

    column_types = [
        _sqlite_type(row[index] for row in rows) for index in range(len(columns))
    ]
    definition = ", ".join(
        f"{_quote(name)} {column_type}"
        for name, column_type in zip(columns, column_types)
    )
    target_connection.execute(f"DROP TABLE IF EXISTS {_quote(table_name)}")
    target_connection.execute(f"CREATE TABLE {_quote(table_name)} ({definition})")

    if rows:
        placeholders = ", ".join("?" for _ in columns)
        target_connection.executemany(
            f"INSERT INTO {_quote(table_name)} VALUES ({placeholders})",
            [tuple(_coerce(row[index]) for index in range(len(columns))) for row in rows],
        )
    return len(rows)


def export_snapshot(
    source_connection: Any,
    target_path: Path,
    *,
    tenant_id: str,
    generated_at: str,
    exports: tuple[tuple[str, str], ...] = EXPORT_QUERIES,
) -> dict[str, Any]:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()

    target = sqlite3.connect(target_path)
    try:
        target.execute("PRAGMA journal_mode = DELETE")
        target.execute("PRAGMA foreign_keys = ON")
        row_counts: dict[str, int] = {}
        for table_name, sql in exports:
            row_counts[table_name] = export_query(
                source_connection, target, table_name, sql
            )
        target.execute(
            """
            CREATE TABLE snapshot_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        target.executemany(
            "INSERT INTO snapshot_metadata(key, value) VALUES (?, ?)",
            (
                ("generated_at", generated_at),
                ("tenant_id", tenant_id),
                ("source", "Azure Database for PostgreSQL - tenant-scoped export"),
                ("format_version", "1"),
            ),
        )
        target.commit()
        target.execute("VACUUM")
    finally:
        target.close()

    digest = hashlib.sha256(target_path.read_bytes()).hexdigest()
    return {
        "generated_at": generated_at,
        "tenant_id": tenant_id,
        "format_version": 1,
        "sqlite_file": target_path.name,
        "sqlite_sha256": digest,
        "row_counts": row_counts,
    }


def build_csv_zip(
    snapshot_path: Path,
    zip_path: Path,
    manifest: dict[str, Any],
) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    source = sqlite3.connect(snapshot_path)
    source.row_factory = sqlite3.Row
    try:
        with zipfile.ZipFile(
            zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(
                "README.txt",
                "IBKR analyst CSV export\n"
                "Generated after the successful daily import and health check.\n"
                "Files are tenant-scoped by PostgreSQL row-level security.\n"
                "The PostgreSQL database remains the system of record.\n",
            )
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True),
            )
            for table_name in manifest["row_counts"]:
                cursor = source.execute(f"SELECT * FROM {_quote(table_name)}")
                columns = [item[0] for item in cursor.description or ()]
                from io import StringIO

                buffer = StringIO(newline="")
                writer = csv.writer(buffer, lineterminator="\n")
                writer.writerow(columns)
                while True:
                    batch = cursor.fetchmany(1000)
                    if not batch:
                        break
                    writer.writerows(tuple(row) for row in batch)
                archive.writestr(f"{table_name}.csv", buffer.getvalue())
    finally:
        source.close()
