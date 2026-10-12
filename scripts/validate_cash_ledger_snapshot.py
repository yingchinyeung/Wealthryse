from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.derived_portfolio import (  # noqa: E402
    refresh_alpha_world_ms_portfolio,
    refresh_alpha_world_t_portfolio,
    refresh_hong_kong_china_portfolio,
)


SOURCE_TABLES = (
    "ingestion_runs",
    "accounts",
    "instruments",
    "flex_rows",
    "trades",
    "cash_transactions",
    "corporate_actions",
    "position_snapshots",
    "cash_snapshots",
    "nav_snapshots",
    "fx_rates",
    "fx_transactions",
    "market_data_prices",
)


def _columns(connection: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [
        row[1]
        for row in connection.execute(f'PRAGMA {schema}.table_info("{table}")')
    ]


def _copy_snapshot(source: Path, destination: Path) -> sqlite3.Connection:
    if destination.exists():
        destination.unlink()
    connection = sqlite3.connect(destination)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        (PROJECT_ROOT / "migrations" / "001_init.sql").read_text(encoding="utf-8")
    )
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("ATTACH DATABASE ? AS source", (str(source),))
    for table in SOURCE_TABLES:
        destination_columns = _columns(connection, "main", table)
        source_columns = set(_columns(connection, "source", table))
        common = [column for column in destination_columns if column in source_columns]
        if not common:
            raise RuntimeError(f"No common columns for {table}")
        quoted = ", ".join(f'"{column}"' for column in common)
        connection.execute(
            f'INSERT INTO main."{table}" ({quoted}) '
            f'SELECT {quoted} FROM source."{table}"'
        )
    connection.commit()
    connection.execute("DETACH DATABASE source")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the China & Hong Kong cash-ledger release against an analyst snapshot."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--reference-data",
        type=Path,
        default=PROJECT_ROOT / "data" / "reference_data.json",
    )
    args = parser.parse_args()

    previous = sqlite3.connect(args.source)
    previous.row_factory = sqlite3.Row
    try:
        previous_alpha_t = {
            row["snapshot_date"]: row["total_nav"]
            for row in previous.execute(
                """
                SELECT snapshot_date, total_nav
                FROM derived_portfolio_nav
                WHERE portfolio_id = 'alpha-world-t'
                ORDER BY snapshot_date
                """
            )
        }
    finally:
        previous.close()

    connection = _copy_snapshot(args.source, args.output)
    try:
        hong_kong_china = refresh_hong_kong_china_portfolio(
            connection, args.reference_data
        )
        alpha_world_ms = refresh_alpha_world_ms_portfolio(connection)
        alpha_world_t = refresh_alpha_world_t_portfolio(connection)

        included_twd = connection.execute(
            """
            SELECT COUNT(*)
            FROM derived_portfolio_cash_ledger
            WHERE portfolio_id = 'hong-kong-china'
              AND included = 1 AND currency = 'TWD'
            """
        ).fetchone()[0]
        included_other = connection.execute(
            """
            SELECT COUNT(*)
            FROM derived_portfolio_cash_ledger
            WHERE portfolio_id = 'hong-kong-china'
              AND included = 1 AND currency NOT IN ('HKD', 'USD')
            """
        ).fetchone()[0]
        opening = {
            row["currency"]: row["amount"]
            for row in connection.execute(
                """
                SELECT currency, amount
                FROM derived_portfolio_cash_ledger
                WHERE portfolio_id = 'hong-kong-china'
                  AND source_type = 'opening_balance'
                """
            )
        }
        alpha_ms_initial = connection.execute(
            """
            SELECT total_nav
            FROM derived_portfolio_nav
            WHERE portfolio_id = 'alpha-world-ms'
              AND snapshot_date = '2025-01-20'
            """
        ).fetchone()[0]
        current_alpha_t = {
            row["snapshot_date"]: row["total_nav"]
            for row in connection.execute(
                """
                SELECT snapshot_date, total_nav
                FROM derived_portfolio_nav
                WHERE portfolio_id = 'alpha-world-t'
                ORDER BY snapshot_date
                """
            )
        }
        shared_dates = sorted(set(previous_alpha_t).intersection(current_alpha_t))
        alpha_t_max_difference = max(
            (
                abs(previous_alpha_t[day] - current_alpha_t[day])
                for day in shared_dates
            ),
            default=0.0,
        )

        assert opening == {"HKD": 484300.0, "USD": 0.0}
        assert included_twd == 0
        assert included_other == 0
        assert abs(alpha_ms_initial - 100.0) < 1e-9
        assert alpha_t_max_difference < 1e-9
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()

        excluded_reasons = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT exclusion_reason, COUNT(*)
                FROM derived_portfolio_cash_ledger
                WHERE portfolio_id = 'hong-kong-china' AND included = 0
                GROUP BY exclusion_reason
                ORDER BY COUNT(*) DESC, exclusion_reason
                """
            )
        ]
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "hong_kong_china": hong_kong_china,
                    "alpha_world_ms": alpha_world_ms,
                    "alpha_world_t": alpha_world_t,
                    "opening_cash": opening,
                    "included_twd_events": included_twd,
                    "included_other_currency_events": included_other,
                    "alpha_world_ms_initial_nav": alpha_ms_initial,
                    "alpha_world_t_max_difference": alpha_t_max_difference,
                    "excluded_cash_reasons": excluded_reasons,
                    "sqlite_quick_check": "ok",
                    "foreign_key_violations": 0,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
