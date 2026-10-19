"""Daily Kiwoom collection, run by the institutional daily job after the Flex worker.

Does nothing unless KIWOOM_COLLECTOR_ENABLED=true. Each Kiwoom connection's
app key and secret are read from Key Vault for this run only and never logged;
see ibkr_etl.kiwoom_collector for what is loaded and which checks are recorded.
Prints one JSON summary with no secrets. One account's failure is recorded on
its connection and never stops the others or the job.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import lineage  # noqa: E402
from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402
from ibkr_etl.kiwoom_client import KiwoomClient  # noqa: E402
from ibkr_etl.kiwoom_collector import SECRET_PREFIX, Target, collector_enabled, run  # noqa: E402

VAULT_SCOPE = "https://vault.azure.net/.default"
SEOUL = ZoneInfo("Asia/Seoul")
# KRX closes at 15:30; Kiwoom's prices are final a few minutes later.
CLOSE_SETTLED = time(15, 40)


def snapshot_day(now: datetime) -> date:
    """The KRX trading day whose close Kiwoom's prices show at this moment."""

    local = now.astimezone(SEOUL)
    day = local.date() if local.time() >= CLOSE_SETTLED else local.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


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
    if not collector_enabled():
        print(json.dumps({"status": "disabled"}))
        return 0
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("The Kiwoom collector is restricted to internal production")
    vault_uri, client_id = _required("FLEX_TOKEN_VAULT_URI"), _required("AZURE_CLIENT_ID")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    include_us = os.environ.get("KIWOOM_INCLUDE_US", "true").strip().lower() != "false"

    def client_for(target: Target) -> KiwoomClient:
        keys = read_secret(vault_uri, target.secret_name, client_id)
        return KiwoomClient(environment=target.environment, app_key=keys["app_key"], app_secret=keys["app_secret"])

    now = datetime.now(timezone.utc)
    connection = connect(Path("/tmp/unused.sqlite3"), _required("DATABASE_URL"), tenant_id)
    try:
        results = run(connection, tenant_id, client_for=client_for, today=snapshot_day(now), now=now,
                      include_us=include_us)
    finally:
        connection.close()
    record = lineage.current()
    for table in ("position_snapshots", "cash_snapshots", "nav_snapshots", "fx_rates", "trades", "cash_transactions"):
        rows = sum(int((item.get("rows") or {}).get(table, 0)) for item in results)
        if rows:
            record.output(f"silver.{table}", rows=rows, source="kiwoom_rest")
    print(json.dumps({"status": "ran", "accounts": results}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(lineage.run_main("ibkrv2stg-institutional-daily", main))
