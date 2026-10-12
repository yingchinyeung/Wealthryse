from __future__ import annotations

import subprocess
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


HKT = timezone(timedelta(hours=8), name="Asia/Hong_Kong")
RUNNER_LOG = PROJECT_ROOT / "logs" / "cloud_runner.log"


def log(status: str, message: str = "") -> None:
    line = f"{datetime.now(HKT).isoformat(timespec='seconds')} {status}"
    if message:
        line += f" | {message}"
    print(line, flush=True)
    RUNNER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUNNER_LOG.open("a", encoding="utf-8") as target:
        target.write(line + "\n")


def run(label: str, command: list[str]) -> None:
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode:
        raise RuntimeError(f"{label} exit code {completed.returncode}")
    log("PASS", label)


def main() -> int:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("Cloud job requires DATABASE_URL")
    if not settings.azure_storage_account_url:
        raise RuntimeError("Cloud job requires AZURE_STORAGE_ACCOUNT_URL")

    reference_path = PROJECT_ROOT / "data" / "reference_data.json"
    reference_blob = f"{settings.tenant_id}/reference/reference_data.json"
    download_file_if_exists(
        settings.azure_storage_account_url,
        settings.azure_report_container,
        reference_blob,
        reference_path,
    )

    lock_connection = connect(
        settings.database_path, settings.database_url, settings.tenant_id
    )
    lock_name = f"ibkr-daily:{settings.tenant_id}"
    try:
        acquired = lock_connection.execute(
            "SELECT pg_try_advisory_lock(hashtext(?))", (lock_name,)
        ).fetchone()[0]
        if not acquired:
            log("SKIP", "another daily job owns the tenant lock")
            return 0
        log("START", f"tenant={settings.tenant_id}")
        run("IBKR import", [sys.executable, "-m", "ibkr_etl.cli", "daily"])
        run("health check", [sys.executable, "scripts/health_check.py"])
        run(
            "analytics reference data",
            [sys.executable, "scripts/refresh_reference_data.py"],
        )
        upload_file(
            settings.azure_storage_account_url,
            settings.azure_report_container,
            reference_blob,
            reference_path,
            overwrite=True,
        )
        run(
            "derived portfolios",
            [sys.executable, "scripts/refresh_derived_portfolios.py"],
        )
        run(
            "versioned risk snapshots",
            [sys.executable, "scripts/build_risk_snapshots.py"],
        )
        run("analytics dashboard", [sys.executable, "scripts/generate_daily_report.py"])
        run(
            "analyst SQLite and CSV exports",
            [sys.executable, "scripts/export_analyst_snapshot.py"],
        )
        log("COMPLETE")
        return 0
    except Exception as error:
        log("FAIL", str(error))
        return 1
    finally:
        try:
            lock_connection.execute(
                "SELECT pg_advisory_unlock(hashtext(?))", (lock_name,)
            )
            lock_connection.commit()
        finally:
            lock_connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
