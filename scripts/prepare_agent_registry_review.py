"""Prepare a metadata-only review from captured Azure inventory; no Azure calls."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ibkr_etl.agent_governance import planning_inventory

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    source = args.capture.resolve(strict=True)
    if not source.is_relative_to((ROOT / ".work/upgrade-posture-20261002").resolve()):
        raise ValueError("Expected captured upgrade inventory")
    raw = source.read_bytes()
    result = planning_inventory(json.loads(raw), now=datetime.now(timezone.utc),
        subscription_id="3956e821-cee4-403f-8708-e84e2ed490eb", resource_group="rg-ibkr-database-v11")
    result["source_sha256"] = hashlib.sha256(raw).hexdigest()
    payload = json.dumps(result, sort_keys=True, indent=2).encode()
    digest = hashlib.sha256(payload).hexdigest()
    folder = ROOT / ".work/agentic-phase1"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (digest + ".json")
    if target.exists():
        if target.read_bytes() != payload:
            raise ValueError("Refusing to replace existing review")
    else:
        with target.open("xb") as output:
            output.write(payload)
    print(json.dumps({"review": str(target), "sha256": digest, "agents": len(result["agents"]),
        "resources": len(result["resources"]), "unreviewed_resources": len(result["resources"]),
        "runtime_deployed": False, "execution_authorized": False}))


if __name__ == "__main__":
    main()
