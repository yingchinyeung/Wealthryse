from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ibkr_etl.config import load_settings
from ibkr_etl.db import HybridRow, PostgresConnection, _hybrid_row_factory


class _FakeRawConnection:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def execute(self, *args, **kwargs):
        self.calls.append((*args, kwargs))
        return _FakeRawCursor()


class _FakeRawCursor:
    rowcount = 1

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return [(1,)]

    def __iter__(self):
        return iter([(1,)])


class PostgresAdapterTests(unittest.TestCase):
    def test_row_factory_accepts_statements_without_result_columns(self) -> None:
        cursor = type("Cursor", (), {"description": None})()
        row = _hybrid_row_factory(cursor)(())
        self.assertEqual(dict(row), {})

    def test_hybrid_row_supports_index_name_and_dict(self) -> None:
        row = HybridRow(("account_id", "total"), ("A1", 125.5))
        self.assertEqual(row[0], "A1")
        self.assertEqual(row["total"], 125.5)
        self.assertEqual(dict(row), {"account_id": "A1", "total": 125.5})

    def test_qmark_parameters_are_translated_for_psycopg(self) -> None:
        raw = _FakeRawConnection()
        connection = PostgresConnection(raw)
        connection.execute("SELECT * FROM t WHERE a = ? AND b = ?", (1, 2))
        self.assertEqual(raw.calls[0][0], "SELECT * FROM t WHERE a = %s AND b = %s")
        self.assertEqual(raw.calls[0][1], (1, 2))
        self.assertEqual(raw.calls[0][2], {"prepare": False})

    def test_crisis_as_of_null_check_has_an_explicit_postgres_type(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "ibkr_etl"
            / "crisis_replay.py"
        ).read_text(encoding="utf-8")
        self.assertIn("CAST(? AS TEXT) IS NULL", source)
        self.assertNotIn("(? IS NULL OR p.snapshot_date", source)

    def test_cloud_settings_are_loaded_without_exposing_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / ".env"
            env_file.write_text(
                "DATABASE_URL=postgresql://example.invalid/ibkr\n"
                "PORTFOLIO_TENANT_ID=11111111-1111-1111-1111-111111111111\n"
                "AZURE_STORAGE_ACCOUNT_URL=https://example.blob.core.windows.net\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(env_file)
        self.assertEqual(settings.database_backend, "postgresql")
        self.assertEqual(settings.tenant_id, "11111111-1111-1111-1111-111111111111")
        self.assertEqual(
            settings.azure_storage_account_url,
            "https://example.blob.core.windows.net",
        )


class SecurityDefinitionTests(unittest.TestCase):
    def test_postgres_schema_enforces_tenant_isolation(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "postgres"
            / "001_init.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS tenants", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS flex_request_state", migration)
        self.assertGreaterEqual(migration.count("FORCE ROW LEVEL SECURITY"), 10)
        self.assertIn("CREATE POLICY tenant_isolation", migration)
        self.assertIn("CREATE POLICY account_isolation", migration)
        self.assertIn("security_invoker=true", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS derived_portfolios", migration)
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS derived_portfolio_cash_ledger", migration
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS derived_portfolio_cash_balances", migration
        )
        self.assertIn("ALTER TABLE derived_portfolio_nav FORCE ROW LEVEL SECURITY", migration)
        self.assertIn(
            "ALTER TABLE derived_portfolio_cash_ledger FORCE ROW LEVEL SECURITY",
            migration,
        )
        self.assertIn(
            "ALTER TABLE derived_portfolio_cash_balances FORCE ROW LEVEL SECURITY",
            migration,
        )
        self.assertIn("CREATE POLICY tenant_isolation ON derived_portfolio_exclusions", migration)
        self.assertIn(
            "CREATE POLICY tenant_isolation ON derived_portfolio_cash_ledger",
            migration,
        )
        self.assertIn(
            "CREATE POLICY tenant_isolation ON derived_portfolio_cash_balances",
            migration,
        )

    def test_cloud_schedule_is_exactly_1600_hkt_and_has_no_platform_retry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (
            root / "infra" / "job.bicep"
        ).read_text(encoding="utf-8")
        self.assertIn("param scheduleUtc string = '0 8 * * *'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        # Scheduled unless the switch to v2's own Flex pull turns it off.
        self.assertIn("param scheduleEnabled bool = true", template)
        self.assertIn("triggerType: scheduleEnabled ? 'Schedule' : 'Manual'", template)
        self.assertIn("keyVaultUrl:", template)
        self.assertIn("name: 'IBKR_RUN_MIGRATIONS'", template)
        self.assertIn("value: 'false'", template)
        self.assertIn("name: 'AZURE_CLIENT_ID'", template)
        self.assertIn("value: jobIdentity.properties.clientId", template)
        self.assertNotIn("IBKR_FLEX_TOKEN=", template)
        deploy = (root / "scripts" / "deploy_azure.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("daily 16:00 HKT", deploy)
        self.assertIn("Schedule: 0 8 * * * UTC", deploy)
        self.assertNotIn("06:00 HKT", deploy)
        status = (root / "scripts" / "check_azure_status.ps1").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("0 22 * * *", status)
        self.assertNotIn("06:00 HKT", status)
        local_schedule = (root / "scripts" / "install_task.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('[string]$At = "16:00"', local_schedule)
        runner = (
            root / "scripts" / "run_cloud_job.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"derived portfolios"', runner)
        self.assertLess(
            runner.index('"derived portfolios"'), runner.index('"analytics dashboard"')
        )
        rebuild = (
            root / "scripts" / "run_cloud_derived_rebuild.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("ibkr_etl.cli", rebuild)
        self.assertIn('"derived portfolios"', rebuild)
        self.assertIn('"analytics dashboard"', rebuild)
        self.assertIn('"analyst SQLite and CSV exports"', rebuild)

    def test_cloud_runtime_uses_a_separate_least_privilege_role(self) -> None:
        root = Path(__file__).resolve().parents[1]
        main = (root / "infra" / "main.bicep").read_text(encoding="utf-8")
        bootstrap = (root / "scripts" / "bootstrap_postgres.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("param applicationLogin string = 'ibkr_app'", main)
        self.assertIn("name: 'database-admin-url'", main)
        self.assertIn("name: 'database-url'", main)
        self.assertIn("GRANT SELECT, INSERT, UPDATE, DELETE", bootstrap)
        self.assertNotIn("GRANT ALL", bootstrap)

    def test_blob_recovery_controls_are_declared(self) -> None:
        main = (
            Path(__file__).resolve().parents[1] / "infra" / "main.bicep"
        ).read_text(encoding="utf-8")
        self.assertIn("isVersioningEnabled: true", main)
        self.assertIn("delete-previous-versions-after-30-days", main)
        self.assertIn("daysAfterCreationGreaterThan: 30", main)

    def test_deployment_locks_the_production_image(self) -> None:
        deploy = (
            Path(__file__).resolve().parents[1] / "scripts" / "deploy_azure.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("az acr manifest show-metadata", deploy)
        self.assertIn('"ibkr-etl@$ImageDigest"', deploy)
        self.assertIn("--write-enabled false", deploy)
        self.assertIn("--delete-enabled false", deploy)

    def test_platform_reporting_migration_is_tenant_isolated(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "postgres"
            / "002_platform_reporting.sql"
        ).read_text(encoding="utf-8")
        for table in (
            "benchmark_master",
            "benchmark_prices",
            "risk_free_rates",
            "performance_comparison_daily",
            "reporting_snapshots",
            "report_generation_runs",
            "app_users",
            "agent_tool_audit",
            "approved_actions",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
        self.assertGreaterEqual(migration.count("FORCE ROW LEVEL SECURITY"), 14)
        self.assertIn("CREATE POLICY tenant_isolation", migration)

    def test_crisis_replay_migration_is_tenant_isolated(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "postgres"
            / "007_crisis_holdings_replay.sql"
        ).read_text(encoding="utf-8")
        for table in ("crisis_replay_components", "crisis_replay_daily"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", migration)
            self.assertIn(f"CREATE POLICY tenant_isolation ON {table}", migration)

    def test_historical_report_queue_has_an_idempotency_key(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "postgres" / "011_historical_reporting_batch.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE UNIQUE INDEX", migration)
        self.assertIn(
            "tenant_id, snapshot_id, report_type, template_id, template_version",
            " ".join(migration.split()),
        )

    def test_historical_report_job_is_manual_fail_closed_and_no_retry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "infra" / "staging-historical-report-job.bicep").read_text(
            encoding="utf-8"
        )
        script = (
            root / "scripts" / "generate_historical_month_end_batch.py"
        ).read_text(encoding="utf-8")
        self.assertIn("triggerType: 'Manual'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("parallelism: 1", template)
        self.assertIn("ibkr-etl@${imageDigest}", template)
        self.assertNotIn("ibkr-platform@${imageDigest}", template)
        self.assertIn("historicalYahooTransmissionApproved bool = false", template)
        self.assertNotIn("FLEX_TOKEN", template)
        self.assertIn("DEPLOYMENT_ENVIRONMENT", script)
        self.assertIn("YAHOO_HISTORICAL_HOLDINGS_APPROVED", script)
        self.assertIn("ALLOW_UNLICENSED_BENCHMARK", script)
        self.assertIn("refresh_sector_classifications_range", script)

    def test_pilot_benchmark_contract_is_explicit_and_unlicensed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "postgres" / "003_pilot_benchmark_contract.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("'U16088571', 'XNDX', 'primary', '2025-01-20'", migration)
        self.assertIn("'gross_total_return'", migration)
        self.assertIn("'pending'", migration)
        self.assertNotIn("'approved'", migration)

        provisional = (
            root / "migrations" / "postgres" / "004_provisional_benchmark_feed.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("'provisional_close'", provisional)
        self.assertIn("symbol = 'QQQ'", provisional)
        self.assertIn("QQQ adjusted close", provisional)
        self.assertIn("license_status = 'restricted'", provisional)
        self.assertNotIn("license_status = 'approved'", provisional)

    def test_provisional_benchmark_job_is_staging_only_and_has_no_retry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "infra" / "staging-benchmark-job.bicep").read_text(
            encoding="utf-8"
        )
        script = (root / "scripts" / "refresh_provisional_benchmark.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("environment: 'internal-production'", template)
        self.assertIn("dataLicense: 'yfinance-internal-only'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("cronExpression: '30 8 * * *'", template)
        self.assertIn("ibkr-platform@${imageDigest}", template)
        self.assertIn("DEPLOYMENT_ENVIRONMENT", script)
        self.assertIn("ALLOW_UNLICENSED_BENCHMARK", script)
        self.assertIn("refresh_crisis_replay", script)
        self.assertIn("crisis_replay_observations", script)

    def test_daily_account_sync_is_bounded_multi_account_digest_pinned_and_no_retry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (
            root / "infra" / "staging-daily-account-sync.bicep"
        ).read_text(encoding="utf-8")
        role = (
            root / "infra" / "staging-production-export-reader.bicep"
        ).read_text(encoding="utf-8")
        script = (
            root / "scripts" / "sync_daily_pilot_account.py"
        ).read_text(encoding="utf-8")
        self.assertIn("cronExpression: '10 8 * * *'", template)
        self.assertIn("param scheduleEnabled bool = true", template)
        self.assertIn("triggerType: scheduleEnabled ? 'Schedule' : 'Manual'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("parallelism: 1", template)
        self.assertIn("ibkr-platform@${imageDigest}", template)
        self.assertIn("sourceScope: 'physical-account-wave-0'", template)
        self.assertIn("account_id: 'U16088571'", template)
        self.assertIn("account_id: 'U16088606'", template)
        self.assertIn("ACCOUNT_SYNC_TARGETS_JSON", template)
        self.assertIn("ONBOARDING_WAVE0_ACCOUNT_ID", template)
        self.assertIn("Storage/storageAccounts/blobServices/containers", role)
        self.assertIn("storageBlobReaderRoleId", role)
        self.assertIn("build_pilot_snapshot", script)
        self.assertIn("validate_production_export_manifest", script)
        self.assertIn("parse_account_sync_targets", script)
        self.assertIn("register_source_discoveries", script)
        self.assertNotIn("FLEX_TOKEN", template)
        self.assertNotIn("ibkr-flex", template.lower())

    def test_treasury_risk_free_job_is_staging_only_and_has_no_retry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "infra" / "staging-risk-free-job.bicep").read_text(
            encoding="utf-8"
        )
        script = (root / "scripts" / "refresh_staging_risk_free.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("environment: 'staging'", template)
        self.assertIn("dataSource: 'us-treasury'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("cronExpression: '0 23 * * *'", template)
        self.assertIn("parallelism: 1", template)
        self.assertIn("ibkr-platform@${imageDigest}", template)
        self.assertNotIn("FLEX_TOKEN", template)
        self.assertIn("DEPLOYMENT_ENVIRONMENT", script)
        self.assertIn("staging-us-treasury-risk-free", script)

    def test_controlled_autonomy_is_tenant_isolated_and_narrowly_scoped(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "postgres" / "013_controlled_autonomy.sql"
        ).read_text(encoding="utf-8")
        template = (root / "infra" / "staging-autonomous-admin.bicep").read_text(
            encoding="utf-8"
        )
        script = (root / "scripts" / "run_autonomous_administrator.py").read_text(
            encoding="utf-8"
        )
        for table in (
            "autonomy_control",
            "autonomous_agent_runs",
            "system_incidents",
            "autonomous_actions",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("max_actions_per_run BETWEEN 0 AND 1", migration)
        self.assertIn("cooldown_minutes >= 60", migration)
        self.assertIn("cronExpression: '*/5 * * * *'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("parallelism: 1", template)
        self.assertIn("failed-execution", template)
        self.assertIn("ibkrv2stg-alerts", template)
        self.assertIn("database-approval-url", template)
        self.assertIn("ibkrv2stg-benchmark-daily", template)
        self.assertIn("ibkrv2stg-risk-free-daily", template)
        self.assertNotIn("FLEX_TOKEN", template)
        self.assertNotIn("ibkr-flex", template.lower())
        self.assertIn("pg_try_advisory_lock", script)
        self.assertIn("plan_safe_repairs", script)
        self.assertNotIn("subprocess", script)
        recovery_migration = (
            root / "migrations" / "postgres" / "014_autonomy_recovery_and_reviews.sql"
        ).read_text(encoding="utf-8")
        for table in (
            "autonomy_release_baselines",
            "autonomy_recovery_state",
            "autonomy_authority_reviews",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", recovery_migration)
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", recovery_migration)
        self.assertIn("rollback_failure_threshold = 3", recovery_migration)
        self.assertIn("Microsoft.App/containerApps/revisions/activate/action", template)
        self.assertIn("Microsoft.App/containerApps/revisions/read", template)
        self.assertNotIn("Microsoft.App/containerApps/write", template)
        self.assertIn("cronExpression: '0 1 1 * *'", template)
        self.assertIn("run_monthly_authority_review.py", template)

    def test_stage_four_acceptance_is_tenant_isolated_audited_and_daily(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "postgres" / "015_burn_in_acceptance.sql"
        ).read_text(encoding="utf-8")
        template = (
            root / "infra" / "staging-burn-in-monitoring.bicep"
        ).read_text(encoding="utf-8")
        script = (root / "scripts" / "run_burn_in_check.py").read_text(
            encoding="utf-8"
        )
        for table in ("burn_in_acceptance_windows", "burn_in_daily_results"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("required_consecutive_days = 14", migration)
        self.assertIn("idx_burn_in_one_running_window", migration)
        self.assertIn("cronExpression: '15 9 * * *'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        # v1.1's pull stopped on 17 Oct 2026; its own alert stays with v1.1.
        self.assertNotIn("  'ibkr-v11-daily'\n", template)
        self.assertIn("ibkrv2stg-institutional-daily", template)
        self.assertIn("param actionGroupName string = 'ibkrv2stg-alerts'", template)
        self.assertIn("ibkrv2stg-autonomous-admin", template)
        self.assertIn("ibkrv2stg-autonomy-review", template)
        self.assertIn("database-approval-url", template)
        self.assertIn("ibkr-platform@${imageDigest}", template)
        self.assertNotIn("ibkr-portfolio@${imageDigest}", template)
        self.assertIn("_record_acceptance_result", script)
        self.assertIn("ON CONFLICT (tenant_id, window_id, result_date)", script)
        self.assertIn("default=str", script)
        self.assertIn("ibkrv2stg-analyst-assurance", template)
        self.assertIn(
            "The daily portfolio-analyst data, metrics and report assurance job failed.",
            template,
        )
        bootstrap = (root / "scripts" / "bootstrap_postgres.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "GRANT SELECT ON TABLE accounts, nav_snapshots, benchmark_prices, risk_free_rates",
            bootstrap,
        )

    def test_portfolio_analyst_assurance_is_daily_grounded_and_tenant_isolated(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "postgres" / "016_portfolio_analyst_assurance.sql"
        ).read_text(encoding="utf-8")
        template = (
            root / "infra" / "staging-portfolio-analyst-assurance.bicep"
        ).read_text(encoding="utf-8")
        script = (
            root / "scripts" / "run_portfolio_analyst_assurance.py"
        ).read_text(encoding="utf-8")
        for table in (
            "portfolio_analyst_assurance_runs",
            "portfolio_analyst_assurance_findings",
        ):
            self.assertIn(f"CREATE TABLE {table}", migration)
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("cronExpression: '10 9 * * *'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("gpt-5-6-sol-apac", template)
        self.assertIn("apac-data-zone", template)
        self.assertIn("run_registry_portfolio_assurance.py", template)
        registry_script = (
            root / "scripts" / "run_registry_portfolio_assurance.py"
        ).read_text(encoding="utf-8")
        self.assertIn("subject_operating_registrations", registry_script)
        self.assertIn("run_portfolio_analyst_assurance.py", registry_script)
        self.assertIn("ibkr-platform@${imageDigest}", template)
        self.assertNotIn("ibkr-portfolio@${imageDigest}", template)
        self.assertIn("generate_assurance_review", script)
        self.assertIn("evaluate_portfolio_assurance", script)
        self.assertIn("ON CONFLICT (tenant_id, subject_id, assurance_date)", script)

    def test_month_end_package_job_is_scheduled_pinned_ai_grounded_and_layout_locked(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (
            root / "infra" / "staging-month-end-factsheet-job.bicep"
        ).read_text(encoding="utf-8")
        script = (
            root / "scripts" / "generate_month_end_package.py"
        ).read_text(encoding="utf-8")
        renderer = (
            root / "scripts" / "render_monthly_presentation.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn("cronExpression: '0 9 * * *'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("parallelism: 1", template)
        self.assertIn("ibkr-etl@${imageDigest}", template)
        self.assertNotIn("ibkr-platform@${imageDigest}", template)
        self.assertIn("approved-august-2026-v6", template)
        self.assertIn("approved-august-2026-20-slide-v24", template)
        self.assertIn("gpt-5-6-sol-apac", template)
        self.assertIn("AI_PROCESSING_SCOPE", template)
        self.assertIn("PRESENTATION_TEMPLATE_SHA256", template)
        self.assertIn("4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191", template)
        self.assertIn("5e0bd9bd-7b93-4f28-af87-19fc36ad61bd", template)
        self.assertNotIn("FLEX_TOKEN", template)
        self.assertIn("factsheet_layout_version", script)
        self.assertIn("active_factsheet_layout", script)
        self.assertIn("render_factsheet", script)
        self.assertIn("layout_authority", script)
        self.assertIn("deterministic-renderers", script)
        # Factsheets only: no deck and no commentary (commentary belonged to the deck).
        self.assertNotIn("generate_month_end_commentary", script)
        self.assertNotIn("render_monthly_presentation.mjs", script)
        self.assertNotIn("PRESENTATION_TEMPLATE_PATH", script)
        self.assertIn('PACKAGE_REPORT_TYPES = ("factsheet",)', script)
        self.assertIn("month_end_report_package_generated", script)
        self.assertIn("'system', 'gpt-5.6-sol-portfolio-analyst'", script)
        self.assertIn("awaiting_approval", script)
        self.assertIn("active_month_end_snapshot_id", script)
        self.assertIn('snapshot_action = "reused_existing"', script)
        self.assertIn("asset_role IN ('template', 'completed_example')", script)
        self.assertIn('set(assets) != {"template", "completed_example"}', script)
        self.assertIn('render_asset = assets["template"]', script)
        self.assertIn("supporting-sha256", script)
        self.assertIn('setText("sh/rip4retw", "Results ");', renderer)
        self.assertIn('U16088606: "iStrategy: Greater China"', renderer)
        self.assertIn('setText("sh/nu58f2hs", governedDisplayName)', renderer)
        self.assertIn("Greater China presentation contains the wrong strategy identity", renderer)
        self.assertNotIn("monthShort(periodEnd)", renderer)

    def test_factsheet_layout_upgrade_is_manual_approved_and_zero_retry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (
            root / "infra" / "staging-nav-axis-upgrade-job.bicep"
        ).read_text(encoding="utf-8")
        script = (
            root / "scripts" / "register_historical_review_batch.py"
        ).read_text(encoding="utf-8")
        self.assertIn("triggerType: 'Manual'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("ALLOW_RELEASED_INTERNAL_REPLACEMENT", template)
        self.assertIn("REPORT_REPLACEMENT_APPROVED_BY", template)
        self.assertIn("approved-august-2026-v6", template)
        self.assertIn("approved-august-2026-20-slide-v24", template)
        self.assertNotIn("FLEX_TOKEN", template)
        self.assertIn("LAYOUT_UPGRADE_EVENT_TYPE", template)
        self.assertIn("historical_reports_layout_upgraded", script)
        self.assertIn("Client-published run cannot be replaced", script)

    def test_return_panel_historical_replacement_is_fixed_scope_and_audited(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (
            root / "scripts" / "upgrade_historical_factsheets_v6.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"U16088571"', script)
        self.assertIn('"U16088606"', script)
        self.assertIn("UPGRADE_SUBJECT_ID", script)
        self.assertIn("qa-return-panel-v7", script)
        self.assertIn("REPORT_REPLACEMENT_APPROVED_BY", script)
        self.assertIn("historical_factsheet_return_panel_v7_applied", script)
        self.assertIn("status == \"published\"", script)
        self.assertNotIn("FLEX_TOKEN", script)

    def test_presentation_v24_historical_upgrade_is_manual_scoped_and_zero_retry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (
            root / "infra" / "staging-historical-presentation-v24-job.bicep"
        ).read_text(encoding="utf-8")
        script = (
            root / "scripts" / "upgrade_historical_presentations_v24.py"
        ).read_text(encoding="utf-8")
        self.assertIn("triggerType: 'Manual'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("approved-august-2026-20-slide-v24", template)
        self.assertIn("4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191", template)
        self.assertNotIn("FLEX_TOKEN", template)
        self.assertIn("EXPECTED_PERIODS", script)
        self.assertIn("build_monthly_reporting_analytics", script)
        self.assertIn("historical_presentations_v24_applied", script)
        self.assertIn("Client-published presentation cannot be replaced", script)

    def test_presentation_v24_label_upgrade_is_manual_scoped_and_zero_retry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (
            root / "infra" / "staging-presentation-v24-upgrade-job.bicep"
        ).read_text(encoding="utf-8")
        script = (
            root / "scripts" / "upgrade_august_presentation_v24.py"
        ).read_text(encoding="utf-8")
        self.assertIn("triggerType: 'Manual'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("approved-august-2026-20-slide-v24", template)
        self.assertIn("4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191", template)
        self.assertNotIn("FLEX_TOKEN", template)
        self.assertIn('ACCOUNT_ID = "U16088571"', script)
        self.assertIn('PERIOD_END = "2026-08-31"', script)
        self.assertIn('"label": "Results"', script)
        self.assertIn(r're.search(r">Results\s*</a:t>", slide_18)', script)
        self.assertIn("released_internal_replaced", script)

    def test_sp500_total_return_replaces_xndx_as_primary(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "postgres" / "005_sp500_total_return_primary.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("'SP500TR', '^SP500TR', 'S&P 500 Total Return Index'", migration)
        self.assertIn("benchmark_id = 'XNDX'", migration)
        self.assertIn("SET active = false", migration)
        self.assertIn("DELETE FROM portfolio_benchmark_mapping", migration)
        self.assertNotIn("license_status = 'approved'", migration)

    def test_price_index_primary_and_secondary_contracts_replace_sp500tr(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "postgres" / "006_price_index_benchmarks.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("'GSPC', '^GSPC', 'S&P 500 Price Index'", migration)
        self.assertIn("'IXIC', '^IXIC', 'NASDAQ Composite Price Index'", migration)
        self.assertIn("'GSPC', 'primary', '2025-01-20'", migration)
        self.assertIn("'IXIC', 'secondary', '2025-01-20'", migration)
        self.assertIn("benchmark_id = 'SP500TR'", migration)
        self.assertIn("SET active = false", migration)
        self.assertIn("DELETE FROM portfolio_benchmark_mapping", migration)
        self.assertNotIn("license_status = 'approved'", migration)

        web = (root / "src" / "ibkr_etl" / "web.py").read_text(encoding="utf-8")
        self.assertIn('id="benchmark-select"', web)
        self.assertIn('id="asof-select"', web)
        self.assertIn('/available-dates', web)
        self.assertIn("benchmark_id: str | None = None", web)
        self.assertGreaterEqual(web.count("as_of: str | None = None"), 4)
        self.assertIn("%s::text IS NULL", web)
        self.assertIn("encodeURIComponent(id)", web)
        self.assertIn("encodeURIComponent(asOf)", web)
        self.assertIn("observation_date <= %s", web)
        self.assertIn("renderAsOf", web)
        self.assertIn('/api/portfolio/{account_id}/cash', web)
        self.assertIn('/api/portfolio/{account_id}/risk', web)
        self.assertIn('/api/system/health', web)
        self.assertIn('/export/{dataset}.csv', web)
        self.assertIn("_csv_safe", web)
        self.assertIn("m.mapping_type", web)
        self.assertEqual(web.count('class="chart-tooltip"'), 3)
        self.assertIn("ctx.fillText('Date'", web)
        self.assertIn("yLabel:'Rebased value'", web)
        self.assertIn("yLabel:`NAV (${{reportingCurrency}})`", web)
        self.assertIn("unit:reportingCurrency", web)
        self.assertIn("c.onmousemove", web)
        self.assertIn("exact(row[line.key])", web)

    def test_wave0_financial_contract_registers_names_and_price_benchmarks(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "postgres" / "020_wave0_financial_contract.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("U16088606-Greater China", migration)
        self.assertIn("U16088571 (U.S. Technology and Biotechnology)", migration)
        self.assertIn("'TAIEX', '^TWII', 'TAIEX Price Index'", migration)
        self.assertIn("'CSI300', '000300.SS', 'CSI 300 Price Index'", migration)
        self.assertIn("'SHCOMP', '000001.SS', 'Shanghai Composite Price Index'", migration)
        self.assertIn("'HSI', '^HSI', 'Hang Seng Price Index'", migration)
        self.assertIn("'TAIEX', 'primary', '2025-01-20'", migration)
        self.assertIn("'CSI300', 'secondary', '2025-01-20'", migration)
        self.assertIn("'SHCOMP', 'secondary', '2025-01-20'", migration)
        self.assertIn("'HSI', 'secondary', '2025-01-20'", migration)
        self.assertIn("'benchmark_fx_conversion_required', true", migration)
        self.assertIn("stage = 'analytics_configuration'", migration)
        self.assertNotIn("license_status = 'approved'", migration)

    def test_u16088571_display_name_uses_platform_account_format(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root
            / "migrations"
            / "postgres"
            / "050_u16088571_display_name_format.sql"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "display_name = 'U16088571-U.S. Technology and Biotechnology'",
            migration,
        )
        self.assertIn("subject_id = 'U16088571'", migration)
        self.assertNotIn("DELETE FROM", migration)

    def test_benchmark_job_refreshes_all_registered_accounts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "infra" / "staging-benchmark-job.bicep").read_text(
            encoding="utf-8"
        )
        script = (root / "scripts" / "refresh_provisional_benchmark.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("param benchmarkAccountIds string = ''", template)
        self.assertIn("BENCHMARK_ACCOUNT_IDS", template)
        self.assertIn("BENCHMARK_ACCOUNT_IDS", script)
        self.assertIn("subject_operating_registrations", script)
        self.assertIn("benchmark_refresh", script)
        self.assertIn("_external_market_data_approved", script)
        self.assertIn("account_onboarding_approvals", script)

        gate = (
            root / "migrations" / "postgres" / "021_wave0_benchmark_fx_gate.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("benchmark_fx_reconciled", gate)
        self.assertIn("provisional_close", gate)

        revised_contract = (
            root / "migrations" / "postgres" / "022_remove_wave0_csi300.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("DELETE FROM portfolio_benchmark_mapping", revised_contract)
        self.assertIn("benchmark_id = 'CSI300'", revised_contract)
        self.assertIn("jsonb_build_array('SHCOMP', 'HSI')", revised_contract)

    def test_staging_manual_jobs_have_no_retry_and_no_flex_secret(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for name in (
            "staging-bootstrap-job.bicep",
            "staging-import-job.bicep",
            "staging-verify-job.bicep",
            "staging-report-snapshot-job.bicep",
            "staging-template-intake-job.bicep",
        ):
            template = (root / "infra" / name).read_text(encoding="utf-8")
            self.assertIn("triggerType: 'Manual'", template)
            self.assertIn("replicaRetryLimit: 0", template)
            self.assertIn("environment: 'staging'", template)
            self.assertNotIn("ibkr-flex-token", template.lower())

    def test_template_intake_is_hash_verified_private_and_fail_closed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "infra" / "staging-template-intake-job.bicep").read_text(
            encoding="utf-8"
        )
        script = (
            root / "scripts" / "register_account_template_intake.py"
        ).read_text(encoding="utf-8")
        migration = (
            root
            / "migrations"
            / "postgres"
            / "023_subject_report_template_registry.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("EXPECTED_TEMPLATE_MANIFEST_SHA256", template)
        self.assertIn("internal-reports", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("download_blob_bytes", script)
        self.assertIn("verify_template_assets", script)
        self.assertNotIn("ibkr-flex", template.lower())
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)

    def test_wave0_report_renderer_uses_greater_china_owner_template(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (
            root / "infra" / "staging-onboarding-report-render-job.bicep"
        ).read_text(encoding="utf-8")
        script = (
            root / "scripts" / "render_onboarding_historical_reports.py"
        ).read_text(encoding="utf-8")
        validator = (
            root / "scripts" / "validate_monthly_presentation.py"
        ).read_text(encoding="utf-8")
        renderer = (
            root / "scripts" / "render_monthly_presentation.mjs"
        ).read_text(encoding="utf-8")
        migration = (
            root
            / "migrations"
            / "postgres"
            / "031_u16088606_presentation_contract.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("ibkr-etl@${imageDigest}", template)
        self.assertNotIn("ibkr-platform@${imageDigest}", template)
        self.assertIn("run_self_service_onboarding_reports.py", template)
        self.assertIn("subject_report_template_assets", (
            root / "scripts" / "run_self_service_onboarding_reports.py"
        ).read_text(encoding="utf-8"))
        self.assertIn(
            "/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx",
            template,
        )
        self.assertIn("render_monthly_presentation.mjs", script)
        self.assertIn(
            "approved_u16088571_platform_layout_with_subject_identity",
            script,
        )
        self.assertIn("PLATFORM_PRESENTATION_TEMPLATE_SHA256", script)
        self.assertIn("presentation_identity_scope", script)
        self.assertIn('if subject_id == "U16088606"', script)
        self.assertIn("compatibility path", script)
        self.assertIn("owner-approved presentation identity", script)
        self.assertIn("account_onboarding_approvals", script)
        self.assertIn("Presentation contains a strategy name from another account", validator)
        self.assertIn("expected_security_axis_labels", validator)
        self.assertIn("must use security names from the frozen snapshot", validator)
        self.assertIn("categories: holdings.slice(0, 10).map(securityName)", renderer)
        self.assertIn("categories: contributionRows.map(securityName)", renderer)
        self.assertIn('["Net asset value", money2(nav)]', renderer)
        self.assertIn('`${securityName(holdings[0])} · ${percent(weights[0])}`', renderer)
        self.assertIn('`${securityName(largestContributor)} · ${points(largestContributor.contribution)}`', renderer)
        self.assertIn('`${securityName(largestDetractor)} · ${points(largestDetractor.contribution)}`', renderer)
        self.assertIn('setTableCellFontSize("tb/wfmpkre1", 1, 1, 10)', renderer)
        self.assertIn('setTableCellFontSize("tb/edoralwf", 1, 1, 10)', renderer)
        self.assertIn('setTableCellFontSize("tb/edoralwf", 2, 1, 10)', renderer)
        self.assertIn('widenSummaryTable("tb/wfmpkre1")', renderer)
        self.assertIn('widenSummaryTable("tb/edoralwf")', renderer)
        self.assertIn("table.columns.get(1).width = 170", renderer)
        self.assertIn("expected_summary_table_rows", validator)
        self.assertIn("_validate_summary_table_rows", validator)
        self.assertIn("expected_slide_text", validator)
        self.assertIn("_validate_slide_text", validator)
        self.assertIn('"PRESENTATION_STRATEGY_NAME": expected_strategy', script)
        self.assertIn("governedStrategyName", renderer)
        self.assertIn("setText(id, governedStrategyName)", renderer)
        self.assertIn("const subjectLabel = governedDisplayName", renderer)
        output_gate = (root / "scripts" / "validate_subject_presentation.py").read_text(encoding="utf-8")
        self.assertIn("validate_subject_presentation", script)
        self.assertIn('expected_slide_text=expectations["slide_text"]', output_gate)
        self.assertIn("platform_content_expectations", output_gate)
        self.assertIn("presentation_content_requirements", script)
        self.assertIn("presentation_content_contract_version", script)
        self.assertIn("ms_financial_audit", output_gate)
        self.assertNotIn(
            "u16088606-greater-china-platform-v24-security-name-commentary-v7",
            script,
        )
        month_end_script = (
            root / "scripts" / "generate_month_end_package.py"
        ).read_text(encoding="utf-8")
        # Per-account decks are no longer built by the month-end job.
        self.assertNotIn("validate_subject_presentation", month_end_script)
        self.assertIn("platform_content_expectations", output_gate)
        self.assertIn("expected_security_axis_labels", output_gate)
        self.assertIn("expected_summary_table_rows", output_gate)
        self.assertIn("expected_slide_text", output_gate)
        self.assertIn(
            '`${securityName(holdings[0])} is the largest ${holdingNoun}.',
            renderer,
        )
        self.assertNotIn(
            '`${holdings[0].symbol} is the largest holding,',
            renderer,
        )
        commentary_migration = (
            root
            / "migrations"
            / "postgres"
            / "040_u16088606_security_name_commentary.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("u16088606_security_name_commentary", commentary_migration)
        self.assertIn("security name instead of its ticker", commentary_migration)
        self.assertIn("status = 'superseded'", commentary_migration)
        fit_migration = (
            root
            / "migrations"
            / "postgres"
            / "041_u16088606_security_name_commentary_fit.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("u16088606_security_name_commentary_fit", fit_migration)
        self.assertIn("inside the mint commentary box", fit_migration)
        self.assertIn("status = 'superseded'", fit_migration)
        self.assertIn("cases.target_subject_id = 'U16088606'", migration)
        self.assertNotIn("cases.subject_id", migration)
        self.assertIn("status = 'superseded'", migration)

        owner_identity = (
            root
            / "migrations"
            / "postgres"
            / "032_owner_controlled_presentation_identity.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("'presentation_identity'", owner_identity)
        self.assertIn("iStrategy: Greater China", owner_identity)
        self.assertIn("'uploader_editable', false", owner_identity)

        evidence_layout = (
            root
            / "migrations"
            / "postgres"
            / "033_u16088606_platform_evidence_layout.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("u16088606_platform_evidence_layout_upgrade", evidence_layout)
        self.assertIn("approved U16088571 v24", evidence_layout)
        self.assertIn("iStrategy: Greater China", evidence_layout)
        self.assertIn("status = 'superseded'", evidence_layout)

        multi_region_layout = (
            root
            / "migrations"
            / "postgres"
            / "034_factsheet_multi_region_allocation.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("factsheet_multi_region_allocation_upgrade", multi_region_layout)
        self.assertIn("every represented region", multi_region_layout)
        self.assertIn("status = 'superseded'", multi_region_layout)
        self.assertIn("checks.check_id = 'historical_reports_backfilled'", multi_region_layout)

    def test_portal_onboarding_intake_is_private_and_narrowly_scoped(self) -> None:
        root = Path(__file__).resolve().parents[1]
        intake = (root / "infra" / "staging-onboarding-intake.bicep").read_text(
            encoding="utf-8"
        )
        web = (root / "infra" / "staging-web.bicep").read_text(encoding="utf-8")
        self.assertIn("param intakeContainerName string = 'onboarding-intake'", intake)
        self.assertIn("publicAccess: 'None'", intake)
        self.assertIn("scope: intake", intake)
        self.assertIn("ibkrv2stg-web-identity", intake)
        self.assertIn("ONBOARDING_INTAKE_CONTAINER", web)
        self.assertNotIn("FLEX_TOKEN", intake)

    def test_reporting_snapshot_is_immutable_and_nonpublishing_by_default(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "postgres" / "009_reporting_snapshot_payload.sql"
        ).read_text(encoding="utf-8")
        template = (root / "infra" / "staging-report-snapshot-job.bicep").read_text(
            encoding="utf-8"
        )
        self.assertIn("publication_allowed BOOLEAN NOT NULL DEFAULT FALSE", migration)
        self.assertIn("reject_frozen_reporting_snapshot_mutation", migration)
        self.assertIn("OLD.status IN ('frozen', 'approved')", migration)
        self.assertIn("scripts/freeze_month_end_snapshot.py", template)
        self.assertIn("ibkr-platform@${imageDigest}", template)
        self.assertIn("secrets/database-app-url", template)
        self.assertIn("DEPLOYMENT_ENVIRONMENT", template)
        self.assertIn("AZURE_STORAGE_ACCOUNT_URL", template)
        self.assertIn("REPORT_DRAFT_CONTAINER", template)
        self.assertIn("report-drafts", template)
        self.assertNotIn("database-admin-url", template)
        self.assertNotIn("ibkr-flex", template.lower())

    def test_internal_release_is_private_and_cannot_bypass_client_gate(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "migrations" / "postgres" / "012_internal_production_release.sql"
        ).read_text(encoding="utf-8")
        foundation = (
            root / "infra" / "staging-internal-release.bicep"
        ).read_text(encoding="utf-8")
        job = (
            root / "infra" / "staging-internal-release-job.bicep"
        ).read_text(encoding="utf-8")
        script = (
            root / "scripts" / "release_internal_reports.py"
        ).read_text(encoding="utf-8")
        self.assertIn("released_internal", migration)
        self.assertIn("internal_shareholder", migration)
        self.assertIn("Client release is blocked until publication_allowed is true", migration)
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("publicAccess: 'None'", foundation)
        self.assertIn("internal-reports", foundation)
        self.assertIn("replicaRetryLimit: 0", job)
        self.assertIn("scripts/release_internal_reports.py", job)
        self.assertIn("client_distribution", script)
        self.assertIn('"prohibited"', script)
        self.assertNotIn("ibkr-flex", job.lower())

    def test_sector_job_requires_explicit_transmission_approval(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (
            root / "infra" / "staging-sector-classification-job.bicep"
        ).read_text(encoding="utf-8")
        script = (
            root / "scripts" / "refresh_staging_sector_classifications.py"
        ).read_text(encoding="utf-8")
        migration = (
            root
            / "migrations"
            / "postgres"
            / "010_provisional_sector_classifications.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("SECTOR_CLASSIFICATION_ACCOUNT_IDS", template)
        self.assertIn("SECTOR_CLASSIFICATION_ACCOUNT_IDS", script)
        self.assertIn("account_onboarding_approvals", script)
        self.assertIn("approval_status = 'approved'", script)
        self.assertIn("revoked_at IS NULL", script)
        self.assertIn("cronExpression: '45 8 * * *'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("SELECT MIN(snapshot_date)", script)
        self.assertIn("refresh_sector_classifications_range", script)
        self.assertIn("license_status", migration)
        self.assertIn("restricted", migration)
        self.assertIn("ibkr-platform@${imageDigest}", template)
        self.assertNotIn("ibkr-flex", template.lower())

    def test_self_service_jobs_are_ordered_and_fail_closed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        institutional = (
            root / "infra" / "staging-institutional-daily.bicep"
        ).read_text(encoding="utf-8")
        derived = (root / "infra" / "staging-derived-refresh.bicep").read_text(
            encoding="utf-8"
        )
        analytics = (
            root / "infra" / "staging-onboarding-analytics-job.bicep"
        ).read_text(encoding="utf-8")
        # Deploying with the defaults keeps the live job since v2's own Flex pull began on
        # 17 Oct 2026: 08:20 UTC, data up to yesterday, sends on.
        self.assertIn("param scheduleUtc string = '20 8 * * *'", institutional)
        self.assertIn("cronExpression: scheduleUtc", institutional)
        self.assertIn("param dispatchRunDate string = 'previous_hkt_day'", institutional)
        self.assertIn("{ name: 'INSTITUTIONAL_DISPATCH_RUN_DATE', value: dispatchRunDate }", institutional)
        self.assertIn("param flexSendsEnabled bool = true", institutional)
        self.assertIn("{ name: 'INSTITUTIONAL_FLEX_SENDS_ENABLED', value: flexSendsEnabled ? 'true' : 'false' }",
                      institutional)
        self.assertIn("INSTITUTIONAL_FLEX_LOOKBACK_DAYS", institutional)
        self.assertIn("{ name: 'IBKR_RUN_MIGRATIONS', value: 'false' }", institutional)
        self.assertIn("AZURE_RAW_CONTAINER", institutional)
        self.assertIn("cronExpression: '50 8 * * *'", derived)
        self.assertIn("run_self_service_derived_refresh.py", derived)
        # Reference data comes from v2's own tables; nothing points at v1.1's storage.
        self.assertIn("param referenceDataSource string = 'database'", derived)
        self.assertNotIn("REFERENCE_STORAGE_ACCOUNT_URL", derived)
        self.assertNotIn("ibkrv115", derived)
        self.assertIn("triggerType: 'Manual'", analytics)
        for template in (institutional, derived, analytics):
            self.assertIn("replicaRetryLimit: 0", template)
            self.assertIn("parallelism: 1", template)

        worker = (
            root / "scripts" / "run_self_service_derived_refresh.py"
        ).read_text(encoding="utf-8")
        market_refresh = worker.index("market_data = refresh_alpha_world_market_data")
        empty_target_gate = worker.index("if not targets:")
        self.assertLess(market_refresh, empty_target_gate)
        self.assertIn('"market_data": market_data', worker)

    def test_legacy_active_subject_approval_is_recorded_without_fake_case(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root
            / "migrations"
            / "postgres"
            / "027_legacy_external_market_data_approval.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("ALTER COLUMN case_id DROP NOT NULL", migration)
        self.assertIn("'U16088571'", migration)
        self.assertIn("legacy_active_subject", migration)
        self.assertIn("ON CONFLICT DO NOTHING", migration)

    def test_staging_web_uses_secure_ingress_and_read_only_credential(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "infra" / "staging-web.bicep").read_text(
            encoding="utf-8"
        )
        bootstrap = (root / "scripts" / "bootstrap_postgres.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("param externalIngress bool = true", template)
        self.assertIn("allowInsecure: false", template)
        self.assertIn("minReplicas: 1", template)
        self.assertIn("maxReplicas: 2", template)
        self.assertIn("cpu: json('1.0')", template)
        self.assertIn("memory: '2Gi'", template)
        self.assertIn("secrets/database-web-url", template)
        self.assertIn("ALLOWED_ENTRA_OBJECT_IDS", template)
        self.assertNotIn("database-admin-url", template)
        self.assertNotIn("database-app-url", template)
        self.assertIn("default_transaction_read_only = on", bootstrap)
        self.assertIn("GRANT SELECT ON ALL TABLES", bootstrap)
        self.assertIn("DATABASE_APPROVAL_PASSWORD", bootstrap)
        self.assertIn("GRANT SELECT, INSERT, UPDATE ON TABLE approved_actions", bootstrap)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL TABLES", bootstrap)
        self.assertIn("APPROVAL_DATABASE_URL", template)
        self.assertIn("ADMIN_ACTIONS_ENABLED", template)

    def test_admin_action_rbac_is_scoped_to_two_staging_jobs(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "infra" / "staging-action-rbac.bicep").read_text(
            encoding="utf-8"
        )
        self.assertIn("ibkrv2stg-benchmark-daily", template)
        self.assertIn("ibkrv2stg-risk-free-daily", template)
        self.assertIn("scope: benchmarkJob", template)
        self.assertIn("scope: riskFreeJob", template)
        self.assertIn("b9a307c4-5aa3-4b52-ba60-2b17c136cd7b", template)
        self.assertIn("4633458b-17de-408a-b874-0445c86b69e6", template)
        self.assertIn("scope: approvalDatabaseSecret", template)
        self.assertNotIn("ibkr-v11-daily", template)

    def test_staging_web_auth_has_fixed_session_and_nonce_validation(self) -> None:
        template = (
            Path(__file__).resolve().parents[1]
            / "infra"
            / "staging-web-auth.bicep"
        ).read_text(encoding="utf-8")
        self.assertIn("unauthenticatedClientAction: 'RedirectToLoginPage'", template)
        self.assertIn("requireHttps: true", template)
        self.assertIn("convention: 'FixedTime'", template)
        self.assertIn("param sessionLifetime string = '08:00:00'", template)
        self.assertIn("validateNonce: true", template)
        self.assertIn("nonceExpirationInterval: '00:05:00'", template)
        self.assertIn("enabled: false", template)


if __name__ == "__main__":
    unittest.main()
