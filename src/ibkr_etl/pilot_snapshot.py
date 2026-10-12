from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .snapshot_evidence import (
    TABLE as EVIDENCE_TABLE, copy_financial_evidence, inspect_financial_evidence,
    legacy_evidence_status, table_fingerprint,
)


ACCOUNT_TABLES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "trades": ("trade_date", "", ("account_id", "source_key")),
    "cash_transactions": (
        "effective_date",
        "",
        ("account_id", "source_key"),
    ),
    "corporate_actions": (
        "effective_date",
        "",
        ("account_id", "source_key"),
    ),
    "position_snapshots": (
        "snapshot_date",
        "",
        ("account_id", "snapshot_date", "conid", "model_code"),
    ),
    "cash_snapshots": (
        "snapshot_date",
        "",
        ("account_id", "snapshot_date", "currency"),
    ),
    "nav_snapshots": (
        "snapshot_date",
        "",
        ("account_id", "snapshot_date", "currency", "asset_class"),
    ),
    "fx_rates": (
        "rate_date",
        "",
        ("account_id", "rate_date", "from_currency", "to_currency"),
    ),
    "fx_transactions": (
        "report_date",
        "",
        ("account_id", "source_key"),
    ),
}


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _create_transfer_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
    columns: list[str],
) -> None:
    info = {row[1]: row for row in source.execute(f"PRAGMA table_info({_quote(table)})")}
    definitions = []
    for column in columns:
        declared = str(info[column][2] or "TEXT")
        definitions.append(f"{_quote(column)} {declared}")
    target.execute(
        f"CREATE TABLE {_quote(table)} ({', '.join(definitions)})"
    )


def _copy_query(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
    columns: list[str],
    query: str,
    parameters: tuple[Any, ...],
) -> int:
    _create_transfer_table(source, target, table, columns)
    rows = source.execute(query, parameters).fetchall()
    if rows:
        placeholders = ", ".join("?" for _ in columns)
        target.executemany(
            f"INSERT INTO {_quote(table)} VALUES ({placeholders})",
            [tuple(row[column] for column in columns) for row in rows],
        )
    return len(rows)


def build_pilot_snapshot(
    source_path: Path,
    output_path: Path,
    *,
    account_id: str,
    start_date: str,
    generated_at: str | None = None,
    include_financial_evidence: bool = False,
) -> dict[str, Any]:
    """Publish an export only after validation; retain an earlier file on failure."""
    source_path, output_path = source_path.resolve(), output_path.resolve()
    if source_path == output_path:
        raise ValueError("Source and output snapshot paths must differ")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".snapshot-", suffix=".sqlite", dir=output_path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        result = _build_pilot_snapshot(
            source_path, temporary, account_id=account_id, start_date=start_date,
            generated_at=generated_at, include_financial_evidence=include_financial_evidence,
        )
        validate_pilot_snapshot(temporary, account_id=account_id, start_date=start_date)
        os.replace(temporary, output_path)
        return {**result, "output_path": str(output_path)}
    finally:
        temporary.unlink(missing_ok=True)


def _build_pilot_snapshot(
    source_path: Path,
    output_path: Path,
    *,
    account_id: str,
    start_date: str,
    generated_at: str | None = None,
    include_financial_evidence: bool = False,
) -> dict[str, Any]:
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    if date.fromisoformat(start_date).isoformat() != start_date:
        raise ValueError("Start date must be an ISO calendar date")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path == output_path:
        raise ValueError("Source and output snapshot paths must differ")

    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro", uri=True
    )
    source.row_factory = sqlite3.Row
    source.execute("BEGIN")  # All tables and evidence see one consistent read snapshot.
    integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        source.close()
        raise RuntimeError(f"Source snapshot integrity check failed: {integrity}")
    account = source.execute(
        "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()
    if account is None:
        source.close()
        raise ValueError(f"Account not found in source snapshot: {account_id}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    target = sqlite3.connect(output_path)
    target.row_factory = sqlite3.Row
    row_counts: dict[str, int] = {}
    try:
        account_columns = [
            row[1]
            for row in source.execute("PRAGMA table_info(accounts)")
            if row[1] != "tenant_id"
        ]
        row_counts["accounts"] = _copy_query(
            source,
            target,
            "accounts",
            account_columns,
            f"SELECT {', '.join(_quote(c) for c in account_columns)} FROM accounts WHERE account_id = ?",
            (account_id,),
        )

        referenced_conids: set[str] = set()
        for table, (date_column, _, _) in ACCOUNT_TABLES.items():
            if include_financial_evidence:
                for row in source.execute(
                    f"SELECT DISTINCT {_quote(date_column)} FROM {_quote(table)} WHERE account_id=?",
                    (account_id,),
                ):
                    try:
                        valid_date = date.fromisoformat(row[0]).isoformat() == row[0]
                    except (TypeError, ValueError):
                        valid_date = False
                    if not valid_date:
                        raise RuntimeError(f"{table} has an undated or malformed source observation")
            columns = [
                row[1]
                for row in source.execute(f"PRAGMA table_info({_quote(table)})")
                if row[1] not in {"last_seen_run_id", "tenant_id"}
            ]
            query = (
                f"SELECT {', '.join(_quote(c) for c in columns)} "
                f"FROM {_quote(table)} WHERE account_id = ? "
                f"AND {_quote(date_column)} >= ?"
            )
            row_counts[table] = _copy_query(
                source, target, table, columns, query, (account_id, start_date)
            )
            if "conid" in columns:
                referenced_conids.update(
                    str(row[0])
                    for row in target.execute(
                        f"SELECT DISTINCT conid FROM {_quote(table)} "
                        "WHERE conid IS NOT NULL AND conid <> ''"
                    )
                )

        instrument_columns = [
            row[1] for row in source.execute("PRAGMA table_info(instruments)")
        ]
        _create_transfer_table(source, target, "instruments", instrument_columns)
        if referenced_conids:
            ordered_conids = sorted(referenced_conids)
            placeholders = ",".join("?" for _ in ordered_conids)
            rows = source.execute(
                f"SELECT {', '.join(_quote(c) for c in instrument_columns)} "
                f"FROM instruments WHERE conid IN ({placeholders})",
                tuple(ordered_conids),
            ).fetchall()
            target.executemany(
                f"INSERT INTO instruments VALUES ({', '.join('?' for _ in instrument_columns)})",
                [tuple(row[column] for column in instrument_columns) for row in rows],
            )
            row_counts["instruments"] = len(rows)
        else:
            row_counts["instruments"] = 0

        source_metadata = dict(source.execute("SELECT key, value FROM snapshot_metadata"))
        metadata = {
            "format_version": "3" if include_financial_evidence else "2",
            "account_id": account_id,
            "start_date": start_date,
            "generated_at": generated_at
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_generated_at": source_metadata.get("generated_at", ""),
            "source_sha256": _source_sha256(source_path),
            "row_counts_json": json.dumps(row_counts, sort_keys=True),
        }
        if include_financial_evidence:
            metadata["source_hash_scope"] = "sqlite_main_file_only; transferred values are bound by table fingerprints"
            evidence = copy_financial_evidence(
                source, target, account_id=account_id, start_date=start_date
            )
            row_counts[EVIDENCE_TABLE] = evidence["preserved_rows"]
            metadata["row_counts_json"] = json.dumps(row_counts, sort_keys=True)
            metadata["financial_evidence_json"] = json.dumps(evidence, sort_keys=True)
            metadata["table_fingerprints_json"] = json.dumps(
                {table: table_fingerprint(target, table) for table in row_counts}, sort_keys=True
            )
        target.execute(
            "CREATE TABLE snapshot_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        target.executemany(
            "INSERT INTO snapshot_metadata(key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        target.commit()
        target.execute("VACUUM")
    finally:
        target.close()
        source.close()

    digest = _source_sha256(output_path)
    return {
        **metadata,
        "output_path": str(output_path),
        "output_sha256": digest,
        "row_counts": row_counts,
    }


def validate_pilot_snapshot(
    snapshot_path: Path, *, account_id: str, start_date: str
) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{snapshot_path.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Pilot snapshot integrity check failed")
        metadata = dict(connection.execute("SELECT key, value FROM snapshot_metadata"))
        version = metadata.get("format_version")
        if version not in {"2", "3"}:
            raise RuntimeError("Unsupported pilot snapshot format version")
        if metadata.get("account_id") != account_id:
            raise RuntimeError("Pilot snapshot account metadata does not match")
        if metadata.get("start_date") != start_date:
            raise RuntimeError("Pilot snapshot start-date metadata does not match")
        accounts = [
            row[0] for row in connection.execute("SELECT account_id FROM accounts")
        ]
        if accounts != [account_id]:
            raise RuntimeError(f"Pilot snapshot contains unexpected accounts: {accounts}")
        counts: dict[str, int] = {}
        coverage: dict[str, dict[str, Any]] = {}
        for table, (date_column, _, keys) in ACCOUNT_TABLES.items():
            unexpected = connection.execute(
                f"SELECT COUNT(*) FROM {_quote(table)} WHERE account_id IS NULL OR account_id <> ?",
                (account_id,),
            ).fetchone()[0]
            if unexpected:
                raise RuntimeError(f"{table} contains another account")
            too_early = connection.execute(
                f"SELECT COUNT(*) FROM {_quote(table)} WHERE {_quote(date_column)} < ?",
                (start_date,),
            ).fetchone()[0]
            if too_early:
                raise RuntimeError(f"{table} contains rows before {start_date}")
            if version == "3":
                seen = set()
                for item in connection.execute(f"SELECT * FROM {_quote(table)}"):
                    try:
                        valid_date = date.fromisoformat(item[date_column]).isoformat() == item[date_column]
                    except (TypeError, ValueError):
                        valid_date = False
                    identity = tuple(item[key] for key in keys)
                    if not valid_date or None in identity or identity in seen:
                        raise RuntimeError(f"{table} has invalid dates or duplicate/missing identities")
                    seen.add(identity)
            row = connection.execute(
                f"SELECT COUNT(*), MIN({_quote(date_column)}), MAX({_quote(date_column)}) FROM {_quote(table)}"
            ).fetchone()
            counts[table] = int(row[0])
            coverage[table] = {"first": row[1], "last": row[2]}
        counts["accounts"] = 1
        counts["instruments"] = int(
            connection.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        )
        evidence = legacy_evidence_status()
        if version == "3":
            recorded = json.loads(metadata["financial_evidence_json"])
            evidence = inspect_financial_evidence(
                connection, account_id=account_id, start_date=start_date,
                source_table_available=recorded.get("source_table_available"),
            )
            if evidence != recorded:
                raise RuntimeError("Financial evidence summary does not match stored records")
            counts[EVIDENCE_TABLE] = evidence["preserved_rows"]
            hashes = {table: table_fingerprint(connection, table) for table in counts}
            if hashes != json.loads(metadata["table_fingerprints_json"]):
                raise RuntimeError("Pilot snapshot table content fingerprints do not match")
        expected = json.loads(metadata["row_counts_json"])
        if counts != expected:
            raise RuntimeError(
                f"Pilot snapshot row counts do not match metadata: {counts} != {expected}"
            )
        return {
            "status": "PASS",
            "validation_scope": "snapshot_structure_and_integrity",
            "financial_evidence": evidence,
            "metadata": metadata,
            "row_counts": counts,
            "coverage": coverage,
            "sha256": _source_sha256(snapshot_path),
        }
    finally:
        connection.close()
