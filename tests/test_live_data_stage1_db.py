"""The stage 1 payload against a real PostgreSQL shaped like production before 063.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI
starts one). Skipped otherwise. The payload runs as a non-superuser schema
owner with CREATEROLE, like the Azure admin login, on a database where every
migration before 063 is applied and recorded and the website role reads new
tables through default privileges, as bootstrap_postgres.py sets up.
"""
from __future__ import annotations

import hashlib
import os
import sys
import unittest
import uuid
from pathlib import Path

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER = "stage1_test_owner"
WEB = "stage1_test_web"
COLLECTOR = "ibkr_live_collector_trial"
TEST_PASSWORD = "stage1-test-only-password"  # throwaway roles in a disposable test server
COLLECTOR_PASSWORD = "stage1-collector-test-only-password-0123456789"
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}


def _dsn(database: str, user: str | None = None, password: str = TEST_PASSWORD) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(ADMIN_DSN)
    params["dbname"] = database
    params["options"] = f"-c app.tenant_id={TENANT}"
    if user:
        params["user"] = user
        params["password"] = password
    return make_conninfo(**params)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class Stage1PayloadDatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        import rollout_live_data_stage1 as rollout

        self.bundle = dict(rollout.bundle(), role=COLLECTOR, web_role=WEB)
        self.database = "stage1_test_" + uuid.uuid4().hex[:8]
        self.bundle["database"] = self.database
        self.verifier = rollout.scram_verifier(COLLECTOR_PASSWORD)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (COLLECTOR, WEB):
                admin.execute(f"DROP ROLE IF EXISTS {role}")
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
                admin.execute(f"CREATE ROLE {OWNER} LOGIN CREATEROLE PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f"CREATE ROLE {WEB} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{self.database}" OWNER {OWNER}')
        with psycopg.connect(_dsn(self.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            owner.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, "
                          "applied_at TEXT NOT NULL)")
            for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
                if path.name == self.bundle["target"]:
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
            owner.execute(f"GRANT USAGE ON SCHEMA public TO {WEB}")
            owner.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {WEB}")
            owner.execute(f"GRANT EXECUTE ON FUNCTION public.app_tenant_id() TO {WEB}")
            owner.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {WEB}")
            owner.commit()

    def tearDown(self):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{self.database}" WITH (FORCE)')
            for role in (COLLECTOR, WEB):
                admin.execute(f"DROP ROLE IF EXISTS {role}")

    def _run(self, bundle=None):
        import psycopg
        import live_data_stage1_payload as payload

        with psycopg.connect(_dsn(self.database, OWNER), autocommit=True) as owner:
            return payload.run(owner, bundle or self.bundle, self.verifier)

    def _migrations(self):
        import psycopg

        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            return dict(owner.execute("SELECT version, sha256 FROM schema_migrations").fetchall())

    def test_applies_063_creates_the_login_and_is_safe_to_rerun(self):
        import psycopg

        first = self._run()
        self.assertEqual(first["status"], "pass", first)
        self.assertTrue(first["migration_applied"])
        self.assertTrue(first["role_created"])
        self.assertEqual(first["website_cannot_read"], [])
        self.assertEqual(self._migrations()[self.bundle["target"]], self.bundle["expected"][self.bundle["target"]])

        # The verifier became a working password, and the login reaches only the live tables.
        with psycopg.connect(_dsn(self.database, COLLECTOR, COLLECTOR_PASSWORD)) as collector:
            collector.execute("INSERT INTO live_gateway_status(observed_at, gateway_state, collector_version) "
                              "VALUES (now(), 'connected', 'test')")
            collector.commit()
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                collector.execute("SELECT 1 FROM accounts")
        with psycopg.connect(_dsn(self.database, WEB)) as web:
            self.assertEqual(web.execute("SELECT count(*) FROM live_gateway_status").fetchone()[0], 1)
            web.execute("SELECT * FROM subject_live_summary").fetchall()

        second = self._run()
        self.assertEqual(second["status"], "pass", second)
        self.assertFalse(second["migration_applied"])
        self.assertFalse(second["role_created"])

    def test_refuses_without_changes_when_the_database_differs_from_main(self):
        before = self._migrations()
        name = sorted(before)[5]
        changed = dict(self.bundle, expected=dict(self.bundle["expected"], **{name: "0" * 64}))
        result = self._run(changed)
        self.assertEqual((result["status"], result["changed"]), ("refused", [name]))

        extra = dict(self.bundle, expected=dict(self.bundle["expected"], **{"064_not_applied.sql": "1" * 64}))
        result = self._run(extra)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["pending"], ["063_live_ibkr_data.sql", "064_not_applied.sql"])

        missing = dict(self.bundle, expected={k: v for k, v in self.bundle["expected"].items() if k != name})
        self.assertEqual(self._run(missing)["not_on_main"], [name])
        self.assertEqual(self._migrations(), before)

        import psycopg

        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            self.assertIsNone(owner.execute("SELECT to_regclass('public.live_quotes')").fetchone()[0])
            self.assertIsNone(owner.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (COLLECTOR,)).fetchone())

    def _forget(self, *names):
        import psycopg

        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            owner.execute("DELETE FROM schema_migrations WHERE version = ANY(%s)", (list(names),))

    def test_applies_063_while_the_held_back_migrations_stay_pending(self):
        # Production never recorded 054, 055 or 059-061.
        held = self.bundle["held_back"]
        self._forget(*held)
        result = self._run()
        self.assertEqual(result["status"], "pass", result)
        self.assertTrue(result["migration_applied"])
        self.assertEqual(result["held_back_pending"], sorted(held))
        ledger = self._migrations()
        self.assertIn(self.bundle["target"], ledger)
        self.assertFalse(set(held) & set(ledger))

    def test_refuses_when_a_migration_outside_the_held_back_list_is_missing(self):
        import psycopg

        missing = "058_specialist_observation_authority.sql"
        self._forget(*self.bundle["held_back"], missing)
        result = self._run()
        self.assertEqual(result["status"], "refused")
        self.assertIn(missing, result["pending"])
        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            self.assertIsNone(owner.execute("SELECT to_regclass('public.live_quotes')").fetchone()[0])
            self.assertIsNone(owner.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (COLLECTOR,)).fetchone())

    def test_reports_when_the_website_role_cannot_read_the_new_tables(self):
        import psycopg

        with psycopg.connect(_dsn(self.database, OWNER), autocommit=True) as owner:
            owner.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM {WEB}")
        result = self._run()
        self.assertEqual(result["status"], "fail")
        self.assertIn("live_quotes", result["website_cannot_read"])


if __name__ == "__main__":
    unittest.main()
