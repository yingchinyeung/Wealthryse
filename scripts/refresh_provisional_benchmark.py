from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import (  # noqa: E402
    connect,
    finish_run_failure,
    finish_run_success,
    start_run,
)
from ibkr_etl import input_readiness  # noqa: E402
from ibkr_etl.provisional_benchmark import refresh_and_materialize  # noqa: E402
from ibkr_etl.crisis_replay import (  # noqa: E402
    refresh_composite_crisis_replay,
    refresh_crisis_replay,
)
from ibkr_etl import crisis_scenarios, lineage, raw_capture  # noqa: E402
from ibkr_etl.util import canonical_json, sha256_text  # noqa: E402


def _external_market_data_approved(connection, account_id: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM account_onboarding_approvals
        WHERE subject_id = ? AND approval_type = 'external_market_data'
          AND approval_status = 'approved' AND revoked_at IS NULL
          AND disclosure_version = 'yahoo-ticker-disclosure-v1'
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    return row is not None


def _crisis_scenario_subjects(connection) -> tuple[list[str], list[str]]:
    """Active subjects whose owner approved sending holdings symbols to Yahoo."""

    rows = connection.execute(
        """
        SELECT DISTINCT s.subject_id, s.subject_type
        FROM platform_subjects s
        JOIN account_onboarding_approvals a ON a.subject_id = s.subject_id
        WHERE s.lifecycle_status = 'active'
          AND a.approval_type = 'external_market_data'
          AND a.approval_status = 'approved' AND a.revoked_at IS NULL
          AND a.disclosure_version = 'yahoo-ticker-disclosure-v1'
        ORDER BY s.subject_id
        """
    ).fetchall()
    accounts = [str(r["subject_id"]) for r in rows
                if r["subject_type"] in {"physical_account", "filtered_portfolio"}]
    composites = [str(r["subject_id"]) for r in rows if r["subject_type"] == "composite_portfolio"]
    return accounts, composites


def _refresh_crisis_scenarios(connection) -> dict:
    """The extra crisis scenarios; a failure here never fails the benchmark job."""

    if not crisis_scenarios.website_enabled():
        return {"status": "disabled"}
    try:
        accounts, composites = _crisis_scenario_subjects(connection)
        return crisis_scenarios.refresh(connection, accounts=accounts, composites=composites)
    except Exception as error:  # noqa: BLE001
        connection.rollback()
        return {"status": "failed", "error": " ".join(str(error).split())[:300]}


def _mark_wave0_benchmark_fx_reconciled(connection, result: dict) -> None:
    if result.get("account_id") != "U16088606":
        return
    benchmarks = list(result.get("benchmarks", []))
    expected = {"TAIEX", "SHCOMP", "HSI"}
    received = {str(item.get("benchmark_id")) for item in benchmarks}
    if received != expected:
        raise RuntimeError("Wave 0 benchmark reconciliation did not cover the approved set")
    failed = [
        str(item.get("benchmark_id"))
        for item in benchmarks
        if item.get("quality_status") == "fail"
    ]
    if any(
        item.get("native_currency") != "USD" and not item.get("fx_symbol")
        for item in benchmarks
    ):
        raise RuntimeError("Wave 0 benchmark reconciliation is missing an FX series")
    evidence = {
        "provider": result.get("provider"),
        "reporting_currency": result.get("reporting_currency"),
        "requested_end_date": result.get("requested_end_date"),
        "benchmark_observations": result.get("benchmark_observations"),
        "comparison_rows": result.get("comparison_rows"),
        "benchmarks": [
            {
                key: item.get(key)
                for key in (
                    "benchmark_id",
                    "symbol",
                    "native_currency",
                    "reporting_currency",
                    "fx_symbol",
                    "fx_observations_used",
                    "maximum_fx_age_days",
                    "comparison_rows",
                    "quality_status",
                )
            }
            for item in benchmarks
        ],
    }
    if failed:
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'warn',
                message = ?,
                evidence_json = ?::jsonb,
                checked_at = now()
            WHERE case_id = (
                SELECT case_id FROM account_onboarding_cases
                WHERE source_system = 'existing_v11' AND source_account_id = 'U16088606'
            ) AND check_id = 'benchmark_fx_reconciled'
            """,
            (
                "Benchmark and FX reconciliation remains incomplete for: "
                + ", ".join(failed),
                canonical_json(evidence),
            ),
        )
        return
    connection.execute(
        """
        UPDATE account_onboarding_checks
        SET status = 'pass',
            message = 'All approved native-currency benchmarks and USD FX conversions passed reconciliation.',
            evidence_json = ?::jsonb,
            checked_at = now()
        WHERE case_id = (
            SELECT case_id FROM account_onboarding_cases
            WHERE source_system = 'existing_v11' AND source_account_id = 'U16088606'
        ) AND check_id = 'benchmark_fx_reconciled'
        """,
        (canonical_json(evidence),),
    )
    connection.execute(
        """
        UPDATE account_onboarding_cases
        SET stage = 'template_intake', status = 'in_progress',
            configuration_json = configuration_json || ?::jsonb,
            updated_at = now()
        WHERE source_system = 'existing_v11' AND source_account_id = 'U16088606'
          AND stage = 'analytics_configuration'
        """,
        (canonical_json({"benchmark_fx_reconciliation": evidence}),),
    )


def _record_outputs(result: dict) -> None:
    """What this run wrote, for its lineage record (a no-op unless recording is on)."""
    record = lineage.current()
    if not record.active:
        return
    try:
        accounts = result.get("accounts") or []
        record.output("silver.benchmark_prices", rows=result.get("benchmark_observations"))
        record.output("silver.reference_fx_rates")
        record.output("gold.performance_comparison_daily",
                      rows=sum(int(item.get("comparison_rows") or 0) for item in accounts))
        if any(item.get("crisis_replay") for item in accounts):
            record.output("gold.crisis_replay_daily", rows=result.get("crisis_replay_observations"))
            record.output("gold.crisis_replay_components")
            record.output("silver.market_data_prices")
        if (result.get("crisis_scenarios") or {}).get("subjects"):
            record.output("gold.crisis_scenario_results")
    except Exception:  # noqa: BLE001 - lineage never fails the job
        pass


def main() -> int:
    input_readiness.gate("benchmark-daily")
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() not in {
        "staging",
        "internal-production",
    }:
        raise RuntimeError(
            "The provisional yfinance benchmark job may run only in staging or internal production"
        )
    if os.environ.get("ALLOW_UNLICENSED_BENCHMARK", "").lower() not in {
        "1", "true", "yes", "on"
    }:
        raise RuntimeError("Explicit internal-use yfinance benchmark opt-in is absent")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    start = date.fromisoformat(os.environ.get("BENCHMARK_START_DATE", "2025-01-20"))
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    # With lineage recording on, the gold rows this session writes name this run.
    lineage.current().bind(connection)
    configured_ids = os.environ.get("BENCHMARK_ACCOUNT_IDS", "").strip()
    if configured_ids:
        account_ids = [
            value.strip().upper()
            for value in configured_ids.split(",")
            if value.strip()
        ]
    else:
        account_ids = [
            str(row["subject_id"])
            for row in connection.execute(
                """
                SELECT r.subject_id
                FROM subject_operating_registrations r
                JOIN platform_subjects s
                  ON s.tenant_id = r.tenant_id AND s.subject_id = r.subject_id
                WHERE r.capability = 'benchmark_refresh'
                  AND r.operating_state = 'active' AND r.effective_to IS NULL
                  AND s.lifecycle_status = 'active'
                ORDER BY r.subject_id
                """
            ).fetchall()
        ]
    if len(account_ids) != len(set(account_ids)):
        raise RuntimeError("BENCHMARK_ACCOUNT_IDS contains a duplicate account")
    run_id = start_run(
        connection, "staging-yfinance-benchmark", start.isoformat(), None
    )
    raw_capture.set_job_run_id(run_id)
    lineage.current().facet(job_run_id=run_id)
    try:
        accounts: list[dict] = []
        for account_id in account_ids:
            subject = connection.execute(
                "SELECT subject_type FROM platform_subjects WHERE subject_id = ?",
                (account_id,),
            ).fetchone()
            result = refresh_and_materialize(
                connection, account_id=account_id, start_date=start
            )
            _mark_wave0_benchmark_fx_reconciled(connection, result)
            # Crisis-period holdings and symbols may leave Azure only after the
            # owner has approved the exact disclosure in the onboarding wizard.
            if _external_market_data_approved(connection, account_id) and subject is not None:
                subject_type = str(subject["subject_type"])
                if subject_type in {"physical_account", "filtered_portfolio"}:
                    result["crisis_replay"] = refresh_crisis_replay(
                        connection, account_id=account_id
                    )
                elif subject_type == "composite_portfolio":
                    result["crisis_replay"] = refresh_composite_crisis_replay(
                        connection, account_id=account_id
                    )
            accounts.append(result)
        scenarios = _refresh_crisis_scenarios(connection)
        result = {
            "status": "success",
            "accounts": accounts,
            "account_ids": account_ids,
            "benchmark_observations": sum(
                int(item["benchmark_observations"]) for item in accounts
            ),
            "crisis_replay_observations": sum(
                int(item.get("crisis_replay", {}).get("observations", 0))
                for item in accounts
            ),
            "crisis_scenarios": scenarios,
        }
        payload = canonical_json(result)
        finish_run_success(
            connection,
            run_id,
            reference_code=",".join(account_ids),
            raw_sha256=sha256_text(payload),
            raw_path="yfinance://contract-driven-benchmarks-and-fx",
            row_count=int(result["benchmark_observations"])
            + int(result["crisis_replay_observations"]),
            reconciliation_json=payload,
        )
        _record_outputs(result)
        print(payload)
        return 0
    except Exception as error:
        finish_run_failure(connection, run_id, str(error))
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(lineage.run_main("ibkrv2stg-benchmark-daily", main))
