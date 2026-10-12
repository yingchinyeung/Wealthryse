from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import upload_bytes  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.historical_reporting import run_historical_month_end_batch  # noqa: E402
from ibkr_etl.sector_classification import (  # noqa: E402
    refresh_sector_classifications_range,
)
from ibkr_etl.util import canonical_json  # noqa: E402


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "staging":
        raise RuntimeError("The historical reporting batch may run only in staging")
    if not _enabled("ALLOW_UNLICENSED_BENCHMARK"):
        raise RuntimeError("Explicit staging-only unlicensed benchmark opt-in is absent")
    if not _enabled("YAHOO_HISTORICAL_HOLDINGS_APPROVED"):
        raise RuntimeError(
            "Historical holdings ticker transmission to Yahoo Finance is not approved"
        )
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")

    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    account_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        start_date = os.environ.get("HISTORICAL_REPORT_START", "2025-01-20")
        end_date = os.environ.get("HISTORICAL_REPORT_END", "2026-08-31")
        classifications = refresh_sector_classifications_range(
            connection,
            subject_id=account_id,
            start_date=start_date,
            end_date=end_date,
            workers=int(os.environ.get("SECTOR_CLASSIFICATION_WORKERS", "4")),
        )
        if classifications["unclassified"]:
            raise RuntimeError(
                "Historical sector classification is incomplete: "
                f"{classifications['unclassified_symbols']}"
            )
        result = run_historical_month_end_batch(
            connection,
            tenant_id=tenant_id,
            account_id=account_id,
            period_start=os.environ.get("REPORTING_PERIOD_START", "2025-01-20"),
            start_date=start_date,
            end_date=end_date,
            reporting_currency=os.environ.get("REPORTING_CURRENCY", "USD"),
            factsheet_template_id=os.environ.get(
                "FACTSHEET_TEMPLATE_ID", "U16088571-monthly-factsheet"
            ),
            factsheet_template_version=os.environ["FACTSHEET_TEMPLATE_VERSION"],
            presentation_template_id=os.environ.get(
                "PRESENTATION_TEMPLATE_ID", "U16088571-monthly-presentation"
            ),
            presentation_template_version=os.environ[
                "PRESENTATION_TEMPLATE_VERSION"
            ],
        )
        result["historical_sector_classification"] = classifications
        storage_url = os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "").strip()
        container = os.environ.get("REPORT_DRAFT_CONTAINER", "report-drafts").strip()
        uploaded = 0
        for item in result["items"]:
            row = connection.execute(
                """
                SELECT snapshot_payload_json, data_sha256
                FROM reporting_snapshots
                WHERE tenant_id = ? AND snapshot_id = ? AND subject_id = ?
                  AND status IN ('frozen', 'approved')
                """,
                (tenant_id, item["snapshot_id"], account_id),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Frozen snapshot {item['snapshot_id']} is unavailable")
            payload = str(row["snapshot_payload_json"])
            if hashlib.sha256(payload.encode("utf-8")).hexdigest() != row["data_sha256"]:
                raise RuntimeError(f"Snapshot integrity check failed: {item['snapshot_id']}")
            blob_name = (
                f"snapshots/{account_id}/{item['period_end']}/"
                f"{item['snapshot_id']}.json"
            )
            uploaded_url = upload_bytes(
                storage_url,
                container,
                blob_name,
                payload.encode("utf-8"),
                overwrite=True,
                content_type="application/json; charset=utf-8",
            )
            item["snapshot_blob"] = blob_name if uploaded_url else None
            item["snapshot_blob_uploaded"] = bool(uploaded_url)
            uploaded += int(bool(uploaded_url))
        result["snapshots_uploaded"] = uploaded
        print(canonical_json(result))
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
