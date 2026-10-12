"""One-off isolated blob drill; preserves existing job definitions and schedules."""
import argparse
import base64
import copy
import hashlib
import json
import re
import uuid
from pathlib import Path
from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request

ROOT = Path(__file__).resolve().parents[1]
JOB = "ibkrv2stg-bootstrap"
SUB = "3956e821-cee4-403f-8708-e84e2ed490eb"


def settings(job):
    return hashlib.sha256(json.dumps({"identity": job.get("identity"),
        "configuration": job["properties"]["configuration"], "template": job["properties"]["template"]},
        sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "read"))
    parser.add_argument("--execution")
    args = parser.parse_args()
    if az("account", "show", "--query", "id") != SUB:
        raise ValueError("Unexpected subscription")
    folder = ROOT / ".work/report-blob-recovery-20261002"
    folder.mkdir(parents=True, exist_ok=True)
    job = az("containerapp", "job", "show", "-g", RG, "-n", JOB)
    baseline = settings(job)
    if args.action == "start":
        template = copy.deepcopy(job["properties"]["template"])
        if template.get("volumes") or len(template["containers"]) != 1 or job["properties"]["configuration"]["replicaRetryLimit"] != 0:
            raise ValueError("Unreviewed diagnostic configuration")
        identities = job["identity"]["userAssignedIdentities"]
        if len(identities) != 1:
            raise ValueError("Single reviewed managed identity required")
        client_id = next(iter(identities.values()))["clientId"]
        if client_id != "bbed2e9f-115e-4338-8acd-06e8ac5f237a":
            raise ValueError("Unexpected diagnostic identity")
        container = template["containers"][0]
        if not container["image"].endswith("@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c"):
            raise ValueError("Unexpected diagnostic image")
        run_id = str(uuid.uuid4())
        container["env"] = [{"name": "DATABASE_URL", "secretRef": "database-admin-url"},
            {"name": "AZURE_CLIENT_ID", "value": client_id}, {"name": "RECOVERY_DRILL_ID", "value": run_id},
            {"name": "PORTFOLIO_TENANT_ID", "value": "00000000-0000-0000-0000-000000000001"}]
        source = (ROOT / "scripts/verify_report_blob_recovery.py").read_bytes()
        encoded = base64.b64encode(source).decode()
        container["command"] = ["python"]
        container["args"] = ["-c", f"import base64;exec(compile(base64.b64decode('{encoded}'),'blob-recovery','exec'))"]
        template.pop("volumes", None)
        response = request("POST", job["id"] + "/start?api-version=2024-03-01", data=template)
        result = {"execution": response["name"], "run_id": run_id, "job_settings_sha256": baseline,
                  "source_sha256": hashlib.sha256(source).hexdigest(), "originals_read_only": True}
        suffix = "-dispatch"
    else:
        if not re.fullmatch(JOB + r"-[a-z0-9]{7}", args.execution or ""):
            raise ValueError("Exact execution required")
        dispatch = json.loads((folder / (args.execution + "-dispatch.json")).read_text(encoding="utf-8"))
        state = az("containerapp", "job", "execution", "show", "-g", RG, "-n", JOB,
                   "--job-execution-name", args.execution)
        logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb",
            "--analytics-query", "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + args.execution +
            "' | where Log_s startswith 'BLOB_RECOVERY ' | project Log_s")
        records = [json.loads(row["Log_s"].removeprefix("BLOB_RECOVERY ")) for row in logs]
        result = {"execution": args.execution, "status": state["properties"]["status"], "records": records,
                  "job_settings_unchanged": dispatch["job_settings_sha256"] == baseline}
        suffix = ""
    (folder / (result["execution"] + suffix + ".json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.action == "read":
        print(json.dumps({key: value for key, value in result.items() if key != "records"}
                         | {"assets": sum(r.get("recovered") is True for r in result["records"]),
                            "summaries": [r for r in result["records"] if "recovered" not in r]}))
    else:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
