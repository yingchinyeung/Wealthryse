from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.agent_tools import AgentToolContext  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.portfolio_assurance import (  # noqa: E402
    AssuranceModelConfig,
    approved_report_versions,
    assurance_date,
    collect_assurance_inputs,
    completed_at,
    evaluate_portfolio_assurance,
    generate_assurance_review,
)
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").strip().lower() != "internal-production":
        raise RuntimeError("Portfolio-analyst assurance is restricted to internal production")
    database_url = _required("DATABASE_URL")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    subject_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    run_date = assurance_date()
    model_config = AssuranceModelConfig(
        endpoint=_required("AZURE_AI_ENDPOINT"),
        deployment=_required("AZURE_AI_DEPLOYMENT"),
        region=_required("AZURE_AI_REGION"),
        processing_scope=_required("AZURE_AI_PROCESSING_SCOPE"),
        managed_identity_client_id=_required("AZURE_CLIENT_ID"),
    )
    context = AgentToolContext(
        tenant_id=tenant_id,
        actor_object_id="portfolio-analyst-assurance-job",
        allowed_subject_ids=frozenset({subject_id}),
        correlation_id=str(uuid.uuid4()),
    )
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    started_at = utc_now_iso()
    try:
        tool_results, report_rows = collect_assurance_inputs(
            connection, context, subject_id=subject_id
        )
        deterministic = evaluate_portfolio_assurance(
            tool_results,
            report_rows,
            subject_id=subject_id,
            approved_versions=approved_report_versions(connection, tenant_id, subject_id),
        )
        model_review = generate_assurance_review(deterministic, model_config)
        result = {
            **deterministic,
            "model_review": model_review["review"],
            "correlation_id": context.correlation_id,
            "completed_at": completed_at(),
        }
        row = connection.execute(
            """
            INSERT INTO portfolio_analyst_assurance_runs(
                subject_id, assurance_date, as_of_date, policy_version,
                started_at, completed_at, status, checks_passed, checks_warned,
                checks_failed, result_json, model_deployment,
                model_request_sha256, model_response_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?, ?)
            ON CONFLICT (tenant_id, subject_id, assurance_date) DO UPDATE SET
                as_of_date = excluded.as_of_date,
                policy_version = excluded.policy_version,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at,
                status = excluded.status,
                checks_passed = excluded.checks_passed,
                checks_warned = excluded.checks_warned,
                checks_failed = excluded.checks_failed,
                result_json = excluded.result_json,
                model_deployment = excluded.model_deployment,
                model_request_sha256 = excluded.model_request_sha256,
                model_response_sha256 = excluded.model_response_sha256
            RETURNING assurance_run_id
            """,
            (
                subject_id,
                run_date,
                deterministic.get("as_of"),
                deterministic["policy_version"],
                started_at,
                result["completed_at"],
                deterministic["status"],
                deterministic["checks_passed"],
                deterministic["checks_warned"],
                deterministic["checks_failed"],
                canonical_json(result),
                model_review["model_deployment"],
                model_review["request_sha256"],
                model_review["response_sha256"],
            ),
        ).fetchone()
        run_id = int(row["assurance_run_id"])
        connection.execute(
            "DELETE FROM portfolio_analyst_assurance_findings WHERE assurance_run_id = ?",
            (run_id,),
        )
        for finding in deterministic["findings"]:
            connection.execute(
                """
                INSERT INTO portfolio_analyst_assurance_findings(
                    assurance_run_id, check_id, severity, message, evidence_json
                ) VALUES (?, ?, ?, ?, ?::jsonb)
                """,
                (
                    run_id,
                    finding["check_id"],
                    finding["severity"],
                    finding["message"],
                    canonical_json(finding.get("evidence") or {}),
                ),
            )
        connection.commit()
        print(
            json.dumps(
                {
                    "status": deterministic["status"].upper(),
                    "assurance_run_id": run_id,
                    "assurance_date": run_date,
                    "as_of": deterministic.get("as_of"),
                    "checks_passed": deterministic["checks_passed"],
                    "checks_warned": deterministic["checks_warned"],
                    "checks_failed": deterministic["checks_failed"],
                    "findings": [
                        {
                            "check_id": finding["check_id"],
                            "severity": finding["severity"],
                            "message": finding["message"],
                        }
                        for finding in deterministic["findings"]
                    ],
                    "model_deployment": model_review["model_deployment"],
                },
                sort_keys=True,
            )
        )
        return 1 if deterministic["status"] == "fail" else 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
