"""Dispatch/read a one-off read-only cache audit using the existing risk job."""
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
JOB = "ibkrv2stg-risk-snapshots"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "read"])
    parser.add_argument("--execution")
    parser.add_argument("--recalculate", action="store_true", help="Compare mismatched sampled caches in memory; still no database writes.")
    args = parser.parse_args()
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise RuntimeError("Unexpected subscription")
    job = az("containerapp", "job", "show", "-g", RG, "-n", JOB)
    if args.action == "start":
        template = copy.deepcopy(job["properties"]["template"])
        if template.get("volumes") or len(template["containers"]) != 1 or job["properties"]["configuration"]["replicaRetryLimit"] != 0:
            raise RuntimeError("Unexpected risk diagnostic configuration")
        template.pop("volumes", None)
        container = template["containers"][0]
        if "@sha256:" not in container["image"]:
            raise RuntimeError("Risk diagnostic requires pinned image")
        source = "SOURCE_RISK=" + repr((ROOT / "src/ibkr_etl/risk_snapshots.py").read_text(encoding="utf-8")) + "\n"
        source += "ALLOW_SAMPLE_RECALCULATION=" + repr(args.recalculate) + "\n"
        source += (ROOT / "scripts/audit_risk_cache.py").read_text(encoding="utf-8")
        container["command"] = ["python"]
        encoded = base64.b64encode(source.encode()).decode()
        container["args"] = ["-c", f"import base64;exec(compile(base64.b64decode('{encoded}'),'risk-cache-audit','exec'))"]
        result = request("POST", job["id"] + "/start?api-version=2024-03-01", data=template)
        print(json.dumps({"execution":result["name"], "source_sha256":hashlib.sha256(source.encode()).hexdigest(), "mode":"read_only_sample"}))
        return
    if not re.fullmatch(JOB + r"-[a-z0-9]{7}", args.execution or ""):
        raise ValueError("Expected exact audit execution")
    execution = request("GET", job["id"] + "/executions/" + args.execution + "?api-version=2024-03-01")
    print(json.dumps({"execution":args.execution,"status":execution["properties"]["status"]}))
    if execution["properties"]["status"] != "Succeeded":
        return
    logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb", "--analytics-query",
              "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + args.execution + "' | where Log_s startswith 'RISK_CACHE_AUDIT ' | project Log_s")
    records = {row["Log_s"]:json.loads(row["Log_s"].removeprefix("RISK_CACHE_AUDIT ")) for row in logs}
    rows = list(records.values())
    complete = [r for r in rows if r["kind"] == "complete"]
    if len(complete) != 1 or any(r["kind"] == "error" for r in rows):
        raise RuntimeError("Missing or conflicting audit completion")
    if sum(r["kind"] == "sample" for r in rows) != complete[0]["samples"] or sum(r["kind"] == "inventory" for r in rows) != complete[0]["subjects"]:
        raise RuntimeError("Incomplete audit log delivery")
    folder = ROOT / ".work/risk-cache-audit-20261001"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (args.execution + ".json")).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
