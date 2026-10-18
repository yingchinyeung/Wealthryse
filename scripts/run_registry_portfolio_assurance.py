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

from ibkr_etl import input_readiness  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402


def main() -> int:
    input_readiness.gate("analyst-assurance")
    database_url = os.environ.get("DATABASE_URL", "").strip()
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        rows = connection.execute(
            """
            SELECT r.subject_id
            FROM subject_operating_registrations r
            JOIN platform_subjects s
              ON s.tenant_id = r.tenant_id AND s.subject_id = r.subject_id
            WHERE r.capability = 'analyst_assurance'
              AND r.operating_state = 'active' AND r.effective_to IS NULL
              AND s.lifecycle_status = 'active'
            ORDER BY r.subject_id
            """
        ).fetchall()
    finally:
        connection.close()
    results: list[dict[str, str]] = []
    failures: list[str] = []
    for row in rows:
        subject_id = str(row["subject_id"])
        environment = dict(os.environ)
        environment["PILOT_ACCOUNT_ID"] = subject_id
        completed = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "run_portfolio_analyst_assurance.py")],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            timeout=600,
        )
        status = "pass" if completed.returncode == 0 else "fail"
        results.append({"subject_id": subject_id, "status": status})
        if completed.returncode:
            failures.append(subject_id)
    print(json.dumps({"status": "fail" if failures else "success", "subjects": results}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
