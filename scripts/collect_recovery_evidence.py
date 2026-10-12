"""Persist secret-free recovery evidence, refusing incomplete or mismatched checks."""
import json
from pathlib import Path
from audit_azure_runtime import az


def main():
    logs = az("monitor", "log-analytics", "query", "-w", "5264a124-10cd-45f6-93a5-6d65c37e9bfb",
        "--analytics-query", "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith 'ibkrv2stg-bootstrap-osqqyo2' or ContainerGroupName_s startswith 'ibkrv2stg-bootstrap-xd8ub2k' or ContainerGroupName_s startswith 'ibkrv2stg-bootstrap-7qjzzex' | project Log_s")
    records, authorization = {}, None
    for row in logs:
        line = row["Log_s"]
        if line.startswith("RECOVERY_EVIDENCE "):
            value = json.loads(line.removeprefix("RECOVERY_EVIDENCE "))
            records[value["mode"]] = value
        elif line.startswith('{"status": "pass", "users":'):
            authorization = json.loads(line)
    source, restored = records["source"], records["restored"]
    for key in ("schema_sha256", "tables", "database", "latest_nav_date", "read_only"):
        if source[key] != restored[key]:
            raise RuntimeError("Recovery comparison failed: " + key)
    if source["read_only"] != "on" or len(source["tables"]) != 11:
        raise RuntimeError("Recovery evidence is incomplete")
    if not authorization or authorization["checks"] != 29 or authorization["clone_changes"] != "rolled_back":
        raise RuntimeError("Authorization integration evidence is incomplete")
    result = {"status": "pass", "restored_server": "ibkrv2stg-recovery-20261001",
        "restore_point": "2026-10-01T16:45:06Z", "database_evidence": records,
        "authorization_evidence": authorization,
        "limits": ["Table-scoped data parity, not a full application failover",
                   "Report database manifests checked; storage blob restore not exercised",
                   "Observed zero loss in checked tables is not a guaranteed zero RPO"]}
    folder = Path(__file__).resolve().parents[1] / ".work" / "recovery-20261001"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "verified-recovery.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"PASS: 11 table fingerprints, schema, latest NAV date; 29 authorization checks. Evidence: {path}")


if __name__ == "__main__":
    main()
