from __future__ import annotations

import sqlite3
from typing import Any


def reconcile_run(connection: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    result: dict[str, Any] = {"run_id": run_id, "checks": {}, "warnings": []}

    flex_count = connection.execute(
        "SELECT COUNT(*) FROM flex_rows WHERE last_seen_run_id = ?", (run_id,)
    ).fetchone()[0]
    result["checks"]["normalized_rows_seen"] = int(flex_count)

    accounts = [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT account_id FROM flex_rows WHERE last_seen_run_id = ? ORDER BY account_id",
            (run_id,),
        )
    ]
    result["checks"]["accounts"] = accounts
    if not accounts or accounts == ["UNKNOWN"]:
        result["warnings"].append("No identifiable IBKR account was found")

    duplicate_execution_ids = connection.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT account_id, execution_id
            FROM trades
            WHERE execution_id IS NOT NULL AND execution_id <> ''
            GROUP BY account_id, execution_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    result["checks"]["duplicate_execution_ids"] = int(duplicate_execution_ids)
    if duplicate_execution_ids:
        result["warnings"].append("Duplicate execution IDs exist")

    for table in (
        "trades", "cash_transactions", "corporate_actions", "position_snapshots",
        "cash_snapshots", "nav_snapshots", "fx_rates", "fx_transactions",
    ):
        count = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE last_seen_run_id = ?", (run_id,)
        ).fetchone()[0]
        result["checks"][table] = int(count)

    result["status"] = "WARN" if result["warnings"] else "PASS"
    return result
