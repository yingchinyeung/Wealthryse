"""Own-storage reads plus repeatable-read PostgreSQL; no Flex calls or imports."""
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from azure.core.exceptions import HttpResponseError
from ibkr_etl.cloud_storage import _service
from ibkr_etl.daily_account_sync import validate_production_export_manifest
from ibkr_etl.pilot_snapshot import build_pilot_snapshot
from ibkr_etl.snapshot_atomic_import import read_combined_snapshot
from ibkr_etl.snapshot_preflight import archive_blob_location, inspect_archived_xml, preflight_snapshot
from ibkr_etl.fx_source_evidence import rate_status


def emit(value):
    print("SNAPSHOT_PREFLIGHT " + json.dumps(value, sort_keys=True), flush=True)


def main():
    tenant = os.environ["PORTFOLIO_TENANT_ID"]
    account_url = os.environ["PRODUCTION_STORAGE_ACCOUNT_URL"]
    service = _service(account_url)
    container = os.environ.get("PRODUCTION_EXPORT_CONTAINER", "analytics-reports")
    prefix = os.environ.get("PRODUCTION_EXPORT_PREFIX", tenant + "/latest").strip("/")
    manifest_bytes = service.get_blob_client(container, prefix + "/IBKR_Analyst_Export_Manifest_Latest.json").download_blob().readall()
    # Diagnostic tolerance only. Do not alter the scheduled sync's 12h policy.
    manifest = validate_production_export_manifest(manifest_bytes, expected_tenant_id=tenant,
        maximum_age=timedelta(hours=72), require_current_hkt_date=False)
    with TemporaryDirectory(prefix="snapshot-preflight-") as folder:
        folder = Path(folder)
        source_path = folder / "source.sqlite"
        content = service.get_blob_client(container, prefix + "/" + manifest.sqlite_file).download_blob().readall()
        if hashlib.sha256(content).hexdigest() != manifest.sqlite_sha256:
            raise ValueError("Source SQLite artifact hash mismatch")
        source_path.write_bytes(content)
        del content
        emit({"phase": "source", "source_sha256": manifest.sqlite_sha256,
              "source_generated_at": manifest.generated_at.isoformat(),
              "age_hours": round((datetime.now(timezone.utc)-manifest.generated_at).total_seconds()/3600, 2),
              "diagnostic_only": True})
        with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, connect_timeout=25,
                options="-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000") as pg:
            pg.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            pg.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
            accounts = [r["subject_id"] for r in pg.execute("""SELECT subject_id FROM public.platform_subjects
                WHERE tenant_id=%s AND subject_type='physical_account' AND lifecycle_status='active'
                AND subject_id IN ('U16088571','U16088606') ORDER BY subject_id""", (tenant,))]
            if set(accounts) != {"U16088571", "U16088606"}:
                raise ValueError("Expected active physical-account scope changed")
            for account in accounts:
                artifact = folder / (account + ".sqlite")
                built = build_pilot_snapshot(source_path, artifact, account_id=account, start_date="2025-01-20",
                    generated_at=manifest.generated_at.isoformat(), include_financial_evidence=True)
                bundle = read_combined_snapshot(artifact, expected_sha256=built["output_sha256"],
                                                account_id=account, start_date="2025-01-20")
                emit({"phase": "compatibility", **preflight_snapshot(pg, bundle, tenant_id=tenant)})
            pg.rollback()
        with closing(sqlite3.connect(source_path.as_uri()+"?mode=ro", uri=True)) as source:
            source.row_factory = sqlite3.Row
            invalid = {}
            affected_runs = set()
            for row in source.execute("SELECT account_id,rate_date,row_sha256,rate_text,last_seen_run_id FROM fx_rates WHERE account_id IN (?,?)", tuple(accounts)):
                if rate_status(row["rate_text"]) != "positive":
                    invalid[(row["account_id"], row["rate_date"], row["row_sha256"])] = row["rate_text"]
                    affected_runs.add(row["last_seen_run_id"])
            raw = [dict(row) for row in source.execute("SELECT run_id,content_sha256,byte_count,file_path,received_at FROM raw_reports ORDER BY received_at")]
            affected = [r for r in raw if r["run_id"] in affected_runs]
            selected = raw[-2:]
            if affected:
                selected += [affected[0], affected[len(affected)//2], affected[-1]]
            selected = list({r["content_sha256"]: r for r in selected}.values())
            emit({"phase": "archive_inventory", "registered_raw_files": len(raw), "selected_raw_files": len(selected),
                  "invalid_normalized_observations": len(invalid), "sample_only": True})
            for row in selected:
                try:
                    raw_container, blob = archive_blob_location(row["file_path"], expected_host=urlsplit(account_url).hostname)
                    if row["byte_count"] > 64*1024*1024:
                        emit({"phase": "archive", "source_run_id": row["run_id"], "status": "size_limit", "source_completeness": "unverified"})
                        continue
                    client = service.get_blob_client(raw_container, blob)
                    props = client.get_blob_properties()
                    if props.size != row["byte_count"]:
                        raise ValueError("Archived blob size differs from registry")
                    data = client.download_blob().readall()
                    result = inspect_archived_xml(data, expected_sha256=row["content_sha256"],
                                                  accounts=set(accounts), invalid_identities=invalid)
                    emit({"phase": "archive", "source_run_id": row["run_id"], "archive_sha256": row["content_sha256"], **result})
                except HttpResponseError as error:
                    emit({"phase": "archive", "source_run_id": row["run_id"], "status": "unavailable",
                          "http_status": error.status_code, "source_completeness": "unverified"})
        emit({"probe_complete": True, "mode": "read_only", "execution_authorized": False})


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit({"probe_complete": False, "error_type": type(error).__name__})
        raise SystemExit(1)
