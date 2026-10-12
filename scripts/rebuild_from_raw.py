from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ibkr_etl.config import load_settings
from ibkr_etl.service import ingest_content


def optional_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild a portfolio database from its raw report catalog")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    target = args.target.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(target)

    connection = sqlite3.connect(source)
    connection.row_factory = sqlite3.Row
    try:
        reports = connection.execute(
            """
            SELECT rr.file_path, rr.content_type, ir.run_type,
                   ir.requested_from, ir.requested_to, ir.reference_code
            FROM raw_reports rr
            JOIN ingestion_runs ir ON ir.run_id = rr.run_id
            ORDER BY ir.run_id
            """
        ).fetchall()
    finally:
        connection.close()
    if not reports:
        raise ValueError("Source database has no raw reports")

    settings = replace(load_settings(), database_path=target)
    for index, report in enumerate(reports, start=1):
        raw_path = Path(report["file_path"])
        ingest_content(
            settings,
            raw_path.read_bytes(),
            run_type=report["run_type"],
            requested_from=optional_date(report["requested_from"]),
            requested_to=optional_date(report["requested_to"]),
            reference_code=report["reference_code"],
            content_type=report["content_type"] or "application/xml",
            existing_raw_path=raw_path,
        )
        print(f"Rebuilt report {index}/{len(reports)}")
    print(f"Rebuilt database: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
