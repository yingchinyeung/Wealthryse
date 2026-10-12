"""Verify a missing monthly-dispatch alert, then optionally deploy this one rule."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from audit_azure_runtime import az, RG
from ibkr_etl.monthly_schedule_monitor import JOB, CRON, monthly_query

ROOT = Path(__file__).resolve().parents[1]
SUB = "3956e821-cee4-403f-8708-e84e2ed490eb"
WORKSPACE = "5264a124-10cd-45f6-93a5-6d65c37e9bfb"
RULE = "ibkr-v2-monthly-autonomy-schedule-watchdog"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deploy", action="store_true")
    args = parser.parse_args()
    if az("account", "show", "--query", "id") != SUB:
        raise RuntimeError("Unexpected subscription")
    config = az("containerapp", "job", "show", "-g", RG, "-n", JOB)["properties"]["configuration"]
    if (config["triggerType"] != "Schedule" or config["scheduleTriggerConfig"]["cronExpression"] != CRON
            or config["replicaRetryLimit"] != 0 or config["replicaTimeout"] % 60):
        raise RuntimeError("Unreviewed monthly schedule")
    query = monthly_query(config["replicaTimeout"] // 60)
    def run(query_text):
        return az("monitor", "log-analytics", "query", "-w", WORKSPACE, "--analytics-query", query_text)
    live = run(query)
    empty = "datatable(TimeGenerated:datetime,JobName_s:string,Reason_s:string,ExecutionName_s:string)[]"
    synthetic_query = query.replace("ContainerAppSystemLogs_CL", empty).replace(
        "let Clock = now();", "let Clock = datetime(2026-10-01T01:15:00Z);")
    synthetic = run(synthetic_query)
    quiet = run(synthetic_query.replace("2026-10-01T01:15:00Z", "2026-10-04T01:15:00Z"))
    if len(synthetic) != 1 or synthetic[0]["Issue"] != "MissingMonthlyStartTelemetry" or quiet:
        raise RuntimeError("Monthly query acceptance failed")
    if live:
        raise RuntimeError("Existing monthly telemetry gap requires investigation before enabling")
    params = {"rules": {"value": [{"name": RULE, "workspaceName": "ibkrv2stg-logs", "query": query}]},
              "enabled": {"value": True}}
    folder = ROOT / ".work/upgrade-deployments-20261002"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = folder / ("monthly-watchdog-" + stamp + "-parameters.json")
    path.write_text(json.dumps(params, indent=2), encoding="utf-8")
    result = {"rule": RULE, "captured_at": datetime.now(timezone.utc).isoformat(),
              "live_missing": 0, "synthetic_missing": 1, "outside_window_missing": 0,
              "observation_window_hours": 48, "deployed": False}
    if args.deploy:
        preview = az("deployment", "group", "what-if", "-g", RG, "--no-pretty-print",
            "--template-file", str(ROOT / "infra/platform-schedule-watchdogs.bicep"), "--parameters", "@" + str(path))
        changes = preview.get("changes", [])
        unexpected = [item for item in changes if item["changeType"] not in ("NoChange", "Ignore")
                      and not item["resourceId"].lower().endswith("/scheduledqueryrules/" + RULE)]
        if unexpected:
            raise RuntimeError("What-if includes unrelated resource changes")
        result["what_if"] = [{"resource_id": item["resourceId"], "change": item["changeType"]} for item in changes]
        deployment = az("deployment", "group", "create", "-g", RG, "-n", "monthly-watchdog-" + stamp,
            "--template-file", str(ROOT / "infra/platform-schedule-watchdogs.bicep"), "--parameters", "@" + str(path))
        if deployment["properties"]["provisioningState"] != "Succeeded":
            raise RuntimeError("Monthly watchdog deployment did not succeed")
        resource = az("rest", "--method", "get", "--url",
            f"https://management.azure.com/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.Insights/scheduledQueryRules/{RULE}?api-version=2023-12-01")
        props = resource["properties"]
        if (not props["enabled"] or props["criteria"]["allOf"][0]["query"] != query
                or props["evaluationFrequency"] != "PT15M" or props["overrideQueryTimeRange"] != "P2D"
                or props["severity"] != 2 or props["autoMitigate"] is not True
                or len(props["actions"]["actionGroups"]) != 1
                or not props["actions"]["actionGroups"][0].lower().endswith("/actiongroups/ibkr-v11-daily-alerts")):
            raise RuntimeError("Monthly watchdog readback mismatch")
        after = az("containerapp", "job", "show", "-g", RG, "-n", JOB)["properties"]["configuration"]
        if config != after:
            raise RuntimeError("Monthly job configuration changed during deployment")
        result.update(deployed=True, job_configuration_unchanged=True)
    (folder / ("monthly-watchdog-" + stamp + "-evidence.json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
