"""One-off private-network capture; read-only SQL and create-only output blobs."""
import hashlib
import json
import os
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.core.exceptions import ResourceExistsError

TENANT = "00000000-0000-0000-0000-000000000001"
SUBJECT = "alpha-world-ms"
PREFIX = "render-inputs/alpha-world-ms/v13-20261007/"
CONTAINER = "internal-reports"


def sha(content):
    return hashlib.sha256(content).hexdigest()


def put(service, name, content, content_type):
    blob = service.get_blob_client(CONTAINER, PREFIX + name)
    try:
        blob.upload_blob(content, overwrite=False,
            content_settings=ContentSettings(content_type=content_type),
            metadata={"sha256": sha(content), "subject_id": SUBJECT})
    except ResourceExistsError:
        if blob.download_blob().readall() != content:
            raise ValueError("Existing captured input differs")


def main():
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row,
            connect_timeout=25, options="-c default_transaction_read_only=on -c statement_timeout=120000") as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        rows = connection.execute("""SELECT snapshot_id::text,period_end,status,
            quality_status,data_sha256,snapshot_payload_json,publication_allowed,
            publication_blockers_json FROM reporting_snapshots
            WHERE tenant_id=%s AND subject_id=%s
              AND status IN ('frozen','approved') AND quality_status<>'fail'
            ORDER BY period_end,snapshot_id""", (TENANT,SUBJECT)).fetchall()
        assets = connection.execute("""SELECT template_asset_id::text,report_type,
            asset_role,container_name,blob_name,sha256,approval_status
            FROM subject_report_template_assets WHERE tenant_id=%s AND subject_id=%s
              AND report_type='factsheet' AND is_current ORDER BY asset_role""",
            (TENANT,SUBJECT)).fetchall()
        expected = [str(r["period_end"]) for r in connection.execute("""
            SELECT DISTINCT i.period_end FROM onboarding_historical_report_items i
            JOIN onboarding_historical_report_batches b ON b.batch_id=i.batch_id
            WHERE b.tenant_id=%s AND b.subject_id=%s ORDER BY i.period_end""", (TENANT,SUBJECT))]
        connection.rollback()
    if not expected or len(expected)>120:
        raise ValueError("Unexpected historical period scope")
    selected = {}
    for row in rows:
        period = str(row["period_end"])
        if period not in expected:
            continue
        if period in selected:
            raise ValueError("Duplicate active frozen snapshots")
        content = row["snapshot_payload_json"].encode("utf-8")
        payload = json.loads(content)
        if sha(content)!=row["data_sha256"] or payload.get("subject_id")!=SUBJECT or payload.get("period_end")!=period:
            raise ValueError("Frozen snapshot identity or hash mismatch")
        selected[period] = (row,content)
    if set(selected)!=set(expected):
        raise ValueError("Missing eligible frozen monthly snapshots")
    if {a["asset_role"] for a in assets}!={"template","completed_example"} or len(assets)!=2:
        raise ValueError("Uploaded design pair missing")
    service = BlobServiceClient(os.environ["AZURE_STORAGE_ACCOUNT_URL"],
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    downloads = []
    for asset in assets:
        content = service.get_blob_client(asset["container_name"],asset["blob_name"]).download_blob().readall()
        if sha(content)!=asset["sha256"]:
            raise ValueError("Uploaded design hash mismatch")
        downloads.append((asset["asset_role"]+".pdf",content))
    entries = []
    for period,(row,content) in selected.items():
        put(service,"snapshots/"+period+".json",content,"application/json")
        entries.append({key:value for key,value in row.items() if key!="snapshot_payload_json"})
    for name,content in downloads:
        put(service,name,content,"application/pdf")
    manifest = {"subject_id":SUBJECT,"tenant_id":TENANT,"database_mode":"repeatable_read_read_only",
        "assets":assets,"snapshots":entries,"expected_periods":expected,"source_data_changed":False}
    put(service,"capture.json",json.dumps(manifest,indent=2,default=str).encode(),"application/json")
    print(json.dumps({"status":"captured","count":len(entries),"prefix":PREFIX,"periods":expected}),flush=True)


if __name__ == "__main__":
    main()
