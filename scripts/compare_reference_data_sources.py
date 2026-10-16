"""Compare v1.1's published reference_data.json with the same data built from v2's tables.

Run once in production before setting REFERENCE_DATA_SOURCE=database on
derived-daily. Writes nothing: the database session is read only, the blob is
read into memory, and no local file is created. Prints one JSON line.

Exit code 0: switching would not change which holdings or cash rows any approved
filtered portfolio excludes, nor the stored exclusion reasons. 1: it would.
Coverage and value differences are reported either way.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = SCRIPT_ROOT.parent / "src"
for root in (SCRIPT_ROOT, SOURCE_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from ibkr_etl.cloud_storage import download_blob_snapshot  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.reference_data_source import (  # noqa: E402
    DEFAULT_EXAMPLES,
    DEFAULT_RATE_TOLERANCE,
    build_reference_data_from_database,
    compare_risk_free,
    compare_sectors,
    exclusion_impact,
    filtered_portfolio_inputs,
    usd_risk_free_row,
)
from ibkr_etl.reference_snapshot import inspect_reference_bytes  # noqa: E402
from run_self_service_derived_refresh import _approved_targets, _reference_sources  # noqa: E402


def _published_reference(settings) -> tuple[bytes, dict]:
    """The bytes derived-daily would read today, held in memory."""

    blob_name = f"{settings.tenant_id}/reference/reference_data.json"
    for account_url, container, source in _reference_sources(settings):
        if not account_url or not container:
            continue
        snapshot = download_blob_snapshot(account_url, container, blob_name)
        if snapshot is not None:
            content, storage = snapshot
            return content, {"source": source, "storage": storage}
    raise RuntimeError("Published reference_data.json is missing")


def run_comparison(connection, tenant_id: str, file_content: bytes, file_receipt: dict, *,
                   rate_tolerance: float = DEFAULT_RATE_TOLERANCE,
                   examples: int = DEFAULT_EXAMPLES) -> dict:
    # Any write in this session now fails.
    connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    connection.commit()
    file_data, file_manifest = inspect_reference_bytes(file_content)
    database_data = build_reference_data_from_database(connection, tenant_id)
    file_date = str((file_data["risk_free"].get("USD") or {}).get("observation_date") or "")
    on_file_date = usd_risk_free_row(connection, tenant_id, file_date) if file_date else None
    subjects = []
    for row in _approved_targets(connection):
        if row["subject_type"] != "filtered_portfolio":
            continue
        value = row["configuration_json"]
        definition = json.loads(value) if isinstance(value, str) else dict(value)
        subjects.append(exclusion_impact(
            file_data, database_data, definition=definition,
            examples=examples, **filtered_portfolio_inputs(connection, definition),
        ))
    connection.rollback()
    switch_safe = all(
        subject["excluded_status_changes"]["count"] == 0
        and subject["reason_text_changes"]["count"] == 0
        for subject in subjects
    )
    return {
        "switch_safe_for_derived_daily": switch_safe,
        "file": {**file_receipt, "classification_rows": file_manifest["classification_rows"],
                 "risk_free_rows": file_manifest["risk_free_rows"],
                 "content_sha256": file_manifest["content_sha256"]},
        "derived_daily_impact": subjects,
        "sectors": compare_sectors(file_data["sectors"], database_data["sectors"], examples=examples),
        "risk_free": compare_risk_free(
            file_data["risk_free"], database_data["risk_free"],
            database_rate_on_file_date=None if on_file_date is None else float(on_file_date["annual_rate"]),
            rate_tolerance=rate_tolerance,
        ),
        "writes": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, help="compare this local file instead of the published blob")
    parser.add_argument("--rate-tolerance", type=float, default=DEFAULT_RATE_TOLERANCE)
    parser.add_argument("--examples", type=int, default=DEFAULT_EXAMPLES)
    args = parser.parse_args(argv)
    settings = load_settings()
    if not settings.database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The comparison requires a PostgreSQL DATABASE_URL")
    if args.file is not None:
        content, receipt = args.file.read_bytes(), {"source": "local-file", "path": str(args.file)}
    else:
        content, receipt = _published_reference(settings)
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    try:
        report = run_comparison(connection, settings.tenant_id, content, receipt,
                                rate_tolerance=args.rate_tolerance, examples=args.examples)
    finally:
        connection.close()
    print("REFERENCE_DATA_COMPARISON " + json.dumps(report, sort_keys=True, default=str), flush=True)
    return 0 if report["switch_safe_for_derived_daily"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
