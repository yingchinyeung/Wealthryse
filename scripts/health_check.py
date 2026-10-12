from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import blob_exists  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect, is_postgres  # noqa: E402


HEALTH_LOG = PROJECT_ROOT / "logs" / "health.log"
HKT = timezone(timedelta(hours=8), name="Asia/Hong_Kong")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _raw_exists(settings, location: str | None) -> bool:
    if not location:
        return False
    if location.startswith(("https://", "http://")) and settings.azure_storage_account_url:
        marker = f"/{settings.azure_raw_container}/"
        if marker not in location:
            return False
        blob_name = location.split(marker, 1)[1].split("?", 1)[0]
        return blob_exists(
            settings.azure_storage_account_url,
            settings.azure_raw_container,
            blob_name,
        )
    return Path(location).is_file()


def _requested_to_is_fresh(
    requested_to: str | None,
    *,
    today_hkt: date,
    max_request_lag_days: int,
) -> bool:
    try:
        requested_date = date.fromisoformat(requested_to or "")
    except ValueError:
        return False
    earliest_acceptable = today_hkt - timedelta(days=max_request_lag_days)
    return earliest_acceptable <= requested_date < today_hkt


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the latest IBKR ingestion.")
    parser.add_argument(
        "--max-request-lag-days",
        type=int,
        default=1,
        choices=range(1, 8),
        metavar="1-7",
        help=(
            "Maximum age of requested_to relative to today in Hong Kong. "
            "The scheduled daily job keeps the strict default of one day."
        ),
    )
    args = parser.parse_args()
    settings = load_settings()
    failures: list[str] = []
    connection = connect(
        settings.database_path, settings.database_url, settings.tenant_id
    )
    try:
        if is_postgres(connection):
            connection.execute("SELECT 1").fetchone()
        else:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                failures.append("SQLite quick_check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                failures.append("SQLite foreign_key_check failed")
        latest = connection.execute(
            """
            SELECT status, requested_to, reconciliation_json, raw_sha256, raw_path
            FROM ingestion_runs ORDER BY run_id DESC LIMIT 1
            """
        ).fetchone()
        if latest is None or latest["status"] != "success":
            failures.append("Latest ingestion did not succeed")
        else:
            try:
                reconciliation = json.loads(latest["reconciliation_json"] or "{}")
            except json.JSONDecodeError:
                reconciliation = {}
                failures.append("Latest reconciliation JSON is invalid")
            if reconciliation.get("status") != "PASS":
                failures.append("Latest reconciliation did not pass")
            today_hkt = datetime.now(HKT).date()
            if not _requested_to_is_fresh(
                latest["requested_to"],
                today_hkt=today_hkt,
                max_request_lag_days=args.max_request_lag_days,
            ):
                earliest = today_hkt - timedelta(days=args.max_request_lag_days)
                latest_allowed = today_hkt - timedelta(days=1)
                failures.append(
                    f"Latest requested_to is {latest['requested_to']}, expected between "
                    f"{earliest.isoformat()} and {latest_allowed.isoformat()}"
                )
            if not _raw_exists(settings, latest["raw_path"]):
                failures.append("Latest ingestion raw object is missing")
            elif (
                latest["raw_path"]
                and not str(latest["raw_path"]).startswith(("http://", "https://"))
                and latest["raw_sha256"]
                and sha256_file(Path(latest["raw_path"])) != latest["raw_sha256"]
            ):
                failures.append("Latest ingestion raw checksum does not match")
    finally:
        connection.close()

    HEALTH_LOG.parent.mkdir(parents=True, exist_ok=True)
    status = "FAIL" if failures else "PASS"
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {status}"
    if failures:
        line += " | " + " | ".join(failures)
    with HEALTH_LOG.open("a", encoding="utf-8") as log:
        log.write(line + "\n")
    print(line)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
