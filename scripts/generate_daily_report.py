from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import download_file_if_exists, upload_file  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.reference_data import load_reference_data, refresh_reference_data  # noqa: E402
from ibkr_etl.report import generate_daily_report  # noqa: E402
from ibkr_etl.util import utc_now_iso  # noqa: E402


HKT = timezone(timedelta(hours=8), name="Asia/Hong_Kong")
REPORT_LOG = PROJECT_ROOT / "logs" / "report.log"


def prepare_azure_reference_data(settings, database_path: Path) -> dict | None:
    """Restore and validate reference data before publishing an Azure dashboard.

    The full daily runner already performs this work, but this guard is also needed
    here because dashboard-only repair and release commands invoke this script
    directly.  A missing risk-free rate must never overwrite the latest published
    dashboard with null risk-free and Sharpe metrics.
    """
    if not settings.azure_storage_account_url:
        return None

    reference_path = PROJECT_ROOT / "data" / "reference_data.json"
    reference_blob = f"{settings.tenant_id}/reference/reference_data.json"
    download_file_if_exists(
        settings.azure_storage_account_url,
        settings.azure_report_container,
        reference_blob,
        reference_path,
    )

    result = refresh_reference_data(
        database_path,
        reference_path,
        max_sector_lookups=0,
        workers=1,
        timeout=20,
        database_url=settings.database_url,
        tenant_id=settings.tenant_id,
    )
    reference_data = load_reference_data(reference_path)
    usd_risk_free = reference_data.get("risk_free", {}).get("USD", {})
    if usd_risk_free.get("annual_rate") is None:
        warning_text = "; ".join(result.get("warnings", [])) or "no cached value"
        raise RuntimeError(
            "Refusing to publish dashboard without the USD 3-month Treasury "
            f"risk-free rate ({warning_text})"
        )

    upload_file(
        settings.azure_storage_account_url,
        settings.azure_report_container,
        reference_blob,
        reference_path,
        overwrite=True,
    )
    return result


def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Generate the daily IBKR analytics dashboard.")
    parser.add_argument(
        "--database",
        type=Path,
        default=settings.database_path,
        help="Local fallback database path",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="Directory for the latest dashboard and dated archive",
    )
    args = parser.parse_args()

    try:
        prepare_azure_reference_data(settings, args.database)
        latest, archive = generate_daily_report(
            args.database,
            args.output_dir,
            database_url=settings.database_url,
            tenant_id=settings.tenant_id,
        )
        report_location = str(latest)
        if settings.azure_storage_account_url:
            report_location = upload_file(
                settings.azure_storage_account_url,
                settings.azure_report_container,
                f"{settings.tenant_id}/latest/IBKR_Portfolio_Analytics_Latest.html",
                latest,
                overwrite=True,
            ) or report_location
            upload_file(
                settings.azure_storage_account_url,
                settings.azure_report_container,
                f"{settings.tenant_id}/archive/{archive.name}",
                archive,
                overwrite=True,
            )
        if settings.database_url:
            connection = connect(
                settings.database_path, settings.database_url, settings.tenant_id
            )
            try:
                latest_run = connection.execute(
                    "SELECT run_id FROM ingestion_runs ORDER BY run_id DESC LIMIT 1"
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO report_runs(
                        ingestion_run_id, generated_at, report_path, report_sha256, status
                    ) VALUES (?, ?, ?, ?, 'success')
                    """,
                    (
                        latest_run[0] if latest_run else None,
                        utc_now_iso(),
                        report_location,
                        hashlib.sha256(latest.read_bytes()).hexdigest(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
    except Exception as error:
        status = f"{datetime.now(HKT).isoformat(timespec='seconds')} FAIL | {error}"
        REPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with REPORT_LOG.open("a", encoding="utf-8") as log:
            log.write(status + "\n")
        print(status)
        return 1

    status = (
        f"{datetime.now(HKT).isoformat(timespec='seconds')} PASS | "
        f"latest={latest} | archive={archive}"
    )
    REPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_LOG.open("a", encoding="utf-8") as log:
        log.write(status + "\n")
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
