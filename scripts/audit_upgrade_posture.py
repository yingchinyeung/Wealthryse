"""Read-only cloud posture for the approved programme; no tokens/secret bodies."""
import concurrent.futures
import json
from datetime import datetime, timezone
from pathlib import Path

import requests
from azure.identity import AzureCliCredential

from audit_azure_runtime import az, RG

SUB = "3956e821-cee4-403f-8708-e84e2ed490eb"
TENANT = "0578a50b-a7a1-40a4-905b-8b20dba84124"
ROOT = Path(__file__).resolve().parents[1]
QUERIES = {
    "resources": ("resource", "list", "-g", RG, "--query", "[].{id:id,type:type,location:location,tags:tags}"),
    "role_assignments": ("role", "assignment", "list", "--all", "--include-inherited", "--query",
        "[].{principal:principalId,principalType:principalType,role:roleDefinitionName,scope:scope}"),
    "storage": ("storage", "account", "list", "-g", RG, "--query",
        "[].{name:name,publicNetworkAccess:publicNetworkAccess,allowSharedKeyAccess:allowSharedKeyAccess,allowBlobPublicAccess:allowBlobPublicAccess,minimumTlsVersion:minimumTlsVersion,networkRuleSet:networkRuleSet}"),
    "vaults": ("keyvault", "list", "-g", RG, "--query", "[].{name:name,id:id}"),
    "jobs": ("containerapp", "job", "list", "-g", RG, "--query",
        "[].{name:name,trigger:properties.configuration.triggerType,cron:properties.configuration.scheduleTriggerConfig.cronExpression,retry:properties.configuration.replicaRetryLimit}"),
    "web": ("containerapp", "show", "-g", RG, "-n", "ibkrv2stg-web", "--query",
        "{name:name,revision:properties.latestReadyRevisionName,minReplicas:properties.template.scale.minReplicas,maxReplicas:properties.template.scale.maxReplicas,containers:properties.template.containers[].{name:name,image:image,cpu:resources.cpu,memory:resources.memory}}"),
}


def graph_policy(path):
    try:
        token = AzureCliCredential().get_token("https://graph.microsoft.com/.default").token
        response = requests.get("https://graph.microsoft.com/v1.0/" + path,
            headers={"Authorization": "Bearer " + token}, timeout=45)
        if response.status_code != 200:
            return {"readable": False, "http_status": response.status_code}
        return {"readable": True, "data": response.json()}
    except Exception as error:
        return {"readable": False, "error_type": type(error).__name__}


def main():
    identity = az("account", "show", "--query", "{subscription:id,tenant:tenantId}")
    if identity != {"subscription": SUB, "tenant": TENANT}:
        raise RuntimeError("Unexpected subscription/tenant")
    result = {"captured_at": datetime.now(timezone.utc).isoformat(), "read_only": True}
    def collect(item):
        name, args = item
        try:
            data = az(*args)
            if name == "role_assignments":
                # --all and --scope are mutually exclusive in Azure CLI. Keep
                # only this RG/descendants and subscription-wide assignments.
                scope = f"/subscriptions/{SUB}/resourceGroups/{RG}".lower()
                data = [row for row in data if row["scope"].lower() in
                        {scope, f"/subscriptions/{SUB}".lower()}
                        or row["scope"].lower().startswith(scope + "/")]
            return name, {"readable": True, "data": data}
        except Exception as error:
            return name, {"readable": False, "error_type": type(error).__name__}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        result.update(dict(pool.map(collect, QUERIES.items())))
    vaults = result["vaults"].get("data", [])
    result["vault_networks"] = []
    for vault in vaults:
        _, record = collect((vault["name"], ("keyvault", "show", "--name", vault["name"], "-g", RG, "--query",
            "{name:name,rbac:properties.enableRbacAuthorization,publicNetworkAccess:properties.publicNetworkAccess,networkAcls:properties.networkAcls,purgeProtection:properties.enablePurgeProtection,softDeleteRetentionDays:properties.softDeleteRetentionInDays}")))
        result["vault_networks"].append({"name": vault["name"], **record})
    result["security_defaults"] = graph_policy("policies/identitySecurityDefaultsEnforcementPolicy")
    result["conditional_access"] = graph_policy("identity/conditionalAccess/policies?$select=id,displayName,state,conditions,grantControls,sessionControls")
    folder = ROOT / ".work" / "upgrade-posture-20261002"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(path), "read_only": True,
        "sections": {k: v.get("readable") for k,v in result.items() if isinstance(v, dict)},
        "resources": len(result["resources"].get("data", [])),
        "role_assignments": len(result["role_assignments"].get("data", [])),
        "security_defaults_http": result["security_defaults"].get("http_status"),
        "conditional_access_http": result["conditional_access"].get("http_status")}))


if __name__ == "__main__":
    main()
