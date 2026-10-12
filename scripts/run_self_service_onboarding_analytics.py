from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.crisis_replay import (  # noqa: E402
    refresh_composite_crisis_replay,
    refresh_crisis_replay,
)
from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402
from ibkr_etl.provisional_benchmark import refresh_and_materialize  # noqa: E402
from ibkr_etl.risk_snapshots import build_risk_snapshot  # noqa: E402
from ibkr_etl.sector_classification import refresh_sector_classifications_range  # noqa: E402
from ibkr_etl.util import canonical_json  # noqa: E402


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be PostgreSQL")
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    results: list[dict] = []
    try:
        subjects = connection.execute(
            """
            SELECT s.subject_id, s.subject_type, s.history_start_date
            FROM platform_subjects s
            JOIN account_onboarding_cases c
              ON c.tenant_id = s.tenant_id AND c.target_subject_id = s.subject_id
            JOIN account_onboarding_approvals a
              ON a.tenant_id = s.tenant_id AND a.subject_id = s.subject_id
             AND a.approval_type = 'external_market_data'
             AND a.approval_status = 'approved' AND a.revoked_at IS NULL
            WHERE s.lifecycle_status IN ('onboarding', 'preview')
              AND c.status NOT IN ('cancelled', 'active')
              AND EXISTS (
                  SELECT 1 FROM subject_nav_series n WHERE n.subject_id = s.subject_id
              )
            ORDER BY c.created_at
            """
        ).fetchall()
        for subject in subjects:
            subject_id = str(subject["subject_id"])
            subject_type = str(subject["subject_type"])
            start_date = date.fromisoformat(str(subject["history_start_date"]))
            if subject_id == 'alpha-world-t':
                # T must never reach the generic USD crisis/analytics pipeline.
                from ibkr_etl.alpha_world_t_onboarding import refresh_onboarding_analytics
                result = refresh_onboarding_analytics(connection,tenant_id=tenant_id)
                connection.commit()
                results.append({'subject_id':subject_id,'status':'pass',**result})
                continue
            benchmark = refresh_and_materialize(
                connection, account_id=subject_id, start_date=start_date
            )
            if subject_type == "composite_portfolio":
                crisis = refresh_composite_crisis_replay(
                    connection, account_id=subject_id
                )
                classification = {
                    "status": "not_applicable",
                    "classification_basis": "approved composite sleeve types",
                    "unclassified": 0,
                }
            else:
                crisis = refresh_crisis_replay(connection, account_id=subject_id)
                classification = refresh_sector_classifications_range(
                    connection,
                    subject_id=subject_id,
                    start_date=start_date.isoformat(),
                    end_date=str(crisis["holdings_as_of"]),
                    workers=int(os.environ.get("SECTOR_CLASSIFICATION_WORKERS", "4")),
                )
            failed_benchmarks = [
                row.get("benchmark_id")
                for row in benchmark.get("benchmarks", [])
                if row.get("quality_status") == "fail"
            ]
            passed = bool(
                int(benchmark.get("comparison_rows") or 0) >= 30
                and not failed_benchmarks
                and int(crisis.get("observations") or 0) > 0
                and int(classification.get("unclassified") or 0) == 0
            )
            evidence = {
                "benchmark": benchmark,
                "crisis_replay": crisis,
                "classification": classification,
            }
            connection.execute(
                """
                UPDATE account_onboarding_checks
                SET status = ?, message = ?, evidence_json = ?::jsonb,
                    checked_at = now()
                WHERE case_id = (
                    SELECT case_id FROM account_onboarding_cases
                    WHERE target_subject_id = ? AND status NOT IN ('cancelled', 'active')
                ) AND check_id = 'risk_analytics_validated'
                """,
                (
                    "pass" if passed else "fail",
                    (
                        "Benchmark, currency, classification and crisis-risk inputs passed deterministic validation."
                        if passed
                        else "One or more deterministic analytics inputs failed validation."
                    ),
                    canonical_json(evidence),
                    subject_id,
                ),
            )
            connection.commit()
            if not passed:
                raise RuntimeError(f"Analytics validation failed for {subject_id}")
            latest = connection.execute(
                """
                SELECT MAX(snapshot_date) AS latest FROM subject_nav_series
                WHERE tenant_id = ? AND subject_id = ?
                """,
                (tenant_id, subject_id),
            ).fetchone()["latest"]
            risk_snapshot = (
                build_risk_snapshot(
                    connection,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    as_of=str(latest),
                )
                if latest
                else None
            )
            results.append(
                {
                    "subject_id": subject_id,
                    "status": "pass",
                    "risk_snapshot": risk_snapshot,
                }
            )
        print(canonical_json({"status": "success", "subjects": results}))
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
