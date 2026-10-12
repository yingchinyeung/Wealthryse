from __future__ import annotations

import json
import hashlib
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.cloud_storage import upload_bytes  # noqa: E402
from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id, freeze_month_end_snapshot  # noqa: E402


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "staging":
        raise RuntimeError("The reporting-snapshot job may run only in staging")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    account_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    period_start = os.environ.get("REPORTING_PERIOD_START", "2025-01-20")
    period_end = os.environ["REPORTING_PERIOD_END"]
    reporting_currency = os.environ.get("REPORTING_CURRENCY", "USD")

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        # A standalone snapshot run must not invalidate an existing report's
        # snapshot binding. Financial restatements require the governed report
        # replacement workflow, not an implicit supersession here.
        existing = active_month_end_snapshot_id(
            connection, tenant_id=tenant_id, subject_id=account_id, period_end=period_end
        )
        if existing is not None:
            print(json.dumps({"status": "already_frozen", "snapshot_id": existing,
                              "subject_id": account_id, "period_end": period_end,
                              "inserted": False, "snapshot_blob_uploaded": False}, sort_keys=True))
            return 0
        result = freeze_month_end_snapshot(
            connection,
            tenant_id=tenant_id,
            subject_id=account_id,
            period_start=period_start,
            period_end=period_end,
            reporting_currency=reporting_currency,
            reject_quality_fail=True,
        )
        row = connection.execute(
            """
            SELECT snapshot_payload_json, data_sha256
            FROM reporting_snapshots
            WHERE tenant_id = ? AND snapshot_id = ? AND subject_id = ?
              AND status IN ('frozen', 'approved')
            """,
            (tenant_id, result.snapshot_id, account_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("Frozen reporting snapshot could not be read back")
        payload = row["snapshot_payload_json"]
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != row["data_sha256"]:
            raise RuntimeError("Frozen reporting snapshot integrity check failed")

        account_url = os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "").strip()
        container = os.environ.get("REPORT_DRAFT_CONTAINER", "report-drafts").strip()
        blob_name = (
            f"snapshots/{account_id}/{result.period_end}/"
            f"{result.snapshot_id}.json"
        )
        uploaded_url = upload_bytes(
            account_url,
            container,
            blob_name,
            payload.encode("utf-8"),
            overwrite=True,
            content_type="application/json; charset=utf-8",
        )
        output = result.as_dict()
        output.update(
            {
                "snapshot_blob": blob_name if uploaded_url else None,
                "snapshot_blob_uploaded": bool(uploaded_url),
            }
        )
        print(json.dumps(output, sort_keys=True))
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
