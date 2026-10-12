"""Live scheduled-worker qualification without activation or future data invention.

Only imports the installed renderer; does not replace application modules.
Verifies the September frozen parent, uses its reconciled private correction
and uploads one private canary PDF.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

sys.path.insert(0, "/app/scripts")
from generate_month_end_package import _require_factsheet_identity
from ibkr_etl.monthly_artifacts import render_factsheet, factsheet_layout_version


def main():
    tenant = "00000000-0000-0000-0000-000000000001"
    subject = "alpha-world-ms"
    release = json.loads(Path("/app/ALPHA_MS_RENDER_RELEASE.json").read_bytes())
    for name, expected in release["installed_files_sha256"].items():
        if hashlib.sha256(Path("/app", name).read_bytes()).hexdigest() != expected:
            raise ValueError("Installed renderer source differs from release")
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row,
            connect_timeout=25, options="-c default_transaction_read_only=on -c statement_timeout=120000") as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        rows = connection.execute("""SELECT snapshot_payload_json,data_sha256,snapshot_id::text
            FROM reporting_snapshots WHERE tenant_id=%s AND subject_id=%s AND period_end='2026-09-30'
            AND status IN ('frozen','approved') AND quality_status<>'fail'""", (tenant, subject)).fetchall()
        assets = connection.execute("""SELECT asset_role,container_name,blob_name,sha256
            FROM subject_report_template_assets WHERE tenant_id=%s AND subject_id=%s
            AND report_type='factsheet' AND is_current AND approval_status='validated'""", (tenant, subject)).fetchall()
        connection.rollback()
    if len(rows) != 1 or len(assets) != 2 or {a["asset_role"] for a in assets} != {"template", "completed_example"}:
        raise ValueError("Exact eligible snapshot and uploaded design pair required")
    raw = rows[0]["snapshot_payload_json"].encode()
    if hashlib.sha256(raw).hexdigest() != rows[0]["data_sha256"]:
        raise ValueError("Frozen snapshot hash differs")
    service = BlobServiceClient(os.environ["AZURE_STORAGE_ACCOUNT_URL"],
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    recovery = 'review/alpha-world-ms/source-recovery-20261007/'
    corrected_manifest = service.get_blob_client('internal-reports', recovery + 'corrected-reports/manifest.json').download_blob().readall()
    if hashlib.sha256(corrected_manifest).hexdigest() != release['corrected_manifest_sha256']:
        raise ValueError('Reviewed recovery manifest differs')
    corrected = [r for r in json.loads(corrected_manifest)['artifacts'] if r['period_end'] == '2026-09-30']
    if len(corrected) != 1:
        raise ValueError('Unique corrected September input required')
    raw = service.get_blob_client('internal-reports', recovery + 'corrected-inputs/2026-09-30.json').download_blob().readall()
    if hashlib.sha256(raw).hexdigest() != corrected[0]['data_sha256']:
        raise ValueError('Corrected September payload differs')
    with TemporaryDirectory(prefix="ms-monthend-qualification-") as directory:
        folder = Path(directory)
        for asset in assets:
            content = service.get_blob_client(asset["container_name"], asset["blob_name"]).download_blob().readall()
            if hashlib.sha256(content).hexdigest() != asset["sha256"]:
                raise ValueError("Design authority download hash differs")
            (folder / (asset["asset_role"] + ".pdf")).write_bytes(content)
        snapshot = json.loads(raw)
        before = json.dumps(snapshot, sort_keys=True, allow_nan=False)
        output = folder / "qualification.pdf"
        result = render_factsheet(snapshot, template_path=folder / "template.pdf",
            design_model_path=folder / "completed_example.pdf", output_path=output)
        _require_factsheet_identity(output, "iStrategy: Alpha World MS", subject_id=subject)
        if before != json.dumps(snapshot, sort_keys=True, allow_nan=False):
            raise ValueError("Renderer modified input")
        content = output.read_bytes()
    prefix = "qualification/alpha-world-ms/monthend-v14r7-20261007/"
    digest = hashlib.sha256(content).hexdigest()
    target = service.get_blob_client("internal-reports", prefix + "factsheet.pdf")
    target.upload_blob(content, overwrite=False, content_settings=ContentSettings(content_type="application/pdf"),
        metadata={"sha256": digest, "subject_id": subject})
    if target.download_blob().readall() != content:
        raise ValueError("Scheduled worker private artifact readback failed")
    record = {"status": "passed", "subject_id": subject, "period_end": "2026-09-30",
        "image_digest": os.environ["ALPHA_MS_IMAGE_DIGEST"], "layout_version": result["layout_version"],
        "source_sha256": corrected[0]['data_sha256'],
        "frozen_parent_sha256": rows[0]["data_sha256"], "pdf_sha256": digest,
        "private_pdf_blob": prefix + "factsheet.pdf", "database_writes": 0,
        "installed_files_verified": True, "uploaded_design_download_verified": True,
        "private_render_and_upload_verified": True, "future_data_invented": False,
        "reports_released": False, "onboarding_activated": False}
    if result["layout_version"] != release["layout_version"] or factsheet_layout_version(subject) != release["layout_version"]:
        raise ValueError("Wrong scheduled-worker layout")
    service.get_blob_client("internal-reports", prefix + "verification.json").upload_blob(
        json.dumps(record, sort_keys=True).encode(), overwrite=False,
        content_settings=ContentSettings(content_type="application/json"))
    print("ALPHA_MS_MONTHEND_RUNTIME_VERIFIED " + json.dumps(record), flush=True)


if __name__ == "__main__":
    main()
