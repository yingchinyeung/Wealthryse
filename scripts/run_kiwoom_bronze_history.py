"""Ask Kiwoom again for the past days silver holds, into bronze only (ibkr_etl.kiwoom_bronze_history).

    python scripts/run_kiwoom_bronze_history.py [--market KRW|USD] [--limit 250] [--minutes 40] [--no-check]

Run once by hand in the institutional daily job's place (its login, its Key Vault access and its fixed
address for Kiwoom), never during the daily run, and never 16:00-18:00 or 19:00-22:00 UTC, when Kiwoom
rebuilds its daily records and ka01690 answers with empty figures (the Korea account then fails, and is
asked again on the next run). Needs KIWOOM_BRONZE=true and the collector switched on.
Silver is never written: the account reads go through a read-only session and only the bronze recorder
writes, one kiwoom_rest_bronze run per account. Afterwards each run is checked with
kiwoom_conform.check_run (read-only, rolled back) unless --no-check.

Prints one JSON line of counts: never a figure, account number, request key or body. Exits 0 when every
account it asked for was stored, 1 otherwise, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for folder in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402
from ibkr_etl.kiwoom_bronze import KiwoomBronze  # noqa: E402
from ibkr_etl.kiwoom_bronze_history import run  # noqa: E402
from ibkr_etl.kiwoom_client import KiwoomClient  # noqa: E402
from ibkr_etl.kiwoom_collector import Target, collector_enabled  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ask Kiwoom again for silver's past days, into bronze only.")
    parser.add_argument("--market", choices=("KRW", "USD"), help="one market only (default: both)")
    parser.add_argument("--limit", type=int, default=250, help="past days per account (default 250)")
    parser.add_argument("--minutes", type=float, default=40.0, help="stop asking after this long (default 40)")
    parser.add_argument("--no-check", action="store_true", help="skip the read-only check of each run")
    return parser


def _say(document: dict) -> None:
    print(json.dumps(document, default=str), flush=True)


def _check(database_url: str, tenant_id: str, run_ids: list[int]) -> dict[int, dict]:
    from ibkr_etl.kiwoom_conform import check_run

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        connection.execute("SET TRANSACTION READ ONLY")
        return {run_id: check_run(connection, run_id) for run_id in run_ids}
    finally:
        connection.rollback()
        connection.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.limit < 1 or args.minutes <= 0:
        _say({"status": "error", "error": "--limit and --minutes must be positive"})
        return 2
    if os.environ.get("KIWOOM_BRONZE", "").strip().lower() != "true" or not collector_enabled():
        _say({"status": "error", "error": "needs KIWOOM_BRONZE=true and the Kiwoom collector switched on"})
        return 2
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        _say({"status": "error", "error": "restricted to internal production"})
        return 2
    from run_kiwoom_collector import _required, read_secret

    try:
        database_url = _required("DATABASE_URL")
        vault_uri, client_id = _required("FLEX_TOKEN_VAULT_URI"), _required("AZURE_CLIENT_ID")
    except RuntimeError as error:
        _say({"status": "error", "error": str(error)})
        return 2
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    include_us = os.environ.get("KIWOOM_INCLUDE_US", "true").strip().lower() != "false"

    def client_for(target: Target) -> KiwoomClient:
        keys = read_secret(vault_uri, target.secret_name, client_id)
        return KiwoomClient(environment=target.environment, app_key=keys["app_key"], app_secret=keys["app_secret"])

    try:
        connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    except Exception as error:  # noqa: BLE001 - its text could name the server
        _say({"status": "error", "error": type(error).__name__})
        return 1
    bronze = KiwoomBronze(lambda: connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id), tenant_id)
    try:
        # Every read of the accounts from here on is read-only: silver cannot be written by this process.
        connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        connection.commit()
        results = run(connection, tenant_id, client_for=client_for, bronze=bronze,
                      now=lambda: datetime.now(timezone.utc), limit=args.limit, minutes=args.minutes,
                      include_us=include_us, markets=(args.market,) if args.market else ("KRW", "USD"))
    except Exception as error:  # noqa: BLE001 - only the type
        _say({"status": "error", "error": type(error).__name__, "bronze": bronze.summary()})
        return 1
    finally:
        connection.close()
        bronze.close()
    stored = [item["run_id"] for item in results if item.get("status") == "asked" and item.get("run_id")]
    if stored and not args.no_check:
        try:
            checked = _check(database_url, tenant_id, stored)
        except Exception as error:  # noqa: BLE001 - only the type
            checked = {}
            for item in results:
                item.setdefault("check_error", type(error).__name__)
        for item in results:
            if item.get("run_id") in checked:
                item["check"] = {key: value for key, value in checked[item["run_id"]].items() if key != "run_id"}
    _say({"status": "asked", "mode": "bronze-only", "accounts": results, "bronze": bronze.summary()})
    return 0 if all(item.get("status") != "failed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
