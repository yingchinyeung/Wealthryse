"""Self-service onboarding against a real PostgreSQL (migration 085): the worker's run records,
Run now and Check now, up to three open cases, benchmarks in the account terms and viewer access at
go-live.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Creates and drops its own database and uses synthetic data only.
"""
from __future__ import annotations

import sys
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from test_broker_onboarding_db import ADMIN_DSN, IDENTITY, OWNER_ROLE, TEST_PASSWORD, TENANT, _dsn, _MigratedDatabase

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
WEBSITE_WRITER = "onboarding_worker_test_writer"
# What scripts/bootstrap_postgres.py gives the website's write login (the approval role) for these
# pages before 085; the 085 grants come from its APPROVAL_ONBOARDING_GRANTS.
WRITER_BEFORE_085 = (
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT EXECUTE ON FUNCTION app_tenant_id() TO {role}",
    "GRANT SELECT, INSERT, UPDATE ON TABLE broker_connections, broker_connection_accounts, broker_onboarding_cases, "
    "broker_onboarding_checks, broker_onboarding_approvals, platform_subjects, app_user_roles TO {role}",
    "GRANT SELECT, INSERT ON TABLE broker_onboarding_events TO {role}",
    "GRANT INSERT ON TABLE audit_events TO {role}",
    "GRANT USAGE, SELECT ON SEQUENCE audit_events_audit_event_id_seq TO {role}",
)

AFTER_CHAIN = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
IN_CHAIN = datetime(2026, 10, 26, 8, 30, tzinfo=timezone.utc)
IN_REBUILD = datetime(2026, 10, 26, 16, 30, tzinfo=timezone.utc)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class OnboardingWorkerDatabaseTests(_MigratedDatabase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row

        self.db = psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE), row_factory=dict_row,
                                  options=f"-c app.tenant_id={TENANT}")
        self.addCleanup(self.db.close)
        self.addCleanup(self._clean)

    def _clean(self):
        # The worker commits, so each test removes what it made.
        self.db.rollback()
        for statement in (
            "DELETE FROM onboarding_worker_runs",
            "DELETE FROM portfolio_benchmark_mapping",
            "DELETE FROM user_account_access",
            "DELETE FROM app_user_roles",
            "DELETE FROM app_users",
        ):
            self.db.execute(statement)
        self.db.execute("UPDATE broker_onboarding_cases SET status = 'cancelled' WHERE status <> 'cancelled' "
                        "AND stage IN ('request', 'load_and_reconcile', 'build_history')")
        self.db.commit()

    def _kiwoom(self, account: str) -> str:
        from ibkr_etl.broker_connections import new_connection_record
        from ibkr_etl.onboarding_v2_web import register_connection

        record = new_connection_record(
            broker="kiwoom_rest", display_name=f"Kiwoom {uuid.uuid4().hex[:6]}", environment="mock",
            credentials={"app_key": "k", "app_secret": "s"}, expires_on=None, ip_locked_to=None,
            today=date(2026, 10, 13))
        register_connection(self.db, TENANT, record=record, stored_fingerprint="0" * 16, query_id=None,
                            account_id=account, actor=IDENTITY)
        return record.connection_id

    def _start(self, account: str, **extra) -> dict:
        from ibkr_etl.onboarding_v2_web import start_case

        return start_case(self.db, TENANT, connection_id=self._kiwoom(account), broker_account_id=account,
                          display_name=f"Synthetic {account}", reporting_currency="KRW",
                          history_start_date=date(2025, 1, 2), actor=IDENTITY, today=date(2026, 10, 13), **extra)

    def _benchmark(self, benchmark_id: str) -> None:
        self.db.execute(
            "INSERT INTO benchmark_master (tenant_id, benchmark_id, symbol, name, provider, currency, "
            "return_convention, created_at, updated_at) VALUES (%s, %s, %s, %s, 'synthetic', 'KRW', 'price', "
            "'2026-01-01', '2026-01-01') ON CONFLICT DO NOTHING",
            (TENANT, benchmark_id, benchmark_id.upper(), f"Synthetic {benchmark_id}"))

    def _worker(self):
        from ibkr_etl.db import PostgresConnection

        return PostgresConnection(self.db)

    def test_three_cases_can_be_open_and_a_fourth_waits(self):
        from ibkr_etl.onboarding_v2_web import OnboardingError, onboarding_overview

        accounts = [f"5{n:09d}" for n in range(1, 5)]
        for account in accounts[:3]:
            self._start(account)
        overview = onboarding_overview(self.db, TENANT, today=date(2026, 10, 13), platform_ip=None)
        self.assertEqual((overview["open_cases"], overview["can_start"]), (3, False))
        with self.assertRaisesRegex(OnboardingError, "3 accounts are already being onboarded"):
            self._start(accounts[3])

    def test_benchmarks_are_account_terms_and_mapped_at_approval_one(self):
        from ibkr_etl.onboarding_v2_web import (
            OnboardingError, approve_case, case_detail, record_platform_check, set_case_terms)

        for benchmark in ("kospi", "kosdaq", "msci-korea"):
            self._benchmark(benchmark)
        with self.assertRaisesRegex(OnboardingError, "Not an active benchmark"):
            self._start("5100000001", benchmarks={"primary": "nikkei"})
        self.db.rollback()
        for benchmark in ("kospi", "kosdaq", "msci-korea"):
            self._benchmark(benchmark)
        case_id = self._start("5100000001", benchmarks={"primary": "kospi", "secondary": ["kosdaq"]},
                              include_us=False)["case_id"]
        detail = case_detail(self.db, TENANT, case_id)
        self.assertEqual(detail["account_terms"]["benchmarks"], {"primary": "kospi", "secondary": ["kosdaq"]})
        self.assertIs(detail["account_terms"]["kiwoom_us_account"], False)
        self.assertEqual(detail["benchmarks"]["primary"]["name"], "Synthetic kospi")
        self.assertTrue(detail["terms_open"])
        set_case_terms(self.db, TENANT, case_id, benchmarks={"primary": "msci-korea", "secondary": ["kospi"]},
                       include_us=True, actor=IDENTITY)
        record_platform_check(self.db, TENANT, case_id, "connection_sees_account", "pass", "Answered.")
        for check_id in ("holdings_and_cash_loaded", "securities_mapped", "nav_reconciled", "transactions_loaded"):
            record_platform_check(self.db, TENANT, case_id, check_id, "pass", "Fine.")
        detail = case_detail(self.db, TENANT, case_id)
        self.assertEqual(detail["account_terms"]["benchmarks"], {"primary": "msci-korea", "secondary": ["kospi"]})
        approve_case(self.db, TENANT, case_id, approval="account_terms", terms_sha256=detail["account_terms_sha256"],
                     acknowledge_warnings=[], confirmation=None, actor=IDENTITY)
        mapped = {(row["benchmark_id"], row["mapping_type"], row["effective_from"]) for row in self.db.execute(
            "SELECT benchmark_id, mapping_type, effective_from FROM portfolio_benchmark_mapping WHERE subject_id = %s",
            ("KIWOOM-5100000001",)).fetchall()}
        self.assertEqual(mapped, {("msci-korea", "primary", "2025-01-02"), ("kospi", "secondary", "2025-01-02")})
        with self.assertRaisesRegex(OnboardingError, "already approved"):
            set_case_terms(self.db, TENANT, case_id, benchmarks=None, include_us=None, actor=IDENTITY)

    def test_run_now_is_one_request_that_the_worker_takes(self):
        from ibkr_etl.onboarding_v2_web import case_detail, record_platform_check, request_worker_run
        from ibkr_etl.onboarding_worker import Steps, run_worker

        case_id = self._start("5200000001")["case_id"]
        first = request_worker_run(self.db, TENANT, actor=IDENTITY, case_id=case_id)
        again = request_worker_run(self.db, TENANT, actor=IDENTITY, case_id=case_id)
        self.assertEqual((first["status"], again["status"], again["run_id"]),
                         ("requested", "already_waiting", first["run_id"]))
        self.db.commit()
        # A fixed time after the daily chain, so the check passes at any hour (the chain plans nothing).
        waiting = case_detail(self.db, TENANT, case_id, now=AFTER_CHAIN)["platform_work"]
        self.assertEqual(waiting["waiting"]["status"], "requested")
        self.assertIn("Kiwoom load and history", waiting["run_now_does"])
        self.db.rollback()

        # During the daily chain nothing runs and the request keeps waiting.
        skipped = run_worker(self._worker(), TENANT, now=IN_CHAIN, steps=Steps())
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(self.db.execute("SELECT status FROM onboarding_worker_runs WHERE run_id = %s",
                                         (first["run_id"],)).fetchone()["status"], "requested")
        self.db.rollback()

        asked = []

        def kiwoom(case_ids, connection_ids, history):
            asked.append((set(case_ids), set(connection_ids), history))
            worker = self._worker()
            record_platform_check(self.db, TENANT, case_id, "connection_sees_account", "pass", "Answered.")
            worker.commit()
            return [{"case_id": case_id, "connection_id": None, "status": "loaded", "stage": "load_and_reconcile"}]

        summary = run_worker(self._worker(), TENANT, now=AFTER_CHAIN, steps=Steps(kiwoom=kiwoom))
        self.assertEqual(summary["requests"], 1)
        self.assertEqual(asked[0][0], {case_id})
        self.assertTrue(asked[0][2])
        run = self.db.execute("SELECT status, message, steps_json FROM onboarding_worker_runs WHERE run_id = %s",
                              (first["run_id"],)).fetchone()
        self.assertEqual(run["status"], "done")
        self.assertIn("Kiwoom load and history", run["message"])
        self.assertEqual([item["step"] for item in run["steps_json"]], ["kiwoom"])
        work = case_detail(self.db, TENANT, case_id)["platform_work"]
        self.assertIsNone(work["waiting"])
        self.assertEqual(work["runs"][0]["status"], "done")
        self.db.rollback()

    def test_a_failed_step_is_recorded_and_check_now_reaches_a_new_connection(self):
        from ibkr_etl.onboarding_v2_web import request_worker_run
        from ibkr_etl.onboarding_worker import Steps, run_worker

        connection_id = self._kiwoom("5300000001")
        request = request_worker_run(self.db, TENANT, actor=IDENTITY, connection_id=connection_id)
        self.db.commit()
        calls = []

        def kiwoom(case_ids, connection_ids, history):
            calls.append((set(case_ids), set(connection_ids), history))
            raise RuntimeError("Kiwoom said no")

        summary = run_worker(self._worker(), TENANT, now=IN_REBUILD, steps=Steps(kiwoom=kiwoom))
        # A pending connection is checked even without a request; in Kiwoom's rebuild hours, without history.
        self.assertIn(connection_id, calls[0][1])
        self.assertFalse(calls[0][2])
        self.assertEqual(summary["steps"][0]["outcome"], "failed")
        run = self.db.execute("SELECT status, message FROM onboarding_worker_runs WHERE run_id = %s",
                              (request["run_id"],)).fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertIn("Kiwoom said no", run["message"])

    def test_go_live_gives_ticked_viewers_read_access(self):
        from ibkr_etl.onboarding_v2_web import OnboardingError, _grant_viewers, viewer_choices

        self.db.execute("INSERT INTO platform_subjects (tenant_id, subject_id, subject_type, display_name, "
                        "base_currency, reporting_currency, history_start_date, inception_date, lifecycle_status, "
                        "metadata_json) VALUES (%s, 'KIWOOM-5400000001', 'physical_account', 'Synthetic', 'KRW', "
                        "'KRW', '2025-01-02', '2025-01-02', 'active', '{}'::jsonb) ON CONFLICT DO NOTHING", (TENANT,))
        for object_id, role in (("viewer-one", "viewer"), ("viewer-two", "viewer"), ("analyst", "onboarding_operator")):
            self.db.execute("INSERT INTO app_users (tenant_id, entra_object_id, email, display_name, role_name, "
                            "status, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 'active', '2026-01-01', "
                            "'2026-01-01')", (TENANT, object_id, f"{object_id}@example.com", object_id,
                                              "viewer" if role == "viewer" else "analyst"))
            self.db.execute("INSERT INTO app_user_roles (tenant_id, entra_object_id, role_name, effective_from) "
                            "VALUES (%s, %s, %s, '2026-01-01')", (TENANT, object_id, role))
        choices = viewer_choices(self.db, TENANT, "KIWOOM-5400000001")
        self.assertEqual([item["object_id"] for item in choices], ["viewer-one", "viewer-two"])
        with self.assertRaisesRegex(OnboardingError, "Only active viewers"):
            _grant_viewers(self.db, TENANT, "KIWOOM-5400000001", ["analyst"], IDENTITY)
        self.db.rollback()
        self.db.execute("INSERT INTO platform_subjects (tenant_id, subject_id, subject_type, display_name, "
                        "base_currency, reporting_currency, history_start_date, inception_date, lifecycle_status, "
                        "metadata_json) VALUES (%s, 'KIWOOM-5400000001', 'physical_account', 'Synthetic', 'KRW', "
                        "'KRW', '2025-01-02', '2025-01-02', 'active', '{}'::jsonb) ON CONFLICT DO NOTHING", (TENANT,))
        for object_id in ("viewer-one", "viewer-two"):
            self.db.execute("INSERT INTO app_users (tenant_id, entra_object_id, email, display_name, role_name, "
                            "status, created_at, updated_at) VALUES (%s, %s, %s, %s, 'viewer', 'active', "
                            "'2026-01-01', '2026-01-01')", (TENANT, object_id, f"{object_id}@example.com", object_id))
            self.db.execute("INSERT INTO app_user_roles (tenant_id, entra_object_id, role_name, effective_from) "
                            "VALUES (%s, %s, 'viewer', '2026-01-01')", (TENANT, object_id))
        self.assertEqual(_grant_viewers(self.db, TENANT, "KIWOOM-5400000001", ["viewer-one"], IDENTITY), 1)
        self.assertEqual(_grant_viewers(self.db, TENANT, "KIWOOM-5400000001", ["viewer-one"], IDENTITY), 0)
        levels = self.db.execute("SELECT entra_object_id, access_level FROM user_account_access "
                                 "WHERE subject_id = 'KIWOOM-5400000001'").fetchall()
        self.assertEqual([(row["entra_object_id"], row["access_level"]) for row in levels], [("viewer-one", "read")])


    def test_the_website_login_can_do_run_now_and_viewer_access_with_the_bootstrap_grants(self):
        import psycopg
        from psycopg.rows import dict_row

        from ibkr_etl.onboarding_v2_web import OnboardingError, _grant_viewers, note_worker_start, request_worker_run

        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        from bootstrap_postgres import APPROVAL_ONBOARDING_GRANTS

        case_id = self._start("5500000001")["case_id"]
        self.db.execute("INSERT INTO platform_subjects (tenant_id, subject_id, subject_type, display_name, "
                        "base_currency, reporting_currency, history_start_date, inception_date, lifecycle_status, "
                        "metadata_json) VALUES (%s, 'KIWOOM-5500000001', 'physical_account', 'Synthetic', 'KRW', "
                        "'KRW', '2025-01-02', '2025-01-02', 'active', '{}'::jsonb) ON CONFLICT DO NOTHING", (TENANT,))
        self.db.execute("INSERT INTO app_users (tenant_id, entra_object_id, email, display_name, role_name, status, "
                        "created_at, updated_at) VALUES (%s, 'viewer-five', 'viewer-five@example.com', 'Five', "
                        "'viewer', 'active', '2026-01-01', '2026-01-01')", (TENANT,))
        self.db.execute("INSERT INTO app_user_roles (tenant_id, entra_object_id, role_name, effective_from) "
                        "VALUES (%s, 'viewer-five', 'viewer', '2026-01-01')", (TENANT,))
        self.db.commit()
        with psycopg.connect(_dsn(ADMIN_DSN, "postgres"), autocommit=True) as admin:
            admin.execute(f"DROP ROLE IF EXISTS {WEBSITE_WRITER}")
            admin.execute(f"CREATE ROLE {WEBSITE_WRITER} LOGIN PASSWORD '{TEST_PASSWORD}'")
        self.addCleanup(self._drop_writer)
        for statement in WRITER_BEFORE_085:
            self.db.execute(statement.format(role=WEBSITE_WRITER))
        self.db.commit()

        def writer():
            connection = psycopg.connect(_dsn(ADMIN_DSN, self.database, WEBSITE_WRITER), row_factory=dict_row,
                                         options=f"-c app.tenant_id={TENANT}")
            self.addCleanup(connection.close)
            return connection

        before = writer()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            request_worker_run(before, TENANT, actor=IDENTITY, case_id=case_id)
        before.rollback()
        with self.assertRaisesRegex(OnboardingError, "cannot give viewers access"):
            _grant_viewers(before, TENANT, "KIWOOM-5500000001", ["viewer-five"], IDENTITY)
        before.close()

        for _table, statement in APPROVAL_ONBOARDING_GRANTS:
            self.db.execute(statement.format(WEBSITE_WRITER))
        self.db.commit()
        website = writer()
        request = request_worker_run(website, TENANT, actor=IDENTITY, case_id=case_id)
        note_worker_start(website, TENANT, request["run_id"], "ibkrv2stg-onboard-worker-abc", None)
        self.assertEqual(request_worker_run(website, TENANT, actor=IDENTITY, case_id=case_id)["status"],
                         "already_waiting")
        self.assertEqual(_grant_viewers(website, TENANT, "KIWOOM-5500000001", ["viewer-five"], IDENTITY), 1)
        website.commit()
        # Read access only is added; the website login can never change or remove a grant.
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            website.execute("DELETE FROM user_account_access WHERE subject_id = 'KIWOOM-5500000001'")
        website.rollback()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            website.execute("UPDATE user_account_access SET access_level = 'write' "
                            "WHERE subject_id = 'KIWOOM-5500000001'")
        website.rollback()
        website.close()
        self.assertEqual(self.db.execute("SELECT execution_reference FROM onboarding_worker_runs WHERE run_id = %s",
                                         (request["run_id"],)).fetchone()["execution_reference"],
                         "ibkrv2stg-onboard-worker-abc")
        self.db.rollback()

    def _drop_writer(self):
        import psycopg

        self.db.rollback()
        with psycopg.connect(_dsn(ADMIN_DSN, self.database), autocommit=True) as admin:
            admin.execute(f"DROP OWNED BY {WEBSITE_WRITER}")
        with psycopg.connect(_dsn(ADMIN_DSN, "postgres"), autocommit=True) as admin:
            admin.execute(f"DROP ROLE IF EXISTS {WEBSITE_WRITER}")


if __name__ == "__main__":
    unittest.main()
