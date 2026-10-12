"""One-off isolated-schema evidence-import rehearsal; no job template updates."""
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
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["start", "read"])
    p.add_argument("--execution")
    args = p.parse_args()
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise ValueError("Unexpected subscription")
    folder = ROOT / ".work/snapshot-evidence-import-20261001"
    folder.mkdir(parents=True, exist_ok=True)
    job = az("containerapp", "job", "show", "-g", RG, "-n", JOB)
    if args.action == "read":
        if not re.fullmatch(JOB + r"-[a-z0-9]{7}", args.execution or ""):
            raise ValueError("Exact execution required")
        state = az("containerapp", "job", "execution", "show", "-g", RG, "-n", JOB,
                   "--job-execution-name", args.execution)
        logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb",
                  "--analytics-query", "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"
                  + args.execution + "' | where Log_s startswith 'SNAPSHOT_EVIDENCE_TRIAL ' | project Log_s")
        result = {"execution": args.execution, "status": state["properties"]["status"],
                  "records": [json.loads(r["Log_s"].removeprefix("SNAPSHOT_EVIDENCE_TRIAL ")) for r in logs]}
    else:
        template = copy.deepcopy(job["properties"]["template"])
        if template.get("volumes") or len(template["containers"]) != 1 or job["properties"]["configuration"]["replicaRetryLimit"] != 0:
            raise ValueError("Unexpected diagnostic configuration")
        template.pop("volumes", None)
        container = template["containers"][0]
        tenant = next(e for e in container["env"] if e["name"] == "PORTFOLIO_TENANT_ID")
        container["env"] = [tenant, {"name": "DATABASE_URL", "secretRef": "database-admin-url"}]
        source = "import sys,types\nsys.path.insert(0,'/app/src')\n"
        for name in ("snapshot_evidence", "pilot_snapshot", "snapshot_evidence_import", "snapshot_atomic_import"):
            code = (ROOT / ("src/ibkr_etl/" + name + ".py")).read_text(encoding="utf-8")
            source += "m=types.ModuleType('ibkr_etl." + name + "');m.__package__='ibkr_etl'\n"
            source += "sys.modules[m.__name__]=m\nexec(compile(" + repr(code) + ",'evidence-trial','exec'),m.__dict__)\n"
        source += (ROOT / "scripts/verify_snapshot_evidence_import.py").read_text(encoding="utf-8")
        encoded = base64.b64encode(source.encode()).decode()
        container["command"] = ["python"]
        container["args"] = ["-c", f"import base64;exec(compile(base64.b64decode('{encoded}'),'evidence-trial','exec'))"]
        response = request("POST", job["id"] + "/start?api-version=2024-03-01", data=template)
        result = {"execution": response["name"], "mode": "isolated_schema_rollback_only",
                  "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "image": container["image"]}
    suffix = "-dispatch" if args.action == "start" else ""
    (folder / (result["execution"] + suffix + ".json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
