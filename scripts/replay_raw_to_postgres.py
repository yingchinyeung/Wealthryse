from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

from ibkr_etl.config import load_settings
from ibkr_etl.service import ingest_content


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay successful v1 raw Flex statements into the configured v1.1 PostgreSQL database."
    )
    parser.add_argument("--source-sqlite", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    if settings.database_backend != "postgresql":
        raise SystemExit("DATABASE_URL must point to the v1.1 PostgreSQL database.")
    source = args.source_sqlite.resolve()
    if not source.is_file():
        raise SystemExit(f"Source SQLite database not found: {source}")

    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT ir.requested_from, ir.requested_to, rr.file_path, rr.content_sha256
            FROM ingestion_runs ir
            JOIN raw_reports rr ON rr.run_id = ir.run_id
            WHERE ir.status = 'success'
            ORDER BY ir.run_id
            """
        ).fetchall()
    finally:
        connection.close()

    seen: set[str] = set()
    candidates: list[sqlite3.Row] = []
    missing = 0
    for row in rows:
        if row["content_sha256"] in seen:
            continue
        seen.add(row["content_sha256"])
        if not Path(row["file_path"]).is_file():
            missing += 1
            continue
        candidates.append(row)

    print(f"Unique successful raw reports found: {len(seen)}")
    print(f"Readable reports ready to replay: {len(candidates)}")
    print(f"Missing raw files: {missing}")
    if args.dry_run:
        return 0 if missing == 0 else 2

    completed = 0
    for row in candidates:
        raw_path = Path(row["file_path"])
        ingest_content(
            settings,
            raw_path.read_bytes(),
            run_type="migration-replay",
            requested_from=_date(row["requested_from"]),
            requested_to=_date(row["requested_to"]),
        )
        completed += 1
        print(f"Replayed {completed}/{len(candidates)} reports")

    print("Replay complete. Run health_check.py and generate_daily_report.py next.")
    return 0 if missing == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
