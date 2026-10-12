from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.analyst_export import build_csv_zip, export_snapshot  # noqa: E402
from ibkr_etl.cloud_storage import upload_file  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402


HKT = timezone(timedelta(hours=8), name="Asia/Hong_Kong")


def main() -> int:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("Analyst snapshot export requires DATABASE_URL")
    if not settings.azure_storage_account_url:
        raise RuntimeError("Analyst snapshot export requires AZURE_STORAGE_ACCOUNT_URL")

    generated_at = datetime.now(HKT).isoformat(timespec="seconds")
    output_dir = PROJECT_ROOT / "exports"
    snapshot_path = output_dir / "IBKR_Analyst_Snapshot_Latest.sqlite"
    csv_zip_path = output_dir / "IBKR_Analyst_CSV_Exports_Latest.zip"
    manifest_path = output_dir / "IBKR_Analyst_Export_Manifest_Latest.json"

    connection = connect(
        settings.database_path, settings.database_url, settings.tenant_id
    )
    try:
        manifest = export_snapshot(
            connection,
            snapshot_path,
            tenant_id=settings.tenant_id,
            generated_at=generated_at,
        )
    finally:
        connection.close()

    build_csv_zip(snapshot_path, csv_zip_path, manifest)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )

    base = f"{settings.tenant_id}/latest"
    for path in (snapshot_path, csv_zip_path, manifest_path):
        upload_file(
            settings.azure_storage_account_url,
            settings.azure_report_container,
            f"{base}/{path.name}",
            path,
            overwrite=True,
        )

    print(
        "PASS | analyst snapshot and CSV exports uploaded | "
        f"tables={len(manifest['row_counts'])} | rows={sum(manifest['row_counts'].values())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
