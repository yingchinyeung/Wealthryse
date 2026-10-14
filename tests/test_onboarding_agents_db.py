"""The case page's agent parts and the scheduled recorders against a real database.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN (CI starts one). Skipped otherwise. Creates and drops its own
database and uses synthetic data only.
"""
from __future__ import annotations

import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER_ROLE = "onboarding_agents_test_owner"
READER = "onboarding_agents_test_reader"
TEST_PASSWORD = "onboarding-agents-test-only"  # throwaway roles in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
IDENTITY = "a" * 64
ON = {"ONBOARDING_AGENTS_ENABLED": "true"}


def _dsn(base: str, database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(base)
    params["dbname"] = database
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class OnboardingAgentsDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection

        cls.database = "onboarding_agents_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (OWNER_ROLE, READER):
                if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
                    # Not superusers, so forced row-level security applies as on Azure.
                    admin.execute(f"CREATE ROLE {role} LOGIN CREATEROLE PASSWORD '{TEST_PASSWORD}'")
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
            # A role that can read the cases but was never given the agents' results (no --platform-reader).
            owner.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER}")

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

    def _composite_before_approval_one(self, db, subject_id):
        from datetime import date

        from ibkr_etl.onboarding_v2_web import start_derived_case
        from ibkr_etl.subject_onboarding import fixed_unit_composite_definition

        account = "U" + uuid.uuid4().hex[:7].upper()
        db.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, base_currency, "
                   "lifecycle_status) VALUES (?, 'physical_account', 'Synthetic tech', 'USD', 'active')", (account,))
        definition = fixed_unit_composite_definition(
            subject_id=subject_id, display_name="Synthetic agents", base_currency="HKD",
            normalization_date="2025-01-20", constant_cash=58.0,
            components=[{"component_id": account, "component_name": "Synthetic tech",
                         "component_type": "account", "coefficient": 0.42, "source_currency": "USD"}])
        case_id = start_derived_case(db, TENANT, definition=definition, description="A synthetic composite case.",
                                     actor=IDENTITY, today=date(2025, 3, 3), report_style="alpha-world-ms")["case_id"]
        db.commit()
        return case_id

    def test_the_case_page_shows_the_latest_draft_and_the_agents_checks(self):
        from ibkr_etl.onboarding_v2_web import case_detail, record_platform_check

        db = self._platform()
        case_id = self._composite_before_approval_one(db, "synthetic-agents")
        for n, outcome in ((1, "fail"), (2, "warn")):
            db.execute(
                "INSERT INTO agent.onboarding_term_drafts (tenant_id, case_id, subject_id, documents_sha256, "
                "outcome, summary, terms, findings, documents) VALUES (?, ?, 'synthetic-agents', ?, ?, ?, "
                "?::jsonb, ?::jsonb, ?::jsonb)",
                (TENANT, case_id, str(n) * 64, outcome, f"Draft {n}.",
                 json.dumps({"reporting_currency": {"label": "Reporting currency", "value": "HKD", "status": "found",
                                                    "sources": [{"document": "mandate.pdf", "page": 2,
                                                                 "quote": "in Hong Kong dollars", "value": "HKD"}]}}),
                 json.dumps([{"severity": "medium", "message": "No fee frequency."}]),
                 json.dumps([{"document": "mandate.pdf", "sha256": "e" * 64}])))
            db.commit()
        record_platform_check(db, TENANT, case_id, "calculation_dry_run", "pass", "Ran.",
                              {"recorded_by": "agent", "reconciliation_id": 3})
        db.commit()

        with patch.dict(os.environ, {"ONBOARDING_AGENTS_ENABLED": ""}):
            off = case_detail(db, TENANT, case_id)
        self.assertEqual((off["agents"], off["documents_open"]), (None, False))
        with patch.dict(os.environ, ON):
            detail = case_detail(db, TENANT, case_id)
        draft = detail["agents"]["term_draft"]
        self.assertEqual((draft["summary"], draft["outcome"]), ("Draft 2.", "warn"))
        self.assertEqual(draft["terms"]["reporting_currency"]["sources"][0]["quote"], "in Hong Kong dollars")
        self.assertEqual(detail["agents"]["checks"], {"calculation_dry_run": {"recorded_by": "agent",
                                                                            "reconciliation_id": 3}})
        self.assertTrue(detail["documents_open"])
        db.rollback()

        # A website role never given the agents' results sees the case without them, not an error.
        reader = self._platform(READER)
        with patch.dict(os.environ, ON):
            limited = case_detail(reader, TENANT, case_id)
        self.assertEqual(limited["agents"]["term_draft"], None)
        self.assertEqual(limited["display_name"], "Synthetic agents")

    def _website(self, role=OWNER_ROLE):
        """A raw psycopg connection with dict rows, as the website's read-only database gives."""
        import psycopg
        from psycopg.rows import dict_row

        db = psycopg.connect(_dsn(ADMIN_DSN, self.database, role), row_factory=dict_row,
                             options=f"-c app.tenant_id={TENANT}")
        self.addCleanup(db.close)
        return db

    def test_the_readiness_pack_and_connection_reports_on_a_real_database(self):
        from ibkr_etl.onboarding_agents import case_facts, case_readiness, connection_reports

        db = self._platform()
        # One case at a time: reuse the one the other test opened when it ran first.
        open_case = db.execute("SELECT case_id FROM broker_onboarding_cases WHERE status <> 'cancelled' "
                               "AND stage IN ('request', 'load_and_reconcile') LIMIT 1").fetchone()
        case_id = str(open_case["case_id"]) if open_case else self._composite_before_approval_one(db, "synthetic-agents")
        connection_id = uuid.uuid4()
        for n, state in ((1, "queued"), (2, "found_accounts")):
            db.execute("INSERT INTO agent.connection_reports (tenant_id, connection_id, flex_source_id, report_key, "
                       "state, title, body, owner_steps) VALUES (?, ?, ?, ?, ?, ?, ?, ?::jsonb)",
                       (TENANT, connection_id, uuid.uuid4(), f"report-{n}", state, f"Report {n}",
                        "The login sees one account.", json.dumps(["Nothing to do."])))
        db.commit()

        website = self._website()
        pack = case_readiness(website, TENANT, case_id, "go_live", environ=ON)
        self.assertFalse(pack["ready"])
        self.assertEqual(len(pack["pack_sha256"]), 64)
        self.assertIn("Account terms is not approved yet.", " ".join(pack["blockers"]))
        self.assertIsNone(case_readiness(website, TENANT, case_id, "account_terms", environ=ON))
        reports = connection_reports(website, TENANT, environ=ON)
        self.assertEqual(reports[str(connection_id)]["state"], "found_accounts")
        self.assertEqual(reports[str(connection_id)]["owner_steps"], ["Nothing to do."])
        # What the analyst reads to answer "Ask about this case": plain JSON, the pack and the agents' results.
        facts = case_facts(website, TENANT, case_id)
        self.assertEqual(facts["case"]["case_id"], case_id)
        self.assertEqual(set(facts), {"case", "drafted_terms", "proposed_securities"})
        self.assertEqual(json.loads(json.dumps(facts)), facts)
        # The case page still reads after both, in the same read-only transaction.
        self.assertEqual(website.execute("SELECT 1 AS one").fetchone()["one"], 1)

        # A website role never given the agents' results sees no reports, not an error.
        self.assertEqual(connection_reports(self._website(READER), TENANT, environ=ON), {})

    def test_the_recorders_run_on_a_real_database(self):
        from ibkr_etl.onboarding_agents import run_reconciliations, run_reviews

        db = self._platform()
        self.assertEqual(run_reconciliations(db, TENANT, ON), {"status": "recorded", "cases": []})
        self.assertEqual(run_reviews(db, TENANT, ON), {"status": "recorded", "cases": []})
        reader = self._platform(READER)
        self.assertEqual(run_reconciliations(reader, TENANT, ON), {"status": "no_agent_results"})
        self.assertEqual(run_reviews(reader, TENANT, ON), {"status": "no_agent_results"})


if __name__ == "__main__":
    unittest.main()
