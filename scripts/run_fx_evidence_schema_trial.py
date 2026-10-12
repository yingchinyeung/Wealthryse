"""Execution-only synthetic migration rehearsal; no persistent configuration."""
import argparse
import base64
import copy
import hashlib
import json
import re
from pathlib import Path
from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request

ROOT = Path(__file__).resolve().parents[1]
JOB = "ibkrv2stg-bootstrap"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "read"])
    parser.add_argument("--execution")
    parser.add_argument("--component-only", action="store_true", help="Skip the already-verified full-size synthetic workload")
    parser.add_argument("--preflight", action="store_true", help="Read-only live migration/default-privilege inventory")
    parser.add_argument("--real-inputs", type=Path, help="Private metadata manifest for rollback-only real-bundle acceptance")
    args = parser.parse_args()
    if args.preflight and args.real_inputs:
        raise ValueError("Read-only preflight cannot stage inputs")
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise ValueError("Unexpected subscription")
    folder = ROOT / ".work/fx-evidence-schema-20261002"
    folder.mkdir(parents=True, exist_ok=True)
    job = az("containerapp", "job", "show", "-g", RG, "-n", JOB)
    job_settings_hash = hashlib.sha256(json.dumps({"identity":job.get("identity"),
        "configuration":job["properties"]["configuration"],"template":job["properties"]["template"]},
        sort_keys=True,separators=(",",":")).encode()).hexdigest()
    if args.action == "read":
        if not re.fullmatch(JOB+r"-[a-z0-9]{7}", args.execution or ""):
            raise ValueError("Exact execution required")
        state = az("containerapp", "job", "execution", "show", "-g", RG, "-n", JOB, "--job-execution-name", args.execution)
        logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb",
            "--analytics-query", "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith 'FX_EVIDENCE_SCHEMA_TRIAL ' or Log_s startswith 'FX_EVIDENCE_SCALE_PROGRESS ' | project Log_s")
        result = {"execution":args.execution,"status":state["properties"]["status"],
            "records":[json.loads(r["Log_s"].removeprefix("FX_EVIDENCE_SCHEMA_TRIAL ")) for r in logs
                       if r["Log_s"].startswith("FX_EVIDENCE_SCHEMA_TRIAL ")],
            "progress":[json.loads(r["Log_s"].removeprefix("FX_EVIDENCE_SCALE_PROGRESS ")) for r in logs
                        if r["Log_s"].startswith("FX_EVIDENCE_SCALE_PROGRESS ")]}
        dispatch_path = folder / (args.execution + "-dispatch.json")
        if dispatch_path.exists():
            dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
            expected_settings = dispatch.get("job_settings_sha256")
            if expected_settings:
                result["job_settings_unchanged"] = expected_settings == job_settings_hash
    else:
        real_inputs = None
        if args.real_inputs:
            path = args.real_inputs.resolve()
            if not path.is_relative_to((ROOT / ".work").resolve()) or path.stat().st_size > 32768:
                raise ValueError("Bounded private trial manifest required")
            real_inputs = json.loads(path.read_text(encoding="utf-8"))
            if set(real_inputs) != {"tenant_id", "source_snapshot_sha256", "container", "records", "prepared_at", "execution_authorized"}:
                raise ValueError("Unexpected trial manifest fields")
            fields = {"account_id", "artifact_sha256", "bundle_sha256", "blob_name", "etag", "byte_count"}
            if (real_inputs["execution_authorized"] is not False or len(real_inputs["records"]) != 2
                    or any(set(r) != fields for r in real_inputs["records"])):
                raise ValueError("Metadata-only non-authorizing inputs required")
        template = copy.deepcopy(job["properties"]["template"])
        if template.get("volumes") or len(template["containers"]) != 1 or job["properties"]["configuration"]["replicaRetryLimit"] != 0:
            raise ValueError("Unreviewed diagnostic configuration")
        template.pop("volumes", None)
        container = template["containers"][0]
        if not container["image"].endswith("@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c"):
            raise ValueError("Diagnostic image changed")
        tenant = next(e for e in container["env"] if e["name"]=="PORTFOLIO_TENANT_ID")
        container["env"] = [tenant,{"name":"DATABASE_URL","secretRef":"database-admin-url"}]
        if real_inputs:
            identities = job.get("identity", {}).get("userAssignedIdentities", {})
            if len(identities) != 1:
                raise ValueError("A single reviewed trial identity is required")
            client_id = next(iter(identities.values()))["clientId"]
            container["env"].append({"name": "AZURE_CLIENT_ID", "value": client_id})
        migration = (ROOT/"migrations/postgres/055_fx_evidence_versions.sql").read_text(encoding="utf-8")
        source = "import sys,types\nsys.path.insert(0,'/app/src')\nMIGRATION_SQL="+repr(migration)+"\nRUN_FULL_SIZE="+repr(not args.component_only)+"\n"
        source += "REAL_INPUT_MANIFEST="+repr(real_inputs)+"\n"
        source += "WORKFLOW_MIGRATION_SQL="+repr((ROOT/"migrations/postgres/054_shadow_workflow_evidence.sql").read_text(encoding="utf-8"))+"\n"
        for name in ("parser","fx_evidence_contract","fx_evidence_import","fx_import_lifecycle","evidence_acl"):
            code=(ROOT/("src/ibkr_etl/"+name+".py")).read_text(encoding="utf-8")
            source += "m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\n"
            source += "sys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'fx-evidence-import','exec'),m.__dict__)\n"
        source += (ROOT/"scripts/verify_fx_evidence_schema.py").read_text(encoding="utf-8")
        if args.preflight:
            source = (ROOT/"scripts/probe_fx_rollout.py").read_text(encoding="utf-8")
        encoded = base64.b64encode(source.encode()).decode()
        container["command"] = ["python"]
        container["args"] = ["-c", "import base64;exec(compile(base64.b64decode('"+encoded+"'),'fx-schema-trial','exec'))"]
        response = request("POST",job["id"]+"/start?api-version=2024-03-01",data=template)
        result = {"execution":response["name"],"mode":"read_only_rollout_preflight" if args.preflight else "isolated_schema_force_rollback",
            "source_sha256":hashlib.sha256(source.encode()).hexdigest(),"image":container["image"],
            "job_settings_sha256":job_settings_hash}
    suffix = "-dispatch" if args.action=="start" else ""
    (folder/(result["execution"]+suffix+".json")).write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
