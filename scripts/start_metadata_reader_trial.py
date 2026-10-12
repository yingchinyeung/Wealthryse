"""Start the allow-listed transactional permission rehearsal, not bootstrap."""
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
        raise RuntimeError("Unexpected subscription")
    root = Path(__file__).resolve().parents[1]
    sources = {
        "SOURCE_COLLECTOR":(root / "scripts/collect_operational_evidence.py").read_text(encoding="utf-8"),
        "SOURCE_ACCESS":(root / "src/ibkr_etl/metadata_monitor_access.py").read_text(encoding="utf-8"),
    }
    script = "\n".join(f"{key}={value!r}" for key,value in sources.items()) + "\n" + (
        root / "scripts/verify_metadata_reader.py").read_text(encoding="utf-8")
    job = az("containerapp", "job", "show", "-g", RG, "-n", "ibkrv2stg-bootstrap")
    template = copy.deepcopy(job["properties"]["template"])
    if template.get("volumes") or len(template["containers"]) != 1 or job["properties"]["configuration"]["replicaRetryLimit"] != 0:
        raise RuntimeError("Unexpected diagnostic job configuration")
    template.pop("volumes", None)
    container = template["containers"][0]
    tenant = next(e for e in container["env"] if e["name"] == "PORTFOLIO_TENANT_ID")
    container["env"] = [tenant,{"name":"DATABASE_URL","secretRef":"database-admin-url"}]
    container["command"] = ["python"]
    encoded = base64.b64encode(script.encode()).decode()
    container["args"] = ["-c", f"import base64;exec(compile(base64.b64decode('{encoded}'),'metadata-trial','exec'))"]
    token = AzureCliCredential().get_token("https://management.azure.com/.default").token
    response = requests.post("https://management.azure.com" + job["id"] + "/start?api-version=2024-03-01",
        headers={"Authorization":"Bearer " + token}, json=template, timeout=60)
    if not response.ok:
        raise RuntimeError(f"Trial dispatch HTTP {response.status_code}")
    print(json.dumps({"execution":response.json()["name"],"trial_sha256":hashlib.sha256(script.encode()).hexdigest()}))


if __name__ == "__main__":
    main()
