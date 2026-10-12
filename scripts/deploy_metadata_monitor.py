"""Explicit staged deployment of the isolated shadow monitor.

No existing application/job definition or credential is updated. Run prepare,
provision, install, test, schedule in order; schedule requires complete live
evidence from the restricted identity. Secret material remains in memory/KV.
"""
import argparse
import base64
import hashlib
import json
import secrets
import uuid
from pathlib import Path
from urllib.parse import quote

import requests
from azure.identity import AzureCliCredential
from audit_azure_runtime import az, RG

SUB = "3956e821-cee4-403f-8708-e84e2ed490eb"
BASE = f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/"
VAULT = "ibkrv2stg-auhecwc4itaoo"
SECRET = "database-metadata-monitor-url"
IDENTITY = "ibkrv2stg-metadata-monitor-identity"
JOB = "ibkrv2stg-metadata-monitor"
PROVISION = "ibkrv2stg-metadata-provision"
TENANT = "00000000-0000-0000-0000-000000000001"
IMAGE = "ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c"
ROOT = Path(__file__).resolve().parents[1]


def request(method, path, *, data=None, vault=False, missing=False):
    audience = "https://vault.azure.net/.default" if vault else "https://management.azure.com/.default"
    url = path if vault else "https://management.azure.com" + path
    token = AzureCliCredential().get_token(audience).token
    response = requests.request(method, url, headers={"Authorization":"Bearer " + token}, json=data, timeout=60)
    if missing and response.status_code == 404:
        return None
    if not response.ok:
        # Never print HTTP bodies/URLs containing a password, request data or tokens.
        raise RuntimeError(f"Azure {method} failed HTTP {response.status_code}")
    return response.json() if response.content else {}


def bundle(provision=False):
    paths = {"SOURCE_COLLECTOR":"scripts/collect_operational_evidence.py",
             "SOURCE_ACCESS":"src/ibkr_etl/metadata_monitor_access.py",
             "SOURCE_POLICY":"src/ibkr_etl/operational_monitor.py",
             "SOURCE_CALENDAR":"src/ibkr_etl/market_calendar.py"}
    source = "\n".join(f"{key}={(ROOT / path).read_text(encoding='utf-8')!r}" for key,path in paths.items())
    runner = (ROOT / "scripts/run_metadata_shadow_monitor.py").read_text(encoding="utf-8")
    if provision:
        # Load helper definitions without invoking the normal monitor entrypoint.
        runner = runner.split('\nif __name__ == "__main__":')[0]
        runner += "\n" + (ROOT / "scripts/provision_metadata_monitor.py").read_text(encoding="utf-8")
    source += "\n" + runner
    encoded = base64.b64encode(source.encode()).decode()
    return ["-c", f"import base64;exec(compile(base64.b64decode('{encoded}'),'metadata-monitor','exec'))"], hashlib.sha256(source.encode()).hexdigest()


def definition(provision=False, scheduled=False):
    identity = BASE + "Microsoft.ManagedIdentity/userAssignedIdentities/" + (
        "ibkrv2stg-ingestion-identity" if provision else IDENTITY)
    args, digest = bundle(provision)
    secret_refs = [{"name":"monitor-url", "keyVaultUrl":f"https://{VAULT}.vault.azure.net/secrets/{SECRET}", "identity":identity}]
    env = [{"name":"PORTFOLIO_TENANT_ID", "value":TENANT},
           {"name":"DATABASE_URL", "secretRef":"admin-url" if provision else "monitor-url"}]
    if provision:
        secret_refs.append({"name":"admin-url", "keyVaultUrl":f"https://{VAULT}.vault.azure.net/secrets/database-admin-url", "identity":identity})
        env.append({"name":"MONITOR_DATABASE_URL", "secretRef":"monitor-url"})
    config = {"triggerType":"Schedule" if scheduled else "Manual", "replicaTimeout":180,
        "replicaRetryLimit":0, "registries":[{"server":"ibkrv2stgauhecwc4itaoo.azurecr.io", "identity":identity}],
        "secrets":secret_refs}
    config["scheduleTriggerConfig" if scheduled else "manualTriggerConfig"] = {
        "parallelism":1, "replicaCompletionCount":1,
        **({"cronExpression":"7,37 * * * *"} if scheduled else {})}
    return {"location":"eastasia", "identity":{"type":"UserAssigned", "userAssignedIdentities":{identity:{}}},
        "tags":{"application":"ibkr-portfolio-platform", "purpose":"metadata-shadow-monitor",
                "owner":"portfolio-platform", "mode":"shadow", "payloadSha256":digest},
        "properties":{"environmentId":BASE + "Microsoft.App/managedEnvironments/ibkrv2stg-env",
            "configuration":config, "template":{"containers":[{"name":"metadata-monitor", "image":IMAGE,
                "command":["python"], "args":args, "env":env, "resources":{"cpu":0.25, "memory":"0.5Gi"}}]}}}


def job_path(name):
    return BASE + "Microsoft.App/jobs/" + name


def verify_execution(name, execution, prefix):
    import re
    if not re.fullmatch(re.escape(name) + r"-[a-z0-9]{7,12}", execution or ""):
        raise ValueError("Expected exact execution name")
    result = request("GET", job_path(name) + "/executions/" + execution + "?api-version=2024-03-01")
    if result["properties"]["status"] != "Succeeded":
        raise RuntimeError("Execution has not succeeded")
    expected = definition(provision=(name == PROVISION))
    expected_container = expected["properties"]["template"]["containers"][0]
    executed = result["properties"].get("template", {}).get("containers", [])
    if len(executed) != 1 or any(executed[0].get(key) != expected_container[key] for key in ("image", "command", "args")):
        raise RuntimeError("Execution differs from the reviewed payload/configuration")
    # Azure replaces secretRef names in execution snapshots with its internal
    # cappjob alias. Verify literal values and the live job's real secret binding;
    # do not weaken image/command/payload checks or read resolved secret values.
    observed_env = executed[0].get("env", [])
    if len(observed_env) != len(expected_container["env"]):
        raise RuntimeError("Execution environment differs")
    for actual, wanted in zip(observed_env, expected_container["env"]):
        if actual.get("name") != wanted["name"] or (
            actual.get("secretRef") not in {wanted["secretRef"], "cappjob-" + name}
            if "secretRef" in wanted else actual.get("value") != wanted["value"]):
            raise RuntimeError("Execution environment differs")
    current = request("GET", job_path(name) + "?api-version=2024-03-01")
    live_container = current["properties"]["template"]["containers"][0]
    if any(live_container.get(key) != expected_container[key] for key in ("image", "command", "args", "env")):
        raise RuntimeError("Current job differs from reviewed configuration")
    live_secrets = current["properties"]["configuration"].get("secrets", [])
    def bindings(secrets_list):
        return sorted((s.get("name"), s.get("keyVaultUrl"), s.get("identity", "").lower()) for s in secrets_list)
    if bindings(live_secrets) != bindings(expected["properties"]["configuration"]["secrets"]) or {v.lower() for v in current["identity"]["userAssignedIdentities"]} != {v.lower() for v in expected["identity"]["userAssignedIdentities"]}:
        raise RuntimeError("Job identity or Key Vault binding differs")
    logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb", "--analytics-query",
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution +
        "' | where Log_s startswith '" + prefix + " ' | project Log_s")
    rows = [json.loads(row["Log_s"][len(prefix)+1:]) for row in logs]
    if prefix == "METADATA_PROVISION":
        if not rows or any(row.get("status") != "pass" for row in rows):
            raise RuntimeError("Provision verification evidence missing or failed")
    else:
        complete = [row for row in rows if row.get("kind") == "complete"]
        indexed = {}
        for row in rows:
            if "index" in row:
                if row["index"] in indexed and indexed[row["index"]] != row:
                    raise RuntimeError("Conflicting duplicate monitor record")
                indexed[row["index"]] = row
        if not complete or any(row != complete[0] for row in complete) or any(row.get("kind") == "error" for row in rows):
            raise RuntimeError("Incomplete or conflicting monitor evidence")
        if set(indexed) != set(range(complete[0]["records"])) or complete[0].get("access_check") != "passed":
            raise RuntimeError("Missing monitor rows or access checks")
    folder = ROOT / ".work/operational-observability-20261001"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (execution + ".json")).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps({"execution":execution, "verified_records":len(rows)}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare","provision","install","test","verify","schedule","cleanup-provisioner"])
    parser.add_argument("--execution")
    args = parser.parse_args()
    if az("account", "show", "--query", "id") != SUB:
        raise RuntimeError("Unexpected subscription")
    if args.stage == "prepare":
        # Fixed new names. Never overwrite a credential or an existing job.
        if request("GET", job_path(PROVISION) + "?api-version=2024-03-01", missing=True):
            raise RuntimeError("Provision job already exists; inspect before repeating prepare")
        names = az("keyvault", "secret", "list", "--vault-name", VAULT, "--query", "[].name")
        if SECRET in names:
            raise RuntimeError("Monitor secret already exists; do not rotate implicitly")
        identity_path = BASE + "Microsoft.ManagedIdentity/userAssignedIdentities/" + IDENTITY
        identity = request("PUT", identity_path + "?api-version=2023-01-31", data={"location":"eastasia", "tags":{"purpose":"metadata-shadow-monitor"}})
        password = secrets.token_urlsafe(48)
        value = f"postgresql://ibkr_metadata_monitor:{quote(password,safe='')}@ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com:5432/ibkr_staging?sslmode=require"
        request("PUT", f"https://{VAULT}.vault.azure.net/secrets/{SECRET}?api-version=7.4", vault=True,
                data={"value":value, "contentType":"application/x-postgresql-connection-string", "tags":{"purpose":"metadata-only-shadow-monitor"}})
        del password, value
        scopes = [(BASE + "Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo", "7f951dda-4ed3-4680-a7ca-43fe172d538d"),
                  (BASE + f"Microsoft.KeyVault/vaults/{VAULT}/secrets/{SECRET}", "4633458b-17de-408a-b874-0445c86b69e6")]
        for scope, role in scopes:
            assignment = str(uuid.uuid5(uuid.NAMESPACE_URL, identity_path + scope + role))
            request("PUT", scope + "/providers/Microsoft.Authorization/roleAssignments/" + assignment + "?api-version=2022-04-01",
                data={"properties":{"principalId":identity["properties"]["principalId"], "principalType":"ServicePrincipal",
                    "roleDefinitionId":f"/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{role}"}})
        request("PUT", job_path(PROVISION) + "?api-version=2024-03-01", data=definition(provision=True))
        print(json.dumps({"prepared":PROVISION, "secret_value_exposed":False}))
    elif args.stage in {"provision","test"}:
        name = PROVISION if args.stage == "provision" else JOB
        result = request("POST", job_path(name) + "/start?api-version=2024-03-01")
        print(json.dumps({"execution":result["name"]}))
    elif args.stage == "install":
        verify_execution(PROVISION, args.execution, "METADATA_PROVISION")
        if request("GET", job_path(JOB) + "?api-version=2024-03-01", missing=True):
            raise RuntimeError("Monitor already exists; refusing implicit replacement")
        request("PUT", job_path(JOB) + "?api-version=2024-03-01", data=definition())
        print(json.dumps({"installed":JOB, "trigger":"Manual"}))
    elif args.stage in {"verify","schedule"}:
        verify_execution(JOB, args.execution, "METADATA_MONITOR")
        if args.stage == "schedule":
            current = request("GET", job_path(JOB) + "?api-version=2024-03-01")
            if current.get("tags",{}).get("payloadSha256") != bundle()[1]:
                raise RuntimeError("Local payload differs from tested deployment")
            request("PUT", job_path(JOB) + "?api-version=2024-03-01", data=definition(scheduled=True))
            print(json.dumps({"scheduled":JOB, "cron_utc":"7,37 * * * *", "mode":"shadow", "retry":0}))
    elif args.stage == "cleanup-provisioner":
        verify_execution(JOB, args.execution, "METADATA_MONITOR")
        # Delete only our new, explicitly named temporary provisioning resource;
        # preserve its collected evidence and all existing bootstrap jobs.
        current = request("GET", job_path(PROVISION) + "?api-version=2024-03-01")
        if current.get("tags",{}).get("purpose") != "metadata-shadow-monitor" or current["properties"]["configuration"]["triggerType"] != "Manual":
            raise RuntimeError("Unexpected provisioning resource; refusing deletion")
        executions = request("GET", job_path(PROVISION) + "/executions?api-version=2024-03-01").get("value", [])
        if not executions or any(row["properties"]["status"] not in {"Succeeded", "Failed", "Stopped"} for row in executions):
            raise RuntimeError("Provisioning execution not terminal")
        request("DELETE", job_path(PROVISION) + "?api-version=2024-03-01")
        print(json.dumps({"removed_temporary_job":PROVISION, "preserved":"database login, monitor, secret, local evidence and Azure logs"}))


if __name__ == "__main__":
    main()
