from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.burn_in import burn_in_completion_date, now_hkt_date  # noqa: E402
from ibkr_etl.util import utc_now_iso  # noqa: E402


def main() -> int:
    import psycopg

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL is required")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    subject_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    start_date = now_hkt_date()
    earliest = burn_in_completion_date(start_date)
    started_at = utc_now_iso()
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        existing = connection.execute(
            """
            SELECT window_id, subject_id, start_date, earliest_completion_date, status
            FROM burn_in_acceptance_windows
            WHERE subject_id = %s AND status = 'running'
            """,
            (subject_id,),
        ).fetchone()
        if existing:
            print(json.dumps({"status": "ALREADY_RUNNING", **dict(existing)}, sort_keys=True))
            return 0
        row = connection.execute(
            """
            INSERT INTO burn_in_acceptance_windows(
                subject_id, start_date, earliest_completion_date, status,
                started_at, started_by
            ) VALUES (%s, %s, %s, 'running', %s, 'owner-approved-stage-4')
            RETURNING window_id, subject_id, start_date, earliest_completion_date, status
            """,
            (subject_id, start_date.isoformat(), earliest.isoformat(), started_at),
        ).fetchone()
        connection.commit()
    print(json.dumps({"status": "STARTED", **dict(row)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
