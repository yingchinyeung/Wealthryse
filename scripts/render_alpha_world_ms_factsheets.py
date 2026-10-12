"""Render MS private factsheets from hash-verified, read-only frozen snapshots.

Does not recalculate source data, publish reports, update approvals or activate
onboarding. Azure/database reads are isolated from offline PDF rendering.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SUBJECT = "alpha-world-ms"
TENANT = "00000000-0000-0000-0000-000000000001"
STORAGE = "ibkrv2stgauhecwc4itaoo"
CAPTURE = ROOT / ".work/alpha-ms-factsheets-v13-20261007"
OUTPUT = ROOT / "output/pdf/AlphaWorld_MS_Monthly_Factsheets_v14r2"


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def az(*args: str) -> str:
    result = subprocess.run(["az.cmd", *args], capture_output=True, text=True,
                            timeout=90, check=False)
    if result.returncode:
        # Do not echo arbitrary Azure output or secret-bearing arguments.
        raise RuntimeError("Azure read failed for " + "/".join(args[:2]))
    return result.stdout.strip()


def capture() -> None:
    import psycopg
    from psycopg.rows import dict_row

    if az("account", "show", "--query", "id", "-o", "tsv") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise RuntimeError("Unexpected Azure subscription")
    connection_url = az("keyvault", "secret", "show", "--vault-name",
                        "ibkrv2stg-auhecwc4itaoo", "--name", "database-app-url",
                        "--query", "value", "-o", "tsv")
    try:
        connection = psycopg.connect(connection_url, connect_timeout=20,
            row_factory=dict_row, options="-c default_transaction_read_only=on")
    except Exception as error:
        message = str(error).lower()
        category = next((item for item in (
            "timeout", "password authentication failed", "certificate verify failed",
            "no pg_hba.conf entry", "could not translate host name", "connection refused",
            "invalid connection option", "network is unreachable",
        ) if item in message), type(error).__name__)
        from psycopg.conninfo import conninfo_to_dict
        safe_error = str(error).replace(connection_url, "[redacted connection]")
        for key, value in conninfo_to_dict(connection_url).items():
            if key in {"password", "user"} and value:
                safe_error = safe_error.replace(value, "[redacted]")
        raise RuntimeError("Read-only report database connection failed: " + category
                           + "; " + safe_error[:600]) from None
    del connection_url
    with connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT,))
        rows = connection.execute("""
            SELECT snapshot_id::text, period_end, status, quality_status,
                   data_sha256, snapshot_payload_json, publication_allowed,
                   publication_blockers_json
            FROM reporting_snapshots
            WHERE tenant_id = %s AND subject_id = %s
              AND status IN ('frozen', 'approved') AND quality_status <> 'fail'
            ORDER BY period_end, snapshot_id
        """, (TENANT, SUBJECT)).fetchall()
        assets = connection.execute("""
            SELECT template_asset_id::text, report_type, asset_role,
                   container_name, blob_name, sha256, approval_status
            FROM subject_report_template_assets
            WHERE tenant_id = %s AND subject_id = %s AND report_type = 'factsheet'
              AND is_current
            ORDER BY asset_role
        """, (TENANT, SUBJECT)).fetchall()
        periods = connection.execute("""
            SELECT DISTINCT i.period_end
            FROM onboarding_historical_report_items i
            JOIN onboarding_historical_report_batches b ON b.batch_id = i.batch_id
            WHERE b.tenant_id = %s AND b.subject_id = %s
            ORDER BY i.period_end
        """, (TENANT, SUBJECT)).fetchall()
    expected = [str(row["period_end"]) for row in periods]
    if not expected:
        raise RuntimeError("No historical batch periods registered")
    selected = {}
    for row in rows:
        period = str(row["period_end"])
        if period not in expected:
            continue
        if period in selected:
            raise RuntimeError("Multiple active snapshots for " + period)
        payload = str(row["snapshot_payload_json"]).encode("utf-8")
        if digest(payload) != row["data_sha256"]:
            raise RuntimeError("Frozen snapshot integrity failure for " + period)
        snapshot = json.loads(payload)
        if snapshot.get("subject_id") != SUBJECT or snapshot.get("period_end") != period:
            raise RuntimeError("Snapshot identity mismatch for " + period)
        if snapshot.get("synthetic_only") or snapshot.get("synthetic_data"):
            raise RuntimeError("Synthetic snapshots cannot enter a real-data batch")
        selected[period] = (row, payload)
    missing = sorted(set(expected) - selected.keys())
    if missing:
        raise RuntimeError("Missing eligible frozen snapshots: " + ", ".join(missing))
    by_role = {asset["asset_role"]: asset for asset in assets}
    if set(by_role) != {"template", "completed_example"}:
        raise RuntimeError("Both current owner-uploaded factsheet assets required")
    CAPTURE.mkdir(parents=True, exist_ok=True)
    entries = []
    for period, (row, payload) in selected.items():
        path = CAPTURE / "snapshots" / (period + ".json")
        path.parent.mkdir(exist_ok=True)
        if path.exists() and path.read_bytes() != payload:
            raise RuntimeError("Existing captured snapshot differs")
        path.write_bytes(payload)
        entries.append({key: row[key] for key in row if key != "snapshot_payload_json"})
    for role, asset in by_role.items():
        path = CAPTURE / (role + ".pdf")
        if not path.exists():
            az("storage", "blob", "download", "--account-name", STORAGE,
               "--container-name", asset["container_name"], "--name", asset["blob_name"],
               "--auth-mode", "login", "--file", str(path), "--no-progress", "-o", "none")
        if digest(path.read_bytes()) != asset["sha256"]:
            raise RuntimeError("Uploaded design asset hash mismatch: " + role)
    manifest = {"subject_id": SUBJECT, "tenant_id": TENANT,
        "database_mode": "read_only", "assets": assets, "snapshots": entries,
        "expected_periods": expected, "source_data_changed": False}
    (CAPTURE / "capture.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": "captured", "count": len(entries), "periods": expected}))


def render() -> None:
    from ibkr_etl.monthly_artifacts import (
        ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION, render_factsheet,
    )
    manifest = json.loads((CAPTURE / "capture.json").read_text(encoding="utf-8"))
    for asset in manifest["assets"]:
        if digest((CAPTURE / (asset["asset_role"] + ".pdf")).read_bytes()) != asset["sha256"]:
            raise RuntimeError("Captured design asset changed")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    entries = []
    for row in manifest["snapshots"]:
        period = row["period_end"]
        payload = (CAPTURE / "snapshots" / (period + ".json")).read_bytes()
        if digest(payload) != row["data_sha256"]:
            raise RuntimeError("Captured snapshot changed: " + period)
        snapshot = json.loads(payload)
        before = json.dumps(snapshot, sort_keys=True, allow_nan=False)
        target = OUTPUT / ("AlphaWorld_MS_Factsheet_" + period + ".pdf")
        if target.exists():
            raise FileExistsError(target)
        result = render_factsheet(snapshot, template_path=CAPTURE / "template.pdf",
            design_model_path=CAPTURE / "completed_example.pdf", output_path=target)
        assert json.dumps(snapshot, sort_keys=True, allow_nan=False) == before
        assert result["layout_version"] == ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION
        entries.append({**row, "factsheet": str(target), "sha256": digest(target.read_bytes()),
                        "render": result})
        print(json.dumps({"status": "rendered", "period": period}), flush=True)
    record = {"subject_id": SUBJECT, "count": len(entries),
        "layout_version": ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION,
        "status": "private_rendered_not_released", "artifacts": entries,
        "source_data_changed": False, "onboarding_activated": False,
        "website_reports_replaced": False}
    (OUTPUT / "manifest.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({"status": "complete", "count": len(entries), "output": str(OUTPUT)}))


def dispatch_capture() -> None:
    job = json.loads(az("containerapp", "job", "show", "-g", "rg-ibkr-database-v11",
                        "-n", "ibkrv2stg-onboard-render", "-o", "json"))
    template = copy.deepcopy(job["properties"]["template"])
    if template.get("volumes") or len(template["containers"]) != 1:
        raise RuntimeError("Unexpected report-worker shape")
    if job["properties"]["configuration"]["replicaRetryLimit"] != 0:
        raise RuntimeError("Report worker must retain zero immediate retries")
    template.pop("volumes", None)
    source = Path(__file__).with_name("capture_alpha_world_ms_render_inputs.py").read_bytes()
    container = template["containers"][0]
    container["command"] = ["python"]
    container["args"] = ["-c", "import base64;exec(compile(base64.b64decode('"
        + base64.b64encode(source).decode() + "'),'alpha-ms-read-only-capture','exec'))"]
    token = json.loads(az("account", "get-access-token", "--resource", "https://management.azure.com/", "-o", "json"))["accessToken"]
    request = Request("https://management.azure.com" + job["id"] + "/start?api-version=2024-03-01",
        data=json.dumps(template).encode(), headers={"Authorization": "Bearer " + token,
                                                   "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=60) as response:
        result = json.load(response)
    CAPTURE.mkdir(parents=True, exist_ok=True)
    record = {"execution": result["name"], "source_sha256": digest(source),
              "mode": "read_only_source_create_only_capture", "job_configuration_changed": False}
    (CAPTURE / "dispatch.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record))


def download_capture() -> None:
    prefix = "render-inputs/alpha-world-ms/v13-20261007/"
    names = json.loads(az("storage", "blob", "list", "--account-name", STORAGE,
        "--container-name", "internal-reports", "--auth-mode", "login",
        "--prefix", prefix, "--query", "[].name", "-o", "json"))
    if prefix + "capture.json" not in names:
        raise RuntimeError("Cloud capture has not completed")
    def download(name):
        suffix = name.removeprefix(prefix)
        if ".." in suffix or suffix.startswith(("/", "\\")):
            raise RuntimeError("Invalid captured input path")
        path = CAPTURE / suffix
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            az("storage", "blob", "download", "--account-name", STORAGE,
               "--container-name", "internal-reports", "--name", name,
               "--auth-mode", "login", "--file", str(path), "--no-progress", "-o", "none")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, names))
    manifest = json.loads((CAPTURE / "capture.json").read_text(encoding="utf-8"))
    for row in manifest["snapshots"]:
        if digest((CAPTURE / "snapshots" / (row["period_end"] + ".json")).read_bytes()) != row["data_sha256"]:
            raise RuntimeError("Downloaded snapshot hash mismatch")
    print(json.dumps({"status": "downloaded", "count": len(manifest["snapshots"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["capture", "dispatch-capture", "download-capture", "render"])
    args = parser.parse_args()
    {"capture": capture, "dispatch-capture": dispatch_capture,
     "download-capture": download_capture, "render": render}[args.mode]()
