from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect, initialize, transaction  # noqa: E402
from ibkr_etl.evidence_acl import quarantine_evidence_objects  # noqa: E402
from ibkr_etl.reference_subject_rename import (  # noqa: E402
    reconcile_china_hong_kong_display_name,
)


def main() -> int:
    admin_url = os.environ.get("DATABASE_ADMIN_URL", "")
    app_password = os.environ.get("DATABASE_APP_PASSWORD", "")
    app_role = os.environ.get("DATABASE_APP_ROLE", "ibkr_app")
    web_password = os.environ.get("DATABASE_WEB_PASSWORD", "")
    web_role = os.environ.get("DATABASE_WEB_ROLE", "ibkr_web")
    approval_password = os.environ.get("DATABASE_APPROVAL_PASSWORD", "")
    approval_role = os.environ.get("DATABASE_APPROVAL_ROLE", "ibkr_approval")
    if not admin_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_ADMIN_URL must be a PostgreSQL connection URL")
    if len(app_password) < 12:
        raise RuntimeError("DATABASE_APP_PASSWORD must contain at least 12 characters")

    settings = load_settings()
    previous = os.environ.get("IBKR_RUN_MIGRATIONS")
    os.environ["IBKR_RUN_MIGRATIONS"] = "true"
    try:
        initialize(
            settings.database_path,
            admin_url,
            settings.tenant_id,
            settings.tenant_name,
        )
    finally:
        if previous is None:
            os.environ.pop("IBKR_RUN_MIGRATIONS", None)
        else:
            os.environ["IBKR_RUN_MIGRATIONS"] = previous

    migration_connection = connect(
        settings.database_path, admin_url, settings.tenant_id
    )
    try:
        with transaction(migration_connection):
            rename_result = reconcile_china_hong_kong_display_name(
                migration_connection, tenant_id=settings.tenant_id
            )
    finally:
        migration_connection.close()

    import psycopg
    from psycopg import sql

    # Broad compatibility grants and the evidence quarantine must commit as one
    # transaction: do not temporarily expose unactivated evidence to web/app roles.
    with psycopg.connect(admin_url) as connection:
        role_exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (app_role,)
        ).fetchone()
        if role_exists is None:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(app_role))
            )
        connection.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(app_role), sql.Literal(app_password)
            )
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(connection.info.dbname), sql.Identifier(app_role)
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(app_role)
            )
        )
        connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
            ).format(sql.Identifier(app_role))
        )
        connection.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                sql.Identifier(app_role)
            )
        )
        connection.execute(
            sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {}").format(
                sql.Identifier(app_role)
            )
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ).format(sql.Identifier(app_role))
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT USAGE, SELECT ON SEQUENCES TO {}"
            ).format(sql.Identifier(app_role))
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {}"
            ).format(sql.Identifier(app_role))
        )
        connection.execute(
            sql.SQL(
                "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON schema_migrations FROM {}"
            ).format(sql.Identifier(app_role))
        )

        if web_password:
            if len(web_password) < 12:
                raise RuntimeError("DATABASE_WEB_PASSWORD must contain at least 12 characters")
            web_exists = connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (web_role,)
            ).fetchone()
            if web_exists is None:
                connection.execute(
                    sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(web_role))
                )
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(web_role), sql.Literal(web_password)
                )
            )
            connection.execute(
                sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                    sql.Identifier(web_role)
                )
            )
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(connection.info.dbname), sql.Identifier(web_role)
                )
            )
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(web_role)
                )
            )
            connection.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
                    sql.Identifier(web_role)
                )
            )
            connection.execute(
                sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {}").format(
                    sql.Identifier(web_role)
                )
            )
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}"
                ).format(sql.Identifier(web_role))
            )
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {}"
                ).format(sql.Identifier(web_role))
            )

        if approval_password:
            if len(approval_password) < 20:
                raise RuntimeError("DATABASE_APPROVAL_PASSWORD must contain at least 20 characters")
            approval_exists = connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (approval_role,)
            ).fetchone()
            if approval_exists is None:
                connection.execute(
                    sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(approval_role))
                )
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(approval_role), sql.Literal(approval_password)
                )
            )
            connection.execute(
                sql.SQL("ALTER ROLE {} SET default_transaction_read_only = off").format(
                    sql.Identifier(approval_role)
                )
            )
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(connection.info.dbname), sql.Identifier(approval_role)
                )
            )
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(approval_role)
                )
            )
            connection.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(
                    sql.Identifier(approval_role)
                )
            )
            connection.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(
                    sql.Identifier(approval_role)
                )
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE ON TABLE approved_actions TO {}"
                ).format(sql.Identifier(approval_role))
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE ON TABLE autonomy_control, "
                    "autonomous_agent_runs, system_incidents, autonomous_actions, "
                    "autonomy_release_baselines, autonomy_recovery_state, "
                    "autonomy_authority_reviews, burn_in_acceptance_windows, "
                    "burn_in_daily_results, portfolio_analyst_assurance_runs, "
                    "portfolio_analyst_assurance_findings, ai_analyst_conversations, "
                    "ai_analyst_messages, admin_chat_conversations, "
                    "admin_chat_messages, platform_subjects, "
                    "onboarding_source_accounts, account_onboarding_cases, "
                    "account_onboarding_checks, subject_feature_states, "
                    "subject_dependencies, subject_report_template_assets, "
                    "ibkr_flex_credentials, ibkr_flex_queries, "
                    "ibkr_flex_source_accounts, subject_flex_source_bindings, "
                    "ibkr_flex_source_runs, ibkr_flex_source_run_accounts, "
                    "ibkr_credential_circuit_state, "
                    "ibkr_flex_source_validation_requests, "
                    "account_onboarding_approvals, "
                    "account_onboarding_queues, account_onboarding_queue_items, "
                    "subject_calculation_definitions, "
                    "master_account_definitions, master_account_memberships, "
                    "subject_calculation_runs, "
                    "onboarding_historical_report_batches, "
                    "onboarding_historical_report_items, "
                    "app_user_roles, identity_registry_revisions, "
                    "subject_operating_registrations, workflow_outbox, "
                    "institutional_work_items, "
                    "institutional_shadow_reconciliations TO {}"
                ).format(sql.Identifier(approval_role))
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT ON TABLE accounts, nav_snapshots, benchmark_prices, risk_free_rates, "
                    "position_snapshots, derived_portfolio_nav, "
                    "derived_portfolio_positions, benchmark_master, market_data_prices, "
                    "performance_comparison_daily, ingestion_runs, reporting_snapshots, "
                    "report_generation_runs, report_release_decisions, "
                    "report_artifact_releases, "
                    "subject_nav_series, subject_position_series, "
                    "subject_cash_balance_series, subject_cash_activity_series TO {}"
                ).format(sql.Identifier(approval_role))
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE ON TABLE "
                    "portfolio_benchmark_mapping TO {}"
                ).format(sql.Identifier(approval_role))
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE ON TABLE report_generation_runs, "
                    "report_release_decisions, report_artifact_releases TO {}"
                ).format(sql.Identifier(approval_role))
            )
            # The new account onboarding pages (064/065) read through the website role and
            # write through this one; their event history only reads and appends.
            connection.execute(sql.SQL(
                "GRANT SELECT, INSERT, UPDATE ON TABLE broker_connections, broker_connection_accounts, "
                "broker_onboarding_cases, broker_onboarding_checks, broker_onboarding_approvals TO {}"
            ).format(sql.Identifier(approval_role)))
            connection.execute(sql.SQL(
                "GRANT SELECT, INSERT ON TABLE broker_onboarding_events TO {}"
            ).format(sql.Identifier(approval_role)))
            connection.execute(sql.SQL(
                "GRANT SELECT ON TABLE qualified_report_designs, active_report_designs TO {}"
            ).format(sql.Identifier(approval_role)))
            connection.execute(sql.SQL(
                "GRANT SELECT, INSERT, UPDATE ON TABLE report_design_update_requests TO {}"
            ).format(sql.Identifier(approval_role)))
            # Factsheet design drafts (067): files and versions only append; drafts and edit
            # proposals change status. Nothing here is deleted.
            connection.execute(sql.SQL(
                "GRANT SELECT, INSERT ON TABLE factsheet_design_files, factsheet_design_versions TO {}"
            ).format(sql.Identifier(approval_role)))
            connection.execute(sql.SQL(
                "GRANT SELECT, INSERT, UPDATE ON TABLE factsheet_design_drafts, factsheet_design_proposals TO {}"
            ).format(sql.Identifier(approval_role)))
            design_privileges = connection.execute(
                """SELECT has_table_privilege(%s, 'public.report_design_update_requests', 'INSERT')
                    AND has_table_privilege(%s, 'public.qualified_report_designs', 'SELECT')
                    AND NOT has_table_privilege(%s, 'public.qualified_report_designs', 'INSERT')
                    AND NOT has_table_privilege(%s, 'public.qualified_report_designs', 'UPDATE')
                    AND NOT has_table_privilege(%s, 'public.qualified_report_designs', 'DELETE')
                    AND NOT has_table_privilege(%s, 'public.active_report_designs', 'INSERT')
                    AND NOT has_table_privilege(%s, 'public.active_report_designs', 'UPDATE')
                    AND NOT has_table_privilege(%s, 'public.report_design_update_requests', 'DELETE')""",
                (approval_role,) * 8,
            ).fetchone()[0]
            if not design_privileges:
                raise RuntimeError('Website report-design permissions are not least-privilege compliant')
            connection.execute(
                sql.SQL("GRANT INSERT ON TABLE audit_events TO {}").format(
                    sql.Identifier(approval_role)
                )
            )
            connection.execute(
                sql.SQL(
                    "GRANT USAGE, SELECT ON SEQUENCE "
                    "report_release_decisions_release_decision_id_seq, "
                    "report_artifact_releases_artifact_release_id_seq, "
                    "audit_events_audit_event_id_seq TO {}"
                ).format(sql.Identifier(approval_role))
            )
            connection.execute(
                sql.SQL(
                    "GRANT USAGE, SELECT ON SEQUENCE approved_actions_approval_id_seq TO {}"
                ).format(sql.Identifier(approval_role))
            )
            connection.execute(
                sql.SQL(
                    "GRANT USAGE, SELECT ON SEQUENCE autonomous_agent_runs_agent_run_id_seq, "
                    "system_incidents_incident_id_seq, autonomous_actions_action_id_seq, "
                    "autonomy_release_baselines_baseline_id_seq, "
                    "autonomy_authority_reviews_review_id_seq, "
                    "burn_in_acceptance_windows_window_id_seq, "
                    "burn_in_daily_results_result_id_seq, "
                    "portfolio_analyst_assurance_runs_assurance_run_id_seq, "
                    "portfolio_analyst_assurance_findings_finding_id_seq TO {}"
                ).format(sql.Identifier(approval_role))
            )
            connection.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION app_tenant_id() TO {}").format(
                    sql.Identifier(approval_role)
                )
            )
            benchmark_mapping_privileges = connection.execute(
                """
                SELECT has_table_privilege(%s, 'public.portfolio_benchmark_mapping', 'SELECT')
                   AND has_table_privilege(%s, 'public.portfolio_benchmark_mapping', 'INSERT')
                   AND has_table_privilege(%s, 'public.portfolio_benchmark_mapping', 'UPDATE')
                   AND NOT has_table_privilege(%s, 'public.portfolio_benchmark_mapping', 'DELETE')
                """,
                (approval_role, approval_role, approval_role, approval_role),
            ).fetchone()[0]
            if not benchmark_mapping_privileges:
                raise RuntimeError(
                    "The approval role benchmark-mapping privileges are not least-privilege compliant"
                )
            derived_reconciliation_privileges = connection.execute(
                """
                SELECT has_table_privilege(%s, 'public.derived_portfolio_nav', 'SELECT')
                   AND has_table_privilege(%s, 'public.derived_portfolio_positions', 'SELECT')
                   AND NOT has_table_privilege(%s, 'public.derived_portfolio_nav', 'INSERT')
                   AND NOT has_table_privilege(%s, 'public.derived_portfolio_nav', 'UPDATE')
                   AND NOT has_table_privilege(%s, 'public.derived_portfolio_nav', 'DELETE')
                   AND NOT has_table_privilege(%s, 'public.derived_portfolio_positions', 'INSERT')
                   AND NOT has_table_privilege(%s, 'public.derived_portfolio_positions', 'UPDATE')
                   AND NOT has_table_privilege(%s, 'public.derived_portfolio_positions', 'DELETE')
                """,
                (
                    approval_role,
                    approval_role,
                    approval_role,
                    approval_role,
                    approval_role,
                    approval_role,
                    approval_role,
                    approval_role,
                ),
            ).fetchone()[0]
            if not derived_reconciliation_privileges:
                raise RuntimeError(
                    "The approval role derived-reconciliation privileges are not least-privilege compliant"
                )
            reference_market_privileges = connection.execute(
                """
                SELECT has_table_privilege(%s, 'public.market_data_prices', 'SELECT')
                   AND NOT has_table_privilege(%s, 'public.market_data_prices', 'INSERT')
                   AND NOT has_table_privilege(%s, 'public.market_data_prices', 'UPDATE')
                   AND NOT has_table_privilege(%s, 'public.market_data_prices', 'DELETE')
                """,
                (approval_role, approval_role, approval_role, approval_role),
            ).fetchone()[0]
            if not reference_market_privileges:
                raise RuntimeError(
                    "The approval role reference-market privileges are not least-privilege compliant"
                )

        quarantine_evidence_objects(connection)

    print(
        "PostgreSQL schema and least-privilege application roles are ready. "
        f"Reference-subject display reconciliation: {rename_result['status']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
