"""Freeze the current registered export using existing read-only user access."""
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from azure.core import MatchConditions
from azure.storage.blob import BlobServiceClient
from ibkr_etl.daily_account_sync import validate_production_export_manifest
from rehearse_archive_evidence import StorageReadCredential


def main():
    tenant = "00000000-0000-0000-0000-000000000001"
    prefix = tenant + "/latest/"
    service = BlobServiceClient("https://ibkrv115rcc62xxqblay.blob.core.windows.net",
        credential=StorageReadCredential(), retry_total=0)
    manifest_client = service.get_blob_client("analytics-reports", prefix + "IBKR_Analyst_Export_Manifest_Latest.json")
    raw_manifest = manifest_client.download_blob().readall()
    manifest = validate_production_export_manifest(raw_manifest, expected_tenant_id=tenant,
        maximum_age=timedelta(hours=72), require_current_hkt_date=False)
    client = service.get_blob_client("analytics-reports", prefix + manifest.sqlite_file)
    props = client.get_blob_properties()
    if props.size > 512*1024*1024:
        raise ValueError("Diagnostic export size limit")
    content = client.download_blob(etag=props.etag, match_condition=MatchConditions.IfNotModified).readall()
    if hashlib.sha256(content).hexdigest() != manifest.sqlite_sha256:
        raise ValueError("Source manifest hash mismatch")
    if manifest_client.download_blob().readall() != raw_manifest:
        raise ValueError("Source manifest changed during capture")
    folder = Path(__file__).resolve().parents[1] / ".work/archive-evidence-20261002"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (manifest.sqlite_sha256 + ".sqlite")
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != manifest.sqlite_sha256:
            raise ValueError("Existing frozen export differs")
    else:
        target.write_bytes(content)
    evidence = {"source_path": str(target), "source_sha256": manifest.sqlite_sha256,
        "generated_at": manifest.generated_at.isoformat(), "diagnostic_age_tolerance_hours": 72,
        "scheduled_import_policy_changed": False}
    (folder / "capture.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
