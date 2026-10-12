"""Delete ONLY the verified temporary restore; source servers are never accepted."""
import argparse
import json
from pathlib import Path
from audit_azure_runtime import az, RG

NAME = "ibkrv2stg-recovery-20261001"
EXPECTED_ID = "/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/Microsoft.DBforPostgreSQL/flexibleServers/" + NAME


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parents[1] / ".work" / "recovery-20261001"
    evidence = json.loads((folder / "verified-recovery.json").read_text(encoding="utf-8"))
    if evidence["status"] != "pass" or evidence["restored_server"] != NAME:
        raise RuntimeError("Verified restore evidence is required before cleanup")
    server = az("postgres", "flexible-server", "show", "-g", RG, "-n", NAME)
    if server["id"].lower() != EXPECTED_ID.lower() or server["network"]["publicNetworkAccess"] != "Disabled":
        raise RuntimeError("Temporary server identity/network does not match reviewed target")
    if args.apply:
        az("postgres", "flexible-server", "delete", "-g", RG, "-n", NAME, "--yes")
        remaining = az("postgres", "flexible-server", "list", "-g", RG, "--query", "[].name")
        if NAME in remaining or not {"ibkrv2stg-pg-auhecwc4itaoo", "ibkrv11-pg-5rcc62xxqblay"}.issubset(remaining):
            raise RuntimeError("Cleanup resource inventory requires review")
        (folder / "cleanup.json").write_text(json.dumps({"removed": NAME, "production_servers_preserved": True}), encoding="utf-8")
    print("Verified temporary restore " + ("removed" if args.apply else "eligible for cleanup"))


if __name__ == "__main__":
    main()
