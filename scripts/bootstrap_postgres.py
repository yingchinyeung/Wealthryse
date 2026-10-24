from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect, initialize, transaction  # noqa: E402
from ibkr_etl.evidence_acl import quarantine_evidence_objects  # noqa: E402
from ibkr_etl.layer_quality import COUNT_COLUMNS, HISTORY_TABLE  # noqa: E402
from ibkr_etl.medallion_layers import (  # noqa: E402
    LOADER_GROUP,
    grant_silver_loader,
    history_write_rights,
    keep_history_read_only,
    loader_group_exists,
    lock_silver_writes,
    mirror_privileges,
    mismatches,
    present_layers,
    runtime_search_path,
    silver_write_rights,
)
from ibkr_etl.reference_subject_rename import (  # noqa: E402
    reconcile_china_hong_kong_display_name,
)

# The schema-wide rights each login has on public, repeated for each medallion layer
# schema once migrations 068 to 070 have created it.
APP_LAYER_STATEMENTS = (
    "GRANT USAGE ON SCHEMA {schema} TO {role}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {role}",
    "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT USAGE, SELECT ON SEQUENCES TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT EXECUTE ON FUNCTIONS TO {role}",
)
WEB_LAYER_STATEMENTS = (
    "GRANT USAGE ON SCHEMA {schema} TO {role}",
    "GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}",
    "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT EXECUTE ON FUNCTIONS TO {role}",
)
# What the website's write login (the approval role) needs for self-service onboarding (085), by the
# table that must exist first. tests/test_onboarding_worker_db.py runs the website's writes with exactly these.
APPROVAL_ONBOARDING_GRANTS = (
    ("public.onboarding_worker_runs", "GRANT SELECT, INSERT, UPDATE ON TABLE onboarding_worker_runs TO {}"),
    ("public.onboarding_worker_runs",
     "GRANT SELECT (tenant_id, entra_object_id, email, display_name, status) ON TABLE app_users TO {}"),
    ("public.onboarding_worker_runs", "GRANT SELECT, INSERT ON TABLE user_account_access TO {}"),
)
# The approval login gets nothing schema-wide. Its named grants below reach the
# public views, and mirror_privileges copies them onto the tables behind them.
APPROVAL_LAYER_STATEMENTS = (
    "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schema} FROM {role}",
    "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {schema} FROM {role}",
)
# The columns the approval login reads for the layer freshness check (medallion S2).
FRESHNESS_COLUMNS = (
    ("raw_reports", ("tenant_id", "received_at")),
    ("flex_rows", ("tenant_id", "flex_row_id", "first_seen_run_id")),
    ("risk_analytics_snapshots", ("tenant_id", "as_of_date")),
    ("crisis_scenario_results", ("tenant_id", "holdings_as_of")),
    ("crisis_replay_daily", ("tenant_id", "holdings_as_of")),
)


def _layer_statements(connection, statements, layers, role) -> None:
    from psycopg import sql

    for layer in layers:
        for statement in statements:
            connection.execute(sql.SQL(statement).format(
                schema=sql.Identifier(layer), role=sql.Identifier(role)))


# A layer batch (068 to 070) that finds one of its tables busy stops within seconds and
# changes nothing (SQLSTATE 55P03). With --only-migration, bootstrap tries it again a
# minute later, holding no lock in between, up to three tries in all.
BUSY_TRIES = 3
BUSY_PAUSE_SECONDS = 60


def _when_tables_free(apply, only_migration):
    import psycopg

    for attempt in range(1, BUSY_TRIES + 1):
        try:
            return apply()
        except psycopg.errors.LockNotAvailable as error:
            if not only_migration or attempt == BUSY_TRIES:
                raise
            print(f"{only_migration}: a table was busy, so nothing changed "
                  f"({error.diag.message_primary or error}). Trying again in "
                  f"{BUSY_PAUSE_SECONDS} seconds (try {attempt} of {BUSY_TRIES}).", flush=True)
            time.sleep(BUSY_PAUSE_SECONDS)
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply migrations and set the database logins' rights.")
    parser.add_argument(
        "--only-migration",
        help="apply only this migration file (for example 068_medallion_gold_layer.sql) and refuse, "
             "changing nothing, if any other migration differs or is pending outside --allow-pending",
    )
    parser.add_argument(
        "--allow-pending", action="append", default=[],
        help="with --only-migration: migrations that may stay pending (comma-separated, repeatable)",
    )
    parser.add_argument(
        "--allow-applied-without-file", action="append", default=[], metavar="VERSION",
        help="with --only-migration: a version recorded as applied that has no file here, "
             "such as one applied outside the repository (repeatable, one version each)",
    )
    args = parser.parse_args(argv)
    allow_pending = [name for value in args.allow_pending for name in value.split(",") if name]
    if allow_pending and not args.only_migration:
        parser.error("--allow-pending needs --only-migration")
    if args.allow_applied_without_file and not args.only_migration:
        parser.error("--allow-applied-without-file needs --only-migration")

    admin_url = os.environ.get("DATABASE_ADMIN_URL", "")
    app_password = os.environ.get("DATABASE_APP_PASSWORD", "")
    app_role = os.environ.get("DATABASE_APP_ROLE", "ibkr_app")
    web_password = os.environ.get("DATABASE_WEB_PASSWORD", "")
    web_role = os.environ.get("DATABASE_WEB_ROLE", "ibkr_web")
    approval_password = os.environ.get("DATABASE_APPROVAL_PASSWORD", "")
    approval_role = os.environ.get("DATABASE_APPROVAL_ROLE", "ibkr_approval")
    # Medallion step 3: the loader login, the only runtime login that may write silver once
    # SILVER_WRITE_LOCK=on. Without the setting the app login keeps its write rights (the undo).
    loader_password = os.environ.get("DATABASE_LOADER_PASSWORD", "")
    loader_role = os.environ.get("DATABASE_LOADER_ROLE", "ibkr_loader")
    silver_lock = os.environ.get("SILVER_WRITE_LOCK", "off").strip().lower() or "off"
    if silver_lock not in ("on", "off"):
        raise RuntimeError("SILVER_WRITE_LOCK must be on or off")
    if silver_lock == "on" and not loader_password:
        raise RuntimeError("SILVER_WRITE_LOCK=on needs the loader login (DATABASE_LOADER_PASSWORD)")
    if not admin_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_ADMIN_URL must be a PostgreSQL connection URL")
    if len(app_password) < 12:
        raise RuntimeError("DATABASE_APP_PASSWORD must contain at least 12 characters")

    settings = load_settings()
    previous = os.environ.get("IBKR_RUN_MIGRATIONS")
    os.environ["IBKR_RUN_MIGRATIONS"] = "true"
    try:
        applied = _when_tables_free(lambda: initialize(
            settings.database_path,
            admin_url,
            settings.tenant_id,
            settings.tenant_name,
            only_migration=args.only_migration,
            allow_pending=allow_pending,
            allow_applied_without_file=args.allow_applied_without_file,
            set_tenant_name=True,
        ), args.only_migration)
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
        layers = present_layers(connection)  # empty until migration 068 has run
        managed_roles = [app_role]
        _layer_statements(connection, APP_LAYER_STATEMENTS, layers, app_role)
        # The loader group writes silver (nothing until migration 083). It is granted before the
        # approval login, whose rights are taken and given again on every run, so a second run
        # leaves each table's rights exactly as the first did.
        silver_objects = grant_silver_loader(connection) if layers else 0

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
            managed_roles.append(web_role)
            _layer_statements(connection, WEB_LAYER_STATEMENTS, layers, web_role)

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
            managed_roles.append(approval_role)
            _layer_statements(connection, APPROVAL_LAYER_STATEMENTS, layers, approval_role)
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
                    "subject_cash_balance_series, subject_cash_activity_series, "
                    # The layer quality NAV rebuild (value.nav_rebuild) reads cash and FX with positions.
                    "cash_snapshots, fx_rates TO {}"
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
            # Self-service onboarding (085): Run now and Check now write the worker's run requests, and
            # the go-live form gives the ticked viewers read access (only adds rows; never changes or
            # removes one).
            for table, statement in APPROVAL_ONBOARDING_GRANTS:
                if connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
                    connection.execute(sql.SQL(statement).format(sql.Identifier(approval_role)))
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
            # The autonomous administrator's layer freshness check reads only when rows
            # arrived, never their content (flex_rows.data_json stays unreadable).
            for table, columns in FRESHNESS_COLUMNS:
                connection.execute(sql.SQL("GRANT SELECT ({}) ON TABLE {} TO {}").format(
                    sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                    sql.Identifier(table), sql.Identifier(approval_role)))
            # Its layer quality check (medallion review item 7) counts rows per business date,
            # reading one column of each silver and gold table, and appends its results to
            # quality_check_results once migration 072 has created it.
            for table, column in sorted(COUNT_COLUMNS.items()):
                if connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
                    connection.execute(sql.SQL("GRANT SELECT ({}) ON TABLE {} TO {}").format(
                        sql.Identifier(column), sql.Identifier(*table.split(".")), sql.Identifier(approval_role)))
            if connection.execute("SELECT to_regclass(%s)", (HISTORY_TABLE,)).fetchone()[0]:
                connection.execute(sql.SQL("GRANT SELECT, INSERT ON TABLE {} TO {}").format(
                    sql.Identifier(HISTORY_TABLE), sql.Identifier(approval_role)))
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

        if loader_password:
            if len(loader_password) < 20:
                raise RuntimeError("DATABASE_LOADER_PASSWORD must contain at least 20 characters")
            if not loader_group_exists(connection):
                raise RuntimeError(f"The {LOADER_GROUP} group is missing: apply migration 083 first")
            loader_exists = connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (loader_role,)
            ).fetchone()
            if loader_exists is None:
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(loader_role)))
            connection.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(loader_role), sql.Literal(loader_password)))
            connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(connection.info.dbname), sql.Identifier(loader_role)))
            # Everything the app login may do, and through the group, write silver.
            connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(app_role), sql.Identifier(loader_role)))
            connection.execute(sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(LOADER_GROUP), sql.Identifier(loader_role)))
            managed_roles.append(loader_role)

        quarantine_evidence_objects(connection)

        if layers:
            # The FX evidence tables are in bronze once migration 069 has run.
            for layer in layers:
                quarantine_evidence_objects(connection, schema=layer)
            # With the lock on, only the loader group writes silver: the app login no longer does.
            checked_roles = managed_roles + ([LOADER_GROUP] if loader_group_exists(connection) else [])
            if silver_lock == "on":
                if not silver_objects:
                    raise RuntimeError("SILVER_WRITE_LOCK=on needs migration 083 and the silver layer")
                lock_silver_writes(connection, [app_role])
            mirror_privileges(connection, checked_roles)
            problems = mismatches(connection, checked_roles)
            if problems:
                raise RuntimeError("Layer table rights differ from their public views: " + "; ".join(problems))
            # The U8 history and the silver write watch are written only through their triggers
            # (nothing until those migrations).
            keep_history_read_only(connection, checked_roles)
            problems = history_write_rights(connection, checked_roles)
            if problems:
                raise RuntimeError("A login can still write the silver history or its watch: " + "; ".join(problems))
            if silver_lock == "on":
                problems = silver_write_rights(connection, [role for role in managed_roles if role != loader_role])
                if problems:
                    raise RuntimeError("A login other than the loader can still write silver: " + "; ".join(problems))
            # Runtime logins find the layer tables first. This migration login keeps
            # public first, and apply_postgres_migrations sets it for its own transaction.
            for role in managed_roles:
                connection.execute(sql.SQL("ALTER ROLE {} SET search_path = {}").format(
                    sql.Identifier(role), sql.SQL(runtime_search_path(layers))))

    print(
        "PostgreSQL schema and least-privilege application roles are ready. "
        f"Reference-subject display reconciliation: {rename_result['status']}."
    )
    if args.only_migration or layers:
        print(f"Migrations applied: {', '.join(applied) or 'none'}. "
              f"Layer schemas: {', '.join(layers) or 'none'}.")
    if silver_objects:
        print(f"Silver writes: {LOADER_GROUP} on {silver_objects} objects; "
              f"loader login {'set' if loader_password else 'not set'}; "
              f"app login {'locked out' if silver_lock == 'on' else 'still writes (SILVER_WRITE_LOCK off)'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
