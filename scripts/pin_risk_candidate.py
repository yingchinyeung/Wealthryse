"""Promote only the two-live-test-verified risk image, preserving job config."""
import json
from pathlib import Path
from audit_azure_runtime import az, RG
from release_verified_image import invariant

DIGEST = "sha256:a3f878df32c6b708448b4e67c2b2666bad8b1fc025dd265710ab1bfb1d4d7463"
JOB = "ibkrv2stg-risk-snapshots"


def main():
    for execution in ("ibkrv2stg-risk-snapshots-xkw5vcr", "ibkrv2stg-risk-snapshots-l4oh2s0"):
        state = az("containerapp", "job", "execution", "show", "-g", RG, "-n", JOB,
                   "--job-execution-name", execution, "--query", "properties.status")
        if state != "Succeeded":
            raise RuntimeError("Both candidate verification executions must succeed")
    before = az("containerapp", "job", "show", "-g", RG, "-n", JOB)
    image = "ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@" + DIGEST
    record = {"job": JOB, "previous_image": before["properties"]["template"]["containers"][0]["image"],
              "new_image": image, "configuration_sha256": invariant(before, "job"), "status": "planned"}
    path = Path(__file__).resolve().parents[1] / ".work" / "recovery-20261001" / "risk-release.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    az("containerapp", "job", "update", "-g", RG, "-n", JOB, "--container-name", "risk-snapshots", "--image", image)
    after = az("containerapp", "job", "show", "-g", RG, "-n", JOB)
    if invariant(after, "job") != record["configuration_sha256"]:
        raise RuntimeError("Unexpected risk configuration change; inspect saved baseline")
    if after["properties"]["template"]["containers"][0]["image"] != image:
        raise RuntimeError("Risk image pin not verified")
    record["status"] = "verified"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print("PASS: risk image pinned; schedule, zero retries, environment and capacity preserved")


if __name__ == "__main__":
    main()
