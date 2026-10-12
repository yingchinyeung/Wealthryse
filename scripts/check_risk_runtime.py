"""Offline non-root risk-image smoke check. No credentials or network needed."""
import hashlib
import json
import os
from pathlib import Path
from ibkr_etl.risk_snapshots import cache_reuse_status, RISK_ANALYTICS_VERSION, risk_input_fingerprint
from ibkr_etl.benchmark_analytics import CALCULATION_VERSION
from ibkr_etl.util import canonical_json


def main():
    if os.getuid() == 0:
        raise RuntimeError("Risk image must run non-root")
    import psycopg
    from zoneinfo import ZoneInfo
    ZoneInfo("Asia/Hong_Kong")
    if Path("scripts/node_modules").exists() or Path("templates").exists() or Path("private-inputs").exists():
        raise RuntimeError("Risk image must not contain report runtime or private documents")
    inputs = {"subject_id":"smoke", "subject_type":"physical_account", "as_of":"2026-01-02",
              "benchmark_id":"test", "rows":[], "positions":[], "risk_free":None, "crisis_replay":None}
    source_hash = risk_input_fingerprint(inputs)
    payload = {"account_id":"smoke", "as_of":"2026-01-02", "benchmark_id":"test",
               "analytics_version":RISK_ANALYTICS_VERSION, "quality_status":"warn", "metrics":{}}
    record = {"quality_status":"warn", "comparison_version":CALCULATION_VERSION,
              "payload_json":payload, "payload_sha256":hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
              "source_data_sha256":source_hash}
    assert cache_reuse_status(record, inputs=inputs, source_sha256=source_hash) == "current"
    record["payload_json"] = "{broken"
    assert cache_reuse_status(record, inputs=inputs, source_sha256=source_hash) == "invalid_payload"
    record["payload_json"] = {**payload, "account_id":"wrong"}
    assert cache_reuse_status(record, inputs=inputs, source_sha256=source_hash) == "payload_identity_mismatch"
    manifest = json.loads(Path("SOURCE_MANIFEST.json").read_text())
    for entry in manifest["files"]:
        if entry["path"] == "Dockerfile.risk":
            continue
        path = Path(entry["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("Risk source manifest mismatch")
    print(json.dumps({"risk_runtime":"pass", "non_root":True, "renderer_included":False,
                      "source_tree_sha256":manifest["tree_sha256"]}), flush=True)


if __name__ == "__main__":
    main()
