"""Dispatch/read a one-off read-only preflight using existing storage identity."""
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
JOB = "ibkrv2stg-account-sync-daily"
IMAGE = "ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-etl@sha256:44472829f7be06003fd34d0b188794c05003c075c7c0e500e84b523c0808c405"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "read"])
    parser.add_argument("--execution")
    args = parser.parse_args()
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise ValueError("Unexpected subscription")
    folder = ROOT / ".work/snapshot-preflight-20261001"
    folder.mkdir(parents=True, exist_ok=True)
    job = az("containerapp", "job", "show", "-g", RG, "-n", JOB)
    if args.action == "read":
        if not re.fullmatch(JOB+r"-[a-z0-9]{7}", args.execution or ""):
            raise ValueError("Exact execution required")
        state = az("containerapp", "job", "execution", "show", "-g", RG, "-n", JOB, "--job-execution-name", args.execution)
        logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb",
                  "--analytics-query", "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith 'SNAPSHOT_PREFLIGHT ' | project Log_s")
        result = {"execution": args.execution, "status": state["properties"]["status"],
                  "records": [json.loads(r["Log_s"].removeprefix("SNAPSHOT_PREFLIGHT ")) for r in logs]}
    else:
        template = copy.deepcopy(job["properties"]["template"])
        if (template.get("volumes") or len(template["containers"]) != 1
                or job["properties"]["configuration"]["replicaRetryLimit"] != 0
                or template["containers"][0]["image"] != IMAGE):
            raise ValueError("Unreviewed diagnostic runtime")
        template.pop("volumes", None)
        source = "import sys,types\nsys.path.insert(0,'/app/src')\n"
        for name in ("snapshot_evidence", "pilot_snapshot", "snapshot_evidence_import", "snapshot_atomic_import", "fx_source_evidence", "snapshot_preflight"):
            code = (ROOT/("src/ibkr_etl/"+name+".py")).read_text(encoding="utf-8")
            source += "m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\n"
            source += "sys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'snapshot-preflight','exec'),m.__dict__)\n"
        source += (ROOT/"scripts/probe_snapshot_preflight.py").read_text(encoding="utf-8")
        encoded = base64.b64encode(source.encode()).decode()
        template["containers"][0]["command"] = ["python"]
        template["containers"][0]["args"] = ["-c", f"import base64;exec(compile(base64.b64decode('{encoded}'),'snapshot-preflight','exec'))"]
        execution = request("POST", job["id"]+"/start?api-version=2024-03-01", data=template)
        result = {"execution": execution["name"], "mode": "read_only", "image": IMAGE,
                  "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
    suffix = "-dispatch" if args.action == "start" else ""
    (folder/(result["execution"]+suffix+".json")).write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
