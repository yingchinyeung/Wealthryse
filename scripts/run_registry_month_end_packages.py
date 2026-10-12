from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import connect  # noqa: E402


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        missing = connection.execute(
            """
            SELECT s.subject_id
            FROM platform_subjects s
            WHERE s.lifecycle_status = 'active'
              AND s.subject_type <> 'master_account'
              AND NOT EXISTS (
                  SELECT 1 FROM subject_operating_registrations r
                  WHERE r.tenant_id = s.tenant_id
                    AND r.subject_id = s.subject_id
                    AND r.capability = 'month_end_reporting'
                    AND r.operating_state = 'active' AND r.effective_to IS NULL
              )
            ORDER BY s.subject_id
            """
        ).fetchall()
        missing_subjects = [str(row["subject_id"]) for row in missing]
        rows = connection.execute(
            """
            SELECT r.subject_id, s.history_start_date
            FROM subject_operating_registrations r
            JOIN platform_subjects s
              ON s.tenant_id = r.tenant_id AND s.subject_id = r.subject_id
            WHERE r.capability = 'month_end_reporting'
              AND r.operating_state = 'active' AND r.effective_to IS NULL
              AND s.lifecycle_status = 'active'
            ORDER BY r.subject_id
            """
        ).fetchall()
    finally:
        connection.close()
    results: list[dict[str, str]] = [
        {
            "subject_id": subject_id,
            "status": "failed",
            "reason": "active subject lacks month-end reporting registration",
        }
        for subject_id in missing_subjects
    ]
    failed = bool(missing_subjects)
    for row in rows:
        environment = dict(os.environ)
        environment.update(
            {
                "PILOT_ACCOUNT_ID": str(row["subject_id"]),
                "REPORTING_PERIOD_START": str(row["history_start_date"]),
            }
        )
        if str(row["subject_id"]) != "U16088571":
            environment.pop("FACTSHEET_TEMPLATE_BLOB", None)
            environment.pop("FACTSHEET_TEMPLATE_SHA256", None)
        try:
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "generate_month_end_package.py")],
                cwd=PROJECT_ROOT,
                env=environment,
                check=True,
                timeout=1200,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            failed = True
            results.append({"subject_id": str(row["subject_id"]), "status": "failed", "reason": str(error)})
        else:
            results.append({"subject_id": str(row["subject_id"]), "status": "processed"})
    print(json.dumps({"status": "partial_failure" if failed else "success", "subjects": results}, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
