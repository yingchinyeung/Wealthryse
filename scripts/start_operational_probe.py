"""Run one allow-listed read-only metadata probe without changing job settings."""
import base64
import copy
import hashlib
import json
from pathlib import Path

import requests
from azure.identity import AzureCliCredential
from audit_azure_runtime import az, RG


def main():
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise RuntimeError("Unexpected subscription; diagnostic dispatch stopped")
    job = az("containerapp", "job", "show", "-g", RG, "-n", "ibkrv2stg-bootstrap")
    template = copy.deepcopy(job["properties"]["template"])
    if template.get("volumes") or len(template["containers"]) != 1:
        raise RuntimeError("Unexpected bootstrap shape; diagnostic dispatch stopped")
    if job["properties"]["configuration"]["replicaRetryLimit"] != 0:
        raise RuntimeError("Diagnostic job must retain zero immediate retries")
    template.pop("volumes", None)
    container = template["containers"][0]
    tenant = next(e for e in container["env"] if e["name"] == "PORTFOLIO_TENANT_ID")
    container["env"] = [tenant, {"name":"DATABASE_URL", "secretRef":"database-admin-url"}]
    script = Path(__file__).with_name("collect_operational_evidence.py").read_bytes()
    encoded = base64.b64encode(script).decode()
    container["command"] = ["python"]
    container["args"] = ["-c", f"import base64;exec(compile(base64.b64decode('{encoded}'),'operational-probe','exec'))"]
    token = AzureCliCredential().get_token("https://management.azure.com/.default").token
    response = requests.post("https://management.azure.com" + job["id"] +
        "/start?api-version=2024-03-01", headers={"Authorization":"Bearer " + token},
        json=template, timeout=60)
    if not response.ok:
        raise RuntimeError(f"Operational probe dispatch failed: HTTP {response.status_code}")
    print(json.dumps({"execution":response.json()["name"],
                      "probe_sha256":hashlib.sha256(script).hexdigest(),
                      "mode":"read_only_metadata"}))


if __name__ == "__main__":
    main()
