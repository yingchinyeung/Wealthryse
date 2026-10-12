"""Azure-only MS layout qualification: immutable inputs, create-only private PDFs.

Installed in the immutable reporting image, not injected at runtime. No SQL
writes, approval changes, source recalculation or onboarding activation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import psycopg
from psycopg.rows import dict_row
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ibkr_etl.monthly_artifacts import (
    ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION, render_factsheet,
)
from generate_month_end_package import _require_factsheet_identity

SUBJECT = "alpha-world-ms"
TENANT = "00000000-0000-0000-0000-000000000001"
CONTAINER = "internal-reports"
INPUT = "render-inputs/alpha-world-ms/v13-20261007/"
OUTPUT = "review/alpha-world-ms/azure-v14r7-20261007/"
RECOVERY = "review/alpha-world-ms/source-recovery-20261007/"


def sha(content):
    return hashlib.sha256(content).hexdigest()


def main():
    release = json.loads((ROOT / "ALPHA_MS_RENDER_RELEASE.json").read_bytes())
    for name, expected in release["installed_files_sha256"].items():
        if sha((ROOT / name).read_bytes()) != expected:
            raise ValueError("Installed reporting code differs from release manifest")
    if release["layout_version"] != ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION:
        raise ValueError("Installed MS layout version mismatch")
    image = os.environ["ALPHA_MS_IMAGE_DIGEST"]
    if not image.startswith("sha256:") or len(image) != 71:
        raise ValueError("Exact candidate image digest required")
    service = BlobServiceClient(os.environ["AZURE_STORAGE_ACCOUNT_URL"],
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    capture_bytes = service.get_blob_client(CONTAINER, INPUT + "capture.json").download_blob().readall()
    capture = json.loads(capture_bytes)
    if (capture["subject_id"] != SUBJECT or capture["tenant_id"] != TENANT
            or sha(capture_bytes) != release["capture_sha256"]):
        raise ValueError("Exact approved batch capture required")
    snapshots = capture["snapshots"]
    corrected_bytes = service.get_blob_client(CONTAINER, RECOVERY + "corrected-reports/manifest.json").download_blob().readall()
    if sha(corrected_bytes) != release['corrected_manifest_sha256']:
        raise ValueError('Previously validated correction manifest changed')
    corrected = {r['period_end']: r for r in json.loads(corrected_bytes)['artifacts']}
    if set(corrected) != {'2026-08-31', '2026-09-30'}:
        raise ValueError('Correction scope differs from the recovered monthly versions')
    if len(snapshots) != 21 or {r["period_end"] for r in snapshots} != set(capture["expected_periods"]):
        raise ValueError("Historical month scope differs")
    # Verify the captured data and template registration still match the live
    # frozen records. The connection cannot write even accidentally.
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=120000",
            connect_timeout=25) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        rows = connection.execute("""SELECT snapshot_id::text, period_end, data_sha256,
            snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s
            AND subject_id=%s AND status IN ('frozen','approved') AND quality_status<>'fail'""",
            (TENANT, SUBJECT)).fetchall()
        assets = connection.execute("""SELECT asset_role,container_name,blob_name,sha256,
            approval_status FROM subject_report_template_assets WHERE tenant_id=%s
            AND subject_id=%s AND report_type='factsheet' AND is_current""", (TENANT, SUBJECT)).fetchall()
        subject = connection.execute("SELECT lifecycle_status FROM platform_subjects WHERE tenant_id=%s AND subject_id=%s",
                                     (TENANT, SUBJECT)).fetchone()
        registrations = connection.execute("""SELECT operating_state FROM subject_operating_registrations
            WHERE tenant_id=%s AND subject_id=%s AND capability='month_end_reporting' AND effective_to IS NULL""",
            (TENANT, SUBJECT)).fetchall()
        connection.rollback()
    by_id = {r["snapshot_id"]: r for r in rows}
    by_role = {a["asset_role"]: a for a in assets}
    if len(assets) != 2 or set(by_role) != {"template", "completed_example"}:
        raise ValueError("Owner-uploaded design pair unavailable")
    for expected in capture["assets"]:
        if any(by_role[expected["asset_role"]][k] != expected[k]
               for k in ("container_name", "blob_name", "sha256", "approval_status")):
            raise ValueError("Current uploaded design pair changed")
    for row in snapshots:
        actual = by_id.get(row["snapshot_id"])
        if (actual is None or str(actual["period_end"]) != row["period_end"]
                or actual["data_sha256"] != row["data_sha256"]
                or sha(actual["snapshot_payload_json"].encode()) != row["data_sha256"]):
            raise ValueError("Live frozen snapshot differs from captured batch")

    def put(name, data, content_type):
        blob = service.get_blob_client(CONTAINER, OUTPUT + name)
        try:
            blob.upload_blob(data, overwrite=False,
                content_settings=ContentSettings(content_type=content_type),
                metadata={"sha256": sha(data), "subject_id": SUBJECT, "layout": "v14r7"})
        except ResourceExistsError:
            if blob.download_blob().readall() != data:
                raise ValueError("Private output already exists with different bytes")
        if blob.get_blob_properties().metadata.get("sha256") != sha(data):
            raise ValueError("Private artifact metadata readback mismatch")

    artifacts = []
    with TemporaryDirectory(prefix="alpha-ms-v14r7-") as directory:
        folder = Path(directory)
        for role, asset in by_role.items():
            data = service.get_blob_client(asset["container_name"], asset["blob_name"]).download_blob().readall()
            if sha(data) != asset["sha256"]:
                raise ValueError("Uploaded design bytes differ")
            (folder / (role + ".pdf")).write_bytes(data)
        for row in snapshots:
            period = row["period_end"]
            # Preserve the frozen parent and use the already reconciled private
            # correction for August/September. Never revert to their old gaps.
            source_blob = (RECOVERY + 'corrected-inputs/' + period + '.json'
                           if period in corrected else INPUT + 'snapshots/' + period + '.json')
            source_hash = corrected[period]['data_sha256'] if period in corrected else row['data_sha256']
            payload_bytes = service.get_blob_client(CONTAINER, source_blob).download_blob().readall()
            if sha(payload_bytes) != source_hash:
                raise ValueError("Frozen snapshot bytes differ")
            payload = json.loads(payload_bytes)
            if payload.get("subject_id") != SUBJECT or payload.get("period_end") != period:
                raise ValueError("Snapshot account or month mismatch")
            if payload.get("synthetic_only") or payload.get("synthetic_data"):
                raise ValueError("Synthetic inputs rejected")
            unchanged = json.dumps(payload, sort_keys=True, allow_nan=False)
            name = "AlphaWorld_MS_Factsheet_" + period + ".pdf"
            target = folder / name
            result = render_factsheet(payload, template_path=folder / "template.pdf",
                design_model_path=folder / "completed_example.pdf", output_path=target)
            if json.dumps(payload, sort_keys=True, allow_nan=False) != unchanged:
                raise ValueError("Renderer mutated its frozen input")
            if result["layout_version"] != release["layout_version"]:
                raise ValueError("Rendered layout differs")
            _require_factsheet_identity(target, "iStrategy: Alpha World MS", subject_id=SUBJECT)
            data = target.read_bytes()
            put(name, data, "application/pdf")
            artifacts.append({**row, 'parent_data_sha256': row['data_sha256'],
                'data_sha256': source_hash, 'input_blob': source_blob,
                'previously_reconciled_input': period in corrected,
                "blob_name": OUTPUT + name, "sha256": sha(data), "render": result})
            print(json.dumps({"status": "rendered_in_azure", "period_end": period,
                              "sha256": sha(data)}), flush=True)
    # Both installed production entry points import the same versioned renderer.
    import run_registry_month_end_packages
    import run_self_service_onboarding_reports
    record = {"status": "private_rendered_in_azure", "subject_id": SUBJECT,
        "count": len(artifacts), "layout_version": release["layout_version"],
        "image_digest": image, "release_manifest_sha256": sha((ROOT / "ALPHA_MS_RENDER_RELEASE.json").read_bytes()),
        "capture_sha256": sha(capture_bytes), "installed_files_sha256": release["installed_files_sha256"],
        "artifacts": artifacts, "source_data_changed": False, "database_writes": 0,
        "website_reports_replaced": False, "reports_released": False, "onboarding_activated": False,
        "future_month_end_renderer_and_identity_check_verified": True,
        "subject_lifecycle": subject["lifecycle_status"],
        "month_end_reporting_registration": [r["operating_state"] for r in registrations]}
    put("manifest.json", json.dumps(record, sort_keys=True, indent=2).encode(), "application/json")
    print("ALPHA_MS_AZURE_BATCH_COMPLETE " + json.dumps({k: v for k, v in record.items() if k != "artifacts"}), flush=True)


if __name__ == "__main__":
    main()
