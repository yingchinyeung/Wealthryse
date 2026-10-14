"""Create the ibkr_live_collector login for the gateway VM's collector.

One-time and additive: it refuses to touch an existing role. The collector's
connection URL (Key Vault secret database-live-url) supplies the password, so
the password is never typed or printed. Run it with the admin connection after
migration 063, from the private admin VM like the other role scripts.

Environment: DATABASE_ADMIN_URL, LIVE_DATABASE_URL.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ibkr_etl import live_collector_access as access  # noqa: E402

EXPECTED_HOST = "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"
EXPECTED_DATABASE = "ibkr_staging"


def main() -> int:
    import psycopg

    target = urlsplit(os.environ["LIVE_DATABASE_URL"])
    admin_url = os.environ["DATABASE_ADMIN_URL"]
    if (
        target.username != access.ROLE
        or target.hostname != EXPECTED_HOST
        or target.path != "/" + EXPECTED_DATABASE
        or urlsplit(admin_url).hostname != EXPECTED_HOST
    ):
        raise RuntimeError("Unexpected live collector database target")
    with psycopg.connect(admin_url, connect_timeout=20,
                         options="-c statement_timeout=60000 -c lock_timeout=5000") as connection:
        access.provision(connection, password=unquote(target.password or ""),
                         database=EXPECTED_DATABASE)
        problems = access.least_privilege_problems(access.privilege_report(connection))
        if problems:
            raise RuntimeError("Live collector grants are not least-privilege: " + "; ".join(problems))
    with psycopg.connect(os.environ["LIVE_DATABASE_URL"], connect_timeout=20) as connection:
        connection.execute("SELECT count(*) FROM live_gateway_status").fetchone()
    print("LIVE_COLLECTOR_PROVISION " + json.dumps(
        {"status": "pass", "role": access.ROLE, "tables": len(access.LIVE_TABLES)}), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("LIVE_COLLECTOR_PROVISION " + json.dumps(
            {"status": "fail", "error_type": type(error).__name__}), flush=True)
        raise SystemExit(1)
