from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import input_readiness, publish_gate  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.onboarding_agents import agents_enabled, run_reviews  # noqa: E402
from ibkr_etl.onboarding_month_end import live_cases  # noqa: E402


def record_agent_reviews(database_url: str, tenant_id: str) -> dict:
    """Stage 3's agent_review_complete check on composite cases, from the history reviewer's reviews.

    Runs after the composite reports, so a re-render puts the check back to
    pending at once. Does nothing while ONBOARDING_AGENTS_ENABLED is off and
    never changes the month-end packages' result.
    """

    if not agents_enabled():
        return {"status": "switched_off"}
    try:
        connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    except Exception as failure:
        return {"status": "failed", "error_type": type(failure).__name__, "error": str(failure)[:400]}
    try:
        return run_reviews(connection, tenant_id)
    except Exception as failure:
        connection.rollback()
        return {"status": "failed", "error_type": type(failure).__name__, "error": str(failure)[:400]}
    finally:
        connection.close()


def main() -> int:
    input_readiness.gate("month-end-factsheet")
    started = datetime.now(timezone.utc)  # the publish gate judges snapshots written from here
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
        # Subjects that went live from a new-onboarding case have no wizard presentation
        # identity or registered templates; render_onboarding_composite_reports.py renders them.
        live_subjects = {case["target_subject_id"] for case in live_cases(connection, tenant_id)}
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
        if str(row["subject_id"]) in live_subjects:
            results.append({"subject_id": str(row["subject_id"]), "status": "onboarding_month_end"})
            continue
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
    # Stage 3 reports for new-onboarding cases (each case records its own outcome),
    # and the month-end of subjects that went live from one (exit 3 when one failed).
    try:
        subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "render_onboarding_composite_reports.py")],
            cwd=PROJECT_ROOT,
            env=dict(os.environ),
            check=True,
            timeout=1800,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"onboarding_composite_reports": "failed", "reason": str(error)}, sort_keys=True))
        if live_subjects:
            failed = True
    # Judge the snapshots this run wrote and log one line (off by default; never stops the packages).
    publish_gate.report("month-end-factsheet", since=started)
    print(json.dumps({"status": "partial_failure" if failed else "success", "subjects": results}, sort_keys=True))
    print(json.dumps({"onboarding_agent_reviews": record_agent_reviews(database_url, tenant_id)}, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
