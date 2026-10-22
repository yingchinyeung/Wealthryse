"""Verify/deploy a watchdog for the shadow monitor itself, not data-quality paging."""
import argparse
import json
from pathlib import Path
from audit_azure_runtime import az, RG

JOB = "ibkrv2stg-metadata-monitor"
RULE = "ibkr-v2-metadata-monitor-watchdog"
WORKSPACE = "5264a124-10cd-45f6-93a5-6d65c37e9bfb"
ROOT = Path(__file__).resolve().parents[1]


def query():
    # Two 30-minute opportunities plus a 15-minute log-delivery grace. Any
    # execution with a verified completion can establish monitor liveness.
    return """ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(2d)
| where ContainerGroupName_s startswith 'ibkrv2stg-metadata-monitor-'
| where Log_s startswith 'METADATA_MONITOR '
| extend Evidence=parse_json(substring(Log_s, 17))
| where Evidence.kind == 'complete' and Evidence.mode == 'shadow' and Evidence.access_check == 'passed'
| summarize LastCompletion=max(TimeGenerated)
| where isnull(LastCompletion) or LastCompletion < ago(75m)
| extend JobName='ibkrv2stg-metadata-monitor', Issue='No verified monitor completion within 75 minutes'
| project JobName, Issue, LastCompletion"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deploy", action="store_true")
    args = parser.parse_args()
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise RuntimeError("Unexpected subscription")
    current = az("containerapp", "job", "show", "-g", RG, "-n", JOB)["properties"]["configuration"]
    if current["triggerType"] != "Schedule" or current["scheduleTriggerConfig"]["cronExpression"] != "7,37 * * * *" or current["replicaRetryLimit"] != 0:
        raise RuntimeError("Monitor schedule drift: review watchdog")
    live = az("monitor", "log-analytics", "query", "-w", WORKSPACE, "--analytics-query", query())
    empty = "datatable(TimeGenerated:datetime,ContainerGroupName_s:string,Log_s:string)[]"
    synthetic = az("monitor", "log-analytics", "query", "-w", WORKSPACE, "--analytics-query", query().replace("ContainerAppConsoleLogs_CL", empty))
    if len(synthetic) != 1 or synthetic[0]["JobName"] != JOB:
        raise RuntimeError("Watchdog did not detect an empty stream")
    if live:
        raise RuntimeError("Monitor currently lacks recent evidence; investigate before enabling alert")
    parameters = {"rules":{"value":[{"name":RULE,"workspaceName":"ibkrv2stg-logs","query":query()}]}, "enabled":{"value":True}}
    folder = ROOT / ".work/operational-observability-20261001"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "monitor-watchdog-parameters.json"
    path.write_text(json.dumps(parameters, indent=2), encoding="utf-8")
    result = {"live_missing":0, "synthetic_missing":1, "rule":RULE, "deployed":False}
    if args.deploy:
        deployed = az("deployment", "group", "create", "-g", RG, "-n", "metadata-monitor-watchdog-20261001",
                      "--template-file", str(ROOT / "infra/platform-schedule-watchdogs.bicep"), "--parameters", "@" + str(path))
        if deployed["properties"]["provisioningState"] != "Succeeded":
            raise RuntimeError("Watchdog deployment did not succeed")
        resource = az("rest", "--method", "get", "--url", f"https://management.azure.com/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/{RG}/providers/Microsoft.Insights/scheduledQueryRules/{RULE}?api-version=2023-12-01")
        props = resource["properties"]
        if props["enabled"] is not True or props["criteria"]["allOf"][0]["query"] != query() or len(props["actions"]["actionGroups"]) != 1 or not props["actions"]["actionGroups"][0].lower().endswith("/actiongroups/ibkrv2stg-alerts"):
            raise RuntimeError("Watchdog readback mismatch")
        result["deployed"] = True
    (folder / "monitor-watchdog-evidence.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
