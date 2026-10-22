"""The onboarding tables payload against a real PostgreSQL shaped like production before 064.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI
starts one). Skipped otherwise. Every migration before 064 is applied and
recorded except the held-back ones and 063; the website and approval roles are
set up as bootstrap_postgres.py does (the approval role starts with no table rights).
"""
from __future__ import annotations

import hashlib
import os
import sys
import unittest
import uuid
from pathlib import Path

from test_medallion_layers_db import NEEDS_LAYERS

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER = "onboard_test_owner"
WEB = "onboard_test_web"
APPROVAL = "onboard_test_approval"
TEST_PASSWORD = "onboarding-test-only-password"  # throwaway roles in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}


def _dsn(database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(ADMIN_DSN)
    params["dbname"] = database
    params["options"] = f"-c app.tenant_id={TENANT}"
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class OnboardingTablesDatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        import rollout_onboarding_tables as rollout

        self.bundle = dict(rollout.bundle(), web_role=WEB, approval_role=APPROVAL)
        self.database = "onboard_test_" + uuid.uuid4().hex[:8]
        self.bundle["database"] = self.database
        # The medallion layer migrations (068 on) came after this rollout and move its tables, and
        # NEEDS_LAYERS (074) runs only once they have; 082 (Kiwoom's raw answers in bronze) came
        # after it too and refers to its broker_connections.
        later = {path.name for path in (ROOT / "migrations" / "postgres").glob("*_medallion_*.sql")}
        later.update(NEEDS_LAYERS)
        later.add("082_kiwoom_bronze.sql")
        self.bundle["expected"] = {k: v for k, v in self.bundle["expected"].items() if k not in later}
        skipped = (set(self.bundle["targets"]) | set(self.bundle["held_back"]) | set(self.bundle["other_rollouts"])
                   | later)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (WEB, APPROVAL):
                admin.execute(f"DROP ROLE IF EXISTS {role}")
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
                admin.execute(f"CREATE ROLE {OWNER} LOGIN CREATEROLE PASSWORD '{TEST_PASSWORD}'")
            for role in (WEB, APPROVAL):
                admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{self.database}" OWNER {OWNER}')
        with psycopg.connect(_dsn(self.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            owner.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, "
                          "applied_at TEXT NOT NULL)")
            for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
                if path.name in skipped:
                    continue
                text = path.read_text(encoding="utf-8")
                try:
                    owner.execute(text)
                    owner.commit()
                except Exception:
                    owner.rollback()
                    if path.name not in TOLERATED_MIGRATIONS:
                        raise
                owner.execute("INSERT INTO schema_migrations VALUES (%s, %s, '2026-10-01T00:00:00+00:00')",
                              (path.name, hashlib.sha256(text.encode()).hexdigest()))
                if owner.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                        owner.execute("SELECT 1 FROM tenants").fetchone() is None:
                    owner.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                  "VALUES (%s, 'Test', 'active', '2026-01-01')", (TENANT,))
                owner.commit()
            for role in (WEB, APPROVAL):
                owner.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
                owner.execute(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role}")
            owner.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {WEB}")
            owner.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {WEB}")
            owner.commit()

    def tearDown(self):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{self.database}" WITH (FORCE)')
            for role in (WEB, APPROVAL):
                admin.execute(f"DROP ROLE IF EXISTS {role}")

    def _run(self, bundle=None):
        import psycopg
        import onboarding_tables_payload as payload

        with psycopg.connect(_dsn(self.database, OWNER), autocommit=True) as owner:
            return payload.run(owner, bundle or self.bundle)

    def _migrations(self):
        import psycopg

        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            return dict(owner.execute("SELECT version, sha256 FROM schema_migrations").fetchall())

    def test_applies_064_and_065_grants_the_website_and_is_safe_to_rerun(self):
        import psycopg

        first = self._run()
        self.assertEqual(first["status"], "pass", first)
        self.assertEqual(first["applied"], self.bundle["targets"])
        for name in self.bundle["targets"]:
            self.assertEqual(self._migrations()[name], self.bundle["expected"][name])

        # The pages write a connection through the approval role and read it through the website role.
        connection_id = str(uuid.uuid4())
        with psycopg.connect(_dsn(self.database, APPROVAL)) as approval:
            approval.execute(
                "INSERT INTO broker_connections(connection_id, broker, environment, display_name, "
                "key_vault_secret_name, credential_fingerprint, status) VALUES (%s, 'kiwoom_rest', 'mock', 'Test', "
                "%s, %s, 'pending_check')", (connection_id, "broker-kiwoom-rest-" + "a" * 32, "b" * 16))
            approval.execute("UPDATE broker_connections SET status = 'healthy' WHERE connection_id = %s", (connection_id,))
            approval.commit()
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                approval.execute("DELETE FROM broker_connections")
        with psycopg.connect(_dsn(self.database, WEB)) as web:
            self.assertEqual(web.execute("SELECT status FROM broker_connections").fetchone()[0], "healthy")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                web.execute("UPDATE broker_connections SET status = 'failing'")

        second = self._run()
        self.assertEqual(second["status"], "pass", second)
        self.assertEqual(second["applied"], [])

    def test_refuses_when_another_migration_is_pending(self):
        import psycopg

        missing = "062_report_design_updates.sql"
        with psycopg.connect(_dsn(self.database, OWNER), autocommit=True) as owner:
            owner.execute("DELETE FROM schema_migrations WHERE version = %s", (missing,))
        result = self._run()
        self.assertEqual(result["status"], "refused")
        self.assertIn(missing, result["pending"])
        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            self.assertIsNone(owner.execute("SELECT to_regclass('public.broker_connections')").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
