"""Collect a completed probe's allow-listed logs and assess metadata locally."""
import argparse
import json
import re
import sys
from pathlib import Path

from audit_azure_runtime import az, RG

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ibkr_etl.operational_monitor import assess, assemble_evidence, monitor_summary
from ibkr_etl.market_calendar import check_benchmark_dates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("execution")
    args = parser.parse_args()
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise RuntimeError("Unexpected subscription; evidence collection stopped")
    if not re.fullmatch(r"ibkrv2stg-bootstrap-[a-z0-9]{7}", args.execution):
        raise ValueError("Expected an existing operational bootstrap execution")
    execution = az("containerapp", "job", "execution", "show", "-g", RG,
        "-n", "ibkrv2stg-bootstrap", "--job-execution-name", args.execution,
        "--query", "{name:name,status:properties.status}")
    if execution["status"] != "Succeeded":
        print(json.dumps(execution))
        return
    logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb",
        "--analytics-query", "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" +
        args.execution + "' | where Log_s startswith 'OPERATIONAL_EVIDENCE ' | project Log_s")
    records = []
    for row in logs:
        records.append(json.loads(row["Log_s"].removeprefix("OPERATIONAL_EVIDENCE ")))
    try:
        evidence = assemble_evidence(records)
    except ValueError as exc:
        print(str(exc) + "; no health conclusion recorded.")
        return
    assessment = assess(evidence)
    assessment["benchmark_freshness_shadow"] = check_benchmark_dates(evidence)
    assessment.update(monitor_summary(assessment,assessment["benchmark_freshness_shadow"]))
    folder = ROOT / ".work" / "operational-observability-20261001"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{args.execution}.json").write_text(json.dumps(
        {"execution":execution, "evidence":evidence,"assessment":assessment}, indent=2), encoding="utf-8")
    print(json.dumps(assessment, indent=2))
    print("Benchmark dates:", json.dumps(evidence["benchmarks"]))
    print("Risk-free dates:", json.dumps(evidence["risk_free"]))
    print("Workflow counts:", json.dumps(evidence["work_counts"]))
    print("Outbox counts:", json.dumps(evidence["outbox_counts"]))


if __name__ == "__main__":
    main()
