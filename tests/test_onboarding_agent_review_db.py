"""The onboarding agents' database side: the case views, the agents' tables and the recorded check.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN (CI starts one). Skipped otherwise. Creates and drops its own
database and uses synthetic data only.
"""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER_ROLE = "onboarding_review_test_owner"
READER, WRITER, PLATFORM = "onboarding_review_test_reader", "onboarding_review_test_writer", "onboarding_review_test_app"
TEST_PASSWORD = "onboarding-review-test-only"  # throwaway roles in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
IDENTITY = "a" * 64


def _dsn(base: str, database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(base)
    params["dbname"] = database
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class OnboardingReviewDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection

        cls.database = "onboarding_review_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (READER, WRITER, PLATFORM):
                if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
                    admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{TEST_PASSWORD}'")
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER_ROLE,)).fetchone() is None:
                # Not a superuser, so forced row-level security applies as on Azure.
                admin.execute(f"CREATE ROLE {OWNER_ROLE} LOGIN CREATEROLE PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f"GRANT {READER}, {WRITER} TO {OWNER_ROLE} WITH ADMIN OPTION")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER_ROLE}')
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        connection = PostgresConnection(psycopg.connect(_dsn(ADMIN_DSN, cls.database, OWNER_ROLE)))
        connection.execute("SELECT set_config('app.tenant_id', ?, false)", (TENANT,))
        connection.commit()
        for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
            try:
                connection.execute(path.read_text(encoding="utf-8"))
                connection.commit()
            except Exception:
                connection.rollback()
                if path.name not in TOLERATED_MIGRATIONS:
                    raise
            if connection.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                    connection.execute("SELECT 1 FROM tenants").fetchone() is None:
                connection.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                   "VALUES (?, 'Test', 'active', '2026-01-01')", (TENANT,))
                connection.commit()
        connection.close()
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database, OWNER_ROLE), options=f"-c app.tenant_id={TENANT}") as owner:
            for path in sorted((ROOT / "migrations" / "agent").glob("*.sql")):
                owner.execute(path.read_text(encoding="utf-8"))
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:  # roles are cluster-wide
            admin.execute(f"GRANT agent_reader TO {READER}")
            admin.execute(f"GRANT agent_writer TO {WRITER}")

        from wealthryse_agent.config import DatabaseSettings, ServiceAuthSettings
        from wealthryse_agent.tool_server import ToolService

        cls.reader = DatabaseSettings("", cls.database, "", "", TENANT, _dsn(ADMIN_DSN, cls.database, READER))
        cls.writer = DatabaseSettings("", cls.database, "", "", TENANT, _dsn(ADMIN_DSN, cls.database, WRITER))
        cls.service = ToolService(cls.reader, ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True),
                                  frozenset(), frozenset())

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')

    def _platform(self, role=OWNER_ROLE):
        import psycopg
        from psycopg.rows import dict_row

        from ibkr_etl.db import PostgresConnection

        db = PostgresConnection(psycopg.connect(_dsn(ADMIN_DSN, self.database, role), row_factory=dict_row,
                                                options=f"-c app.tenant_id={TENANT}"))
        self.addCleanup(db.close)
        return db

    def _composite_in_stage_three(self, db, before_terms=None):
        """A composite in the MS design, through its dry run and approval 1, with its history built."""
        from datetime import date

        from ibkr_etl.db import transaction
        from ibkr_etl.onboarding_dry_run import record_history_build, run_portfolio_dry_runs
        from ibkr_etl.onboarding_v2_web import approve_case, case_detail, start_derived_case
        from ibkr_etl.subject_onboarding import fixed_unit_composite_definition

        db.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, base_currency, "
                   "lifecycle_status) VALUES ('U0000701', 'physical_account', 'Synthetic tech', 'USD', 'active')")
        definition = fixed_unit_composite_definition(
            subject_id="synthetic-review", display_name="Synthetic review", base_currency="HKD",
            normalization_date="2025-01-20", constant_cash=58.0,
            components=[{"component_id": "U0000701", "component_name": "Synthetic tech",
                         "component_type": "account", "coefficient": 0.42, "source_currency": "USD"}])
        case_id = start_derived_case(db, TENANT, definition=definition, description="A synthetic composite case.",
                                     actor=IDENTITY, today=date(2025, 3, 3), report_style="alpha-world-ms")["case_id"]
        db.commit()

        def write_history(connection, subject_id):
            with transaction(connection):
                connection.execute("INSERT INTO accounts (account_id, first_seen_at, last_seen_at, source_json) "
                                   "VALUES ('composite-source', 'x', 'x', '{}') ON CONFLICT DO NOTHING")
                connection.execute(
                    "INSERT INTO derived_portfolios (portfolio_id, name, source_account_id, base_currency, "
                    "exclusion_rule_json, calculation_version, status, created_at, updated_at) "
                    "VALUES (?, 'Synthetic', 'composite-source', 'HKD', '{}', 'v1', 'active', 'x', 'x')",
                    (subject_id,))
                for day, nav in (("2025-01-20", 100.0), ("2025-01-31", 101.2), ("2025-02-28", 103.4)):
                    connection.execute(
                        "INSERT INTO derived_portfolio_nav (portfolio_id, snapshot_date, currency, total_nav, "
                        "excluded_market_value_base, source_position_count, included_position_count, "
                        "excluded_position_count, generated_at) VALUES (?, ?, 'HKD', ?, 0, 2, 2, 0, 'x')",
                        (subject_id, day, nav))

        run_portfolio_dry_runs(db, TENANT, calculate=lambda dry, contract: write_history(
            dry, contract["subject_id"]) or {}, today=date(2025, 3, 3))
        if before_terms:
            before_terms(db, case_id)
        approve_case(db, TENANT, case_id, approval="account_terms",
                     terms_sha256=case_detail(db, TENANT, case_id)["awaiting_terms_sha256"],
                     acknowledge_warnings=[], confirmation=None, actor=IDENTITY)
        db.commit()
        write_history(db, "synthetic-review")
        record_history_build(db, TENANT, "synthetic-review", error=None, today=date(2025, 3, 3))
        db.commit()
        return case_id

    def _rendered(self, db, case_id, months):
        from ibkr_etl.onboarding_v2_web import record_platform_check

        reports = [{"period_end": month, "report_type": kind, "file_name": f"{month}-{kind}", "sha256": str(n) * 64,
                    "generation_run_id": n, "snapshot_id": "s", "output_reference": f"internal-reports/review/x/{n}",
                    "download_path": f"/api/onboarding-v2/cases/{case_id}/reports/{n}"}
                   for n, (month, kind) in enumerate(((m, k) for m in months for k in ("factsheet", "presentation")), 1)]
        evidence = {"design": "alpha-world-ms", "layout_version": "v1", "reports": reports, "waiting_for": None}
        record_platform_check(db, TENANT, case_id, "reports_rendered", "pass", f"{len(reports)} reports rendered.",
                              evidence)
        db.commit()
        return evidence

    def _drafts_terms_before_approval_one(self, db, case_id):
        from wealthryse_agent.database import connect
        from wealthryse_agent.onboarding_intake import Draft, DraftStore
        from wealthryse_agent.tools import ToolError

        with self.assertRaises(ToolError):
            self._call("list_onboarding_intake_cases", "month_team")
        case, = self._call("list_onboarding_intake_cases", "onboarding")["data"]["cases"]
        self.assertEqual((case["case_id"], case["source_kind"], case["subject_id"], case["reporting_currency"],
                          case["history_start_date"], case["stage"]),
                         (case_id, "composite_portfolio", "synthetic-review", "HKD", "2025-01-20",
                          "load_and_reconcile"))
        store = DraftStore(connect(self.writer, search_path="agent"), TENANT)
        self.addCleanup(store.db.close)
        digest = "d" * 64
        self.assertFalse(store.drafted(case_id, digest))
        draft = Draft("pass", "The intake agent traced 3 of 10 terms to a clause.",
                      {"reporting_currency": {"value": "HKD", "status": "found", "sources": []}}, [],
                      [{"document": "mandate.pdf", "sha256": "e" * 64, "byte_count": 10, "pages": 1, "error": None}],
                      {"input_tokens": 5, "output_tokens": 1})
        store.save(case, digest, draft, "corr")
        store.save(case, digest, draft, "corr")  # the same documents are drafted once
        self.assertTrue(store.drafted(case_id, digest))
        row = db.execute("SELECT outcome, terms->'reporting_currency'->>'value' AS currency, COUNT(*) OVER () AS n "
                         "FROM agent.onboarding_term_drafts WHERE case_id = CAST(? AS uuid)", (case_id,)).fetchone()
        self.assertEqual((row["outcome"], row["currency"], row["n"]), ("pass", "HKD", 1))
        db.rollback()
        with self.assertRaises(Exception):  # the agent cannot change the case or its terms
            store.db.execute("UPDATE public.broker_onboarding_cases SET reporting_currency = 'USD'")
        store.db.rollback()

    def _call(self, name, job, arguments=None):
        return self.service.call(caller_id="", user=None, job=job, correlation_id="test", name=name,
                                 arguments=arguments or {})

    def _broker_case_with_data(self, db):
        """An IBKR account in stage 2 whose Flex data is loaded, beside another account's data."""
        from datetime import date

        from ibkr_etl.broker_connections import new_connection_record
        from ibkr_etl.onboarding_v2_web import register_connection, start_case

        record = new_connection_record(
            broker="ibkr_flex", display_name="Synthetic login", environment="live", credentials={"flex_token": "1"},
            expires_on=date(2026, 1, 1), ip_locked_to=None, today=date(2025, 3, 3))
        register_connection(db, TENANT, record=record, stored_fingerprint="0" * 16, query_id="988", account_id=None,
                            actor=IDENTITY)
        source = db.execute("SELECT flex_source_id FROM ibkr_flex_queries WHERE credential_id = ?",
                            (record.connection_id,)).fetchone()["flex_source_id"]
        db.execute("INSERT INTO ibkr_flex_source_accounts (flex_source_id, source_account_id, base_currency, "
                   "account_name, discovery_status) VALUES (?, 'U0000088', 'HKD', 'Synthetic', 'discovered')", (source,))
        case_id = start_case(db, TENANT, connection_id=record.connection_id, broker_account_id="U0000088",
                             display_name="Synthetic HKD", reporting_currency="HKD",
                             history_start_date=date(2025, 2, 24), actor=IDENTITY, today=date(2025, 3, 3))["case_id"]
        run = db.execute("INSERT INTO ingestion_runs(run_type, started_at, status) VALUES ('test', '2025-03-01', "
                         "'success') RETURNING run_id").fetchone()["run_id"]
        db.execute("INSERT INTO instruments (conid, symbol, description, asset_category, currency, listing_exchange, "
                   "first_seen_at, last_seen_at, source_json) VALUES ('1001', '9999', 'SYNTHETIC HK LTD', 'STK', "
                   "'HKD', 'SEHK', 'x', 'x', '{}') ON CONFLICT DO NOTHING")
        for account, total in (("U0000088", "7012"), ("U0000099", "999")):
            db.execute("INSERT INTO accounts (account_id, base_currency, first_seen_at, last_seen_at, source_json) "
                       "VALUES (?, 'HKD', 'x', 'x', '{}')", (account,))
            for day in ("2025-02-24", "2025-02-25", "2025-02-26", "2025-02-27", "2025-02-28"):
                db.execute("INSERT INTO nav_snapshots (account_id, snapshot_date, currency, asset_class, total_text, "
                           "cash_text, stock_text, accruals_text, row_sha256, last_seen_run_id) "
                           "VALUES (?, ?, 'HKD', 'TOTAL', ?, '2000', '5000', '12', 'x', ?)", (account, day, total, run))
            db.execute("INSERT INTO position_snapshots (account_id, snapshot_date, conid, currency, quantity_text, "
                       "mark_price_text, market_value_text, row_sha256, last_seen_run_id) VALUES "
                       "(?, '2025-02-28', '1001', 'HKD', '500', '10', '5000', 'x', ?)", (account, run))
            db.execute("INSERT INTO cash_snapshots (account_id, snapshot_date, currency, ending_cash_text, "
                       "fx_rate_to_base_text, row_sha256, last_seen_run_id) VALUES (?, '2025-02-28', 'HKD', '2000', "
                       "'1', 'x', ?)", (account, run))
            db.execute("INSERT INTO trades (account_id, source_key, conid, trade_date, settle_date, buy_sell, "
                       "net_cash_text, currency, row_sha256, last_seen_run_id) VALUES (?, 't1', '1001', '2025-02-24', "
                       "'2025-02-26', 'BUY', '-5000', 'HKD', 'x', ?)", (account, run))
        db.commit()
        return case_id

    def test_a_broker_account_is_reconciled_and_the_platform_records_its_four_checks(self):
        from datetime import date

        from ibkr_etl.onboarding_agent_reconcile import record_agent_reconciliations
        from ibkr_etl.onboarding_v2_web import cancel_case, case_detail
        from wealthryse_agent.database import connect
        from wealthryse_agent.onboarding_reconcile import Reconciler, ReconciliationStore, reconcile_waiting_cases
        from wealthryse_agent.tools import ToolError

        db = self._platform()
        self.assertEqual(self._call("list_onboarding_broker_cases", "onboarding")["data"]["cases"], [])
        case_id = self._broker_case_with_data(db)
        try:
            for job in ("month_team", "data_quality"):
                with self.assertRaises(ToolError):
                    self._call("list_onboarding_broker_cases", job)
            case, = self._call("list_onboarding_broker_cases", "onboarding")["data"]["cases"]
            self.assertEqual((case["case_id"], case["broker_account_id"], case["base_currency"]),
                             (case_id, "U0000088", "HKD"))
            # The views show the case's account only; the other account's data is never reachable.
            overview = self._call("get_onboarding_broker_overview", "onboarding", {"case_id": case_id})["data"]
            self.assertEqual(({n["total_text"] for n in overview["nav"]}, len(overview["nav"])), ({"7012"}, 5))
            self.assertEqual([s["conid"] for s in overview["securities"]], ["1001"])
            with self.assertRaises(ToolError):
                self._call("get_onboarding_broker_day", "onboarding",
                           {"case_id": "aaaaaaaa-0000-4000-8000-000000000099", "date": "2025-02-28"})
            reader = connect(self.reader, search_path="agent")
            self.addCleanup(reader.close)
            for view in ("onboarding_nav", "onboarding_positions", "onboarding_cash", "onboarding_activity"):
                accounts = {r["account_id"] for r in reader.execute(f"SELECT account_id FROM {view}").fetchall()}
                self.assertEqual(accounts, {"U0000088"}, view)
            reader.rollback()

            store = ReconciliationStore(connect(self.writer, search_path="agent"), TENANT)
            self.addCleanup(store.db.close)
            call = lambda name, arguments: self._call(name, "onboarding", arguments)  # noqa: E731
            reconciler = Reconciler(lambda: self.fail("a clean account needs no model"))
            self.assertEqual(reconcile_waiting_cases(store, call, reconciler, {}, today=date(2025, 3, 3)), 1)
            self.assertEqual(reconcile_waiting_cases(store, call, reconciler, {}, today=date(2025, 3, 3)), 0)
            with self.assertRaises(Exception):  # the agent cannot touch the case's checks
                store.db.execute("UPDATE public.broker_onboarding_checks SET status = 'pass'")
            store.db.rollback()

            recorded, = record_agent_reconciliations(db, TENANT)
            self.assertEqual(recorded["recorded"], ["holdings_and_cash_loaded", "nav_reconciled", "securities_mapped",
                                                    "transactions_loaded"])
            self.assertEqual(record_agent_reconciliations(db, TENANT)[0]["recorded"], [])
            checks = {k["check_id"]: k for k in case_detail(db, TENANT, case_id)["checks"]}
            self.assertEqual({k: checks[k]["status"] for k in recorded["recorded"]},
                             {"holdings_and_cash_loaded": "pass", "transactions_loaded": "pass",
                              "securities_mapped": "warn", "nav_reconciled": "pass"})
            self.assertIn("1 are new and proposed with their identifiers: 9999", checks["securities_mapped"]["message"])
        finally:
            cancel_case(db, TENANT, case_id, reason="Test case.", actor=IDENTITY)
            db.commit()

    def test_the_reviewer_sees_waiting_cases_and_the_platform_records_its_review(self):
        from ibkr_etl.onboarding_agent_review import record_agent_reviews, reports_digest
        from ibkr_etl.onboarding_v2_web import case_detail
        from wealthryse_agent.database import connect
        from wealthryse_agent.onboarding_review import Review, ReviewStore
        from wealthryse_agent.tools import ToolError

        db = self._platform()
        self.assertEqual(self._call("list_onboarding_report_cases", "onboarding")["data"]["cases"], [])
        case_id = self._composite_in_stage_three(db, before_terms=self._drafts_terms_before_approval_one)
        # After approval 1 the case has left the intake agent's view.
        self.assertEqual(self._call("list_onboarding_intake_cases", "onboarding")["data"]["cases"], [])
        self.assertEqual(record_agent_reviews(db, TENANT), [])  # nothing is rendered yet
        evidence = self._rendered(db, case_id, ["2025-01-31", "2025-02-28"])

        # Only the onboarding job is offered the case view; a person or another job never is.
        for job in ("month_team", "data_quality"):
            with self.assertRaises(ToolError):
                self._call("list_onboarding_report_cases", job)
        case, = self._call("list_onboarding_report_cases", "onboarding")["data"]["cases"]
        self.assertEqual((case["case_id"], case["subject_id"], case["report_style"], case["display_name"]),
                         (case_id, "synthetic-review", "alpha-world-ms", "Synthetic review"))
        self.assertEqual(case["reports_rendered"], evidence)
        digest = reports_digest(case["reports_rendered"])
        self.assertEqual(digest, reports_digest(evidence))  # both sides bind to the same digest

        # Until the reviewer has read these reports, the check waits and approval 2 cannot be given.
        self.assertEqual(record_agent_reviews(db, TENANT), [{"case_id": case_id, "check": "pending", "recorded": True}])
        self.assertEqual(record_agent_reviews(db, TENANT), [{"case_id": case_id, "check": "pending", "recorded": False}])
        self.assertIsNone(case_detail(db, TENANT, case_id)["awaiting_approval"])

        # The reviewer writes only its own table; the platform records the review on the case.
        store = ReviewStore(connect(self.writer, search_path="agent"), TENANT)
        self.addCleanup(store.db.close)
        self.assertFalse(store.reviewed(case_id, digest))
        store.save(case, digest, Review("warn", "One layout point to look at.",
                                        [{"severity": "medium", "period_end": "2025-02-28", "report_type": "factsheet",
                                          "page": 1, "message": "Clipped text (medium): a label is clipped."}],
                                        {"files_checked": 4}, {"input_tokens": 10, "output_tokens": 1}), "corr")
        self.assertTrue(store.reviewed(case_id, digest))
        with self.assertRaises(Exception):  # the agent cannot touch the case's checks
            store.db.execute("UPDATE public.broker_onboarding_checks SET status = 'pass'")
        store.db.rollback()
        recorded, = record_agent_reviews(db, TENANT)
        self.assertEqual(recorded, {"case_id": case_id, "check": "warn", "recorded": True})
        detail = case_detail(db, TENANT, case_id)
        check = {k["check_id"]: k for k in detail["checks"]}["agent_review_complete"]
        self.assertEqual((check["status"], check["message"]), ("warn", "One layout point to look at."))
        self.assertEqual(detail["awaiting_approval"], "historical_reports")

        # A re-render puts the check back to pending until the new reports are reviewed.
        self._rendered(db, case_id, ["2025-01-31", "2025-02-28", "2025-03-31"])
        self.assertEqual(record_agent_reviews(db, TENANT)[0]["check"], "pending")
        self.assertIsNone(case_detail(db, TENANT, case_id)["awaiting_approval"])

    def test_the_platform_role_may_read_reviews_once_granted_and_never_write_them(self):
        import psycopg

        import importlib.util

        spec = importlib.util.spec_from_file_location("install_agent_schema", ROOT / "scripts" / "install_agent_schema.py")
        install_agent_schema = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(install_agent_schema)
        reader = self._platform(PLATFORM)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            reader.execute("SELECT COUNT(*) FROM agent.onboarding_reviews").fetchone()
        reader.rollback()
        url = _dsn(ADMIN_DSN, self.database, OWNER_ROLE)
        self.assertEqual(install_agent_schema.main(["--database-url", url, "--existing-logins",
                                                    "--platform-reader", PLATFORM]), 0)
        for table in ("onboarding_reviews", "onboarding_term_drafts"):
            reader.execute(f"SELECT COUNT(*) AS n FROM agent.{table}").fetchone()
            reader.rollback()
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                reader.execute(f"DELETE FROM agent.{table}")
            reader.rollback()


if __name__ == "__main__":
    unittest.main()
