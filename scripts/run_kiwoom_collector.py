"""Daily Kiwoom collection, run by the institutional daily job after the Flex worker.

Does nothing unless KIWOOM_COLLECTOR_ENABLED=true. Each Kiwoom connection's
app key and secret are read from Key Vault for this run only and never logged;
see ibkr_etl.kiwoom_collector for what is loaded and which checks are recorded.
Prints one JSON summary with no secrets. One account's failure is recorded on
its connection and never stops the others; any other failure is printed in
the summary, and the script still exits 0 so it never fails the daily job.

With KIWOOM_BRONZE=true every inquiry page is stored in bronze.kiwoom_responses
first (ibkr_etl.kiwoom_bronze). With KIWOOM_BRONZE_REQUIRED=true as well, a page
that cannot be stored fails its account before silver changes, and after the run
each loaded account's bronze run is rebuilt and compared with silver
(kiwoom_conform.check_run, read-only), printed as one more line of counts.
KIWOOM_BRONZE_REQUIRED=true without KIWOOM_BRONZE=true collects nothing.

With KIWOOM_SILVER_FROM_BRONZE=true as well, silver is written from bronze: each
page is read back as bronze stored it and the collector computes silver from that
copy, never from the answer it received (ibkr_etl.kiwoom_bronze). It needs both
switches above; without them it collects nothing.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import lineage  # noqa: E402
from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402
from ibkr_etl.kiwoom_bronze import KiwoomBronze, enabled as bronze_enabled, required as bronze_required  # noqa: E402
from ibkr_etl.kiwoom_bronze import silver_from_bronze  # noqa: E402
from ibkr_etl.kiwoom_client import KiwoomClient  # noqa: E402
from ibkr_etl.kiwoom_collector import SECRET_PREFIX, Target, collector_enabled, run, snapshot_day  # noqa: E402

VAULT_SCOPE = "https://vault.azure.net/.default"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def read_secret(vault_uri: str, secret_name: str, client_id: str) -> dict[str, str]:
    from azure.identity import ManagedIdentityCredential

    parsed = urllib.parse.urlparse(vault_uri.rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".vault.azure.net"):
        raise RuntimeError("Key Vault URI is invalid")
    if not secret_name.startswith(SECRET_PREFIX):
        raise ValueError("Kiwoom secret reference is outside its dedicated prefix")
    token = ManagedIdentityCredential(client_id=client_id).get_token(VAULT_SCOPE).token
    request = urllib.request.Request(
        f"{vault_uri.rstrip('/')}/secrets/{secret_name}?api-version=7.4",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.load(response).get("value") or ""
    try:
        keys = json.loads(value)
    except ValueError as error:
        raise ValueError("Kiwoom secret is not the stored key pair") from error
    if not isinstance(keys, dict) or not keys.get("app_key") or not keys.get("app_secret"):
        raise ValueError("Kiwoom secret is missing its app key or secret")
    return {"app_key": str(keys["app_key"]), "app_secret": str(keys["app_secret"])}


def main() -> int:
    """Always exits 0: the collector runs last in the institutional daily job and must never fail it."""

    if not collector_enabled():
        print(json.dumps({"status": "disabled"}))
        return 0
    try:
        return _collect()
    except Exception as error:  # noqa: BLE001 - reported in the summary, never raised into the daily job
        print(json.dumps({"status": "error", "error": f"{type(error).__name__}: {str(error)[:300]}"}))
        return 0


def _collect() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("The Kiwoom collector is restricted to internal production")
    vault_uri, client_id = _required("FLEX_TOKEN_VAULT_URI"), _required("AZURE_CLIENT_ID")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    include_us = os.environ.get("KIWOOM_INCLUDE_US", "true").strip().lower() != "false"

    def client_for(target: Target) -> KiwoomClient:
        keys = read_secret(vault_uri, target.secret_name, client_id)
        return KiwoomClient(environment=target.environment, app_key=keys["app_key"], app_secret=keys["app_secret"])

    if bronze_required() and not bronze_enabled():
        raise RuntimeError("KIWOOM_BRONZE_REQUIRED=true needs KIWOOM_BRONZE=true; nothing was collected")
    if silver_from_bronze() and not (bronze_enabled() and bronze_required()):
        raise RuntimeError("KIWOOM_SILVER_FROM_BRONZE=true needs KIWOOM_BRONZE=true and KIWOOM_BRONZE_REQUIRED=true; "
                           "nothing was collected")
    now = datetime.now(timezone.utc)
    database_url = _required("DATABASE_URL")
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    # Off unless KIWOOM_BRONZE=true; writes through its own connection so it never holds up the load.
    bronze = KiwoomBronze.from_environment(lambda: connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id),
                                           tenant_id)
    try:
        results = run(connection, tenant_id, client_for=client_for, today=snapshot_day(now), now=now,
                      include_us=include_us, bronze=bronze)
    finally:
        connection.close()
        if bronze is not None:
            bronze.close()
    record = lineage.current()
    # Silver from bronze: every silver row was computed from bronze's stored pages.
    via = {"via": "bronze.kiwoom_responses"} if bronze is not None and bronze.read_back else {}
    for table in ("position_snapshots", "cash_snapshots", "nav_snapshots", "fx_rates", "trades", "cash_transactions"):
        rows = sum(int((item.get("rows") or {}).get(table, 0)) for item in results)
        if rows:
            record.output(f"silver.{table}", rows=rows, source="kiwoom_rest", **via)
    if bronze is not None:
        if bronze.stats["written"]:
            record.input("bronze.kiwoom_responses", rows=bronze.stats["written"], source="kiwoom_rest")
        print(json.dumps({"kiwoom_bronze": bronze.summary()}))
    print(json.dumps({"status": "ran", "accounts": results}, default=str))
    if bronze is not None and bronze.required:
        print(json.dumps({"kiwoom_rebuild": rebuild_check(database_url, tenant_id, results)}, default=str))
    return 0


def rebuild_check(database_url: str, tenant_id: str, results: list[dict]) -> list[dict]:
    """Each loaded account's bronze run rebuilt and compared with silver, read-only and rolled back. Counts only:
    never an account number, figure or body. A failure is reported by its type and never fails the run."""

    from ibkr_etl.kiwoom_conform import check_run

    runs = [item["bronze_run_id"] for item in results if item.get("status") == "loaded" and item.get("bronze_run_id")]
    if not runs:
        return []
    checked = []
    try:
        connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    except Exception as error:  # noqa: BLE001 - its text could name the server
        return [{"error": type(error).__name__}]
    try:
        for run_id in runs:
            try:
                connection.execute("SET TRANSACTION READ ONLY")
                result = check_run(connection, run_id)
                checked.append({key: result.get(key) for key in (
                    "run_id", "run_status", "market", "parts", "history_days", "answers", "tables", "clean")})
            except Exception as error:  # noqa: BLE001 - only the type
                checked.append({"run_id": run_id, "error": type(error).__name__})
            finally:
                connection.rollback()
    finally:
        connection.close()
    return checked


if __name__ == "__main__":
    raise SystemExit(lineage.run_main("ibkrv2stg-institutional-daily", main))
