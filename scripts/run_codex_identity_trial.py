"""Synthetic inference on an existing manual diagnostic identity, no job PUT.

Never dispatch the normal application command or pass database/Flex secrets to
the diagnostic process. Job success alone is not inference acceptance.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import re

from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request

ROOT = Path(__file__).resolve().parents[1]
JOB = "ibkrv2stg-institutional-shadow"
IMAGE = "ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:2b0bd37cc80b93b96d40d2d46a6cc13686c6a3afc4a1707269ec772e33f86c39"
CLIENT = "9475b7e9-94e3-4b91-abbf-3f0adc25a315"
PRINCIPAL = "d51e0934-de33-47fa-9507-b68b0a6132fa"
PREFIX = "CODEX_IDENTITY_TRIAL "


def trial_template(job: dict, source: str) -> dict:
    identities = list(job.get("identity", {}).get("userAssignedIdentities", {}).values())
    configuration = job["properties"]["configuration"]
    template = copy.deepcopy(job["properties"]["template"])
    if (identities != [{"clientId": CLIENT, "principalId": PRINCIPAL}]
            or job.get("identity", {}).get("type") != "UserAssigned"
            or configuration.get("triggerType") != "Manual"
            or configuration.get("replicaRetryLimit") != 0
            or configuration.get("manualTriggerConfig", {}).get("replicaCompletionCount") != 1
            or configuration.get("manualTriggerConfig", {}).get("parallelism") != 1
            or template.get("volumes") or len(template.get("containers", [])) != 1):
        raise ValueError("Diagnostic scope/configuration changed")
    container = template["containers"][0]
    if container["image"] != IMAGE or container.get("volumeMounts"):
        raise ValueError("Diagnostic image or mounts changed")
    # Build a minimal template; do not inherit commands, probes, volumes or secrets.
    encoded = base64.b64encode(source.encode()).decode()
    return {"containers": [{"name": container["name"], "image": IMAGE,
                            "resources": container["resources"],
                            "env": [{"name": "AZURE_CLIENT_ID", "value": CLIENT}],
                            "command": ["python"],
                            "args": ["-c", "import base64;exec(compile(base64.b64decode('" + encoded + "'),'codex-identity-trial','exec'))"]}]}


def source_code() -> str:
    source = "import sys,types,json,os\n"
    source += "package=types.ModuleType('trial');package.__path__=[];sys.modules['trial']=package\n"
    for name in ("codex_workspace", "codex_compatibility"):
        code = (ROOT / "src/ibkr_etl" / (name + ".py")).read_text(encoding="utf-8")
        source += ("m=types.ModuleType('trial." + name + "');m.__package__='trial';sys.modules[m.__name__]=m\n"
                   "exec(compile(" + repr(code) + ",'trial-module','exec'),m.__dict__)\n")
    source += '''
from azure.identity import ManagedIdentityCredential
from trial.codex_workspace import AzureCodexProvider
from trial.codex_compatibility import probe_azure_responses, credential_diagnostics
from datetime import datetime, timezone
result = {"state":"blocked","reason":"managed_identity_unavailable","execution_authorized":False,"codex_runtime_verified":False}
credential = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
try:
    token = credential.get_token("https://ai.azure.com/.default").token
    provider = AzureCodexProvider("https://ibkrv2stg-ai-auhecwc4itaoo.openai.azure.com/", "gpt-5-6-sol-apac", "v1", ("ibkrv2stg-ai-auhecwc4itaoo.openai.azure.com",))
    metadata = credential_diagnostics(token, expected_audience="https://ai.azure.com", expected_tenant="0578a50b-a7a1-40a4-905b-8b20dba84124", now=datetime.now(timezone.utc))
    result = probe_azure_responses(provider, token)
    result["credential_diagnostics"] = metadata
    result["authentication"] = "existing_diagnostics_managed_identity"
except Exception:
    pass
finally:
    credential.close()
    token = None
print("CODEX_IDENTITY_TRIAL " + json.dumps(result), flush=True)
'''
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "read"))
    parser.add_argument("--execution")
    args = parser.parse_args()
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise ValueError("Unexpected subscription")
    folder = ROOT / ".work/codex-managed-identity"
    folder.mkdir(parents=True, exist_ok=True)
    job = az("containerapp", "job", "show", "-g", RG, "-n", JOB)
    settings_hash = hashlib.sha256(json.dumps({"identity": job.get("identity"),
        "configuration": job["properties"]["configuration"],
        "template": job["properties"]["template"]}, sort_keys=True).encode()).hexdigest()
    if args.action == "start":
        active = az("containerapp", "job", "execution", "list", "-g", RG, "-n", JOB,
                    "--query", "[?properties.status=='Running' || properties.status=='Processing'].name")
        if active:
            raise ValueError("Diagnostic job already busy")
        source = source_code()
        template = trial_template(job, source)
        result = request("POST", job["id"] + "/start?api-version=2024-03-01", data=template)
        result = {"execution": result["name"], "mode": "synthetic_inference_only",
                  "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                  "job_settings_sha256": settings_hash, "image": IMAGE}
        suffix = "-dispatch"
    else:
        if not re.fullmatch(re.escape(JOB) + r"-[a-z0-9]{7}", args.execution or ""):
            raise ValueError("Exact execution required")
        dispatch = json.loads((folder / (args.execution + "-dispatch.json")).read_text())
        state = az("containerapp", "job", "execution", "show", "-g", RG, "-n", JOB,
                   "--job-execution-name", args.execution)
        logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb",
                  "--analytics-query", "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" +
                  args.execution + "' | where Log_s startswith '" + PREFIX + "' | project Log_s")
        result = {"execution": args.execution, "job_status": state["properties"]["status"],
                  "job_settings_unchanged": dispatch["job_settings_sha256"] == settings_hash,
                  "records": [json.loads(row["Log_s"].removeprefix(PREFIX)) for row in logs]}
        suffix = ""
    (folder / (result["execution"] + suffix + ".json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
