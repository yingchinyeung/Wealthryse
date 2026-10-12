"""Real report/template version-recovery drill confined to a new private prefix.

Production database and original blobs are read-only. Only newly created drill
copies are deliberately overwritten. No financial values or documents are logged.
"""
import hashlib
import json
import os
import re
import time
import uuid

import psycopg
from azure.core import MatchConditions
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from psycopg.rows import dict_row

TENANT = "00000000-0000-0000-0000-000000000001"
ACCOUNT = "https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net"
DESTINATION = "input-snapshots"
MAX_BLOB = 52_428_800
MAX_TOTAL = 256 * 1024 * 1024
CONTEXT = {"phase": "initializing"}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def main():
    start = time.monotonic()
    run_id = str(uuid.UUID(os.environ["RECOVERY_DRILL_ID"]))
    if os.environ["PORTFOLIO_TENANT_ID"] != TENANT:
        raise ValueError("Unexpected tenant")
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, connect_timeout=20,
            options="-c default_transaction_read_only=on -c statement_timeout=60000") as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT,))
        read_only = connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"]
        rows = connection.execute("""SELECT 'released_report' AS kind, container_name, blob_name,
            artifact_sha256 AS sha256 FROM report_artifact_releases
            WHERE tenant_id=%s AND revoked_at IS NULL
            UNION ALL
            SELECT 'current_template' AS kind, container_name, blob_name, sha256
            FROM subject_report_template_assets WHERE tenant_id=%s AND is_current
            AND approval_status IN ('validated','active')
            ORDER BY kind,container_name,blob_name""", (TENANT, TENANT)).fetchall()
    if read_only != "on" or not 1 <= len(rows) <= 256:
        raise ValueError("Unexpected recovery inventory")
    CONTEXT.update(phase="inventory", registry_rows=len(rows),
                   source_containers=sorted({row["container_name"] for row in rows}))
    print("BLOB_RECOVERY " + json.dumps({"kind": "inventory", "run_id": run_id, **CONTEXT}), flush=True)
    service = BlobServiceClient(ACCOUNT, credential=ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]))
    destination = service.get_container_client(DESTINATION)
    if destination.get_container_properties().get("public_access") is not None:
        raise ValueError("Drill destination is not private")
    prefix = f"{TENANT}/recovery-drills/{run_id}/"
    sources = {}
    for row in rows:
        allowed = {"internal-reports"} if row["kind"] == "released_report" else {"internal-reports", "onboarding-intake"}
        if (row["container_name"] not in allowed or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                or not row["blob_name"] or ".." in row["blob_name"].split("/")):
            raise ValueError("Unexpected report source binding")
        key = (row["container_name"], row["blob_name"])
        if key in sources and sources[key]["sha256"] != row["sha256"]:
            raise ValueError("Conflicting registry hashes")
        sources[key] = row
    records = []
    total = 0
    for index, row in enumerate(sources.values()):
        CONTEXT.update(phase="source_read", asset_index=index)
        source_container = service.get_container_client(row["container_name"])
        if source_container.get_container_properties().get("public_access") is not None:
            raise ValueError("Production report container is not private")
        source = source_container.get_blob_client(row["blob_name"])
        properties = source.get_blob_properties()
        version = properties.version_id
        if not version or not 0 < properties.size <= MAX_BLOB:
            raise ValueError("Bounded version-pinned report required")
        total += properties.size
        if total > MAX_TOTAL:
            raise ValueError("Recovery inventory exceeds approved bounded trial")
        content = service.get_blob_client(row["container_name"], row["blob_name"], version_id=version).download_blob().readall()
        if len(content) != properties.size or digest(content) != row["sha256"]:
            raise ValueError("Production report does not match registry hash")
        CONTEXT["phase"] = "isolated_version_recovery"
        name = prefix + f"{index:03d}-{row['sha256']}.recovery"
        target = destination.get_blob_client(name)
        first = target.upload_blob(content, overwrite=False,
            content_settings=ContentSettings(content_type="application/octet-stream"),
            metadata={"purpose": "isolated-recovery-drill", "sha256": row["sha256"]})
        if not first.get("version_id"):
            raise ValueError("Drill versioning unavailable")
        marker = b"Intentional corruption of an isolated recovery-drill copy only."
        second = target.upload_blob(marker, overwrite=True, etag=first["etag"], match_condition=MatchConditions.IfNotModified)
        if not second.get("version_id") or second["version_id"] == first["version_id"]:
            raise ValueError("Independent drill version not recorded")
        original_version = service.get_blob_client(DESTINATION, name, version_id=first["version_id"])
        recovered = original_version.download_blob().readall()
        if digest(recovered) != row["sha256"]:
            raise ValueError("Previous blob version could not be recovered")
        restored = target.upload_blob(recovered, overwrite=True, etag=second["etag"], match_condition=MatchConditions.IfNotModified)
        if digest(target.download_blob(etag=restored["etag"], match_condition=MatchConditions.IfNotModified).readall()) != row["sha256"]:
            raise ValueError("Restored current drill blob differs")
        if source.get_blob_properties().etag != properties.etag:
            raise ValueError("Original source changed during drill; re-review required")
        record = {"index": index, "kind": row["kind"], "source_name_sha256": digest(row["blob_name"].encode()),
            "artifact_sha256": row["sha256"], "bytes": len(content), "source_version": version,
            "drill_blob": name, "versions": [first["version_id"], second["version_id"], restored.get("version_id")],
            "recovered": True, "original_unchanged": True}
        if len(set(record["versions"])) != 3 or None in record["versions"]:
            raise ValueError("Three distinct drill versions required")
        records.append(record)
        print("BLOB_RECOVERY " + json.dumps({"kind": "asset", "run_id": run_id, **record}), flush=True)
    print("BLOB_RECOVERY " + json.dumps({"kind": "complete", "run_id": run_id, "passed": True,
        "database_read_only": True, "registry_rows": len(rows), "verified_assets": len(records),
        "verified_bytes": total, "seconds": round(time.monotonic() - start, 3),
        "drill_prefix": prefix, "originals_modified": False, "cleanup_pending": True}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        safe_messages = {"Unexpected recovery inventory", "Unexpected report source binding",
                         "Bounded version-pinned report required", "Production report does not match registry hash",
                         "Private input container required", "Conflicting registry hashes",
                         "Recovery inventory exceeds approved bounded trial"}
        print("BLOB_RECOVERY " + json.dumps({"kind": "error", "error_type": type(error).__name__,
              "passed": False, "originals_modified": False, **CONTEXT,
              "safe_reason": str(error) if str(error) in safe_messages else "Inspect isolated drill evidence"}), flush=True)
        raise SystemExit(1)
