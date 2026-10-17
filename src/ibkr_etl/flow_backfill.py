"""Load the Flex sections behind external flows from v1.1's archived statements.

Physical accounts imported by account-sync have no Transfers or
CashTransactions rows in flex_rows, so the external-flow reader
(provisional_benchmark._external_flows) finds no flows for them. v2's own
Flex pull stores those sections from its first 14-day window onward. This
backfill loads them for the full history with v2's parser and loader, so
history and new data are read the same way.

Dry run by default. Applying inserts only missing rows and never overwrites.
It refuses to add rows that create an external flow unless the caller
accepts that live returns for those dates will change, and refuses accounts
that do not belong to the tenant.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .db import connect, initialize, start_run
from .loader import load_report
from .parser import ParsedReport, parse_flex_xml
from .provisional_benchmark import _external_flows, _is_external_cash_transaction
from .switch_dry_run import Statement, order_statements
from .util import canonical_json, sha256_bytes, utc_now_iso

SECTIONS = ("Transfers", "CashTransactions")
RUN_TYPE = "v11-archive-flow-backfill"
# Rows the flow reader needs besides the two sections: base currency and FX.
SUPPORT_TYPES = {"flexstatement", "accountinformation", "conversionrate", "fxrate", "currencyconversionrate"}


class BackfillError(RuntimeError):
    pass


def _wanted(report: ParsedReport, accounts: set[str]) -> ParsedReport:
    rows = [row for row in report.rows if row.account_id in accounts
            and (row.section in SECTIONS or row.record_type.lower() in SUPPORT_TYPES)]
    return replace(report, rows=rows)


def load_candidates(statements: Iterable[Statement], content: Callable[[Statement], bytes], *,
                    accounts: Iterable[str], work_dir: Path) -> Path:
    """Load the needed rows of every statement, oldest first, into a temporary SQLite."""

    wanted = set(accounts)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "flows.sqlite"
    initialize(path)
    connection = connect(path)
    try:
        for statement in order_statements(statements):
            raw = content(statement)
            if sha256_bytes(raw) != statement.sha256:
                raise BackfillError(f"Statement {statement.name} changed while being read")
            report = _wanted(parse_flex_xml(raw), wanted)
            if not report.rows:
                continue
            run_id = start_run(connection, RUN_TYPE, statement.from_date, statement.to_date)
            load_report(connection, report, run_id, legacy_key_compat=False)
            connection.commit()
    finally:
        connection.close()
    return path


def _candidate_rows(candidates: sqlite3.Connection, account_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in candidates.execute(
        f"SELECT account_id, section, record_type, source_key, report_date, conid, currency, row_sha256, data_json "
        f"FROM flex_rows WHERE account_id = ? AND section IN ({', '.join('?' for _ in SECTIONS)}) "
        "ORDER BY section, record_type, source_key",
        (account_id, *SECTIONS),
    )]


def _existing(live: Any, tenant_id: str, account_id: str) -> dict[tuple, str]:
    rows = live.execute(
        "SELECT section, record_type, source_key, row_sha256 FROM flex_rows "
        "WHERE tenant_id = %s AND account_id = %s AND section = ANY(%s)",
        (tenant_id, account_id, list(SECTIONS)),
    ).fetchall()
    return {(row[0], row[1], row[2]): row[3] for row in rows}


def _frozen_periods(live: Any, tenant_id: str, account_id: str) -> list[str]:
    return sorted({str(row[0]) for row in live.execute(
        "SELECT period_end FROM reporting_snapshots WHERE tenant_id = %s AND subject_id = %s "
        "AND status IN ('frozen', 'approved')", (tenant_id, account_id),
    ).fetchall()})


def plan(candidates_path: Path, *, accounts: Iterable[str], live: Any = None,
         tenant_id: str = "") -> dict[str, Any]:
    """What applying would insert, and which flows that adds."""

    connection = sqlite3.connect(candidates_path)
    connection.row_factory = sqlite3.Row
    result: dict[str, Any] = {"accounts": {}}
    try:
        for account_id in sorted(set(accounts)):
            rows = _candidate_rows(connection, account_id)
            existing = _existing(live, tenant_id, account_id) if live is not None else {}
            new, same, different = [], 0, 0
            for row in rows:
                key = (row["section"], row["record_type"], row["source_key"])
                if key not in existing:
                    new.append(row)
                elif existing[key] == row["row_sha256"]:
                    same += 1
                else:
                    different += 1
            # Flows from the new rows only: rows already live are already counted.
            new_keys = {(row["section"], row["record_type"], row["source_key"]) for row in new}
            for row in rows:
                if (row["section"], row["record_type"], row["source_key"]) not in new_keys:
                    connection.execute(
                        "DELETE FROM flex_rows WHERE account_id = ? AND section = ? AND record_type = ? AND source_key = ?",
                        (account_id, row["section"], row["record_type"], row["source_key"]),
                    )
            flows = {day: amount for day, amount in sorted(_external_flows(
                connection, account_id, subject_type="physical_account").items()) if amount}
            external = [row for row in new if row["section"] == "CashTransactions"
                        and _is_external_cash_transaction(_json(row["data_json"]))]
            frozen = _frozen_periods(live, tenant_id, account_id) if live is not None else []
            first_flow = min(flows, default=None)
            dates = sorted(row["report_date"] for row in rows if row["report_date"])
            result["accounts"][account_id] = {
                "candidate_rows": len(rows),
                "candidate_sections": dict(Counter(row["section"] for row in rows)),
                "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
                "new_rows": len(new),
                "already_live_same": same,
                "already_live_different": different,
                "new_external_cash_transactions": len(external),
                "new_transfers": sum(1 for row in new if row["section"] == "Transfers"),
                "flows_added": flows,
                "unconverted_external_rows": bool(external) and not flows,
                "frozen_periods_that_would_differ_from_live": [
                    period for period in frozen if first_flow and period >= first_flow
                ],
                "rows": new,
            }
    finally:
        connection.rollback()
        connection.close()
    result["flows_added"] = any(item["flows_added"] or item["new_transfers"] or item["new_external_cash_transactions"]
                                for item in result["accounts"].values())
    result["new_rows"] = sum(item["new_rows"] for item in result["accounts"].values())
    return result


def _json(text: str) -> dict:
    value = json.loads(text)
    return value if isinstance(value, dict) else {}


def without_rows(planned: dict[str, Any]) -> dict[str, Any]:
    """The plan for a report file: counts, dates and flows, no raw rows."""

    accounts = {account: {key: value for key, value in item.items() if key != "rows"}
                for account, item in planned["accounts"].items()}
    return {**planned, "accounts": accounts}


def apply(live: Any, planned: dict[str, Any], *, tenant_id: str, accept_flow_changes: bool = False,
          requested: tuple[str | None, str | None] = (None, None)) -> dict[str, Any]:
    """Insert the planned new rows in one transaction. `live` is a psycopg connection."""

    if planned["flows_added"] and not accept_flow_changes:
        raise BackfillError("The backfill adds external flows, so live returns would change; "
                            "rerun with acceptance after the owner agrees")
    rows = [row for item in planned["accounts"].values() for row in item["rows"]]
    if not rows:
        return {"status": "nothing_to_insert", "inserted": 0}
    live.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    unowned = unowned_accounts(live, planned["accounts"])
    if unowned:
        live.rollback()
        raise BackfillError(f"Accounts not in this tenant, nothing was written: {', '.join(unowned)}")
    # Excludes only jobs that take the same lock (the cloud daily job and repair scripts), not
    # account-sync or the institutional worker. If one of those writes a planned row meanwhile,
    # the count check below rolls back the whole insert.
    if not live.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", (f"ibkr-daily:{tenant_id}",)).fetchone()[0]:
        live.rollback()
        raise BackfillError("Another job holds this tenant's ibkr-daily lock; try again later")
    run_id = live.execute(
        "INSERT INTO ingestion_runs(run_type, requested_from, requested_to, started_at, status) "
        "VALUES (%s, %s, %s, %s, 'running') RETURNING run_id",
        (RUN_TYPE, requested[0], requested[1], utc_now_iso()),
    ).fetchone()[0]
    inserted = 0
    for row in rows:
        cursor = live.execute(
            "INSERT INTO flex_rows(account_id, section, record_type, source_key, report_date, conid, currency, "
            "row_sha256, data_json, first_seen_run_id, last_seen_run_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (account_id, section, record_type, source_key) DO NOTHING",
            (row["account_id"], row["section"], row["record_type"], row["source_key"], row["report_date"],
             row["conid"], row["currency"], row["row_sha256"], row["data_json"], run_id, run_id),
        )
        inserted += cursor.rowcount
    if inserted != len(rows):
        live.rollback()
        raise BackfillError("Another writer added some of these rows meanwhile; the whole insert was "
                            "rolled back and nothing was written")
    summary = {account: {"inserted": item["new_rows"], "sections": dict(Counter(r["section"] for r in item["rows"]))}
               for account, item in planned["accounts"].items()}
    live.execute(
        "UPDATE ingestion_runs SET status = 'success', completed_at = %s, row_count = %s, "
        "reconciliation_json = %s WHERE run_id = %s",
        (utc_now_iso(), inserted, canonical_json({"status": "PASS", "accounts": summary}), run_id),
    )
    live.commit()
    return {"status": "inserted", "inserted": inserted, "run_id": run_id}


def unowned_accounts(live: Any, accounts: Iterable[str]) -> list[str]:
    """Accounts that fail account_belongs_to_current_tenant (app.tenant_id must be set)."""

    return [account for account in sorted(set(accounts)) if not live.execute(
        "SELECT account_belongs_to_current_tenant(%s)", (account,)).fetchone()[0]]


def registry_accounts(live: Any, tenant_id: str) -> list[str]:
    """Registry accounts account-sync imports from v1.1 (its _registry_account_targets rule)."""

    rows = live.execute(
        """
        SELECT r.subject_id FROM subject_operating_registrations r
        JOIN platform_subjects s ON s.tenant_id = r.tenant_id AND s.subject_id = r.subject_id
        WHERE r.tenant_id = %s AND r.capability = 'daily_ingestion'
          AND r.operating_state IN ('preview', 'active') AND r.effective_to IS NULL
          AND s.subject_type = 'physical_account'
          AND r.configuration_json ->> 'source' = 'existing_v11'
        ORDER BY r.subject_id
        """,
        (tenant_id,),
    ).fetchall()
    return [str(row[0]) for row in rows]
