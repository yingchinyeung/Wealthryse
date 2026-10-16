from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
import hashlib
from pathlib import Path

from fastapi import HTTPException
from starlette.requests import Request

from ibkr_etl import web
from ibkr_etl.metrics_guide import METRIC_ANCHORS, METRIC_SECTIONS
from ibkr_etl.web import (
    AdminActionPreviewRequest,
    AnalystNarrativeRequest,
    _authorized_user,
    _database_authorized_user,
    _cash_filter_values,
    _onboarding_user,
    _owner_user,
    _csv_safe,
    _identity_digest,
    _parse_as_of,
    _reconciliation_prerequisite_status,
    _trade_filter_values,
    _security_audit_event,
    SECURITY_HEADERS,
    account_page,
    account_root,
    cash_activity,
    home,
    export_reporting_snapshot,
    institutional_control_plane_status,
    preview_admin_action,
    run_grounded_analysis,
    trade_history,
)


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.row = row
        self.parameters = None

    def execute(self, statement, parameters):
        self.parameters = parameters
        return _Cursor(self.row)


class _AuthorizationRawConnection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, parameters, *, prepare):
        self.calls.append((statement, parameters, prepare))
        if "FROM app_users" in statement:
            return _Cursor(
                {
                    "entra_object_id": "owner-123",
                    "email": "owner@example.com",
                    "display_name": "Owner",
                    "status": "active",
                }
            )
        return _AuthorizationRowsCursor([{"role_name": "owner"}])


class _AuthorizationRowsCursor(_Cursor):
    def fetchall(self):
        return self.row


def _request(headers: dict[str, str], path: str = "/api/private?secret=discard") -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path.split("?", 1)[0],
            "query_string": path.split("?", 1)[1].encode() if "?" in path else b"",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "scheme": "https",
            "server": ("portfolio.example", 443),
            "client": ("127.0.0.1", 12345),
        }
    )
    request.state.correlation_id = "test-correlation-id"
    return request


class PortfolioWebTests(unittest.TestCase):
    def test_final_onboarding_wizard_is_page_local_fail_closed_and_owner_gated(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn("ONBOARDING_STEP_PAGE_SCRIPT", source)
        for step in (
            "connection", "account", "financial-contract", "external-data",
            "documents", "reconciliation", "historical-reports",
            "private-preview", "activation",
        ):
            self.assertIn(step, source)
        self.assertIn(
            '@app.post("/api/onboarding/{subject_id}/approvals/financial-contract")',
            source,
        )
        self.assertIn(
            '@app.post("/api/onboarding/{subject_id}/reconciliation/confirm")',
            source,
        )
        self.assertIn(
            '@app.post("/api/onboarding/{subject_id}/approvals/activation")',
            source,
        )
        self.assertIn("Depends(_owner_user)", source)
        self.assertIn("ACTIVATE {subject['subject_id']}", source)
        self.assertIn("Activation evidence changed", source)
        self.assertIn('"next_page": f"/accounts/{subject}/overview"', source)
        self.assertIn("POST_WAVE0_ONBOARDING_SEQUENCE", source)
        self.assertIn("position <= current_by_stage", source)
        migration = Path(
            "migrations/postgres/042_self_service_onboarding_wizard.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("historical_reports_backfilled", migration)
        self.assertIn("presentation_identity_approved", migration)

    def test_reference_derived_onboarding_is_definition_bound(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn(
            '@app.post("/api/onboarding/reference-programme/{subject_id}/start")',
            source,
        )
        self.assertIn(
            '@app.post("/api/onboarding/{subject_id}/approvals/derived-contract")',
            source,
        )
        self.assertIn("approved_reference_definitions()", source)
        self.assertIn("subject_configuration_sha256", source)
        self.assertIn("reference_definition_summary", source)
        self.assertIn("Locked calculation preview", source)
        self.assertIn("Definition SHA-256", source)
        self.assertIn("The browser cannot edit filters, coefficients or calculation code", source)
        reference_start = source[
            source.index("def start_reference_subject_onboarding(") :
            source.index("@app.post(\"/api/onboarding/master-programme", source.index("def start_reference_subject_onboarding("))
        ]
        self.assertIn("FROM market_data_prices", reference_start)
        self.assertIn("MAX(price_date) FILTER (WHERE price_date <= %s) AS base_date", reference_start)
        self.assertNotIn("FROM benchmark_master", reference_start)
        self.assertIn("(effective_from.isoformat(), TENANT_ID, benchmark_ids)", reference_start)
        self.assertIn('"required_on_or_before": effective_from.isoformat()', reference_start)

    def test_derived_portfolio_studio_is_typed_and_uses_the_existing_wizard(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn("Derived Portfolio Studio", source)
        self.assertIn(
            '@app.post("/api/onboarding/derived-studio/filtered/start")', source
        )
        self.assertIn(
            '@app.post("/api/onboarding/derived-studio/composite/start")', source
        )
        self.assertIn("filtered_portfolio_definition", source)
        self.assertIn("fixed_unit_composite_definition", source)
        self.assertIn("(submission.normalization_date.isoformat(), component_id)", source)
        self.assertIn('"wizard_version": "subject-onboarding-wizard-v6"', source)
        self.assertIn("arbitrary formulas, SQL and Python are prohibited", source)

    def test_onboarding_secondary_benchmarks_use_accessible_checkboxes(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        theme = Path("src/ibkr_etl/web_theme.py").read_text(encoding="utf-8")
        self.assertIn("upgradeSecondaryBenchmarkPicker", source)
        self.assertIn("No Ctrl key is needed", source)
        self.assertIn('type="checkbox" name="secondary_benchmark_ids"', source)
        self.assertIn("sameAsPrimary", source)
        self.assertIn("No secondary benchmarks selected", source)
        self.assertIn("benchmark-choice-grid", theme)
        self.assertIn("benchmark-selection-summary", theme)

    def test_onboarding_errors_use_a_safe_json_envelope_and_plain_text_fallback(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn("@app.exception_handler(Exception)", source)
        self.assertIn('"code": "internal_server_error"', source)
        self.assertIn("No approval was recorded", source)
        self.assertIn("const raw=await response.text()", source)
        self.assertIn("response.headers.get('X-Correlation-ID')", source)
        self.assertIn("detail?.message", source)

    def test_onboarding_analytics_has_persistent_subject_status(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn("Deterministic analytics completed successfully", source)
        self.assertIn("risk_analytics_validated", source)
        self.assertIn('"state": "dispatching"', source)
        self.assertIn('"state": "queued"', source)
        self.assertIn('"status": "already_queued"', source)
        self.assertIn("pollAnalytics(120)", source)
        self.assertIn("DERIVED_REFRESH_JOB_NAME", source)

    def test_governed_calculation_has_persistent_completion_feedback(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        worker = Path("scripts/run_self_service_derived_refresh.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Governed calculation completed successfully", source)
        self.assertIn("Governed calculation is running", source)
        self.assertIn("pollCalculation(120)", source)
        self.assertIn("This page will show completion automatically", source)
        self.assertIn('"status": "already_queued"', source)
        self.assertIn("'data_import_reconciled'", source)
        self.assertIn('"state": "completed"', worker)
        self.assertIn("The governed calculation failed validation.", worker)

    def test_reconciliation_prerequisites_exclude_future_stages(self) -> None:
        checks = [
            {"check_id": check_id, "status": "pass", "message": "Passed"}
            for check_id in web.RECONCILIATION_REQUIRED_CHECK_IDS
        ]
        checks.extend(
            [
                {
                    "check_id": "historical_reports_backfilled",
                    "status": "pending",
                    "message": "Future step",
                },
                {
                    "check_id": "website_preview_validated",
                    "status": "pending",
                    "message": "Future step",
                },
                {
                    "check_id": "owner_activation",
                    "status": "pending",
                    "message": "Future step",
                },
            ]
        )

        result = _reconciliation_prerequisite_status(checks)

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["failed_check_ids"], [])
        self.assertEqual(
            result["message"], "All Step 1–6 reconciliation prerequisites passed."
        )

    def test_reconciliation_prerequisites_name_exact_blockers(self) -> None:
        checks = [
            {"check_id": check_id, "status": "pass", "message": "Passed"}
            for check_id in web.RECONCILIATION_REQUIRED_CHECK_IDS
            if check_id != "risk_analytics_validated"
        ]

        result = _reconciliation_prerequisite_status(checks)

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failed_check_ids"], ["risk_analytics_validated"])
        self.assertIn("risk analytics validated", result["message"])

    def test_reconciliation_privileges_are_read_only_and_failures_are_actionable(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        bootstrap = Path("scripts/bootstrap_postgres.py").read_text(encoding="utf-8")
        self.assertIn(
            "position_snapshots, derived_portfolio_nav, ", bootstrap
        )
        self.assertIn(
            "derived_portfolio_positions, benchmark_master, market_data_prices", bootstrap
        )
        self.assertIn(
            "NOT has_table_privilege(%s, 'public.derived_portfolio_nav', 'UPDATE')",
            bootstrap,
        )
        self.assertIn(
            "NOT has_table_privilege(%s, 'public.derived_portfolio_positions', 'DELETE')",
            bootstrap,
        )
        self.assertIn(
            "has_table_privilege(%s, 'public.market_data_prices', 'SELECT')",
            bootstrap,
        )
        self.assertIn(
            "NOT has_table_privilege(%s, 'public.market_data_prices', 'INSERT')",
            bootstrap,
        )
        self.assertIn(
            "NOT has_table_privilege(%s, 'public.market_data_prices', 'UPDATE')",
            bootstrap,
        )
        self.assertIn(
            "NOT has_table_privilege(%s, 'public.market_data_prices', 'DELETE')",
            bootstrap,
        )
        self.assertIn("except InsufficientPrivilege as error", source)
        self.assertIn('"code": "onboarding_configuration_error"', source)
        self.assertIn("requires an administrator configuration correction", source)
        self.assertIn("onboarding state was changed.", source)
        self.assertIn("onboarding-reconciliation-authorization", source)
        self.assertIn(
            '"next_page": f"/account-onboarding/{subject}/steps/historical-reports"',
            source,
        )

    def test_completed_document_intake_hides_replacement_controls_by_default(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn("All three mandatory documents validated", source)
        self.assertIn("Continue to reconciliation", source)
        self.assertIn("Replace documents", source)
        self.assertIn("document-replacement-slot", source)
        self.assertIn("if(!complete){bindForm(false);return}", source)
        self.assertIn("button.hidden=true", source)

    def test_derived_refresh_runs_complete_deterministic_analytics(self) -> None:
        worker = Path("scripts/run_self_service_derived_refresh.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("refresh_crisis_replay", worker)
        self.assertIn("refresh_composite_crisis_replay", worker)
        self.assertIn("refresh_sector_classifications_range", worker)
        self.assertIn('"crisis_replay": crisis', worker)
        self.assertIn('"classification": classification', worker)
        self.assertIn("comparison_rows >= 30", worker)

    def test_derived_refresh_keeps_calculation_and_analytics_gates_independent(self) -> None:
        worker = Path("scripts/run_self_service_derived_refresh.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "The governed calculation passed, but deterministic risk analytics need attention.",
            worker,
        )
        self.assertIn('"phase": "analytics"', worker)
        self.assertIn('"phase": "calculation"', worker)
        self.assertIn("blocked_by_calculation", worker)
        self.assertNotIn(
            "The governed calculation or deterministic analytics failed validation.",
            worker,
        )
        self.assertIn('"success" if not failures else "partial_failure"', worker)

    def test_approval_role_can_write_benchmark_mapping_without_delete(self) -> None:
        bootstrap = Path("scripts/bootstrap_postgres.py").read_text(encoding="utf-8")
        self.assertIn(
            '"GRANT SELECT, INSERT, UPDATE ON TABLE "\n'
            '                    "portfolio_benchmark_mapping TO {}"',
            bootstrap,
        )
        self.assertIn(
            "NOT has_table_privilege(%s, 'public.portfolio_benchmark_mapping', 'DELETE')",
            bootstrap,
        )

    def test_onboarding_external_data_approval_is_integrity_bound_and_owner_only(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn(
            '@app.post("/api/onboarding/{subject_id}/approvals/external-market-data")',
            source,
        )
        self.assertIn("Depends(_owner_user)", source)
        self.assertIn("YAHOO_ONBOARDING_SCOPE_SHA256", source)
        self.assertIn("account_onboarding_approvals", source)
        self.assertIn('"transmission_started": False', source)

    def test_risk_attribution_converts_validated_native_currency_positions_to_usd(self) -> None:
        source = Path("src/ibkr_etl/risk_snapshots.py").read_text(encoding="utf-8")
        self.assertIn("LEFT JOIN LATERAL", source)
        self.assertIn("FROM reference_fx_rates AS r", source)
        self.assertIn("p.snapshot_date::date - INTERVAL '4 days'", source)
        self.assertIn("WHEN fx.rate IS NOT NULL THEN 'USD'", source)
        self.assertIn("NOT LIKE '%%.REC'", source)
        self.assertIn("p.snapshot_date = ANY(?::text[])", source)

    def test_risk_page_reads_versioned_cache_and_splits_heavy_details(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn("FROM risk_analytics_snapshots", source)
        self.assertIn('@app.get("/api/portfolio/{account_id}/risk/details")', source)
        self.assertIn("riskDetailsRequest", source)
        self.assertNotIn("position_start =", source)

    def test_risk_endpoints_withhold_invalid_cache_and_do_not_recalculate(self):
        from fastapi import Response
        from ibkr_etl.risk_snapshots import RISK_ANALYTICS_VERSION
        from ibkr_etl.benchmark_analytics import CALCULATION_VERSION
        from ibkr_etl.util import canonical_json
        payload = {"account_id":"subject", "as_of":"2026-10-05", "benchmark_id":"GSPC",
                   "analytics_version":RISK_ANALYTICS_VERSION, "metrics":{"value":0.1},
                   "quality_status":"warn", "crisis_replay":{"example":True}}
        record = {"payload_json":payload, "quality_status":"warn", "comparison_version":CALCULATION_VERSION,
                  "expected_benchmark_id":"GSPC", "generated_at":"now", "duration_ms":1,
                  "payload_sha256":hashlib.sha256(canonical_json(payload).encode()).hexdigest()}
        for endpoint in (web.risk_analytics, web.risk_analytics_details):
            with patch.object(web,"_portfolio_access_context"), patch.object(web,"_database") as database:
                connection=database.return_value.__enter__.return_value
                connection.execute.return_value.fetchone.return_value=record
                response=Response()
                valid=endpoint("subject",as_of="2026-10-05",response=response,user={"email":"viewer"})
                self.assertEqual(valid["cache_status"],"hit")
                record["payload_sha256"]="wrong"
                invalid=endpoint("subject",as_of="2026-10-05",response=response,user={"email":"viewer"})
                self.assertEqual(invalid["cache_status"],"invalid")
                self.assertEqual(invalid["validation_status"],"payload_hash_mismatch")
                self.assertEqual(response.headers["Cache-Control"],"no-store")
                self.assertTrue(all('SELECT' in c.args[0] for c in connection.execute.call_args_list))
                record["payload_sha256"]=hashlib.sha256(canonical_json(payload).encode()).hexdigest()

    def test_system_health_live_refresh_explains_stale_status_and_metric_failures(self):
        self.assertIn("Metrics needing reconciliation", web.SYSTEM_HEALTH_PAGE_SCRIPT)
        self.assertIn("60000", web.SYSTEM_HEALTH_PAGE_SCRIPT)
        self.assertIn("visibilitychange", web.SYSTEM_HEALTH_PAGE_SCRIPT)
        self.assertIn("this is not a health pass", web.SYSTEM_HEALTH_PAGE_SCRIPT)
        html=web._render_platform_page({"email":"owner", "role":"owner"},section="system-health")
        self.assertIn('id="health-findings"', html)
        self.assertIn('aria-live="polite"',html)
        self.assertIn('Registered account acceptance burn-in',html)

    def test_onboarding_upload_requires_three_files_and_operator_access(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn(
            '@app.post("/api/onboarding/{subject_id}/template-submissions")', source
        )
        self.assertIn("factsheet_template: UploadFile = File(...)", source)
        self.assertNotIn("presentation_strategy_name: str = Form(...)", source)
        self.assertNotIn("presentation_layout_policy: str = Form(...)", source)
        self.assertIn("presentation_identity_scope", source)
        self.assertIn("presentation_identity_approval_id", source)
        self.assertIn("presentation_strategy_contract(", source)
        self.assertIn("subject_id=subject", source)
        self.assertIn("presentation_template: UploadFile = File(...)", source)
        self.assertIn("completed_factsheet: UploadFile = File(...)", source)
        self.assertIn("user: dict[str, str] = Depends(_onboarding_user)", source)
        self.assertIn("Validate against locked identity and submit", source)

    def test_presentation_identity_is_server_derived_owner_only_and_audited(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn(
            '@app.post("/api/onboarding/{subject_id}/approvals/presentation-identity")',
            source,
        )
        self.assertIn("OnboardingPresentationIdentityApprovalRequest", source)
        self.assertIn("Depends(_owner_user)", source)
        self.assertIn("approved_subject_display_name", source)
        self.assertIn("uploader_editable", source)
        self.assertIn("presentation_identity_approved", source)
        self.assertIn('Literal["platform_standard"]', source)
        self.assertIn('layout_policy="platform_standard"', source)
        self.assertNotIn('<option value="subject_template">', source)
        self.assertNotIn('name="presentation_strategy_name"', source)
        self.assertNotIn('name="presentation_layout_policy"', source)

    def test_flex_source_intake_uses_isolated_broker_and_never_inserts_token(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/onboarding/flex-sources")', source)
        self.assertIn("_store_flex_token_via_broker", source)
        self.assertIn("key_vault_secret_name", source)
        credential_insert = source.split(
            "INSERT INTO ibkr_flex_credentials(", 1
        )[1].split("\n                )", 1)[0]
        self.assertNotIn("token,", credential_insert)
        self.assertIn("schedule_enabled", source)
        self.assertIn("'pending_validation'", source)

    def test_onboarding_preview_is_operator_only_and_keeps_activation_blocked(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn(
            '@app.get("/api/onboarding/{subject_id}/preview")', source
        )
        self.assertIn("user: dict[str, str] = Depends(_onboarding_user)", source)
        self.assertIn('"activation_blocked": True', source)
        self.assertIn("Open private reconciliation preview", source)
        self.assertIn(
            '@app.post("/api/onboarding/{subject_id}/approvals/website-preview")',
            source,
        )
        self.assertIn("Depends(_owner_user)", source)
        self.assertIn("build_physical_preview_validation", source)
        self.assertIn("Approve private website preview", source)
        self.assertIn("Final activation remains separately blocked", source)

    def test_preview_counts_only_comparisons_in_the_mapping_currency(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        preview = source.split("def _onboarding_preview_payload(", 1)[1].split(
            '@app.get("/api/onboarding/{subject_id}/preview")', 1
        )[0]
        query = preview.split("LEFT JOIN performance_comparison_daily p", 1)[1].split(
            "WHERE m.tenant_id", 1
        )[0]
        self.assertIn("p.tenant_id = m.tenant_id", query)
        self.assertIn("p.subject_id = m.subject_id", query)
        self.assertIn("p.benchmark_id = m.benchmark_id", query)
        self.assertIn("p.reporting_currency = m.reporting_currency", query)
        self.assertIn("p.quality_status <> 'fail'", query)

    def test_historical_backfill_is_a_private_owner_approved_onboarding_gate(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn(
            '@app.post("/api/onboarding/{subject_id}/historical-reports/queue")',
            source,
        )
        self.assertIn(
            '@app.post("/api/onboarding/{subject_id}/approvals/historical-reports")',
            source,
        )
        self.assertIn(
            '"/api/onboarding/{subject_id}/historical-reports/{generation_run_id}/download"',
            source,
        )
        self.assertIn("Depends(_onboarding_user)", source)
        self.assertIn("Depends(_owner_user)", source)
        self.assertIn("historical_reports_backfilled", source)
        self.assertIn("Render and approve historical reports", source)
        self.assertIn("private-onboarding-review", source)
        self.assertIn("activation_blocked", source)
        self.assertIn(
            'template_evidence(payload["templates"], subject_id=subject)', source
        )

    def test_historical_backfill_reports_live_progress_and_actionable_failures(self) -> None:
        source = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn("Historical report generation is queued", source)
        self.assertIn("Historical reports are being generated", source)
        self.assertIn("Historical report generation completed successfully", source)
        self.assertIn("Historical report generation needs attention", source)
        self.assertIn('id="history-live-status"', source)
        self.assertIn('aria-live="polite"', source)
        self.assertIn("historyPollTimer", source)
        self.assertIn("scheduleHistoryPoll(5000)", source)
        self.assertIn("Progress will update automatically", source)
        self.assertIn("Retry historical report generation", source)
        self.assertIn("Automatic status check failed", source)
        self.assertIn("AND status <> 'superseded'", source)
        self.assertNotIn("AND status NOT IN ('failed', 'superseded')", source)

    def test_reporting_snapshot_export_is_account_scoped_and_integrity_checked(self) -> None:
        payload = '{"publication_allowed":false,"subject_id":"U16088571"}'
        connection = _Connection(
            {
                "snapshot_payload_json": payload,
                "data_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "subject_id": "U16088571",
                "period_end": "2026-08-31",
                "status": "frozen",
            }
        )
        with patch.object(web, "_database") as database:
            database.return_value.__enter__.return_value = connection
            response = export_reporting_snapshot(
                "342e82f3-15b7-532b-83b5-663f1b3e6570",
                True,
                {"email": "owner@example.com", "object_id": "object-123", "role": "owner"},
            )
        self.assertEqual(response.body.decode("utf-8"), payload)
        self.assertIn("internal-development", response.headers["content-disposition"])
        self.assertEqual(connection.parameters[2], "U16088571")

    def test_latest_reporting_snapshot_export_is_account_scoped(self) -> None:
        payload = '{"publication_allowed":false,"subject_id":"U16088571"}'
        connection = _Connection(
            {
                "snapshot_payload_json": payload,
                "data_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "subject_id": "U16088571",
                "period_end": "2026-08-31",
                "status": "frozen",
            }
        )
        with patch.object(web, "_database") as database:
            database.return_value.__enter__.return_value = connection
            response = export_reporting_snapshot(
                "latest",
                True,
                {"email": "owner@example.com", "object_id": "object-123", "role": "owner"},
            )
        self.assertEqual(response.body.decode("utf-8"), payload)
        self.assertEqual(connection.parameters, (web.TENANT_ID, "U16088571"))

    def test_reporting_snapshot_export_fails_closed_on_hash_mismatch(self) -> None:
        connection = _Connection(
            {
                "snapshot_payload_json": "{}",
                "data_sha256": "0" * 64,
                "subject_id": "U16088571",
                "period_end": "2026-08-31",
                "status": "frozen",
            }
        )
        with patch.object(web, "_database") as database, self.assertRaises(
            HTTPException
        ) as raised:
            database.return_value.__enter__.return_value = connection
            export_reporting_snapshot(
                "342e82f3-15b7-532b-83b5-663f1b3e6570",
                False,
                {"email": "owner@example.com", "object_id": "object-123", "role": "owner"},
            )
        self.assertEqual(raised.exception.status_code, 503)
    def test_security_headers_prevent_storage_sniffing_and_framing(self) -> None:
        self.assertEqual(SECURITY_HEADERS["Cache-Control"], "no-store")
        self.assertEqual(SECURITY_HEADERS["X-Content-Type-Options"], "nosniff")
        self.assertEqual(SECURITY_HEADERS["Referrer-Policy"], "same-origin")
        self.assertIn("frame-ancestors 'none'", SECURITY_HEADERS["Content-Security-Policy"])

    def test_authorized_user_requires_every_configured_identity_claim(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "OBJECT-123",
                "x-ms-client-principal-name": "Analyst@Example.com",
            }
        )
        with patch.object(web, "ALLOWED_OBJECT_IDS", {"object-123"}), patch.object(
            web, "ALLOWED_EMAILS", {"analyst@example.com"}
        ):
            self.assertEqual(
                _authorized_user(request),
                {
                    "object_id": "object-123",
                    "email": "analyst@example.com",
                    "role": "owner",
                },
            )

    def test_authorized_viewer_receives_viewer_role(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "VIEWER-123",
                "x-ms-client-principal-name": "Viewer@Example.com",
            }
        )
        with patch.object(web, "ALLOWED_OBJECT_IDS", {"owner-123"}), patch.object(
            web, "ALLOWED_EMAILS", {"owner@example.com"}
        ), patch.object(web, "VIEWER_OBJECT_IDS", {"viewer-123"}), patch.object(
            web, "VIEWER_EMAILS", {"viewer@example.com"}
        ):
            self.assertEqual(
                _authorized_user(request),
                {
                    "object_id": "viewer-123",
                    "email": "viewer@example.com",
                    "role": "viewer",
                },
            )

    def test_hybrid_authorization_falls_back_to_exact_legacy_claims_on_database_error(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "OWNER-123",
                "x-ms-client-principal-name": "Owner@Example.com",
            }
        )
        with patch.object(web, "AUTHORIZATION_MODE", "hybrid"), patch.object(
            web, "_database", side_effect=RuntimeError("temporary database failure")
        ), patch.object(web, "ALLOWED_OBJECT_IDS", {"owner-123"}), patch.object(
            web, "ALLOWED_EMAILS", {"owner@example.com"}
        ):
            user = _authorized_user(request)
        self.assertEqual(user["role"], "owner")
        self.assertEqual(user["object_id"], "owner-123")

    def test_database_authorization_adapts_portable_placeholders_for_psycopg(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "OWNER-123",
                "x-ms-client-principal-name": "Owner@Example.com",
            }
        )
        raw_connection = _AuthorizationRawConnection()
        with patch.object(web, "AUTHORIZATION_MODE", "database"), patch.object(
            web, "_database"
        ) as database:
            database.return_value.__enter__.return_value = raw_connection
            user = _database_authorized_user(request)
        self.assertEqual(user["authorization_source"], "database")
        self.assertEqual(user["roles"], ["owner"])
        self.assertEqual(len(raw_connection.calls), 2)
        self.assertTrue(
            all("?" not in statement for statement, _, _ in raw_connection.calls)
        )
        self.assertTrue(
            all("%s" in statement for statement, _, _ in raw_connection.calls)
        )
        self.assertTrue(
            all(prepare is False for _, _, prepare in raw_connection.calls)
        )

    def test_hybrid_database_denial_cannot_fall_back_to_legacy_allowlist(self) -> None:
        request = _request({"x-ms-client-principal-id": "OWNER-123",
                            "x-ms-client-principal-name": "Owner@Example.com"})
        with patch.object(web, "AUTHORIZATION_MODE", "hybrid"), patch.object(
            web, "_database"
        ), patch.object(web, "resolve_authorized_identity", return_value=None), patch.object(
            web, "_legacy_authorized_user"
        ) as legacy:
            with self.assertRaises(HTTPException) as caught:
                _authorized_user(request)
        self.assertEqual(caught.exception.status_code, 403)
        legacy.assert_not_called()

    def test_hybrid_outage_does_not_restore_a_legacy_viewer(self) -> None:
        request = _request({"x-ms-client-principal-id": "VIEWER-123",
                            "x-ms-client-principal-name": "Viewer@Example.com"})
        with patch.object(web, "AUTHORIZATION_MODE", "hybrid"), patch.object(
            web, "_database", side_effect=RuntimeError("unavailable")
        ), patch.object(web, "_legacy_authorized_user", return_value={"role": "viewer"}):
            with self.assertRaises(HTTPException) as caught:
                _authorized_user(request)
        self.assertEqual(caught.exception.status_code, 503)

    def test_control_plane_uses_portable_query_adapter(self) -> None:
        raw_connection = _AuthorizationRawConnection()
        expected = {
            "identity_registry": {"database_authorization_ready": True},
            "dead_letters": 0,
            "shadow_reconciliation": {"status": "pass"},
        }
        with patch.object(web, "_database") as database, patch.object(
            web, "control_plane_summary", return_value=expected.copy()
        ) as summary:
            database.return_value.__enter__.return_value = raw_connection
            response = institutional_control_plane_status(
                {"email": "owner@example.com", "role": "owner"}
            )
        adapted = summary.call_args.args[0]
        self.assertIsInstance(adapted, web.PostgresConnection)
        self.assertIs(adapted._connection, raw_connection)
        self.assertTrue(response["authorization_cutover_ready"])

    def test_database_only_authorization_remains_fail_closed_on_database_error(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "OWNER-123",
                "x-ms-client-principal-name": "Owner@Example.com",
            }
        )
        with patch.object(web, "AUTHORIZATION_MODE", "database"), patch.object(
            web, "_database", side_effect=RuntimeError("temporary database failure")
        ), self.assertRaises(HTTPException) as raised:
            _authorized_user(request)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Institutional authorization is unavailable")

    def test_owner_dependency_denies_viewer(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "viewer-123",
                "x-ms-client-principal-name": "viewer@example.com",
            }
        )
        with patch.object(web, "ALLOWED_OBJECT_IDS", {"owner-123"}), patch.object(
            web, "ALLOWED_EMAILS", {"owner@example.com"}
        ), patch.object(web, "VIEWER_OBJECT_IDS", {"viewer-123"}), patch.object(
            web, "VIEWER_EMAILS", {"viewer@example.com"}
        ), self.assertRaises(HTTPException) as raised:
            _owner_user(request)
        self.assertEqual(raised.exception.status_code, 403)

    def test_onboarding_operator_is_authorized_but_not_owner(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "operator-123",
                "x-ms-client-principal-name": "operator@example.com",
            }
        )
        patches = (
            patch.object(web, "ALLOWED_OBJECT_IDS", {"owner-123"}),
            patch.object(web, "ALLOWED_EMAILS", {"owner@example.com"}),
            patch.object(web, "ONBOARDING_OPERATOR_OBJECT_IDS", {"operator-123"}),
            patch.object(web, "ONBOARDING_OPERATOR_EMAILS", {"operator@example.com"}),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            self.assertEqual(_onboarding_user(request)["role"], "onboarding_operator")
            with self.assertRaises(HTTPException) as raised:
                _owner_user(request)
        self.assertEqual(raised.exception.status_code, 403)

    def test_authorized_user_denies_mismatched_claims(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "wrong-object",
                "x-ms-client-principal-name": "other@example.com",
            }
        )
        with patch.object(web, "ALLOWED_OBJECT_IDS", {"object-123"}), patch.object(
            web, "ALLOWED_EMAILS", {"analyst@example.com"}
        ), self.assertRaises(HTTPException) as raised:
            _authorized_user(request)
        self.assertEqual(raised.exception.status_code, 403)

    def test_authorized_user_fails_closed_without_allow_list(self) -> None:
        request = _request({})
        with patch.object(web, "ALLOWED_OBJECT_IDS", set()), patch.object(
            web, "ALLOWED_EMAILS", set()
        ), self.assertRaises(HTTPException) as raised:
            _authorized_user(request)
        self.assertEqual(raised.exception.status_code, 503)

    def test_security_audit_hashes_identity_and_omits_query_string(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "OBJECT-123",
                "x-ms-client-principal-name": "Analyst@Example.com",
            }
        )
        event = _security_audit_event(
            request, status_code=403, duration_ms=12.3456
        )
        serialized = str(event)
        self.assertEqual(event["outcome"], "denied")
        self.assertEqual(event["path"], "/api/private")
        self.assertEqual(event["email_sha256"], _identity_digest("analyst@example.com"))
        self.assertNotIn("Analyst@Example.com", serialized)
        self.assertNotIn("OBJECT-123", serialized)
        self.assertNotIn("secret=discard", serialized)

    def test_as_of_parser_accepts_iso_date(self) -> None:
        self.assertEqual(_parse_as_of("2026-09-18"), "2026-09-18")
        self.assertIsNone(_parse_as_of(None))

    def test_as_of_parser_rejects_invalid_or_non_iso_date(self) -> None:
        for value in ("18/09/2026", "2026-02-30", "not-a-date"):
            with self.subTest(value=value), self.assertRaises(HTTPException) as raised:
                _parse_as_of(value)
            self.assertEqual(raised.exception.status_code, 400)

    def test_trade_filters_are_bounded_and_validated(self) -> None:
        self.assertEqual(
            _trade_filter_values(None, "2026-09-22", "  AAPL  ", "buy"),
            ("2025-01-20", "2026-09-22", "AAPL", "BUY"),
        )
        for values in (
            ("2026-09-23", "2026-09-22", None, None),
            (None, "2026-09-22", None, "short"),
            (None, "2026-09-22", "A" * 65, None),
        ):
            with self.subTest(values=values), self.assertRaises(HTTPException) as raised:
                _trade_filter_values(*values)
            self.assertEqual(raised.exception.status_code, 400)

    def test_cash_filters_accept_custom_range_and_reject_reversed_dates(self) -> None:
        self.assertEqual(
            _cash_filter_values("2025-03-01", "2026-09-22"),
            ("2025-03-01", "2026-09-22"),
        )
        self.assertEqual(
            _cash_filter_values(None, "2026-09-22"),
            ("2025-01-20", "2026-09-22"),
        )
        with self.assertRaises(HTTPException) as raised:
            _cash_filter_values("2026-09-23", "2026-09-22")
        self.assertEqual(raised.exception.status_code, 400)

    def test_cash_activity_returns_every_record_in_custom_range(self) -> None:
        balance = {
            "snapshot_date": "2026-09-22",
            "currency": "USD",
            "ending_cash_text": "1000",
            "settled_cash_text": "1000",
            "fx_rate_to_base_text": "1",
        }
        transactions = [
            {
                "effective_date": "2026-09-20",
                "event_datetime": "2026-09-20T09:30:00",
                "transaction_type": "DIVIDEND",
                "description": "Cash dividend",
                "amount_text": "25",
                "currency": "USD",
                "conid": "1",
            },
            {
                "effective_date": "2026-09-01",
                "event_datetime": "2026-09-01T08:00:00",
                "transaction_type": "FEE",
                "description": "Custody fee",
                "amount_text": "-2",
                "currency": "USD",
                "conid": None,
            },
        ]
        balance_date_cursor = MagicMock()
        balance_date_cursor.fetchone.return_value = {"latest": "2026-09-22"}
        balances_cursor = MagicMock()
        balances_cursor.fetchall.return_value = [balance]
        transactions_cursor = MagicMock()
        transactions_cursor.fetchall.return_value = transactions
        connection = MagicMock()
        connection.execute.side_effect = [
            balance_date_cursor,
            balances_cursor,
            transactions_cursor,
        ]
        database = MagicMock()
        database.__enter__.return_value = connection
        with patch.object(web, "_database", return_value=database):
            result = cash_activity(
                "U16088571",
                from_date="2026-09-01",
                to_date="2026-09-22",
                user={"email": "analyst@example.com", "object_id": "object-123"},
            )
        self.assertEqual(result["activity_from"], "2026-09-01")
        self.assertEqual(result["activity_to"], "2026-09-22")
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["transactions"], transactions)
        transaction_call = connection.execute.call_args_list[2]
        self.assertNotIn("LIMIT", transaction_call.args[0].upper())
        self.assertEqual(
            transaction_call.args[1],
            (web.TENANT_ID, "U16088571", "2026-09-01", "2026-09-22"),
        )

    def test_trade_history_is_account_scoped_filtered_and_limited(self) -> None:
        record = {
            "trade_date": "2026-09-22",
            "trade_time": "14:30:00",
            "settle_date": "2026-09-24",
            "execution_id": "exec-1",
            "trade_id": "trade-1",
            "conid": "265598",
            "symbol": "AAPL",
            "description": "APPLE INC",
            "asset_category": "STK",
            "buy_sell": "BUY",
            "quantity_text": "10",
            "trade_price_text": "200",
            "currency": "USD",
            "proceeds_text": "-2000",
            "commission_text": "-1",
            "commission_currency": "USD",
            "net_cash_text": "-2001",
            "realized_pnl_text": "0",
            "total_count": 1,
        }
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [record]
        database = MagicMock()
        database.__enter__.return_value = connection
        with patch.object(web, "_database", return_value=database):
            result = trade_history(
                "U16088571",
                from_date="2026-09-01",
                to_date="2026-09-22",
                symbol="AAPL",
                side="BUY",
                user={"email": "analyst@example.com", "object_id": "object-123"},
            )
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["returned_count"], 1)
        self.assertFalse(result["truncated"])
        self.assertNotIn("total_count", result["trades"][0])
        self.assertEqual(
            connection.execute.call_args.args[1],
            (
                "U16088571",
                "2026-09-01",
                "2026-09-22",
                "AAPL",
                "AAPL",
                "BUY",
                "BUY",
            ),
        )

        with patch.object(
            web, "_database", side_effect=AssertionError("database must not be opened")
        ), self.assertRaises(HTTPException) as raised:
            trade_history(
                "ANOTHER_ACCOUNT",
                to_date="2026-09-22",
                user={"email": "analyst@example.com", "object_id": "object-123"},
            )
        self.assertEqual(raised.exception.status_code, 404)

    def test_filtered_portfolio_trade_history_uses_kept_source_trades(self) -> None:
        access = {
            "subject_id": "hong-kong-china",
            "subject_type": "filtered_portfolio",
            "reporting_currency": "USD",
            "history_start_date": "2025-01-20",
        }
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"latest": "2026-10-07"}
        connection.execute.return_value.fetchall.return_value = [
            {"trade_date": "2026-10-06", "symbol": "700", "currency": "HKD", "total_count": 1}
        ]
        database = MagicMock()
        database.__enter__.return_value = connection
        with patch.object(web, "_portfolio_access_context", return_value=access), patch.object(
            web, "_database", return_value=database
        ):
            result = trade_history(
                "hong-kong-china",
                user={"email": "analyst@example.com", "object_id": "object-123"},
            )
        self.assertNotIn("not_applicable", result)
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["trades"][0]["symbol"], "700")
        latest_sql, latest_params = connection.execute.call_args_list[0].args
        self.assertIn("subject_nav_series", latest_sql)
        self.assertEqual(latest_params, (web.TENANT_ID, "hong-kong-china", "USD"))
        trade_sql, trade_params = connection.execute.call_args_list[1].args
        self.assertIn("derived_portfolio_cash_ledger", trade_sql)
        self.assertIn("LIMIT 500", trade_sql)
        self.assertIn("t.trade_date DESC", trade_sql)
        self.assertEqual(trade_params[:2], (web.TENANT_ID, "hong-kong-china"))
        self.assertEqual(trade_params[6:8], ("2025-01-20", "2026-10-07"))

    def test_composite_portfolio_trade_history_is_not_applicable(self) -> None:
        access = {
            "subject_id": "alpha-world-ms",
            "subject_type": "composite_portfolio",
            "reporting_currency": "HKD",
            "history_start_date": "2025-01-20",
        }
        with patch.object(web, "_portfolio_access_context", return_value=access), patch.object(
            web, "_database", side_effect=AssertionError("database must not be opened")
        ):
            result = trade_history(
                "alpha-world-ms",
                user={"email": "analyst@example.com", "object_id": "object-123"},
            )
        self.assertTrue(result["not_applicable"])

    def test_home_is_account_directory(self) -> None:
        page = home(None, {"email": "analyst@example.com"})
        self.assertIn("<h1>Accounts</h1>", page)
        self.assertIn("WealthRyse Portfolio Data Platform", page)
        self.assertNotIn(">IBKR Portfolio Platform<", page)
        self.assertIn('href="/accounts/U16088571/overview"', page)
        self.assertIn('<div class="account-id">U.S. Technology and Biotechnology</div>', page)
        self.assertNotIn("U16088571-U.S. Technology and Biotechnology", page)
        self.assertIn("Select an account to open its portfolio workspace", page)
        self.assertIn('class="company-logo"', page)
        self.assertIn('alt="Sprout Asset Management (Asia) Limited company logo"', page)
        self.assertIn(
            'class="company-logo-link" href="/" aria-label="Go to Accounts home"',
            page,
        )
        self.assertIn('class="home-hero"', page)
        self.assertIn("backdrop-filter: saturate(160%) blur(22px)", page)
        self.assertIn("prefers-reduced-motion", page)
        self.assertIn('href="/system-health"', page)
        self.assertIn('href="/metrics-guide"', page)
        self.assertIn('href="/account-onboarding"', page)
        self.assertIn('href="/administration"', page)

        viewer_page = home(
            None,
            {"email": "viewer@example.com", "object_id": "viewer-123", "role": "viewer"},
        )
        self.assertIn('href="/system-health"', viewer_page)
        self.assertIn('href="/metrics-guide"', viewer_page)
        self.assertNotIn('href="/account-onboarding"', viewer_page)
        self.assertNotIn('href="/administration"', viewer_page)

    def test_system_pages_are_platform_scoped_and_old_account_urls_redirect(self) -> None:
        owner = {
            "email": "owner@example.com",
            "object_id": "owner-123",
            "role": "owner",
        }
        health_page = web.system_health_page(None, owner)
        self.assertIn("Platform-wide data freshness", health_page)
        self.assertNotIn("Platform workspace", health_page)
        self.assertIn('aria-current="page">System health</a>', health_page)
        self.assertNotIn('aria-label="U16088571 sections"', health_page)

        metrics_page = web.metrics_guide_page(None, owner)
        self.assertIn("Plain-language definitions", metrics_page)
        self.assertIn('aria-current="page">Metrics guide</a>', metrics_page)
        self.assertIn('id="metric-search"', metrics_page)
        self.assertIn("Understand every number", metrics_page)
        self.assertIn("What it tells you", metrics_page)
        self.assertIn("How we work it out", metrics_page)
        self.assertIn("How to explain it", metrics_page)
        self.assertIn("View technical formula and methodology", metrics_page)
        self.assertIn('id="metric-sharpe-ratio"', metrics_page)
        self.assertIn('id="metric-tracking-error"', metrics_page)
        self.assertEqual(
            len(METRIC_ANCHORS),
            sum(len(section.metrics) for section in METRIC_SECTIONS),
        )
        self.assertIn(
            "Give both series a starting value of 100", metrics_page
        )
        self.assertIn(
            "Subtract the daily equivalent of the risk-free rate", metrics_page
        )
        self.assertIn("Cash-flow-adjusted daily portfolio return", metrics_page)
        self.assertIn("Portfolio total return", metrics_page)
        self.assertIn("Tracking error", metrics_page)
        self.assertIn("Sharpe ratio", metrics_page)
        self.assertIn("Daily Expected Tail Loss (95%)", metrics_page)
        self.assertIn("Crisis maximum drawdown", metrics_page)
        self.assertIn("Ending cash", metrics_page)
        self.assertIn("Realized P&amp;L", metrics_page)
        self.assertIn("Consecutive passing days", metrics_page)
        self.assertIn("Generation pending", metrics_page)
        self.assertIn("benchmark-comparison-v1", metrics_page)
        self.assertNotIn('aria-label="U16088571 sections"', metrics_page)

        viewer_metrics_page = web.metrics_guide_page(
            None,
            {
                "email": "viewer@example.com",
                "object_id": "viewer-123",
                "role": "viewer",
            },
        )
        self.assertIn("Metrics guide", viewer_metrics_page)
        self.assertNotIn('href="/administration"', viewer_metrics_page)

        administration_page = web.administration_page(None, owner)
        self.assertIn("24/7 controlled autonomy", administration_page)
        self.assertIn("Direct owner-approved actions", administration_page)
        self.assertIn("Message the AI System Administrator", administration_page)
        self.assertIn("Administrator chats", administration_page)
        self.assertIn("/api/admin/conversations", administration_page)
        self.assertIn("Institutional onboarding control plane", administration_page)
        self.assertIn("/api/system/institutional-control-plane", administration_page)
        self.assertIn("Supported changes require an exact approval", administration_page)
        self.assertIn('aria-current="page">Administration</a>', administration_page)
        self.assertNotIn('aria-label="U16088571 sections"', administration_page)

        onboarding_page = web.account_onboarding_page(None, owner)
        self.assertIn("Scalable account onboarding", onboarding_page)
        self.assertIn("Guided onboarding wizard", onboarding_page)
        self.assertIn("Four governed branches", onboarding_page)
        self.assertIn('id="onboarding-subject-types"', onboarding_page)
        self.assertIn("Approve external data use", onboarding_page)
        self.assertIn("private historical reporting", onboarding_page)
        self.assertIn("nine controlled steps", onboarding_page)
        self.assertIn("final activation", onboarding_page)
        self.assertIn("Existing v1.1 source accounts", onboarding_page)
        self.assertIn("Platform-wide presentation requirements", onboarding_page)
        self.assertIn("Applied automatically to every future account", onboarding_page)
        self.assertIn("Security-name labels", onboarding_page)
        self.assertIn("Exact NAV", onboarding_page)
        self.assertIn("Fail-closed validation", onboarding_page)
        self.assertIn("IBKR connection prerequisite", onboarding_page)
        self.assertIn('href="/ibkr-connections">Manage IBKR connections</a>', onboarding_page)
        self.assertNotIn('id="flex-source-form"', onboarding_page)
        self.assertNotIn("Current Flex Web Service token", onboarding_page)
        self.assertNotIn("Validate and discover accounts", onboarding_page)
        self.assertIn("/api/onboarding/summary", onboarding_page)
        self.assertIn('aria-current="page">Account onboarding</a>', onboarding_page)
        self.assertNotIn('aria-label="U16088571 sections"', onboarding_page)

        connections_page = web.ibkr_connections_page(None, owner)
        self.assertIn("Connection registry", connections_page)
        self.assertIn('id="flex-source-form"', connections_page)
        self.assertIn("Current Flex Web Service token", connections_page)
        self.assertIn("Validate and discover accounts", connections_page)
        self.assertIn('aria-current="page">IBKR connections</a>', connections_page)

        health_redirect = account_page("U16088571", "system-health", None, owner)
        self.assertEqual(health_redirect.status_code, 307)
        self.assertEqual(health_redirect.headers["location"], "/system-health")
        metrics_redirect = account_page("U16088571", "metrics-guide", None, owner)
        self.assertEqual(metrics_redirect.status_code, 307)
        self.assertEqual(metrics_redirect.headers["location"], "/metrics-guide")
        onboarding_redirect = account_page(
            "U16088571", "account-onboarding", None, owner
        )
        self.assertEqual(onboarding_redirect.status_code, 307)
        self.assertEqual(
            onboarding_redirect.headers["location"], "/account-onboarding"
        )
        administration_redirect = account_page(
            "U16088571", "administration", None, owner
        )
        self.assertEqual(administration_redirect.status_code, 307)
        self.assertEqual(administration_redirect.headers["location"], "/administration")

        with patch("ibkr_etl.web._portfolio_access_context") as access:
            account_redirect = account_root("U16088606", owner)
        access.assert_called_once_with("U16088606", owner)
        self.assertEqual(account_redirect.status_code, 307)
        self.assertEqual(
            account_redirect.headers["location"], "/accounts/U16088606/overview"
        )

        with self.assertRaises(HTTPException) as invalid_account:
            account_root("../administration", owner)
        self.assertEqual(invalid_account.exception.status_code, 404)

    def test_account_subpages_expose_navigation_and_validated_portfolio_tools(self) -> None:
        page = account_page(
            "U16088571",
            "overview",
            None,
            {"email": "analyst@example.com"},
        )
        self.assertIn('id="asof-select"', page)
        self.assertIn('aria-label="Portfolio as of date"', page)
        self.assertIn('aria-label="U16088571 sections"', page)
        self.assertIn('<div class="account-id">U.S. Technology and Biotechnology</div>', page)
        self.assertNotIn("U16088571-U.S. Technology and Biotechnology", page)
        self.assertIn('class="account-hero"', page)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr))", page)
        self.assertIn("@media (max-width: 650px)", page)
        self.assertIn("WealthRyse Portfolio Data Platform", page)
        self.assertIn('<strong class="brand-title">WealthRyse</strong>', page)
        self.assertNotIn(">IBKR Portfolio Platform<", page)
        self.assertIn('href="/accounts/U16088571/performance"', page)
        self.assertIn('href="/accounts/U16088571/trades"', page)
        self.assertIn('href="/accounts/U16088571/ai-analyst"', page)
        self.assertNotIn('href="/accounts/U16088571/system-health"', page)
        self.assertNotIn('href="/accounts/U16088571/administration"', page)
        self.assertIn('href="/system-health"', page)
        self.assertIn('href="/metrics-guide"', page)
        self.assertIn('href="/administration"', page)
        self.assertIn('class="page-view active" data-section="overview"', page)
        self.assertEqual(page.count('class="page-view active"'), 1)
        self.assertIn("renderAsOf", page)
        self.assertIn('const activeSection="overview"', page)
        self.assertIn("const riskRequest=activeSection==='risk'?fetch", page)
        self.assertIn("if(activeSection==='cash')await loadCashActivity()", page)
        self.assertIn("if(activeSection==='trades')await loadTradeHistory()", page)
        self.assertIn(
            "if(activeSection==='performance'&&selected)await renderBenchmark", page
        )
        self.assertIn("Holdings as of", page)
        self.assertIn("Risk analytics through", page)
        self.assertIn("Expected maximum drawdown (1 year)", page)
        self.assertIn("Crisis maximum drawdown (15/9/2008–9/3/2009)", page)
        self.assertIn("Hypothetical fixed-unit crisis replay", page)
        self.assertIn("Daily Expected Tail Loss (95%)", page)
        self.assertIn("Top 5 Expected Tail Loss contributors", page)
        self.assertIn("Bottom 5 Expected Tail Loss contributors", page)
        self.assertIn("const metricGuideAnchors=", page)
        self.assertIn('class="metric metric-guide-card"', page)
        self.assertIn("const chartBounds=(values,options)=>", page)
        self.assertIn("const niceTickStep=(lo,hi,targetIntervals=8)=>", page)
        self.assertIn("--chart-portfolio: #2646a6", page)
        self.assertIn("--chart-benchmark: #d97706", page)
        self.assertIn(".portfolio { background: var(--chart-portfolio); }", page)
        self.assertIn(".index { background: var(--chart-benchmark); }", page)
        self.assertIn("color:chartColors.portfolio", page)
        self.assertIn("color:chartColors.benchmark", page)
        self.assertNotIn(".index { background: #8a5cf6; }", page)
        self.assertIn("yTickStep:10", page)
        self.assertIn("niceYTicks:true,targetYIntervals:8", page)
        self.assertIn("Math.floor(lo/tickStep)*tickStep", page)
        self.assertIn("Math.ceil(hi/tickStep)*tickStep", page)
        self.assertIn('"Sharpe ratio": "metric-sharpe-ratio"', page)
        self.assertIn(
            '"Portfolio total return": "metric-portfolio-total-return"', page
        )
        self.assertIn("const metricGuideHref=label=>`/metrics-guide#", page)
        self.assertIn("/metrics-guide#metric-top-5-etl-contributors", page)
        self.assertIn("/metrics-guide#metric-bottom-5-etl-contributors", page)
        self.assertIn("/metrics-guide#metric-expected-tail-loss-contribution", page)
        self.assertIn("ETL residual contribution", page)
        self.assertIn("Cash activity", page)
        self.assertIn('id="cash-from"', page)
        self.assertIn('id="cash-to"', page)
        self.assertIn("Apply cash filters", page)
        self.assertIn("Download cash activity CSV", page)
        self.assertIn("Showing all", page)
        self.assertIn("/cash?", page)
        self.assertIn("/export/cash-activity.csv?", page)
        self.assertIn("Trade history", page)
        self.assertIn('id="trade-from"', page)
        self.assertIn('id="trade-to"', page)
        self.assertIn('id="trade-symbol"', page)
        self.assertIn('id="trade-side"', page)
        self.assertIn("Download trade history CSV", page)
        self.assertIn("/trades?", page)
        self.assertIn("/export/trades.csv?", page)
        self.assertIn("Data exports", page)
        self.assertIn("Monthly factsheets and presentations", page)
        self.assertIn("Generate secure downloads", page)
        self.assertIn(
            "/api/accounts/${encodeURIComponent(account)}/reporting/monthly", page
        )
        self.assertIn("if(prepared.account_id!==account)", page)
        self.assertNotIn("fetch('/api/reporting/monthly')", page)
        self.assertNotIn("/prepare`,{method:'POST'}", page)
        self.assertIn("System health", page)
        self.assertIn("Live grounded analysis", page)
        self.assertIn("Saved chats", page)
        self.assertIn("New chat", page)
        self.assertIn(".ai-chat-sidebar { min-width: 0; overflow: hidden;", page)
        self.assertIn(".ai-conversation-item { display: block; width: 100%; min-width: 0; max-width: 100%; overflow: hidden;", page)
        self.assertIn("-webkit-line-clamp: 2", page)
        self.assertIn("Save Markdown", page)
        self.assertIn("Save JSON", page)
        self.assertIn("Download analysis JSON", page)
        self.assertIn("CSV data", page)
        self.assertIn("deterministic tools", page)
        self.assertIn("Message the AI Portfolio Analyst", page)
        self.assertIn("Exact figures, requested metrics and chart series", page)
        self.assertIn("Daily portfolio analyst assurance", page)
        self.assertIn("24/7 controlled autonomy", page)
        self.assertIn("Owner-approved administrator actions", page)
        self.assertIn('class="admin-layout"', page)
        self.assertIn("grid-template-columns: minmax(0, 1.7fr) minmax(320px, 1fr)", page)
        self.assertIn(".admin-layout { grid-template-columns: 1fr; }", page)
        self.assertIn("/api/system/autonomous-administrator", page)
        self.assertIn("Preview action", page)
        self.assertIn("Approve and execute once", page)
        self.assertNotIn("Private internal production", page)
        self.assertNotIn("Shareholder-ready", page)
        self.assertIn('class="company-logo"', page)
        self.assertIn('src="data:image/png;base64,', page)
        self.assertIn('alt="Sprout Asset Management (Asia) Limited company logo"', page)
        self.assertIn(
            'class="company-logo-link" href="/" aria-label="Go to Accounts home"',
            page,
        )
        self.assertNotIn("benchmark-warning", page)
        self.assertNotIn(
            "S&P 500 Price Index levels are sourced from Yahoo Finance", page
        )
        self.assertNotIn(
            "NASDAQ Composite Price Index levels are sourced from Yahoo Finance", page
        )
        self.assertNotIn(
            "The August 2026 approved v6 factsheet and 20-slide v23 presentation are the approved design baseline",
            page,
        )

        risk_page = account_page(
            "U16088571",
            "risk",
            None,
            {"email": "analyst@example.com"},
        )
        self.assertIn('class="page-view active" data-section="risk"', risk_page)
        self.assertIn('<h2 class="sr-only">Risk analytics</h2>', risk_page)
        self.assertEqual(risk_page.count('class="page-view active"'), 1)

        modelling_page = account_page(
            "U16088571",
            "modelling",
            None,
            {"email": "analyst@example.com"},
        )
        self.assertIn('class="page-view active" data-section="modelling"', modelling_page)
        self.assertIn('href="/accounts/U16088571/modelling"', modelling_page)
        for card in ("model-projection", "model-whatif", "model-optimise", "model-factors"):
            self.assertIn(f'id="{card}"', modelling_page)
        self.assertIn("if(activeSection==='modelling')renderModelling(asOf);", modelling_page)

    def test_account_subpages_reject_unknown_accounts_and_sections(self) -> None:
        for account_id, section in (
            ("ANOTHER_ACCOUNT", "overview"),
            ("U16088571", "unknown"),
        ):
            with self.subTest(account_id=account_id, section=section), self.assertRaises(
                HTTPException
            ) as raised:
                account_page(
                    account_id,
                    section,
                    None,
                    {"email": "analyst@example.com"},
                )
            self.assertEqual(raised.exception.status_code, 404)

    def test_onboarding_subject_preview_is_owner_only_and_hides_unready_tools(self) -> None:
        owner = {
            "email": "owner@example.com",
            "object_id": "owner-123",
            "role": "owner",
        }
        subject_cursor = MagicMock()
        subject_cursor.fetchone.return_value = {
            "subject_id": "U16088606",
            "display_name": "U16088606-Greater China",
            "reporting_currency": "USD",
            "history_start_date": "2025-01-20",
            "lifecycle_status": "onboarding",
        }
        feature_cursor = MagicMock()
        feature_cursor.fetchall.return_value = [
            {"feature_key": "data_ingestion", "feature_state": "preview"},
            {"feature_key": "website_analytics", "feature_state": "blocked"},
            {"feature_key": "ai_analyst", "feature_state": "disabled"},
        ]
        connection = MagicMock()
        connection.execute.side_effect = [subject_cursor, feature_cursor]
        with patch.object(web, "_database") as database:
            database.return_value.__enter__.return_value = connection
            page = account_page("U16088606", "overview", None, owner)
        self.assertIn("Owner-only onboarding preview", page)
        self.assertIn("U16088606-Greater China", page)
        self.assertIn('href="/accounts/U16088606/performance"', page)
        self.assertNotIn('href="/accounts/U16088606/reports"', page)
        self.assertNotIn('href="/accounts/U16088606/ai-analyst"', page)
        self.assertIn("const previewMode=true", page)
        self.assertIn(
            'id="cash-from" type="date" min="2025-01-20"', page
        )
        self.assertIn(
            'id="trade-from" type="date" min="2025-01-20"', page
        )

        viewer = {
            "email": "viewer@example.com",
            "object_id": "viewer-123",
            "role": "viewer",
        }
        with self.assertRaises(HTTPException) as raised:
            account_page("U16088606", "overview", None, viewer)
        self.assertEqual(raised.exception.status_code, 404)

        subject_cursor.reset_mock()
        feature_cursor.reset_mock()
        connection.execute.side_effect = [subject_cursor, feature_cursor]
        with patch.object(web, "_database") as database, self.assertRaises(
            HTTPException
        ) as raised:
            database.return_value.__enter__.return_value = connection
            account_page("U16088606", "reports", None, owner)
        self.assertEqual(raised.exception.status_code, 404)

    def test_onboarding_preview_rejects_an_incomplete_subject_contract(self) -> None:
        owner = {
            "email": "owner@example.com",
            "object_id": "owner-123",
            "role": "owner",
        }
        subject_cursor = MagicMock()
        subject_cursor.fetchone.return_value = {
            "subject_id": "U16088606",
            "display_name": "U16088606-Greater China",
            "reporting_currency": "USD",
            "history_start_date": None,
            "lifecycle_status": "onboarding",
        }
        connection = MagicMock()
        connection.execute.return_value = subject_cursor
        with patch.object(web, "_database") as database, self.assertRaises(
            HTTPException
        ) as raised:
            database.return_value.__enter__.return_value = connection
            account_page("U16088606", "overview", None, owner)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("history_start_date", raised.exception.detail)

    def test_viewer_can_browse_tools_but_not_administration(self) -> None:
        viewer = {
            "email": "viewer@example.com",
            "object_id": "viewer-123",
            "role": "viewer",
        }
        page = account_page("U16088571", "overview", None, viewer)
        self.assertIn("viewer@example.com · Viewer", page)
        self.assertIn('href="/accounts/U16088571/reports"', page)
        self.assertIn('href="/accounts/U16088571/exports"', page)
        self.assertIn('href="/accounts/U16088571/ai-analyst"', page)
        self.assertNotIn('href="/accounts/U16088571/administration"', page)
        self.assertIn("const portalOwner=false", page)
        with self.assertRaises(HTTPException) as raised:
            account_page("U16088571", "administration", None, viewer)
        self.assertEqual(raised.exception.status_code, 403)
        with self.assertRaises(HTTPException) as raised:
            account_page("U16088571", "account-onboarding", None, viewer)
        self.assertEqual(raised.exception.status_code, 403)

    def test_owner_monthly_page_has_individual_file_approval_checklist(self) -> None:
        owner = {
            "email": "owner@example.com",
            "object_id": "owner-123",
            "role": "owner",
        }
        page = account_page("U16088571", "reports", None, owner)
        self.assertIn('id="report-release-list"', page)
        self.assertIn('id="report-release"', page)
        self.assertIn("Submit approval changes", page)
        self.assertIn("Untick it and submit to revoke", page)
        self.assertIn("if(portalOwner)reportPrepare.style.display='none'", page)
        self.assertNotIn('id="report-release-confirmation"', page)
        self.assertIn("const portalOwner=true", page)
        self.assertIn("client distribution", page)

    def test_viewer_catalog_exposes_only_individually_released_files(self) -> None:
        month = {
            "period_end": "2026-08-31", "ready": False,
            "review_ready": True, "partially_released": True,
            "downloadable": True,
            "artifacts": [{"report_type": "presentation", "review_only": True}],
            "review_artifacts": [{"report_type": "presentation", "review_only": True}],
            "released_artifacts": [{"report_type": "factsheet", "review_only": False}],
        }
        access = {
            "preview_mode": False,
            "feature_states": {"factsheet": "active", "presentation": "active"},
            "display_name": "U16088606-Greater China",
            "reporting_currency": "USD",
        }
        viewer = {"email": "viewer@example.com", "object_id": "viewer-123", "role": "viewer"}
        with (
            patch.object(web, "_portfolio_access_context", return_value=access),
            patch.object(web, "_database") as database,
            patch.object(web, "_monthly_report_catalog", return_value=[month]),
        ):
            database.return_value.__enter__.return_value = MagicMock()
            payload = web._monthly_report_payload("U16088606", viewer)
        self.assertEqual(payload["months"][0]["artifacts"], month["released_artifacts"])
        self.assertTrue(payload["months"][0]["downloadable"])
        self.assertFalse(payload["months"][0]["review_ready"])

    def test_csv_export_neutralizes_spreadsheet_formulas(self) -> None:
        for value in ("=1+1", "+cmd", "-2+3", "@SUM(A1:A2)"):
            with self.subTest(value=value):
                self.assertEqual(_csv_safe(value), "'" + value)
        self.assertEqual(_csv_safe("ordinary text"), "ordinary text")
        self.assertEqual(_csv_safe(123.4), 123.4)

    def test_disabled_ai_fails_before_database_access(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "object-123",
                "x-ms-client-principal-name": "analyst@example.com",
            }
        )
        payload = AnalystNarrativeRequest(
            subject_id="U16088571",
            analysis_type="portfolio_overview",
            as_of="2026-09-18",
        )
        disabled = web.AIAnalystConfig(False, "", "", "southeastasia", "disabled", "")
        with patch.object(web, "AI_ANALYST_CONFIG", disabled), patch.object(
            web, "_database", side_effect=AssertionError("database must not be opened")
        ), self.assertRaises(HTTPException) as raised:
            run_grounded_analysis(
                payload,
                request,
                {"object_id": "object-123", "email": "analyst@example.com"},
            )
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "ai_inference_disabled")

    def test_disabled_admin_actions_fail_before_approval_database_access(self) -> None:
        request = _request(
            {
                "x-ms-client-principal-id": "object-123",
                "x-ms-client-principal-name": "analyst@example.com",
            }
        )
        payload = AdminActionPreviewRequest(action_type="refresh_risk_free_rate")
        disabled = web.AdminActionConfig(False, "staging", "", "", "")
        with patch.object(web, "ADMIN_ACTION_CONFIG", disabled), patch.object(
            web, "_approval_database", side_effect=AssertionError("database must not be opened")
        ), self.assertRaises(HTTPException) as raised:
            preview_admin_action(
                payload,
                request,
                {"object_id": "object-123", "email": "analyst@example.com"},
            )
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "admin_actions_disabled")


if __name__ == "__main__":
    unittest.main()
