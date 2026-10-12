"""Combined v3 staging component. Not enabled in scheduled snapshot imports.

Only missing rows may be inserted. Conflicting values need a separately reviewed
correction path; no deletes, commits, account creation or workflow advancement.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path

from .pilot_snapshot import ACCOUNT_TABLES
from .snapshot_evidence_import import import_financial_evidence, read_evidence_snapshot, validate_bundle
from .util import canonical_json, sha256_text


TABLE_KEYS = {"instruments": ("conid",), **{name: value[2] for name, value in ACCOUNT_TABLES.items()}}


def combined_fingerprint(bundle: dict) -> str:
    return sha256_text(canonical_json({k: v for k, v in bundle.items() if k != "combined_sha256"}))


def read_combined_snapshot(path: Path, *, expected_sha256: str, account_id: str, start_date: str) -> dict:
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("Combined snapshot digest does not match approved artifact")
    with tempfile.TemporaryDirectory(prefix="ibkr-combined-") as folder:
        frozen = Path(folder) / "snapshot.sqlite"
        frozen.write_bytes(content)
        evidence = read_evidence_snapshot(frozen, expected_sha256=expected_sha256,
                                          account_id=account_id, start_date=start_date)
        with closing(sqlite3.connect(frozen.as_uri() + "?mode=ro", uri=True)) as c:
            c.row_factory = sqlite3.Row
            account = dict(c.execute("SELECT * FROM accounts").fetchone())
            tables = {}
            for table, keys in TABLE_KEYS.items():
                columns = [r[1] for r in c.execute(f'PRAGMA table_info("{table}")')]
                tables[table] = {"columns": columns, "rows": [dict(r) for r in c.execute(
                    f'SELECT * FROM "{table}" ORDER BY ' + ','.join('"' + key + '"' for key in keys)
                )]}
    bundle = {"evidence": evidence, "account_base_currency": account["base_currency"], "tables": tables}
    bundle["combined_sha256"] = combined_fingerprint(bundle)
    validate_combined(bundle)
    return bundle


def validate_combined(bundle: dict) -> None:
    if set(bundle) != {"evidence", "account_base_currency", "tables", "combined_sha256"}:
        raise ValueError("Unexpected combined import fields")
    if bundle["combined_sha256"] != combined_fingerprint(bundle):
        raise ValueError("Combined bundle fingerprint mismatch")
    validate_bundle(bundle["evidence"])
    account = bundle["evidence"]["account_id"]
    start = bundle["evidence"]["start_date"]
    if date.fromisoformat(start).isoformat() != start or not bundle["account_base_currency"]:
        raise ValueError("Invalid combined account contract")
    if set(bundle["tables"]) != set(TABLE_KEYS):
        raise ValueError("Combined import table inventory mismatch")
    referenced = set()
    instruments = set()
    for table, keys in TABLE_KEYS.items():
        data = bundle["tables"][table]
        if set(data) != {"columns", "rows"}:
            raise ValueError("Unexpected normalized table fields")
        columns = data["columns"]
        if (not columns or len(columns) != len(set(columns)) or not set(keys).issubset(columns)
                or any(not re.fullmatch(r"[a-z][a-z0-9_]*", k) for k in columns)
                or {"tenant_id", "last_seen_run_id", "first_seen_run_id"}.intersection(columns)):
            raise ValueError("Invalid normalized column contract")
        seen = set()
        for row in data["rows"]:
            if set(row) != set(columns):
                raise ValueError("Normalized row columns differ from contract")
            identity = tuple(row[key] for key in keys)
            if None in identity or identity in seen:
                raise ValueError("Duplicate or missing normalized identity")
            seen.add(identity)
            if table == "instruments":
                instruments.add(row["conid"])
            else:
                if row["account_id"] != account:
                    raise ValueError("Normalized data belongs to another account")
                day = row[ACCOUNT_TABLES[table][0]]
                if date.fromisoformat(day).isoformat() != day or day < start:
                    raise ValueError("Normalized date outside approved scope")
                if row.get("conid") not in (None, ""):
                    referenced.add(row["conid"])
    if instruments != referenced:
        raise ValueError("Instrument inventory does not match referenced securities")


def stage_combined_snapshot(connection, bundle: dict, *, tenant_id: str, run_id: int,
                            schema: str = "public") -> dict:
    from psycopg import sql
    from psycopg.pq import TransactionStatus
    from psycopg.rows import dict_row

    validate_combined(bundle)
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError("Combined import requires a caller-owned transaction")
    # All evidence writes, normalized rows and receipts share this savepoint.
    with connection.transaction():
        evidence_result = import_financial_evidence(connection, bundle["evidence"], tenant_id=tenant_id,
                                                     run_id=run_id, schema=schema)
        account = bundle["evidence"]["account_id"]
        with connection.cursor(row_factory=dict_row) as c:
            accounts = sql.Identifier(schema, "accounts")
            runs = sql.Identifier(schema, "ingestion_runs")
            owner = c.execute(sql.SQL("SELECT base_currency FROM {} WHERE account_id=%s AND tenant_id=%s")
                              .format(accounts), (account, tenant_id)).fetchone()
            if not owner or owner["base_currency"] != bundle["account_base_currency"]:
                raise ValueError("Destination account currency differs from snapshot contract")
            state = json.loads(c.execute(sql.SQL("SELECT reconciliation_json FROM {} WHERE run_id=%s AND tenant_id=%s")
                                         .format(runs), (run_id, tenant_id)).fetchone()["reconciliation_json"])
            prior = state.get("combined_snapshot_import")
            if prior and prior["combined_sha256"] != bundle["combined_sha256"]:
                raise ValueError("Destination run is already bound to another combined snapshot")
            summaries = {}
            inserted_counts = {}
            for table, keys in TABLE_KEYS.items():
                incoming = bundle["tables"][table]
                columns = incoming["columns"]
                target_columns = {r["column_name"] for r in c.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
                    (schema, table),
                )}
                expected_columns = target_columns - {"tenant_id", "last_seen_run_id"}
                if set(columns) != expected_columns:
                    raise ValueError("Source/destination column contract differs: " + table)
                identifier = sql.Identifier(schema, table)
                predicate = sql.SQL(" AND ").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in keys)
                # Account-row lock/ownership checked above also fences ownership
                # changes for normalized tables that scope tenants via accounts.
                if "tenant_id" in target_columns:
                    predicate += sql.SQL(" AND tenant_id=%s")
                select = sql.SQL("SELECT * FROM {} WHERE {} FOR UPDATE").format(identifier, predicate)
                readback = []
                lineage = []
                inserted_counts[table] = 0
                for row in incoming["rows"]:
                    params = [row[k] for k in keys]
                    if "tenant_id" in target_columns:
                        params.append(tenant_id)
                    stored = c.execute(select, params).fetchone()
                    if stored is None:
                        if prior:
                            raise ValueError("Previously staged normalized record is missing")
                        extra = {} if table == "instruments" else {"last_seen_run_id": run_id}
                        if "tenant_id" in target_columns:
                            extra["tenant_id"] = tenant_id
                        values = {**row, **extra}
                        c.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO NOTHING")
                                  .format(identifier, sql.SQL(",").join(map(sql.Identifier, values)),
                                          sql.SQL(",").join(sql.Placeholder() for _ in values),
                                          sql.SQL(",").join(map(sql.Identifier, keys))), list(values.values()))
                        inserted_counts[table] += c.rowcount
                        stored = c.execute(select, params).fetchone()
                    # Shared instrument discovery timestamps may legitimately be
                    # older/newer; retain them. All descriptive values must agree.
                    compared = [k for k in columns if not (table == "instruments" and k in {"first_seen_at", "last_seen_at"})]
                    if not stored or any(stored[k] != row[k] for k in compared):
                        raise ValueError("Normalized values conflict; correction review required: " + table)
                    readback.append({k: stored[k] for k in compared})
                    if table != "instruments":
                        origin = stored["last_seen_run_id"]
                        source_run = c.execute(sql.SQL("SELECT status FROM {} WHERE run_id=%s AND tenant_id=%s")
                                               .format(runs), (origin, tenant_id)).fetchone()
                        if not source_run or (source_run["status"] != "success" and origin != run_id):
                            raise ValueError("Normalized provenance is not valid for this tenant")
                        lineage.append([*[row[k] for k in keys], origin])
                summaries[table] = {
                    "verified_rows": len(readback),
                    "values_sha256": sha256_text(canonical_json(readback)),
                    "destination_lineage_sha256": sha256_text(canonical_json(lineage)),
                }
            receipt = {
                "contract_version": 1, "combined_sha256": bundle["combined_sha256"],
                "snapshot_sha256": bundle["evidence"]["snapshot_sha256"],
                "account_id": account, "tenant_id": tenant_id, "run_id": run_id,
                "account_base_currency": bundle["account_base_currency"], "tables": summaries,
                "source_completeness": "unverified", "reconciliation_authorized": False,
            }
            if prior and prior != receipt:
                raise ValueError("Combined snapshot readback differs from original receipt")
            state["combined_snapshot_import"] = receipt
            c.execute(sql.SQL("UPDATE {} SET reconciliation_json=%s WHERE run_id=%s AND tenant_id=%s")
                      .format(runs), (canonical_json(state), run_id, tenant_id))
            if json.loads(c.execute(sql.SQL("SELECT reconciliation_json FROM {} WHERE run_id=%s AND tenant_id=%s")
                                    .format(runs), (run_id, tenant_id)).fetchone()["reconciliation_json"]) != state:
                raise ValueError("Combined receipt readback mismatch")
    return {"status": "combined_snapshot_staged", "inserted_rows": inserted_counts,
            "verified_tables": summaries, "evidence": evidence_result,
            "source_completeness": "unverified", "reconciliation_authorized": False}
