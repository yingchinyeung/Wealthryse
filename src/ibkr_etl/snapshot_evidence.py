"""Private, account-scoped transfer evidence. Preservation is not completeness.

These hashes detect accidental modification; they are not signatures. Trust in
the artifact still requires its externally recorded SHA-256 and access controls.
No raw-file paths, credentials or unrelated AccountInformation are copied.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from .util import canonical_json, pick, sha256_text


TABLE = "snapshot_flex_evidence"
FX_TYPES = ("conversionrate", "fxrate", "currencyconversionrate")
COLUMNS = (
    "account_id", "section", "record_type", "source_key", "report_date",
    "conid", "currency", "row_sha256", "data_json", "source_lineage_json",
)
RUN_FIELDS = (
    "run_id", "run_type", "requested_from", "requested_to", "status",
    "started_at", "completed_at", "raw_sha256",
)


def table_fingerprint(connection: sqlite3.Connection, table: str) -> str:
    # Names come only from the builder's closed table inventory.
    quoted = '"' + table.replace('"', '""') + '"'
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({quoted})")]
    rows = sorted(canonical_json(list(row)) for row in connection.execute(f"SELECT * FROM {quoted}"))
    return sha256_text(canonical_json({"columns": columns, "rows": rows}))


def copy_financial_evidence(source, target, *, account_id: str, start_date: str) -> dict:
    target.execute(f"CREATE TABLE {TABLE} ({', '.join(c + ' TEXT' for c in COLUMNS)})")
    available = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='flex_rows'"
    ).fetchone() is not None
    if available:
        # Do not silently discard undated records: preserve and flag them.
        rows = source.execute(
            "SELECT * FROM flex_rows WHERE account_id=? AND "
            "(section='Transfers' OR lower(record_type) IN (?,?,?)) "
            "ORDER BY section, record_type, source_key",
            (account_id, *FX_TYPES),
        ).fetchall()
        runs = {}
        for row in rows:
            observation = row["report_date"]
            if observation:
                # Validate BEFORE filtering so malformed dates cannot disappear.
                if date.fromisoformat(observation).isoformat() != observation:
                    raise RuntimeError("Invalid source financial evidence date")
                if observation < start_date:
                    continue
            lineage = {}
            for role in ("first_seen", "last_seen"):
                run_id = row[role + "_run_id"]
                if run_id not in runs:
                    run = source.execute(
                        f"SELECT {', '.join(RUN_FIELDS)} FROM ingestion_runs WHERE run_id=?", (run_id,)
                    ).fetchone()
                    runs[run_id] = dict(zip(RUN_FIELDS, run)) if run else None
                lineage[role] = {"source_run_id": run_id, "run": runs[run_id]}
            values = [row[c] for c in COLUMNS[:-1]] + [canonical_json(lineage)]
            target.execute(f"INSERT INTO {TABLE} VALUES ({','.join('?' for _ in COLUMNS)})", values)
    return inspect_financial_evidence(
        target, account_id=account_id, start_date=start_date, source_table_available=available
    )


def inspect_financial_evidence(connection, *, account_id: str, start_date: str,
                               source_table_available: bool) -> dict:
    if type(source_table_available) is not bool:
        raise RuntimeError("Invalid financial evidence availability")
    columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({TABLE})"))
    if columns != COLUMNS:
        raise RuntimeError("Unexpected financial evidence schema")
    counts = {"Transfers": 0, "FX": 0}
    undated = 0
    incomplete_lineage = 0
    identities = set()
    for row in connection.execute(f"SELECT * FROM {TABLE}"):
        item = dict(zip(COLUMNS, row))
        if item["account_id"] != account_id:
            raise RuntimeError("Financial evidence contains another account")
        category = "Transfers" if item["section"] == "Transfers" else "FX"
        if category == "FX" and str(item["record_type"]).lower() not in FX_TYPES:
            raise RuntimeError("Unapproved financial evidence record type")
        key = tuple(item[c] for c in ("account_id", "section", "record_type", "source_key"))
        if any(not isinstance(value, str) or not value for value in key) or key in identities:
            raise RuntimeError("Invalid or duplicate financial evidence identity")
        identities.add(key)
        observation = item["report_date"]
        if observation:
            try:
                valid_date = date.fromisoformat(observation).isoformat() == observation
            except (ValueError, TypeError):
                valid_date = False
            if not valid_date or observation < start_date:
                raise RuntimeError("Invalid financial evidence date scope")
        else:
            undated += 1
        attrs = json.loads(item["data_json"])
        if not isinstance(attrs, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                              for k, v in attrs.items()):
            raise RuntimeError("Invalid financial evidence attributes")
        if sha256_text(canonical_json(attrs)) != item["row_sha256"]:
            raise RuntimeError("Financial evidence content hash mismatch")
        embedded_account = pick(attrs, "accountId", "account")
        if embedded_account not in ("", "-", "UNKNOWN", account_id):
            raise RuntimeError("Financial evidence attributes contain another account")
        lineage = json.loads(item["source_lineage_json"])
        if not isinstance(lineage, dict) or set(lineage) != {"first_seen", "last_seen"}:
            raise RuntimeError("Invalid financial evidence lineage")
        complete = True
        for link in lineage.values():
            if not isinstance(link, dict) or set(link) != {"source_run_id", "run"}:
                raise RuntimeError("Invalid financial evidence lineage link")
            run = link["run"]
            if run is None:
                complete = False
                continue
            if not isinstance(run, dict) or set(run) != set(RUN_FIELDS) or run["run_id"] != link["source_run_id"]:
                raise RuntimeError("Invalid financial evidence source run")
            if run["status"] != "success" or not run["raw_sha256"] or not run["completed_at"]:
                complete = False
        incomplete_lineage += int(not complete)
        counts[category] += 1
    if not source_table_available and identities:
        raise RuntimeError("Financial evidence availability contradicts its rows")
    return {
        "contract_version": 1,
        "source_table_available": source_table_available,
        "preserved_rows": sum(counts.values()),
        "category_counts": counts,
        "undated_rows": undated,
        "incomplete_lineage_rows": incomplete_lineage,
        "source_completeness": "unverified",
        "raw_xml_verified": False,
        "reconciliation_authorized": False,
        "warning": "Preserved rows do not prove query sections or date ranges were complete; zero rows do not prove no transfers.",
    }


def legacy_evidence_status() -> dict:
    return {
        "source_completeness": "unverified", "raw_xml_verified": False,
        "reconciliation_authorized": False,
        "warning": "Legacy v2 snapshot does not preserve Flex transfer/FX source evidence.",
    }
