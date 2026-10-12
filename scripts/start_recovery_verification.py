"""Launch only the read-only recovery probe, preserving every existing env ref."""
import argparse
import base64
import copy
import json
from pathlib import Path
import requests
from azure.identity import AzureCliCredential

from audit_azure_runtime import az, RG


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restored", action="store_true")
    parser.add_argument("--authorization-test", action="store_true")
    args = parser.parse_args()
    if args.authorization_test and not args.restored:
        raise RuntimeError("Authorization mutation tests are restricted to the disposable restore")
    job = az("containerapp", "job", "show", "-g", RG, "-n", "ibkrv2stg-bootstrap")
    container = job["properties"]["template"]["containers"][0]
    template = copy.deepcopy(job["properties"]["template"])
    if template.get("volumes"):
        raise RuntimeError("A volume-dependent job is unsuitable for this isolated probe")
    template.pop("volumes", None)
    execution_container = template["containers"][0]
    # Admin schema visibility is needed, but PostgreSQL enforces read-only mode.
    # Do not inject any of the bootstrap's unrelated role/password variables.
    tenant = next(entry for entry in container["env"] if entry["name"] == "PORTFOLIO_TENANT_ID")
    execution_container["env"] = [tenant, {"name": "DATABASE_URL", "secretRef": "database-admin-url"}]
    if args.restored:
        execution_container["env"].append({"name": "RECOVERY_HOST", "value":
            "ibkrv2stg-recovery-20261001.postgres.database.azure.com"})
    script_name = "verify_restored_authorization.py" if args.authorization_test else "verify_database_recovery.py"
    script = Path(__file__).with_name(script_name).read_bytes()
    encoded = base64.b64encode(script).decode()
    code = f"import base64;exec(compile(base64.b64decode('{encoded}'),'recovery-probe','exec'))"
    execution_container["command"] = ["python"]
    execution_container["args"] = ["-c", code]
    token = AzureCliCredential().get_token("https://management.azure.com/.default").token
    response = requests.post("https://management.azure.com" + job["id"] +
        "/start?api-version=2024-03-01", headers={"Authorization": "Bearer " + token},
        json=template, timeout=90)
    if not response.ok:
        body = response.json()
        error = body.get("error", body)
        raise RuntimeError(f"Read-only probe dispatch failed with HTTP {response.status_code}: {str(body)[:1200]}")
    result = response.json()
    print(json.dumps({"execution": result["name"], "mode": "restored" if args.restored else "source"}))


if __name__ == "__main__":
    main()
