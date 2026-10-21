"""Render one onboarding case's historical factsheets now, from the backend.

    python scripts/run_onboarding_case_reports.py --case-id <uuid> [--design us-istrategy]

For a case at Build history whose reports come from a factsheet design
(designed_reports.py), this does in one run what the month-end report job and
the designer would do over several:

1. with ``--design``, submits that built-in design for the case
   (builtin_design_submission.py), unless the same design is already submitted;
2. classifies, through the sector job (Yahoo), every holding the account held
   since its history start that has no classification or no English name yet;
3. renders every completed month from the submitted design and the account's
   own stored data, stores each factsheet privately and records the case's
   reports_rendered check, exactly as the month-end report job does.

Only this case is touched. Prints one JSON summary; account numbers are masked.
Exits 1 when the reports could not be rendered.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402


def mask(value) -> str:
    return re.sub(r"(?<![A-Za-z0-9])\d{5,}(?![A-Za-z0-9])", lambda m: "*" * (len(m.group()) - 4) + m.group()[-4:], str(value))


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def us_security_problems(connection, subject_id: str, start: str, end: str, provider: str) -> list[str]:
    from ibkr_etl.us_istrategy_securities import UsIstrategySecurityError, lookup

    rows = connection.execute(
        "SELECT DISTINCT p.conid, COALESCE(i.symbol, p.conid) AS symbol, c.long_name, c.sub_sector "
        "FROM subject_position_series p LEFT JOIN instruments i ON i.conid = p.conid "
        "LEFT JOIN instrument_classifications c ON c.conid = p.conid AND c.provider = ? "
        "WHERE p.subject_id = ? AND p.snapshot_date BETWEEN ? AND ? AND p.conid LIKE 'US:%%' "
        "ORDER BY p.conid", (provider, subject_id, start, end)).fetchall()
    problems = []
    for row in rows:
        try:
            lookup({"symbol": row["symbol"]}, dict(row) if row["long_name"] or row["sub_sector"] else None)
        except UsIstrategySecurityError as error:
            problems.append(str(error))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--design", help="built-in design to submit first, such as us-istrategy")
    args = parser.parse_args(argv)

    from azure.core.exceptions import ResourceExistsError

    from ibkr_etl.builtin_design_submission import submit_built_in_design
    from ibkr_etl.cloud_storage import download_blob_bytes, upload_bytes
    from ibkr_etl.composite_reports import HONG_KONG
    from ibkr_etl.designed_reports import run_designed_report_cases
    from ibkr_etl.sector_classification import YAHOO_PROVIDER, refresh_sector_classifications_range

    database_url = _required("DATABASE_URL")
    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)

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

    summary: dict = {"case_id": args.case_id}
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        case = connection.execute(
            "SELECT target_subject_id, history_start_date FROM broker_onboarding_cases "
            "WHERE tenant_id = ? AND case_id = ?", (tenant_id, args.case_id)).fetchone()
        if case is None:
            raise RuntimeError("No such onboarding case")
        subject_id = str(case["target_subject_id"])
        if args.design:
            summary["design"] = submit_built_in_design(connection, tenant_id, args.case_id, design_id=args.design)
            connection.commit()
        today = datetime.now(HONG_KONG).date()
        classified = refresh_sector_classifications_range(
            connection, subject_id=subject_id, start_date=str(case["history_start_date"])[:10],
            end_date=today.isoformat())
        connection.commit()
        summary["classification"] = {key: classified.get(key) for key in (
            "historical_unique_holdings", "previously_classified", "classified", "unclassified",
            "unclassified_symbols")}
        if args.design == "us-istrategy":
            # Every U.S. holding's name and sector at once, so one run lists all of them rather
            # than stopping at the first month that has one.
            summary["us_securities_problems"] = us_security_problems(
                connection, subject_id, str(case["history_start_date"])[:10], today.isoformat(), YAHOO_PROVIDER)
        summary["reports"] = run_designed_report_cases(connection, tenant_id, container=container, today=today,
                                                       fetch=fetch, store=store, case_ids={args.case_id})
        check = connection.execute(
            "SELECT status, message FROM broker_onboarding_checks WHERE tenant_id = ? AND case_id = ? "
            "AND check_id = 'reports_rendered'", (tenant_id, args.case_id)).fetchone()
        summary["reports_rendered"] = dict(check) if check else None
    except Exception as error:  # noqa: BLE001 - reported, account numbers masked
        connection.rollback()
        summary["error"] = f"{type(error).__name__}: {error}"
    finally:
        connection.close()
    print(mask(json.dumps(summary, default=str, sort_keys=True)), flush=True)
    rendered = (summary.get("reports_rendered") or {}).get("status")
    return 0 if "error" not in summary and rendered in ("pass", "warn", "pending") else 1


if __name__ == "__main__":
    raise SystemExit(main())
