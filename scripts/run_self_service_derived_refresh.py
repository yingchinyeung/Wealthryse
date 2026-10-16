from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import download_blob_snapshot  # noqa: E402
from ibkr_etl.reference_snapshot import inspect_reference_bytes  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.crisis_replay import (  # noqa: E402
    refresh_composite_crisis_replay,
    refresh_crisis_replay,
)
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.derived_portfolio import (  # noqa: E402
    refresh_filtered_portfolio,
    refresh_fixed_unit_composite,
)
from ibkr_etl.market_data import refresh_alpha_world_market_data  # noqa: E402
from ibkr_etl.onboarding_agents import run_reconciliations  # noqa: E402
from ibkr_etl.onboarding_dry_run import record_history_build, run_portfolio_dry_runs  # noqa: E402
from ibkr_etl.profiled_composite import is_profiled  # noqa: E402
from ibkr_etl.provisional_benchmark import refresh_and_materialize  # noqa: E402
from ibkr_etl.reference_data_source import (  # noqa: E402
    BLOB as REFERENCE_FROM_BLOB,
    DATABASE as REFERENCE_FROM_DATABASE,
    materialize_database_reference,
    reference_data_source,
)
from ibkr_etl.risk_snapshots import build_risk_snapshot  # noqa: E402
from ibkr_etl.sector_classification import (  # noqa: E402
    refresh_sector_classifications_range,
)
from ibkr_etl.subject_onboarding import (  # noqa: E402
    validate_typed_runtime_contract,
)
from ibkr_etl.util import canonical_json  # noqa: E402


def _reference_sources(settings) -> list[tuple[str, str, str]]:
    """Where the published file is looked for, in order."""

    return [
        (
            settings.azure_storage_account_url,
            settings.azure_report_container,
            "website",
        ),
        (
            os.environ.get("REFERENCE_STORAGE_ACCOUNT_URL", "").strip(),
            os.environ.get("REFERENCE_STORAGE_CONTAINER", "analytics-reports").strip(),
            "legacy-production",
        ),
    ]


def _download_reference_data(settings, destination: Path) -> dict:
    """Load the governed reference cache, preferring the website archive.

    The current production reference artifact is still published by v1.1.  The
    explicit read-only fallback keeps the derived migration reproducible until
    the institutional importer publishes the same artifact into v2 storage.
    """

    blob_name = f"{settings.tenant_id}/reference/reference_data.json"
    for account_url, container, source in _reference_sources(settings):
        if not account_url or not container:
            continue
        snapshot = download_blob_snapshot(account_url, container, blob_name)
        if snapshot is not None:
            content, storage = snapshot
            _, manifest = inspect_reference_bytes(content, expected_sha256=storage["content_sha256"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            return {
                "source": source,
                "storage": storage,
                **manifest,
            }
    raise RuntimeError("Published reference_data.json is missing")


def _load_reference_input(settings, connection, destination: Path, source: str = REFERENCE_FROM_BLOB) -> dict:
    """The published file by default; v2's own tables when REFERENCE_DATA_SOURCE=database."""

    if source == REFERENCE_FROM_DATABASE:
        return materialize_database_reference(connection, settings.tenant_id, destination)
    return _download_reference_data(settings, destination)


def _approved_targets(connection) -> list[dict]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT d.definition_id, d.subject_id, d.effective_from,
                   d.configuration_json, d.configuration_sha256,
                   s.subject_type, s.lifecycle_status
            FROM subject_calculation_definitions d
            JOIN platform_subjects s
              ON s.tenant_id = d.tenant_id AND s.subject_id = d.subject_id
            WHERE d.approval_status = 'approved' AND d.effective_to IS NULL
              AND s.lifecycle_status IN ('onboarding', 'preview', 'active')
            ORDER BY CASE d.subject_id
                WHEN 'hong-kong-china' THEN 1
                WHEN 'alpha-world-ms' THEN 2
                WHEN 'alpha-world-t' THEN 3
                ELSE 99 END
            """
        )
    ]


def _ordered_targets(rows: list[dict]) -> list[dict]:
    """Place dependent derived portfolios after their derived sleeves."""

    by_id = {str(row["subject_id"]): row for row in rows}
    remaining = set(by_id)
    ordered: list[dict] = []
    while remaining:
        ready: list[str] = []
        for subject_id in sorted(remaining):
            value = by_id[subject_id]["configuration_json"]
            definition = json.loads(value) if isinstance(value, str) else dict(value)
            parents = {
                str(component["component_id"])
                for component in definition.get("components", [])
                if str(component.get("component_type")) == "portfolio"
            }
            if not parents.intersection(remaining):
                ready.append(subject_id)
        if not ready:
            raise RuntimeError("Approved derived definitions contain a dependency cycle")
        for subject_id in ready:
            ordered.append(by_id[subject_id])
            remaining.remove(subject_id)
    return ordered


def _start_run(connection, row: dict, requested_to: str, reference_source=None) -> str:
    run_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO subject_calculation_runs(
            calculation_run_id, subject_id, definition_id,
            requested_from, requested_to, status, input_watermark_json,
            started_at, created_at
        ) VALUES (?, ?, ?, ?, ?, 'running', ?::jsonb, now(), now())
        """,
        (
            run_id,
            row["subject_id"],
            row["definition_id"],
            str(row["effective_from"]),
            requested_to,
            canonical_json(
                {
                    "definition_sha256": row["configuration_sha256"],
                    "requested_to": requested_to,
                    "reference_input": reference_source,
                }
            ),
        ),
    )
    return run_id


def _validate_and_complete(
    connection,
    row: dict,
    run_id: str,
    result: dict,
    runtime_contract: dict,
) -> dict:
    coverage = connection.execute(
        """
        SELECT MIN(snapshot_date) AS first_date,
               MAX(snapshot_date) AS latest_date,
               COUNT(DISTINCT snapshot_date) AS observations
        FROM derived_portfolio_nav WHERE portfolio_id = ?
        """,
        (row["subject_id"],),
    ).fetchone()
    position_count = connection.execute(
        "SELECT COUNT(*) AS count FROM derived_portfolio_positions WHERE portfolio_id = ?",
        (row["subject_id"],),
    ).fetchone()["count"]
    start = str(row["effective_from"])
    valid = bool(
        coverage
        and coverage["first_date"]
        and str(coverage["first_date"]) <= start <= str(coverage["latest_date"])
        and int(coverage["observations"] or 0) > 0
        and (
            row["subject_type"] == "composite_portfolio"
            or int(position_count or 0) > 0
        )
    )
    evidence = {
        "state": "completed",
        "calculation_run_id": run_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "definition_sha256": row["configuration_sha256"],
        "runtime_contract": runtime_contract,
        "coverage": dict(coverage),
        "position_rows": int(position_count or 0),
        "calculation_result": result,
    }
    connection.execute(
        """
        UPDATE subject_calculation_runs
        SET status = ?, result_json = ?::jsonb,
            completed_at = now(), error_code = NULL
        WHERE calculation_run_id = ?
        """,
        ("validated" if valid else "blocked", canonical_json(evidence), run_id),
    )
    connection.execute(
        """
        UPDATE account_onboarding_checks
        SET status = ?, message = ?, evidence_json = ?::jsonb, checked_at = now()
        WHERE case_id = (
            SELECT case_id FROM account_onboarding_cases
            WHERE target_subject_id = ? AND status NOT IN ('cancelled')
            ORDER BY updated_at DESC LIMIT 1
        ) AND check_id = 'data_import_reconciled'
        """,
        (
            "pass" if valid else "fail",
            (
                "The full derived history covers the approved start date and passed deterministic reconciliation."
                if valid
                else "The derived calculation did not produce complete validated history."
            ),
            canonical_json(evidence),
            row["subject_id"],
        ),
    )
    if not valid:
        raise RuntimeError(f"Derived history validation failed for {row['subject_id']}")
    return evidence


def _refresh_analytics_if_approved(
    connection, row: dict, *, tenant_id: str
) -> dict | None:
    if row['subject_id']=='alpha-world-t':
        # Validate T's exact contract and source-bound crisis, without promoting
        # onboarding approvals or calling the legacy USD/composite path.
        from ibkr_etl.alpha_world_t_analytics import require_contract
        require_contract(connection,tenant_id=tenant_id)
        latest=connection.execute("SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id=? AND subject_id='alpha-world-t' AND currency='HKD'",(tenant_id,)).fetchone()['latest']
        from ibkr_etl.derived_benchmark_refresh import refresh_governed_derived_benchmarks
        benchmark={'available':False,'reason':'no validated NAV'} if latest is None else refresh_governed_derived_benchmarks(connection,tenant_id=tenant_id,subject_id='alpha-world-t',as_of=str(latest))
        return {'risk_snapshot':None if latest is None else build_risk_snapshot(connection,
            tenant_id=tenant_id,subject_id='alpha-world-t',as_of=str(latest)),
            'benchmark_comparison':benchmark,
            'onboarding_activated':False,'acceptance_check_changed':False}
    if row['subject_id']=='alpha-world-ms':
        # Core HKD/HIBOR metrics are qualified independently. Do not fabricate
        # benchmark/crisis acceptance or promote the paused onboarding check.
        from ibkr_etl.subject_read_contract import require_ms_hkd_contract
        require_ms_hkd_contract(connection,tenant_id=tenant_id)
        latest=connection.execute("SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id=? AND subject_id='alpha-world-ms' AND currency='HKD'",(tenant_id,)).fetchone()['latest']
        from ibkr_etl.derived_benchmark_refresh import refresh_governed_derived_benchmarks
        benchmark={'available':False,'reason':'no validated NAV'} if latest is None else refresh_governed_derived_benchmarks(connection,tenant_id=tenant_id,subject_id='alpha-world-ms',as_of=str(latest))
        return {'risk_snapshot':None if latest is None else build_risk_snapshot(connection,
            tenant_id=tenant_id,subject_id='alpha-world-ms',as_of=str(latest)),
            'benchmark_comparison':benchmark,
            'crisis_replay':{'available':False,'reason':'Composite crisis attribution not qualified'},
            'onboarding_activated':False,'acceptance_check_changed':False}
    if row.get('subject_type')=='composite_portfolio':
        from ibkr_etl.profiled_composite import require_contract as require_design_contract
        if is_profiled(connection,row['subject_id'],tenant_id=tenant_id):
            # A composite in the Alpha World MS or T design is measured as MS
            # and T are: its own HKD/HIBOR contract, never the USD comparison.
            require_design_contract(connection,row['subject_id'],tenant_id=tenant_id)
            latest=connection.execute("SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id=? AND subject_id=? AND currency='HKD'",(tenant_id,row['subject_id'])).fetchone()['latest']
            return {'risk_snapshot':None if latest is None else build_risk_snapshot(connection,
                tenant_id=tenant_id,subject_id=row['subject_id'],as_of=str(latest)),
                'benchmark_comparison':{'available':False,'reason':'HIBOR + Spread comparison methodology is not qualified'},
                'crisis_replay':{'available':False,'reason':'Composite crisis attribution not qualified'},
                'onboarding_activated':False,'acceptance_check_changed':False}
    approved = connection.execute(
        """
        SELECT 1 FROM account_onboarding_approvals
        WHERE subject_id = ? AND approval_type = 'external_market_data'
          AND approval_status = 'approved' AND revoked_at IS NULL
        LIMIT 1
        """,
        (row["subject_id"],),
    ).fetchone()
    mapping = connection.execute(
        """
        SELECT 1 FROM portfolio_benchmark_mapping
        WHERE subject_id = ? AND effective_to IS NULL LIMIT 1
        """,
        (row["subject_id"],),
    ).fetchone()
    if approved is None or mapping is None:
        return None
    benchmark = refresh_and_materialize(
        connection,
        account_id=row["subject_id"],
        start_date=row["effective_from"],
    )
    if row["subject_type"] == "composite_portfolio":
        crisis = refresh_composite_crisis_replay(
            connection, account_id=row["subject_id"]
        )
        classification = {
            "status": "not_applicable",
            "classification_basis": "approved composite sleeve types",
            "unclassified": 0,
        }
    else:
        crisis = refresh_crisis_replay(connection, account_id=row["subject_id"])
        classification = refresh_sector_classifications_range(
            connection,
            subject_id=row["subject_id"],
            start_date=str(row["effective_from"]),
            end_date=str(crisis["holdings_as_of"]),
            workers=int(os.environ.get("SECTOR_CLASSIFICATION_WORKERS", "4")),
        )
    failed = [
        item["benchmark_id"]
        for item in benchmark.get("benchmarks", [])
        if item.get("quality_status") == "fail"
    ]
    comparison_rows = int(benchmark.get("comparison_rows") or 0)
    passed = bool(
        comparison_rows >= 30
        and not failed
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
        SET status = ?, message = ?, evidence_json = ?::jsonb, checked_at = now()
        WHERE case_id = (
            SELECT case_id FROM account_onboarding_cases
            WHERE target_subject_id = ? AND status NOT IN ('cancelled')
            ORDER BY updated_at DESC LIMIT 1
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
            row["subject_id"],
        ),
    )
    if not passed:
        raise RuntimeError(
            f"Deterministic analytics validation failed for {row['subject_id']}"
        )
    latest = connection.execute(
        """
        SELECT MAX(snapshot_date) AS latest FROM subject_nav_series
        WHERE tenant_id = ? AND subject_id = ?
        """,
        (tenant_id, row["subject_id"]),
    ).fetchone()["latest"]
    if latest:
        evidence["risk_snapshot"] = build_risk_snapshot(
            connection,
            tenant_id=tenant_id,
            subject_id=str(row["subject_id"]),
            as_of=str(latest),
        )
    return evidence


def _has_onboarding_cases(connection) -> bool:
    """The new onboarding's tables exist once migration 064 is applied."""

    return connection.execute(
        "SELECT to_regclass('public.broker_onboarding_cases')"
    ).fetchone()[0] is not None


def _record_history_build(connection, tenant_id: str, subject_id: str, error: str | None) -> dict | None:
    """Stage 3's history check on a new-onboarding portfolio case; never fails the refresh."""

    try:
        return record_history_build(
            connection, tenant_id, subject_id, error=error, today=datetime.now(timezone.utc).date()
        )
    except Exception as failure:  # the subject's own refresh result stands either way
        connection.rollback()
        return {"error_type": type(failure).__name__, "error": str(failure)[:400]}


def _record_agent_reconciliations(connection, tenant_id: str) -> dict:
    """Stage 2's four checks on new IBKR account cases, from the reconciliation agent's newest results.

    Does nothing while ONBOARDING_AGENTS_ENABLED is off; never fails the refresh.
    """

    try:
        return run_reconciliations(connection, tenant_id)
    except Exception as failure:  # the refresh's own result stands either way
        connection.rollback()
        return {"status": "failed", "error_type": type(failure).__name__, "error": str(failure)[:400]}


def _dry_run_portfolio_cases(connection, tenant_id: str, reference_path: Path, reference) -> list[dict]:
    """Stage 2 of each new-onboarding portfolio case: calculate, measure, roll back.

    Nothing runs until migration 064 exists, and nothing is kept for a
    portfolio the owner has not approved.
    """

    if not _has_onboarding_cases(connection):
        return []

    def calculate(dry_connection, definition: dict) -> dict:
        if definition["subject_type"] == "filtered_portfolio":
            return refresh_filtered_portfolio(
                dry_connection, reference_path, contract=definition,
                expected_reference_sha256=reference()["content_sha256"],
            )
        # The builder gives every HKD composite the MS or T design, so its dry
        # run applies the same HKD rules its daily refresh will.
        return refresh_fixed_unit_composite(
            dry_connection, definition, strict_hkd=str(definition["base_currency"]).upper() == "HKD")

    return run_portfolio_dry_runs(
        connection, tenant_id, calculate=calculate, today=datetime.now(timezone.utc).date()
    )


def main() -> int:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("Self-service derived refresh requires DATABASE_URL")
    if not settings.azure_storage_account_url:
        raise RuntimeError("Self-service derived refresh requires AZURE_STORAGE_ACCOUNT_URL")
    reference_kind = reference_data_source()
    connection = connect(
        settings.database_path, settings.database_url, settings.tenant_id
    )
    lock_name = f"self-service-derived:{settings.tenant_id}"
    acquired = False
    try:
        acquired = bool(
            connection.execute(
                "SELECT pg_try_advisory_lock(hashtext(?))", (lock_name,)
            ).fetchone()[0]
        )
        if not acquired:
            print(canonical_json({"status": "skipped", "reason": "lock_busy"}))
            return 0
        # Keep the allow-listed market-component catalogue warm even before the
        # first composite has been approved.  The no-code Studio must be able to
        # validate normalization-date coverage while a portfolio is still in
        # onboarding; waiting until activation would create a circular gate.
        market_data = refresh_alpha_world_market_data(connection)
        reference_path = PROJECT_ROOT / "data" / "reference_data.json"
        downloaded: dict = {}

        def reference() -> dict:
            if "source" not in downloaded:
                downloaded["source"] = _load_reference_input(
                    settings, connection, reference_path, reference_kind
                )
            return downloaded["source"]

        portfolio_cases = _dry_run_portfolio_cases(connection, settings.tenant_id, reference_path, reference)
        agent_reconciliations = _record_agent_reconciliations(connection, settings.tenant_id)
        onboarding_cases = _has_onboarding_cases(connection)
        targets = _ordered_targets(_approved_targets(connection))
        if not targets:
            print(
                canonical_json(
                    {
                        "status": "success",
                        "subjects": [],
                        "market_data": market_data,
                        "portfolio_cases": portfolio_cases,
                        "agent_reconciliations": agent_reconciliations,
                    }
                )
            )
            return 0
        reference_source = None
        if any(row["subject_type"] == "filtered_portfolio" for row in targets):
            reference_source = reference()
        results: list[dict] = []
        failures: list[dict] = []
        requested_to = datetime.now(timezone.utc).date().isoformat()
        for row in targets:
            definition = validate_typed_runtime_contract(
                str(row["subject_id"]),
                str(row["subject_type"]),
                row["configuration_json"],
                str(row["configuration_sha256"]),
            )
            run_id = _start_run(connection, row, requested_to,
                reference_source if row["subject_type"] == "filtered_portfolio" else None)
            connection.commit()
            try:
                if row["subject_type"] == "filtered_portfolio":
                    calculation = refresh_filtered_portfolio(
                        connection, reference_path, contract=definition,
                        expected_reference_sha256=reference_source["content_sha256"],
                    )
                elif is_profiled(connection, str(row["subject_id"]), tenant_id=settings.tenant_id):
                    calculation = refresh_fixed_unit_composite(connection, definition, strict_hkd=True)
                else:
                    calculation = refresh_fixed_unit_composite(connection, definition)
                runtime_contract = {
                    "definition_sha256": row["configuration_sha256"],
                    "definition_version": definition["definition_version"],
                    "calculation_kind": definition["calculation_kind"],
                    "status": "matched_typed_runtime_contract",
                }
                validation = _validate_and_complete(
                    connection, row, run_id, calculation, runtime_contract
                )
                # The governed NAV result is an independently valid gate.  Commit
                # it before optional market-data analytics so a crisis-replay or
                # benchmark-provider problem cannot falsely invalidate the
                # deterministic portfolio calculation.
                connection.commit()
                if onboarding_cases:
                    validation["onboarding_case"] = _record_history_build(
                        connection, settings.tenant_id, str(row["subject_id"]), None
                    )
            except Exception as error:
                connection.rollback()
                connection.execute(
                    """
                    UPDATE subject_calculation_runs
                    SET status = 'failed', error_code = 'calculation_failed',
                        result_json = ?::jsonb, completed_at = now()
                    WHERE calculation_run_id = ?
                    """,
                    (canonical_json({"error": str(error)}), run_id),
                )
                connection.execute(
                    """
                    UPDATE account_onboarding_checks
                    SET status = 'fail',
                        message = 'The governed calculation failed validation.',
                        evidence_json = ?::jsonb, checked_at = now()
                    WHERE case_id = (
                        SELECT case_id FROM account_onboarding_cases
                        WHERE target_subject_id = ? AND status NOT IN ('cancelled', 'active')
                        ORDER BY updated_at DESC LIMIT 1
                    ) AND check_id = 'data_import_reconciled'
                    """,
                    (
                        canonical_json(
                            {
                                "state": "failed",
                                "phase": "calculation",
                                "error_type": type(error).__name__,
                                "calculation_run_id": run_id,
                            }
                        ),
                        row["subject_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE account_onboarding_checks
                    SET status = 'pending',
                        message = 'Risk analytics are waiting for a validated governed calculation.',
                        evidence_json = ?::jsonb, checked_at = now()
                    WHERE case_id = (
                        SELECT case_id FROM account_onboarding_cases
                        WHERE target_subject_id = ? AND status NOT IN ('cancelled', 'active')
                        ORDER BY updated_at DESC LIMIT 1
                    ) AND check_id = 'risk_analytics_validated'
                    """,
                    (
                        canonical_json(
                            {
                                "state": "blocked_by_calculation",
                                "calculation_run_id": run_id,
                            }
                        ),
                        row["subject_id"],
                    ),
                )
                connection.commit()
                failure = {
                    "subject_id": row["subject_id"],
                    "phase": "calculation",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
                if onboarding_cases:
                    failure["onboarding_case"] = _record_history_build(
                        connection, settings.tenant_id, str(row["subject_id"]), str(error)
                    )
                failures.append(failure)
                results.append({**failure, "status": "failed"})
                continue

            try:
                analytics = _refresh_analytics_if_approved(
                    connection, row, tenant_id=settings.tenant_id
                )
                connection.commit()
                results.append(
                    {
                        "subject_id": row["subject_id"],
                        "status": "validated",
                        "calculation_run_id": run_id,
                        "validation": validation,
                        "analytics": analytics,
                    }
                )
            except Exception as error:
                connection.rollback()
                connection.execute(
                    """
                    UPDATE account_onboarding_checks
                    SET status = 'fail',
                        message = 'The governed calculation passed, but deterministic risk analytics need attention.',
                        evidence_json = ?::jsonb, checked_at = now()
                    WHERE case_id = (
                        SELECT case_id FROM account_onboarding_cases
                        WHERE target_subject_id = ? AND status NOT IN ('cancelled', 'active')
                        ORDER BY updated_at DESC LIMIT 1
                    ) AND check_id = 'risk_analytics_validated'
                    """,
                    (
                        canonical_json(
                            {
                                "state": "failed",
                                "phase": "analytics",
                                "error_type": type(error).__name__,
                                "calculation_run_id": run_id,
                            }
                        ),
                        row["subject_id"],
                    ),
                )
                connection.commit()
                failure = {
                    "subject_id": row["subject_id"],
                    "phase": "analytics",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
                failures.append(failure)
                results.append(
                    {
                        **failure,
                        "status": "calculation_validated_analytics_failed",
                        "calculation_run_id": run_id,
                        "validation": validation,
                    }
                )
                continue
        print(
            canonical_json(
                {
                    "status": "success" if not failures else "partial_failure",
                    "subjects": results,
                    "failures": failures,
                    "reference_data": reference_source,
                    "market_data": market_data,
                    "portfolio_cases": portfolio_cases,
                    "agent_reconciliations": agent_reconciliations,
                }
            )
        )
        return 0 if not failures else 1
    finally:
        if acquired:
            connection.execute(
                "SELECT pg_advisory_unlock(hashtext(?))", (lock_name,)
            )
            connection.commit()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
