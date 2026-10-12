"""Pin audited workloads without changing schedules, resources or permissions.

Requires an immutable digest. Saves a secret-free before/after rollback record.
Run tests and the report-runtime image build before invoking --apply.
"""
import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from audit_azure_runtime import az, RG

TARGETS = (
    ("job", "ibkrv2stg-onboard-render"),
    ("job", "ibkrv2stg-month-end-factsheet"),
    ("job", "ibkrv2stg-analyst-assurance"),
    ("job", "ibkrv2stg-burn-in-monitor"),
    ("app", "ibkrv2stg-web"),
)


def invariant(resource, kind):
    properties = resource["properties"]
    template = copy.deepcopy(properties["template"])
    template.pop("revisionSuffix", None)
    for container in template["containers"]:
        container.pop("image", None)
    configuration = copy.deepcopy(properties["configuration"])
    # Azure updates traffic revision pointers during a normal web rollout.
    if kind == "app":
        configuration.get("ingress", {}).pop("traffic", None)
    value = {"identity": resource.get("identity"), "template": template, "configuration": configuration}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("digest")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.digest):
        raise RuntimeError("Only immutable image digests are accepted")
    image = "ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@" + args.digest
    folder = Path(__file__).resolve().parents[1] / ".work" / "system-audit-20261001"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record = folder / f"release-{stamp}.json"
    results = []
    for kind, name in TARGETS:
        prefix = ["containerapp"] + (["job"] if kind == "job" else [])
        before = az(*prefix, "show", "-g", RG, "-n", name)
        containers = before["properties"]["template"]["containers"]
        if len(containers) != 1:
            raise RuntimeError("Unexpected multi-container workload: " + name)
        item = {"name": name, "kind": kind, "previous_image": containers[0]["image"],
                "new_image": image, "configuration_sha256": invariant(before, kind), "status": "planned"}
        results.append(item)
        record.write_text(json.dumps(results, indent=2), encoding="utf-8")
        if args.apply:
            az(*prefix, "update", "-g", RG, "-n", name,
               "--container-name", containers[0]["name"], "--image", image)
            after = az(*prefix, "show", "-g", RG, "-n", name)
            if invariant(after, kind) != item["configuration_sha256"]:
                raise RuntimeError("Non-image configuration changed; inspect before proceeding: " + name)
            if after["properties"]["template"]["containers"][0]["image"] != image:
                raise RuntimeError("Image pin was not applied: " + name)
            item["status"] = "verified"
            item["revision"] = after["properties"].get("latestRevisionName")
            record.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(name, item["status"], flush=True)
    print("Secret-free rollback record:", record)


if __name__ == "__main__":
    main()
