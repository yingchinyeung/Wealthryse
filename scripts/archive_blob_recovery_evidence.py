"""Validate completed drill telemetry and archive evidence, never approve reports.

This validates the drill's evidence, not independent financial correctness or
full portal failover. Original files and all retained drill versions are untouched.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

from audit_azure_runtime import az
from publish_source_archive import publish_file, SUBSCRIPTION, TENANT

ROOT = Path(__file__).resolve().parents[1]
DB_TENANT = "00000000-0000-0000-0000-000000000001"


def validate(result, dispatch):
    if (result.get("status") != "Succeeded" or result.get("job_settings_unchanged") is not True
            or result.get("execution") != dispatch.get("execution")):
        raise ValueError("Completed unchanged diagnostic execution required")
    records = result["records"]
    run = dispatch["run_id"]
    if any(r.get("run_id") != run for r in records):
        raise ValueError("Mixed drill evidence")
    if any(r.get("kind") == "error" for r in records):
        raise ValueError("Drill error present")
    complete = [r for r in records if r.get("kind") == "complete"]
    inventory = [r for r in records if r.get("kind") == "inventory"]
    assets = [r for r in records if r.get("recovered") is True]
    if len(complete) != 1 or len(inventory) != 1:
        raise ValueError("Unique inventory and completion required")
    summary = complete[0]
    count = summary.get("verified_assets")
    if (not isinstance(count, int) or not 1 <= count <= 256 or len(assets) != count
            or summary.get("passed") is not True or summary.get("database_read_only") is not True
            or summary.get("originals_modified") is not False
            or inventory[0].get("registry_rows") != summary.get("registry_rows")
            or summary["registry_rows"] < count):
        raise ValueError("Incomplete recovery coverage")
    prefix = f"{DB_TENANT}/recovery-drills/{run}/"
    if summary.get("drill_prefix") != prefix or sorted(r["index"] for r in assets) != list(range(count)):
        raise ValueError("Unexpected scratch scope or asset indices")
    for r in assets:
        digest = r.get("artifact_sha256", "")
        if (not re.fullmatch(r"[0-9a-f]{64}", digest)
                or r.get("kind") not in {"asset", "current_template", "released_report"}
                or r.get("original_unchanged") is not True or not r.get("source_version")
                or not 0 < r.get("bytes", 0) <= 52_428_800
                or r.get("drill_blob") != prefix + f"{r['index']:03d}-{digest}.recovery"
                or len(r.get("versions", [])) != 3 or len(set(r["versions"])) != 3
                or not all(isinstance(v, str) and v for v in r["versions"])):
            raise ValueError("Invalid version recovery evidence")
    if sum(r["bytes"] for r in assets) != summary.get("verified_bytes"):
        raise ValueError("Recovery byte count mismatch")
    if len(records) != len(assets) + 2:
        raise ValueError("Unrecognized drill evidence")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("execution")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"ibkrv2stg-bootstrap-[a-z0-9]{7}", args.execution):
        raise ValueError("Exact execution required")
    folder = ROOT / ".work/report-blob-recovery-20261002"
    result_path = folder / (args.execution + ".json")
    dispatch_path = folder / (args.execution + "-dispatch.json")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
    summary = validate(result, dispatch)
    source = ROOT / "scripts/verify_report_blob_recovery.py"
    if hashlib.sha256(source.read_bytes()).hexdigest() != dispatch["source_sha256"]:
        raise ValueError("Keep exact executed probe source for archival")
    receipt = {"schema": "wealthryse-blob-recovery-evidence-v1", "summary": summary,
               "execution": args.execution, "financial_acceptance": False,
               "portal_failover_tested": False, "deployment_authorized": False}
    if args.publish:
        if az("account", "show", "--query", "{id:id,tenantId:tenantId}") != {"id": SUBSCRIPTION, "tenantId": TENANT}:
            raise ValueError("Unexpected Azure identity context")
        receipt["artifacts"] = []
        for path in (dispatch_path, result_path, source):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            binding = publish_file("release-evidence", f"recovery/{dispatch['run_id']}/{digest}/{path.name}", path, digest)
            receipt["artifacts"].append(binding)
    (folder / (args.execution + "-verified.json")).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
