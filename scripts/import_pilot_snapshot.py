from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import download_file_if_exists  # noqa: E402
from ibkr_etl.db import DEFAULT_TENANT_ID  # noqa: E402
from ibkr_etl.pilot_import import import_snapshot  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    database_url = _required("DATABASE_URL")
    account_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    start_date = os.environ.get("PILOT_START_DATE", "2025-01-20")
    account_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("PILOT_SNAPSHOT_CONTAINER", "input-snapshots")
    blob_name = _required("PILOT_SNAPSHOT_BLOB")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    local_path = Path("/tmp/pilot-snapshot.sqlite")

    if not download_file_if_exists(account_url, container, blob_name, local_path):
        raise RuntimeError(f"Pilot snapshot blob was not found: {container}/{blob_name}")
    result = import_snapshot(
        database_url=database_url,
        local_path=local_path,
        account_id=account_id,
        start_date=start_date,
        tenant_id=tenant_id,
        source_reference=f"{container}/{blob_name}",
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
