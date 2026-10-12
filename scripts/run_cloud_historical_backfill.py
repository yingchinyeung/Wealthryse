from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import upload_file  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect, is_postgres  # noqa: E402
from ibkr_etl.service import backfill_daily_position_snapshots  # noqa: E402


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD") from error


def _run(label: str, command: list[str]) -> None:
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode:
        raise RuntimeError(f"{label} exit code {completed.returncode}")
    print(f"PASS | {label}", flush=True)


def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(
        description=(
            "Backfill exact daily IBKR position statements, then rebuild and "
            "publish the China & Hong Kong portfolio"
        )
    )
    parser.add_argument(
        "--from", dest="from_date", type=_date, default=date(2025, 1, 20)
    )
    parser.add_argument("--to", dest="to_date", type=_date)
    parser.add_argument("--account", default="U16088606")
    parser.add_argument("--pause-seconds", type=float, default=10.0)
    parser.add_argument("--max-dates", type=int)
    args = parser.parse_args()
    to_date = args.to_date or (
        datetime.now(settings.timezone).date() - timedelta(days=1)
    )

    lock_connection = connect(
        settings.database_path, settings.database_url, settings.tenant_id
    )
    lock_name = f"ibkr-daily:{settings.tenant_id}"
    acquired = True
    try:
        if is_postgres(lock_connection):
            acquired = bool(
                lock_connection.execute(
                    "SELECT pg_try_advisory_lock(hashtext(?))", (lock_name,)
                ).fetchone()[0]
            )
        if not acquired:
            raise RuntimeError("The scheduled daily job is already running; retry later")

        result = backfill_daily_position_snapshots(
            settings,
            args.from_date,
            to_date,
            args.account,
            pause_seconds=args.pause_seconds,
            max_dates=args.max_dates,
        )
        print(json.dumps(result, indent=2, default=str), flush=True)
        if result["remaining"]:
            print(
                "PARTIAL | rerun this command to continue; completed dates will be skipped",
                flush=True,
            )
            return 2

        _run(
            "historical reference classifications",
            [
                sys.executable,
                "scripts/refresh_reference_data.py",
                "--max-sector-lookups",
                "500",
            ],
        )
        reference_path = PROJECT_ROOT / "data" / "reference_data.json"
        if settings.azure_storage_account_url:
            upload_file(
                settings.azure_storage_account_url,
                settings.azure_report_container,
                f"{settings.tenant_id}/reference/reference_data.json",
                reference_path,
                overwrite=True,
            )
        _run(
            "derived portfolios",
            [sys.executable, "scripts/refresh_derived_portfolios.py"],
        )
        _run(
            "analytics dashboard",
            [sys.executable, "scripts/generate_daily_report.py"],
        )
        if settings.database_url and settings.azure_storage_account_url:
            _run(
                "analyst SQLite and CSV exports",
                [sys.executable, "scripts/export_analyst_snapshot.py"],
            )
        print("COMPLETE | historical derived-portfolio data published", flush=True)
        return 0
    finally:
        try:
            if acquired and is_postgres(lock_connection):
                lock_connection.execute(
                    "SELECT pg_advisory_unlock(hashtext(?))", (lock_name,)
                )
                lock_connection.commit()
        finally:
            lock_connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
