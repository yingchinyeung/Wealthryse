"""The research zone's logins on a real PostgreSQL (scripts/research/live_reader_role.py and
scripts/research/research_server_roles.py).

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Each class builds its own database and test roles with unique names and
drops them afterwards. The administrator is the non-superuser test owner, as on Azure.
Synthetic data only (test_medallion_layers_db's seed: one account per tenant).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

import test_medallion_layers_db as layers

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
U8 = ROOT / "infra" / "medallion" / "drafts" / "u8_silver_history.sql"
TENANT, OTHER_TENANT = layers.TENANT, layers.OTHER_TENANT


def script(name: str):
    spec = importlib.util.spec_from_file_location(f"research_db_{name}", ROOT / "scripts" / "research" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def setUpModule():
    if ADMIN_DSN:
        layers.setUpModule()


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class LiveReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles = script("live_reader_role")
        cls.database = f"research_reader_test_{uuid.uuid4().hex[:10]}"
        cls.reader = f"research_reader_test_{uuid.uuid4().hex[:8]}"
        try:
            layers._build(cls.database)
            with layers._connect(cls.database) as owner:
                owner.execute(U8.read_text(encoding="utf-8"))
                owner.execute("UPDATE public.silver_history_settings SET recording = true")
                owner.commit()
                layers._seed(owner)
                cls.first = cls.roles.provision(owner, password=layers.PASSWORD, role=cls.reader, database=cls.database)
                owner.commit()
        except Exception:
            layers._drop(cls.database, roles=(cls.reader,))
            raise

    @classmethod
    def tearDownClass(cls):
        layers._drop(cls.database, roles=(cls.reader,))

    def _reader(self, tenant=TENANT):
        return layers._connect(self.database, user=self.reader, tenant=tenant)

    def test_the_login_was_created_with_every_grant(self):
        self.assertTrue(self.first["created"])
        self.assertEqual(self.first["waiting_for"], [])  # U8 is applied here, so nothing waits
        self.assertEqual(self.first["grants"], len(self.roles.grant_statements(self.reader, self.database)))
        with layers._connect(self.database) as owner:
            again = self.roles.provision(owner, password=layers.PASSWORD, role=self.reader, database=self.database)
            owner.commit()
        self.assertFalse(again["created"])

    def test_it_reads_silver_and_as_known_for_its_own_tenant_only(self):
        for name in self.roles.SILVER_TABLES + self.roles.SILVER_VIEWS + self.roles.HISTORY_RELATIONS:
            with self.subTest(name), self._reader() as reader:
                reader.execute(f"SELECT * FROM silver.{name} LIMIT 1").fetchall()
        with self._reader() as reader:
            own =reader.execute("SELECT count(*) FROM silver.nav_snapshots").fetchone()[0]
            known = reader.execute("SELECT count(*) FROM silver.nav_snapshots_as_known(now())").fetchone()[0]
            accounts = {row[0] for row in reader.execute("SELECT DISTINCT account_id FROM silver.nav_snapshots")}
            before = reader.execute("SELECT count(*) FROM silver.nav_snapshots_as_known(%s)",
                                    (datetime(2020, 1, 1, tzinfo=timezone.utc),)).fetchone()[0]
        self.assertEqual((own, known, before), (10, 10, 0))
        self.assertEqual(accounts, {layers.ACCOUNTS[TENANT]})
        with self._reader(OTHER_TENANT) as reader:
            accounts = {row[0] for row in reader.execute("SELECT DISTINCT account_id FROM silver.trades_as_known(now())")}
        self.assertEqual(accounts, {layers.ACCOUNTS[OTHER_TENANT]})

    def test_it_reaches_no_gold_bronze_ops_or_credential_table_and_writes_nothing(self):
        import psycopg

        with layers._connect(self.database) as owner:
            forbidden = [name for (name,) in owner.execute(
                "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind IN ('r', 'v', 'p') AND (n.nspname IN ('gold', 'bronze', 'agent') OR (n.nspname = 'public' "
                "AND c.relname IN ('audit_events', 'ibkr_flex_credentials', 'broker_connections', 'accounts', "
                "'silver_history_settings', 'schema_migrations'))) ORDER BY 1").fetchall()]
        self.assertIn("gold.reporting_snapshots", forbidden)
        self.assertIn("bronze.flex_rows", forbidden)
        for name in forbidden:
            with self.subTest(name), self._reader() as reader, self.assertRaises(psycopg.errors.InsufficientPrivilege):
                reader.execute(f"SELECT 1 FROM {name} LIMIT 1")
        for statement in ("INSERT INTO silver.accounts (account_id) VALUES ('U9100099')",
                          "UPDATE silver.nav_snapshots SET total_text = '0'", "CREATE TEMP TABLE scratch (x int)",
                          "CREATE TABLE silver.scratch (x int)"):
            with self.subTest(statement), self._reader() as reader, self.assertRaises(psycopg.Error):
                reader.execute(statement)

    def test_the_privilege_check_finds_nothing_beyond_the_list(self):
        with layers._connect(self.database) as owner:
            report = self.roles.privilege_report(owner, self.reader)
        self.assertEqual(self.roles.least_privilege_problems(report), [], report)
        self.assertFalse(report["writable"])
        self.assertEqual(set(report["schemas"]), {"silver", "public"})
        self.assertTrue(all(name.startswith("silver.") for name in report["readable"]))
        self.assertIn("silver.trades_as_known(at timestamp with time zone)", report["functions"])
        # A grant beyond the list is caught.
        with layers._connect(self.database) as owner:
            owner.execute(f"GRANT SELECT ON gold.reporting_snapshots TO {self.reader}")
            report = self.roles.privilege_report(owner, self.reader)
            owner.rollback()
        self.assertTrue(self.roles.least_privilege_problems(report))


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class ResearchServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.roles = script("research_server_roles")
        cls.job_module = script("run_research_job")
        suffix = uuid.uuid4().hex[:8]
        cls.database = f"research_server_test_{suffix}"
        cls.owner_role, cls.job_role = f"research_owner_test_{suffix}", f"research_job_test_{suffix}"
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (layers.OWNER,)).fetchone() is None:
                admin.execute(f"CREATE ROLE {layers.OWNER} LOGIN CREATEROLE PASSWORD '{layers.PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {layers.OWNER}')
        try:
            with layers._connect(cls.database, tenant=None) as administrator:
                cls.first = cls.roles.provision(administrator, password=layers.PASSWORD, owner=cls.owner_role,
                                                job=cls.job_role, database=cls.database)
                administrator.commit()
        except Exception:
            cls._drop()
            raise

    @classmethod
    def _drop(cls):
        layers._drop(cls.database, roles=(cls.job_role, cls.owner_role))

    @classmethod
    def tearDownClass(cls):
        cls._drop()

    def _job(self, tenant=TENANT):
        return layers._connect(self.database, user=self.job_role, tenant=tenant)

    def test_setup_is_repeatable(self):
        self.assertEqual(self.first["created"], [self.owner_role, self.job_role])
        with layers._connect(self.database, tenant=None) as administrator:
            again = self.roles.provision(administrator, password=layers.PASSWORD, owner=self.owner_role,
                                         job=self.job_role, database=self.database)
            administrator.commit()
            report = self.roles.privilege_report(administrator, self.job_role)
        self.assertEqual(again["created"], [])
        self.assertEqual(self.roles.least_privilege_problems(report), [], report)
        self.assertEqual(report["writable"], {"research.feature_exports": ["INSERT"],
                                              "research.job_runs": ["INSERT", "UPDATE"]})

    def test_the_job_records_runs_for_its_tenant_only(self):
        import psycopg

        register = object.__new__(self.job_module.DatabaseRegister)  # the login-name check wants research_job
        register._url = layers._url(self.database, self.job_role)
        register._tenant = TENANT
        run = f"research-test-{uuid.uuid4().hex[:8]}"
        register.started(run, "export-features", "abc1234", {"argv": ["export-features"]})
        self.assertEqual(register.running(other_than=None, within_seconds=3600), 1)
        self.assertEqual(register.running(other_than=run, within_seconds=3600), 0)
        register.export_recorded(run, {"as_known_on": "2026-10-17", "subjects": ["U9100001"], "datasets": {
            "trades": {"manifest": "trades/2026-10-17/U9100001/manifest.json", "rows_sha256": "a" * 64, "rows": 3}}})
        register.export_recorded(run, {"as_known_on": "2026-10-17", "subjects": ["U9100001"], "datasets": {
            "trades": {"manifest": "trades/2026-10-17/U9100001/manifest.json", "rows_sha256": "a" * 64, "rows": 3}}})
        register.finished(run, "passed", {"result": {"ok": True}})
        self.assertEqual(register.running(other_than=None, within_seconds=3600), 0)
        check = register.check()
        self.assertEqual((check["research_usage"], check["research_create"], check["database_create"]),
                         (True, False, False))
        with self._job() as job:
            status, detail = job.execute("SELECT status, detail FROM research.job_runs WHERE run_id = %s",
                                         (run,)).fetchone()
            exports = job.execute("SELECT count(*) FROM research.feature_exports WHERE run_id = %s", (run,)).fetchone()[0]
        self.assertEqual((status, detail["result"], detail["argv"], exports), ("passed", {"ok": True},
                                                                              ["export-features"], 1))
        with self._job(OTHER_TENANT) as job:
            self.assertEqual(job.execute("SELECT count(*) FROM research.job_runs").fetchone()[0], 0)
        for statement in (
                "INSERT INTO research.job_runs (run_id, tenant_id, command, started_at, status, code_version) "
                f"VALUES ('x', '{OTHER_TENANT}', 'c', now(), 'running', 'v')",
                "DELETE FROM research.job_runs", "TRUNCATE research.feature_exports",
                "CREATE TABLE research.scratch (x int)", "CREATE TABLE public.scratch (x int)",
                "CREATE SCHEMA scratch"):
            with self.subTest(statement), self._job() as job, self.assertRaises(psycopg.Error):
                job.execute(statement)


if __name__ == "__main__":
    unittest.main()
