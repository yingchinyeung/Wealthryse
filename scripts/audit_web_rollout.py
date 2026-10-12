"""Inspect safe rollout differences without printing environment/secret values."""
import json
import requests
from azure.identity import AzureCliCredential
from audit_azure_runtime import az, RG


def differences(before, after, path=""):
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            differences(before.get(key), after.get(key), path + "." + key)
    elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for index, (left, right) in enumerate(zip(before, after)):
            differences(left, right, f"{path}[{index}]")
    elif before != after:
        sensitive = any(word in path.lower() for word in ("env", "secret", "password", "token"))
        print(path, "[values withheld]" if sensitive else json.dumps({"before": before, "after": after}))


old = az("containerapp", "revision", "show", "-g", RG, "-n", "ibkrv2stg-web",
         "--revision", "ibkrv2stg-web--0000109")
new = az("containerapp", "revision", "show", "-g", RG, "-n", "ibkrv2stg-web",
         "--revision", "ibkrv2stg-web--0000110")
differences(old["properties"]["template"], new["properties"]["template"], "template")
body = {"subscriptions": ["3956e821-cee4-403f-8708-e84e2ed490eb"], "query":
        "resourcechanges | where properties.targetResourceId endswith '/containerApps/ibkrv2stg-web' "
        "| project timestamp=todatetime(properties.changeAttributes.timestamp), changes=properties.changes "
        "| order by timestamp desc | take 1"}
credential = AzureCliCredential()
token = credential.get_token("https://management.azure.com/.default")
response = requests.post(
    "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01",
    json=body, headers={"Authorization": "Bearer " + token.token}, timeout=60)
if response.status_code != 200:
    print("Resource Graph read failed", response.status_code, response.text[:700])
    raise SystemExit(1)
result = response.json()
for row in result.get("data", []):
    print("Azure change timestamp", row["timestamp"])
    for key, change in row.get("changes", {}).items():
        print(key, "[values withheld]" if any(x in key.lower() for x in ("env", "secret", "password", "token")) else change)
