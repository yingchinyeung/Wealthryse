"""Run a risk-only candidate with the full existing execution environment.

Azure CLI overrides can silently omit env values. Copy the ARM template and
replace only the image and deterministic risk command; never use Flex here.
"""
import argparse
import copy
import json
import re
import requests
from azure.identity import AzureCliCredential
from audit_azure_runtime import az, RG


def candidate_template(template, digest, repository="ibkr-platform"):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("An immutable candidate digest is required")
    if repository not in {"ibkr-platform", "ibkr-risk-worker"}:
        raise ValueError("Unapproved risk image repository")
    result = copy.deepcopy(template)
    if result.get("volumes") or len(result["containers"]) != 1:
        raise ValueError("Unexpected risk execution template")
    result.pop("volumes", None)
    container = result["containers"][0]
    if not any(item["name"] == "DATABASE_URL" and item.get("secretRef") for item in container["env"]):
        raise ValueError("Existing database secret reference is required")
    container["image"] = "ibkrv2stgauhecwc4itaoo.azurecr.io/" + repository + "@" + digest
    container["command"] = ["python"]
    container["args"] = ["scripts/build_risk_snapshots.py"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("digest")
    parser.add_argument("--repository", choices=["ibkr-platform", "ibkr-risk-worker"], default="ibkr-platform")
    args = parser.parse_args()
    job = az("containerapp", "job", "show", "-g", RG, "-n", "ibkrv2stg-risk-snapshots")
    if az("account", "show", "--query", "id") != "3956e821-cee4-403f-8708-e84e2ed490eb":
        raise RuntimeError("Unexpected subscription")
    template = candidate_template(job["properties"]["template"], args.digest, args.repository)
    token = AzureCliCredential().get_token("https://management.azure.com/.default").token
    response = requests.post("https://management.azure.com" + job["id"] + "/start?api-version=2024-03-01",
        headers={"Authorization": "Bearer " + token}, json=template, timeout=90)
    if not response.ok:
        raise RuntimeError(f"Candidate launch rejected: HTTP {response.status_code}")
    print(json.dumps({"execution": response.json()["name"], "image_digest": args.digest}), flush=True)


if __name__ == "__main__":
    main()
