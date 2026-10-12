"""Read only known permission-test evidence, and retain it privately."""
import argparse
import json
import re
from pathlib import Path
from audit_azure_runtime import az, RG


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("execution")
    args = parser.parse_args()
    if not re.fullmatch(r"ibkrv2stg-bootstrap-[a-z0-9]{7}", args.execution):
        raise ValueError("Invalid execution")
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise RuntimeError("Unexpected subscription")
    execution = az("containerapp", "job", "execution", "show", "-g", RG, "-n", "ibkrv2stg-bootstrap",
        "--job-execution-name", args.execution,"--query","{name:name,status:properties.status}")
    print(json.dumps(execution))
    if execution["status"] != "Succeeded":
        return
    logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb",
        "--analytics-query", "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + args.execution +
        "' | where Log_s startswith 'METADATA_READER_EVIDENCE ' | project Log_s")
    evidence = [json.loads(r["Log_s"].removeprefix("METADATA_READER_EVIDENCE ")) for r in logs]
    if not evidence:
        print("Evidence not yet available; no permission-test success claimed")
        return
    if any(r != evidence[0] for r in evidence) or evidence[0].get("status") != "pass" or evidence[0].get("trial_role") != "rolled_back":
        raise RuntimeError("Invalid or conflicting permission evidence")
    folder = Path(__file__).resolve().parents[1] / ".work/operational-observability-20261001"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (args.execution + ".json")).write_text(json.dumps(
        {"execution":execution,"evidence":evidence[0]},indent=2),encoding="utf-8")
    print(json.dumps(evidence[0],indent=2))


if __name__ == "__main__":
    main()
