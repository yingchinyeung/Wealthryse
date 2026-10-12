"""Evidence-import component; not wired into scheduled/normal snapshot imports.

Caller owns the outer transaction and normalized-table reconciliation. This
component never commits, certifies source completeness or advances workflow.
Existing conflicting financial evidence requires a separate correction review.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from uuid import UUID

from .pilot_snapshot import validate_pilot_snapshot
from .snapshot_evidence import COLUMNS, TABLE, inspect_financial_evidence
from .util import canonical_json, sha256_text


PAYLOAD_COLUMNS = COLUMNS[:-1]
KEY_COLUMNS = ("account_id", "section", "record_type", "source_key")


def bundle_fingerprint(bundle: dict) -> str:
    return sha256_text(canonical_json({k: v for k, v in bundle.items() if k != "bundle_sha256"}))


def read_evidence_snapshot(path: Path, *, expected_sha256: str, account_id: str, start_date: str) -> dict:
    """Verify external digest and read a frozen private copy, not a mutable path."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("An externally recorded snapshot SHA-256 is required")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("Snapshot SHA-256 does not match the approved artifact")
    with tempfile.TemporaryDirectory(prefix="ibkr-evidence-") as folder:
        frozen = Path(folder) / "snapshot.sqlite"
        frozen.write_bytes(content)
        validation = validate_pilot_snapshot(frozen, account_id=account_id, start_date=start_date)
        if validation["metadata"]["format_version"] != "3":
            raise ValueError("Evidence import requires a v3 snapshot")
        with closing(sqlite3.connect(frozen.as_uri() + "?mode=ro", uri=True)) as c:
            rows = [dict(zip(COLUMNS, row)) for row in c.execute(
                f"SELECT {','.join(COLUMNS)} FROM {TABLE} ORDER BY section,record_type,source_key"
            )]
    bundle = {
        "account_id": account_id, "start_date": start_date,
        "snapshot_sha256": expected_sha256,
        "summary": validation["financial_evidence"], "rows": rows,
    }
    bundle["bundle_sha256"] = bundle_fingerprint(bundle)
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle: dict) -> None:
    if set(bundle) != {"account_id", "start_date", "snapshot_sha256", "summary", "rows", "bundle_sha256"}:
        raise ValueError("Unexpected evidence bundle fields")
    if not re.fullmatch(r"[0-9a-f]{64}", bundle["snapshot_sha256"]):
        raise ValueError("Invalid snapshot digest")
    if bundle["bundle_sha256"] != bundle_fingerprint(bundle):
        raise ValueError("Evidence bundle fingerprint mismatch")
    with closing(sqlite3.connect(":memory:")) as c:
        c.execute(f"CREATE TABLE {TABLE} ({','.join(name + ' TEXT' for name in COLUMNS)})")
        for row in bundle["rows"]:
            if set(row) != set(COLUMNS):
                raise ValueError("Unexpected evidence row fields")
            c.execute(f"INSERT INTO {TABLE} VALUES ({','.join('?' for _ in COLUMNS)})", [row[k] for k in COLUMNS])
        actual = inspect_financial_evidence(c, account_id=bundle["account_id"], start_date=bundle["start_date"],
                                           source_table_available=bundle["summary"].get("source_table_available"))
    if actual != bundle["summary"]:
        raise ValueError("Evidence summary mismatch")
    if not actual["source_table_available"] or actual["undated_rows"] or actual["incomplete_lineage_rows"]:
        raise ValueError("Evidence needs source/date/lineage review before import")


def same_payload(stored: dict, incoming: dict) -> bool:
    for key in PAYLOAD_COLUMNS:
        if key == "data_json":
            if json.loads(stored[key]) != json.loads(incoming[key]):
                return False
        elif stored[key] != incoming[key]:
            return False
    return True


def import_financial_evidence(connection, bundle: dict, *, tenant_id: str, run_id: int,
                              schema: str = "public") -> dict:
    """Insert missing evidence and a receipt atomically inside caller transaction.

    The schema argument exists for isolated rollback rehearsals, not UI input.
    Normal import_snapshot still rejects v3. This does not mark the run success.
    """
    from psycopg import sql
    from psycopg.pq import TransactionStatus
    from psycopg.rows import dict_row

    validate_bundle(bundle)
    tenant_id = str(UUID(tenant_id))
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("Invalid evidence schema")
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("Invalid destination ingestion run")
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError("Evidence import requires an existing caller-owned transaction")
    account = bundle["account_id"]
    tables = {name: sql.Identifier(schema, name) for name in ("accounts", "ingestion_runs", "flex_rows")}
    with connection.transaction():  # Savepoint: partial evidence never survives a failure.
        with connection.cursor(row_factory=dict_row) as c:
            context = c.execute("SELECT current_setting('app.tenant_id',true) AS tenant").fetchone()["tenant"]
            if context != tenant_id:
                raise ValueError("Tenant session does not match evidence import")
            lock = int.from_bytes(hashlib.sha256(canonical_json([tenant_id, account]).encode()).digest()[:8],
                                  "big", signed=True)
            c.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            owner = c.execute(sql.SQL("SELECT account_id FROM {} WHERE account_id=%s AND tenant_id=%s FOR UPDATE")
                              .format(tables["accounts"]), (account, tenant_id)).fetchone()
            if not owner:
                raise ValueError("Account is not owned by the destination tenant")
            run = c.execute(sql.SQL("SELECT * FROM {} WHERE run_id=%s AND tenant_id=%s FOR UPDATE")
                            .format(tables["ingestion_runs"]), (run_id, tenant_id)).fetchone()
            if (not run or run["run_type"] != "staging-snapshot-import" or run["status"] != "running"
                    or run["raw_sha256"] != bundle["snapshot_sha256"] or run["requested_from"] != bundle["start_date"]):
                raise ValueError("Destination run is not bound to this snapshot import")
            receipt_state = json.loads(run["reconciliation_json"] or "{}")
            if not isinstance(receipt_state, dict):
                raise ValueError("Invalid destination reconciliation state")
            prior = receipt_state.get("financial_evidence_import")
            if prior and prior.get("bundle_sha256") != bundle["bundle_sha256"]:
                raise ValueError("Destination run is already bound to different evidence")

            predicate = sql.SQL(" AND ").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in KEY_COLUMNS)
            fetch = sql.SQL("SELECT * FROM {} WHERE tenant_id=%s AND {} FOR UPDATE").format(tables["flex_rows"], predicate)
            inserted = 0
            mapping = []
            for row in bundle["rows"]:
                parameters = (tenant_id, *(row[k] for k in KEY_COLUMNS))
                existing = c.execute(fetch, parameters).fetchone()
                if existing is None:
                    if prior:
                        raise ValueError("Previously imported evidence is missing; review is required")
                    columns = (*PAYLOAD_COLUMNS, "first_seen_run_id", "last_seen_run_id", "tenant_id")
                    c.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO NOTHING")
                              .format(tables["flex_rows"], sql.SQL(",").join(map(sql.Identifier, columns)),
                                      sql.SQL(",").join(sql.Placeholder() for _ in columns),
                                      sql.SQL(",").join(map(sql.Identifier, KEY_COLUMNS))),
                              [row[k] for k in PAYLOAD_COLUMNS] + [run_id, run_id, tenant_id])
                    inserted += c.rowcount
                stored = c.execute(fetch, parameters).fetchone()
                if not stored or not same_payload(stored, row):
                    raise ValueError("Stored financial evidence conflicts with snapshot; no overwrite allowed")
                # Existing first/last observation provenance is retained, not rewritten.
                for observed_run in {stored["first_seen_run_id"], stored["last_seen_run_id"]}:
                    linked = c.execute(sql.SQL("SELECT status FROM {} WHERE run_id=%s AND tenant_id=%s")
                                       .format(tables["ingestion_runs"]), (observed_run, tenant_id)).fetchone()
                    if not linked or (linked["status"] != "success" and observed_run != run_id):
                        raise ValueError("Stored evidence links to an unverified or foreign-tenant run")
                mapping.append({
                    "key": [row[k] for k in KEY_COLUMNS], "row_sha256": row["row_sha256"],
                    "source_lineage": json.loads(row["source_lineage_json"]),
                    "destination_first_seen_run_id": stored["first_seen_run_id"],
                    "destination_last_seen_run_id": stored["last_seen_run_id"],
                })
            receipt = {
                "contract_version": 1, "tenant_id": tenant_id, "account_id": account,
                "run_id": run_id, "snapshot_sha256": bundle["snapshot_sha256"],
                "bundle_sha256": bundle["bundle_sha256"], "verified_rows": len(mapping),
                "mappings": mapping, "source_completeness": "unverified",
                "raw_xml_verified": False, "reconciliation_authorized": False,
            }
            if prior and prior != receipt:
                raise ValueError("Evidence readback no longer matches the original import receipt")
            receipt_state["financial_evidence_import"] = receipt
            c.execute(sql.SQL("UPDATE {} SET reconciliation_json=%s WHERE run_id=%s AND tenant_id=%s")
                      .format(tables["ingestion_runs"]), (canonical_json(receipt_state), run_id, tenant_id))
            readback = c.execute(sql.SQL("SELECT reconciliation_json FROM {} WHERE run_id=%s AND tenant_id=%s")
                                 .format(tables["ingestion_runs"]), (run_id, tenant_id)).fetchone()
            if json.loads(readback["reconciliation_json"]) != receipt_state:
                raise ValueError("Evidence receipt readback mismatch")
    return {"status": "evidence_staged", "inserted_rows": inserted,
            "verified_rows": len(mapping), "bundle_sha256": bundle["bundle_sha256"],
            "source_completeness": "unverified", "reconciliation_authorized": False}
