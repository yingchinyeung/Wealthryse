"""Explicit, one-request synthetic Azure inference rehearsal. No Codex worker.

Uses the existing Azure CLI identity, not keys or credential changes. Emits
sanitized evidence only. Success is not production deployment acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audit_azure_runtime import az, RG
from ibkr_etl.codex_workspace import AzureCodexProvider
from ibkr_etl.codex_compatibility import probe_azure_responses, credential_diagnostics

ACCOUNT = "ibkrv2stg-ai-auhecwc4itaoo"
HOST = ACCOUNT + ".openai.azure.com"
DEPLOYMENT = "gpt-5-6-sol-apac"
SUBSCRIPTION = "3956e821-cee4-403f-8708-e84e2ed490eb"
TENANT = "0578a50b-a7a1-40a4-905b-8b20dba84124"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-synthetic-inference", action="store_true", required=True)
    parser.add_argument("--api-version", choices=("v1", "2025-04-01-preview"), required=True)
    parser.add_argument("--token-resource", choices=("https://ai.azure.com", "https://cognitiveservices.azure.com"), required=True)
    args = parser.parse_args()
    if not args.run_synthetic_inference:
        return 2
    record = {"checked_at": datetime.now(timezone.utc).isoformat(), "state": "blocked",
              "execution_authorized": False, "codex_runtime_verified": False,
              "token_resource": args.token_resource}
    phase = "azure_identity"
    try:
        identity = az("account", "show", "--query", "{subscription:id,tenant:tenantId}")
        if identity != {"subscription": SUBSCRIPTION, "tenant": TENANT}:
            raise ValueError("Identity scope mismatch")
        phase = "azure_inventory"
        account = az("cognitiveservices", "account", "show", "-g", RG, "-n", ACCOUNT,
                     "--query", "{endpoint:properties.endpoint,region:location}")
        if account != {"endpoint": f"https://{HOST}/", "region": "southeastasia"}:
            raise ValueError("Account scope mismatch")
        deployment = az("cognitiveservices", "account", "deployment", "show", "-g", RG,
                        "-n", ACCOUNT, "--deployment-name", DEPLOYMENT, "--query",
                        "{state:properties.provisioningState,model:properties.model,sku:sku}")
        model = deployment.get("model", {})
        if (deployment.get("state") != "Succeeded" or model.get("name") != "gpt-5.6-sol"
                or model.get("version") != "2026-07-09"
                or deployment.get("sku", {}).get("name") != "DataZoneStandard"):
            raise ValueError("Deployment changed; review required")
        record["inventory"] = {"account": ACCOUNT, "deployment": DEPLOYMENT,
                               "region": "southeastasia", "model": model, "sku": deployment["sku"]}
        provider = AzureCodexProvider(f"https://{HOST}/", DEPLOYMENT,
                                      args.api_version, (HOST,))
        phase = "azure_bearer_token"
        token = az("account", "get-access-token", "--resource", args.token_resource,
                   "--query", "accessToken")
        phase = "synthetic_inference"
        try:
            record["credential_diagnostics"] = credential_diagnostics(
                token, expected_audience=args.token_resource, expected_tenant=TENANT,
                now=datetime.now(timezone.utc))
            record.update(probe_azure_responses(provider, token))
        finally:
            token = None
    except (ValueError, RuntimeError):
        # CLI output and exception strings may contain sensitive details.
        record["reason"] = "preflight_failed"
        record["phase"] = phase
    directory = Path(__file__).resolve().parents[1] / ".work" / "codex-compatibility"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (str(uuid.uuid4()) + ".json")
    with target.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
    print(json.dumps({"state": record["state"], "reason": record.get("reason"),
                      "http_status": record.get("http_status"), "phase": record.get("phase"),
                      "error_hint": record.get("error_hint"),
                      "credential_diagnostics": record.get("credential_diagnostics"),
                      "evidence": str(target), "codex_runtime_verified": False}))
    return 0 if record["state"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
