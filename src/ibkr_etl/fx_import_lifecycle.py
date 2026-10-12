"""Bind verified FX staging to an explicit, non-authorizing ingestion run.

Trusted maintenance interface only. Caller verifies artifact bytes with
read_bundle, prepares the exact contract, and owns the surrounding transaction.
No run creation, commit, success transition, dispatch or analytics cutover.
"""
import hashlib
import json
import re

from .fx_evidence_import import digest, stage_evidence, validate_bundle
from .util import canonical_json, sha256_text

RUN_TYPE = "staging-fx-evidence-import"
CONTRACT_KEY = "fx_evidence_contract"
RECEIPT_KEY = "fx_evidence_import"


def import_contract(bundle, *, expected_bundle_sha256, artifact_sha256):
    digest(artifact_sha256)
    validate_bundle(bundle, expected_sha256=expected_bundle_sha256)
    days = [row["report_date"] for row in bundle["versions"]]
    return {
        "contract_version": 1, "tenant_id": bundle["tenant_id"],
        "account_id": bundle["account_id"], "source_namespace": bundle["source_namespace"],
        "source_snapshot_sha256": bundle["source_snapshot_sha256"],
        "artifact_sha256": artifact_sha256, "bundle_sha256": expected_bundle_sha256,
        "requested_from": min(days), "requested_to": max(days),
        "counts": {group: len(bundle[group]) for group in ("runs", "versions", "observations", "aliases")},
        "source_completeness": "unverified", "execution_authorized": False,
    }


def stage_import_run(connection, bundle, *, expected_bundle_sha256, artifact_sha256,
                     run_id, schema="public", batch_size=500):
    """Atomically stage evidence and attach its receipt to the pre-bound run."""
    from psycopg import sql
    from psycopg.pq import TransactionStatus
    from psycopg.rows import dict_row

    bundle = json.loads(canonical_json(bundle))
    contract = import_contract(bundle, expected_bundle_sha256=expected_bundle_sha256,
                               artifact_sha256=artifact_sha256)
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("Positive destination run required")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("Invalid lifecycle schema")
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError("Caller-owned transaction required")
    tenant, account = contract["tenant_id"], contract["account_id"]
    with connection.transaction():
        with connection.cursor(row_factory=dict_row) as c:
            if c.execute("SELECT current_setting('app.tenant_id',true) AS tenant").fetchone()["tenant"] != tenant:
                raise ValueError("Lifecycle tenant session mismatch")
            lock = int.from_bytes(hashlib.sha256(canonical_json([tenant, account]).encode()).digest()[:8],
                                  "big", signed=True)
            c.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            if not c.execute(sql.SQL("SELECT 1 FROM {} WHERE tenant_id=%s AND account_id=%s FOR UPDATE")
                             .format(sql.Identifier(schema, "accounts")), (tenant, account)).fetchone():
                raise ValueError("Lifecycle account ownership mismatch")
            runs = sql.Identifier(schema, "ingestion_runs")
            lookup = sql.SQL("SELECT * FROM {} WHERE tenant_id=%s AND run_id=%s FOR UPDATE").format(runs)
            run = c.execute(lookup, (tenant, run_id)).fetchone()
            if (not run or run["run_type"] != RUN_TYPE or run["status"] != "running"
                    or run["raw_sha256"] != artifact_sha256
                    or run["requested_from"] != contract["requested_from"]
                    or run["requested_to"] != contract["requested_to"]):
                raise ValueError("Destination run does not match the FX import contract")
            state = json.loads(run["reconciliation_json"] or "{}")
            if not isinstance(state, dict) or state.get(CONTRACT_KEY) != contract:
                raise ValueError("Exact pre-bound FX contract required")
            staged = stage_evidence(connection, bundle, expected_sha256=expected_bundle_sha256,
                                    schema=schema, batch_size=batch_size)
            receipt = {
                "contract_version": 1, "run_id": run_id,
                "contract_sha256": sha256_text(canonical_json(contract)),
                "evidence": staged["receipt"], "state": "evidence_staged",
                "source_completeness": "unverified", "execution_authorized": False,
                "reconciliation_authorized": False,
            }
            # A late receipt conflict also rolls back lower-level inserts and
            # their batch receipt: both nested savepoints belong to this scope.
            if RECEIPT_KEY in state and state[RECEIPT_KEY] != receipt:
                raise ValueError("Destination FX receipt differs; no overwrite permitted")
            updated = {**state, RECEIPT_KEY: receipt}
            if RECEIPT_KEY not in state:
                c.execute(sql.SQL("UPDATE {} SET reconciliation_json=%s WHERE tenant_id=%s AND run_id=%s")
                          .format(runs), (canonical_json(updated), tenant, run_id))
                if c.rowcount != 1:
                    raise ValueError("Destination FX receipt update failed")
            observed = c.execute(lookup, (tenant, run_id)).fetchone()
            if (not observed or json.loads(observed["reconciliation_json"]) != updated
                    or any(observed[key] != run[key] for key in run if key != "reconciliation_json")):
                raise ValueError("Destination FX run readback differs")
    return {"status": "evidence_staged", "run_id": run_id, "receipt": receipt,
            "inserted": staged["inserted"], "group_seconds": staged["group_seconds"],
            "group_queries": staged["group_queries"]}
