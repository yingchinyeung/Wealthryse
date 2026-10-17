"""Stage 3 reports for new-onboarding cases: composites in the Alpha World MS or T
design, and every other case in the design its owner made in the factsheet designer.

The month-end report job runs this after its packages, in the same image and
with the same private storage. It renders factsheets only, as per-account decks
are no longer built. It does nothing until
migration 064 exists, or while no composite case is waiting on its reports.
Each case's outcome is recorded on its reports_rendered check; see
ibkr_etl/composite_reports.py and ibkr_etl/designed_reports.py.

It also renders the month-end factsheets of subjects that went live from a
new-onboarding case (ibkr_etl/onboarding_month_end.py), which the original
month-end package cannot render. It exits 3 when one of those failed.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from azure.core.exceptions import ResourceExistsError  # noqa: E402

from ibkr_etl.cloud_storage import download_blob_bytes, upload_bytes  # noqa: E402
from ibkr_etl.composite_reports import HONG_KONG, run_composite_report_cases  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.designed_reports import run_designed_report_cases  # noqa: E402
from ibkr_etl.onboarding_month_end import run_live_month_end  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("Onboarding composite reports run only in internal production")
    database_url = _required("DATABASE_URL")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports")

    def fetch(bucket: str, blob: str) -> bytes:
        return download_blob_bytes(storage_url, bucket, blob, maximum_bytes=64 * 1024 * 1024)

    def store(bucket: str, blob: str, content: bytes, content_type: str) -> None:
        # Content-addressed and immutable: an existing blob must already hold these bytes.
        try:
            stored = upload_bytes(storage_url, bucket, blob, content, overwrite=False, content_type=content_type)
        except ResourceExistsError:
            stored = fetch(bucket, blob) == content
        if not stored:
            raise RuntimeError("Private report storage refused the report")

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        if connection.execute("SELECT to_regclass('public.broker_onboarding_cases')").fetchone()[0] is None:
            print(json.dumps({"status": "not_installed", "cases": []}, sort_keys=True))
            return 0
        today = datetime.now(HONG_KONG).date()
        results = run_composite_report_cases(connection, tenant_id, container=container,
                                             today=today, fetch=fetch, store=store)
        # Every other case reports in the design its owner made in the factsheet designer.
        designed = run_designed_report_cases(connection, tenant_id, container=container,
                                             today=today, fetch=fetch, store=store)
        # Subjects that went live from a case render each month in their approved design.
        live = run_live_month_end(connection, tenant_id, container=container,
                                  today=today, fetch=fetch, store=store)
    finally:
        connection.close()
    failed = any(item["status"] == "failed" for item in live)
    print(json.dumps({"status": "partial_failure" if failed else "success", "cases": results,
                      "designed_cases": designed, "live_month_end": live}, sort_keys=True))
    # 3 tells the month-end job that a live subject's month-end failed.
    return 3 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
