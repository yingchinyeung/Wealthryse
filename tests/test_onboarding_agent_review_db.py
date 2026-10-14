"""The history reviewer's database side: the case view, the review table and the recorded check.

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

    def _composite_in_stage_three(self, db):
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

    def _call(self, name, job):
        return self.service.call(caller_id="", user=None, job=job, correlation_id="test", name=name, arguments={})

    def test_the_reviewer_sees_waiting_cases_and_the_platform_records_its_review(self):
        from ibkr_etl.onboarding_agent_review import record_agent_reviews, reports_digest
        from ibkr_etl.onboarding_v2_web import case_detail
        from wealthryse_agent.database import connect
        from wealthryse_agent.onboarding_review import Review, ReviewStore
        from wealthryse_agent.tools import ToolError

        db = self._platform()
        self.assertEqual(self._call("list_onboarding_report_cases", "onboarding")["data"]["cases"], [])
        case_id = self._composite_in_stage_three(db)
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
        self.assertEqual(reader.execute("SELECT COUNT(*) AS n FROM agent.onboarding_reviews").fetchone()["n"], 0)
        reader.rollback()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            reader.execute("DELETE FROM agent.onboarding_reviews")
        reader.rollback()


if __name__ == "__main__":
    unittest.main()
