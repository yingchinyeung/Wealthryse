from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.monthly_reports import REQUIRED_REPORT_TYPES, validate_period_end  # noqa: E402
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("Snapshot restoration is restricted to internal production")
    database_url = _required("DATABASE_URL")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")

    tenant_id = _required("PORTFOLIO_TENANT_ID")
    account_id = _required("PILOT_ACCOUNT_ID")
    period_end = validate_period_end(_required("REPAIR_PERIOD_END"))
    restore_id = _required("RESTORE_SNAPSHOT_ID")
    supersede_id = _required("SUPERSEDE_SNAPSHOT_ID")
    if restore_id == supersede_id:
        raise RuntimeError("Restore and supersede snapshot IDs must differ")

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        rows = connection.execute(
            """
            SELECT snapshot_id, status, data_sha256
            FROM reporting_snapshots
            WHERE tenant_id = ? AND subject_id = ? AND period_end = ?
              AND snapshot_id IN (?, ?)
            ORDER BY snapshot_id
            """,
            (tenant_id, account_id, period_end, restore_id, supersede_id),
        ).fetchall()
        snapshots = {str(row["snapshot_id"]): row for row in rows}
        if set(snapshots) != {restore_id, supersede_id}:
            raise RuntimeError("The exact two approved repair snapshots were not found")

        restore_status = str(snapshots[restore_id]["status"])
        supersede_status = str(snapshots[supersede_id]["status"])
        if restore_status == "frozen" and supersede_status == "superseded":
            print(canonical_json({"status": "already_repaired", "period_end": period_end}))
            return 0
        if (restore_status, supersede_status) != ("superseded", "frozen"):
            raise RuntimeError(
                "Snapshot statuses no longer match the approved repair preconditions"
            )

        runs = connection.execute(
            """
            SELECT DISTINCT ON (report_type)
                   report_type, status, output_reference, output_sha256
            FROM report_generation_runs
            WHERE tenant_id = ? AND snapshot_id = ?
              AND report_type IN ('factsheet', 'presentation')
            ORDER BY report_type, generation_run_id DESC
            """,
            (tenant_id, restore_id),
        ).fetchall()
        by_type = {str(row["report_type"]): row for row in runs}
        # A factsheet is the package; a deck is checked only where one was built before decks were retired.
        if not REQUIRED_REPORT_TYPES <= set(by_type):
            raise RuntimeError("The restored snapshot does not have a complete report package")
        for report_type in sorted(by_type):
            run = by_type[report_type]
            if str(run["status"]) != "awaiting_approval":
                raise RuntimeError(f"The {report_type} is not awaiting owner approval")
            if not run["output_reference"] or not run["output_sha256"]:
                raise RuntimeError(f"The {report_type} output metadata is incomplete")

        connection.execute(
            """
            UPDATE reporting_snapshots
            SET status = CASE
                WHEN snapshot_id = ? THEN 'frozen'
                WHEN snapshot_id = ? THEN 'superseded'
                ELSE status
            END
            WHERE tenant_id = ? AND subject_id = ? AND period_end = ?
              AND snapshot_id IN (?, ?)
            """,
            (
                restore_id,
                supersede_id,
                tenant_id,
                account_id,
                period_end,
                restore_id,
                supersede_id,
            ),
        )
        details = canonical_json(
            {
                "period_end": period_end,
                "restored_snapshot_id": restore_id,
                "restored_data_sha256": str(snapshots[restore_id]["data_sha256"]),
                "superseded_snapshot_id": supersede_id,
                "superseded_data_sha256": str(snapshots[supersede_id]["data_sha256"]),
                "reason": "remove_test_snapshot_and_restore_complete_review_package",
            }
        )
        connection.execute(
            """
            INSERT INTO audit_events(
                tenant_id, occurred_at, actor_type, actor_id, event_type,
                subject_id, outcome, details_json
            ) VALUES (?, ?, 'system', 'approved-snapshot-repair',
                      'month_end_snapshot_restored', ?, 'success', ?)
            """,
            (tenant_id, utc_now_iso(), account_id, details),
        )
        connection.commit()
        print(
            json.dumps(
                {
                    "status": "repaired",
                    "period_end": period_end,
                    "restored_snapshot_id": restore_id,
                    "superseded_snapshot_id": supersede_id,
                    "report_types": sorted(by_type),
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
