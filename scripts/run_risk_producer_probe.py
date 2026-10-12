"""One-off read-only producer verification using the pinned risk runtime."""
import argparse
import base64
import copy
import hashlib
import json
import re
from pathlib import Path
from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request
from pin_risk_worker_release import IMAGE

ROOT = Path(__file__).resolve().parents[1]
JOB = "ibkrv2stg-risk-snapshots"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "read"])
    parser.add_argument("--execution")
    parser.add_argument("--probe", choices=["risk", "comparison", "cashflow", "fx-origin"], default="risk")
    args = parser.parse_args()
    module_name, script_name, prefix, folder_name = {
        "risk": ("workflow_risk_evidence", "probe_risk_producer", "RISK_PRODUCER ", "risk-producer"),
        "comparison": ("comparison_source_evidence", "probe_comparison_sources", "COMPARISON_SOURCE ", "comparison-source"),
        "cashflow": ("cashflow_source_evidence", "probe_cashflow_sources", "CASHFLOW_SOURCE ", "cashflow-source"),
        "fx-origin": ("fx_source_evidence", "probe_fx_origins", "FX_ORIGIN ", "fx-origin"),
    }[args.probe]
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise ValueError("Unexpected subscription")
    folder = ROOT / (".work/" + folder_name + "-20261001")
    folder.mkdir(parents=True, exist_ok=True)
    job = az("containerapp", "job", "show", "-g", RG, "-n", JOB)
    if args.action == "read":
        if not re.fullmatch(JOB + r"-[a-z0-9]{7}", args.execution or ""):
            raise ValueError("Exact execution required")
        state = az("containerapp", "job", "execution", "show", "-g", RG, "-n", JOB,
                   "--job-execution-name", args.execution)
        logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb",
            "--analytics-query", "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"
            + args.execution + "' | where Log_s startswith '" + prefix + "' | project Log_s")
        record = {"execution": args.execution, "status": state["properties"]["status"],
                  "records": [json.loads(r["Log_s"].removeprefix(prefix)) for r in logs]}
    else:
        template = copy.deepcopy(job["properties"]["template"])
        if (template.get("volumes") or len(template["containers"]) != 1
                or job["properties"]["configuration"]["replicaRetryLimit"] != 0
                or template["containers"][0]["image"] != IMAGE):
            raise ValueError("Unreviewed risk runtime configuration")
        template.pop("volumes", None)
        source = "import sys,types\nsys.path.insert(0,'/app/src')\n"
        modules = ({"cashflow": ["comparison_source_evidence"], "fx-origin": ["fx_selection"]}.get(args.probe, [])) + [module_name]
        for name in modules:
            module = (ROOT / ("src/ibkr_etl/" + name + ".py")).read_text(encoding="utf-8")
            source += "m=types.ModuleType('ibkr_etl." + name + "');m.__package__='ibkr_etl'\n"
            source += "sys.modules[m.__name__]=m\nexec(compile(" + repr(module) + ", 'risk-evidence', 'exec'),m.__dict__)\n"
        source += (ROOT / ("scripts/" + script_name + ".py")).read_text(encoding="utf-8")
        encoded = base64.b64encode(source.encode()).decode()
        template["containers"][0]["command"] = ["python"]
        template["containers"][0]["args"] = ["-c", f"import base64;exec(compile(base64.b64decode('{encoded}'),'risk-producer-probe','exec'))"]
        result = request("POST", job["id"] + "/start?api-version=2024-03-01", data=template)
        record = {"execution": result["name"], "mode": "read_only", "probe": args.probe, "image": IMAGE,
                  "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
    suffix = "-dispatch" if args.action == "start" else ""
    (folder / (record["execution"] + suffix + ".json")).write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
