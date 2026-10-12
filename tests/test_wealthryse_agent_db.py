"""Database tests for the agent schema, tool server and store.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN (CI starts one). Skipped otherwise. Creates and drops its own
database; uses synthetic data from tests/fixtures/agent_seed.sql only.
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
OWNER = "11111111-1111-1111-1111-111111111111"
VIEWER = "22222222-2222-2222-2222-222222222222"
STRANGER = "33333333-3333-3333-3333-333333333333"
TEST_PASSWORD = "agent-test-only"  # throwaway roles in a disposable test server


def _dsn(base: str, database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(base)
    params["dbname"] = database
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class AgentDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import POSTGRES_MIGRATIONS_DIR, PostgresConnection

        cls.database = "agent_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in ("agent_test_reader", "agent_test_writer"):
                if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
                    admin.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{TEST_PASSWORD}'")
            # Like Azure: the schema owner is not a superuser, so the platform's
            # forced row-level security applies to the agent's definer functions.
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'agent_test_owner'").fetchone() is None:
                admin.execute(f"CREATE ROLE agent_test_owner LOGIN CREATEROLE PASSWORD '{TEST_PASSWORD}'")
            admin.execute("GRANT agent_test_reader, agent_test_writer TO agent_test_owner WITH ADMIN OPTION")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER agent_test_owner')
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        raw = psycopg.connect(_dsn(ADMIN_DSN, cls.database, "agent_test_owner"))
        connection = PostgresConnection(raw)
        connection.execute("SELECT set_config('app.tenant_id', ?, false)", (TENANT,))
        connection.commit()
        # Two historical migrations correct production rows that a fresh database
        # does not have; their failure leaves the schema these tests need intact.
        for path in sorted(POSTGRES_MIGRATIONS_DIR.glob("*.sql")):
            try:
                connection.execute(path.read_text(encoding="utf-8"))
                connection.commit()
            except Exception:
                connection.rollback()
            if connection.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                    connection.execute("SELECT 1 FROM tenants").fetchone() is None:
                connection.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                   "VALUES (?, 'Test', 'active', '2026-01-01')", (TENANT,))
                connection.commit()
        connection.close()
        # Raw psycopg: the SQL files use the jsonb "?" operator family and $$ bodies.
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database, "agent_test_owner"),
                             options=f"-c app.tenant_id={TENANT}") as owner:
            owner.execute((ROOT / "migrations/agent/001_agent_schema.sql").read_text(encoding="utf-8"))
            owner.execute((ROOT / "tests/fixtures/agent_seed.sql").read_text(encoding="utf-8"))
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:  # roles are cluster-wide
            admin.execute("GRANT agent_reader TO agent_test_reader")
            admin.execute("GRANT agent_writer TO agent_test_writer")

        from wealthryse_agent.config import DatabaseSettings, ServiceAuthSettings
        from wealthryse_agent.tool_server import ToolService, create_app

        cls.reader_settings = DatabaseSettings("", cls.database, "", "", TENANT,
                                               _dsn(ADMIN_DSN, cls.database, "agent_test_reader"))
        cls.writer_settings = DatabaseSettings("", cls.database, "", "", TENANT,
                                               _dsn(ADMIN_DSN, cls.database, "agent_test_writer"))
        auth = ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True)
        cls.service = ToolService(cls.reader_settings, auth, frozenset(), frozenset())
        from fastapi.testclient import TestClient

        cls.client = TestClient(create_app(cls.service))

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')

    def _call(self, name, arguments, *, user=None, job=None):
        headers = {"X-WR-User": user} if user else {"X-WR-Job": job}
        response = self.client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        self.assertEqual(response.status_code, 200)
        return response.json()["result"]

    def _accounts(self, **who):
        result = self._call("list_accounts", {}, **who)
        return [a["account_id"] for a in result["structuredContent"]["data"]["accounts"]]

    # Access ------------------------------------------------------------------

    def test_people_and_jobs_see_only_their_accounts(self):
        self.assertEqual(self._accounts(user=OWNER), ["UTEST1", "UTEST2"])
        self.assertEqual(self._accounts(user=VIEWER), ["UTEST1"])
        self.assertEqual(self._accounts(user=STRANGER), [])
        self.assertEqual(self._accounts(job="holdings_explainer"), ["UTEST1"])
        self.assertEqual(self._accounts(job="month_end_drafter"), [])

    def test_other_accounts_are_refused_and_audited(self):
        result = self._call("get_holdings", {"account_id": "UTEST2"}, user=VIEWER)
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "account_not_allowed")
        from wealthryse_agent.database import connect

        writer = connect(self.writer_settings, search_path="agent")
        row = writer.execute("SELECT status, error_code FROM agent.tool_calls WHERE subject_id = 'UTEST2' "
                             "ORDER BY tool_call_id DESC LIMIT 1").fetchone()
        writer.close()
        self.assertEqual((row["status"], row["error_code"]), ("denied", "account_not_allowed"))

    def test_roles_cannot_reach_platform_tables_or_each_others_objects(self):
        import psycopg

        from wealthryse_agent.database import connect

        reader = connect(self.reader_settings, search_path="agent")
        for table in ("nav_snapshots", "position_snapshots", "fx_rates", "flex_rows", "platform_subjects", "app_users"):
            row = reader.execute("SELECT has_table_privilege(current_user, ?, 'SELECT') AS p", ("public." + table,)).fetchone()
            self.assertFalse(row["p"], table)
        reader.rollback()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            reader.execute("INSERT INTO agent.conversations(conversation_id, tenant_id, user_object_id, subject_id) "
                           "VALUES (gen_random_uuid(), ?::uuid, 'x', 'UTEST1')", (TENANT,))
        reader.rollback()
        # Without a scope every account-scoped view is empty.
        self.assertEqual(reader.execute("SELECT COUNT(*) AS n FROM agent.subject_nav_series").fetchone()["n"], 0)
        reader.close()
        writer = connect(self.writer_settings, search_path="agent")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            writer.execute("SELECT 1 FROM agent.subject_nav_series")
        writer.close()

    # Figures -----------------------------------------------------------------

    def test_holdings_changes_split_adds_up(self):
        data = self._call("get_holdings_changes", {"account_id": "UTEST1", "from_date": "2026-01-30",
                                                    "to_date": "2026-03-31"}, user=VIEWER)["structuredContent"]["data"]
        self.assertAlmostEqual(data["holdings_value_start"], 2000 + 40000 * 0.128)
        self.assertAlmostEqual(data["holdings_value_end"], 3300 + 42000 * 0.129)
        self.assertAlmostEqual(data["from_trades"] + data["from_price"] + data["from_fx"], data["total_change"])
        self.assertAlmostEqual(data["from_trades"], 5 * 220)
        self.assertAlmostEqual(data["from_fx"], 40000 * 0.001)

    def test_performance_uses_the_deposit(self):
        data = self._call("get_performance", {"account_id": "UTEST1"}, user=VIEWER)["structuredContent"]["data"]
        mtd = next(p for p in data["periods"] if p["key"] == "mtd")
        self.assertAlmostEqual(mtd["twr"], 12705 / 12100 - 1)
        flows = self._call("get_cash_flows", {"account_id": "UTEST1", "from_date": "2026-01-01",
                                              "to_date": "2026-03-31"}, user=VIEWER)["structuredContent"]["data"]
        self.assertEqual(flows["net"], 1000.0)

    def test_holdings_and_sectors_in_reporting_currency(self):
        data = self._call("get_sector_allocation", {"account_id": "UTEST1"}, user=VIEWER)["structuredContent"]["data"]
        weights = {a["sector"]: a["weight"] for a in data["allocation"]}
        self.assertAlmostEqual(weights["Technology"], 3300 / (3300 + 5418))
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    # Store -------------------------------------------------------------------

    def test_store_conversations_and_inbox(self):
        from wealthryse_agent.database import connect
        from wealthryse_agent.store import Store

        store = Store(connect(self.writer_settings, search_path="agent"), TENANT)
        cid = store.create_conversation(VIEWER, "UTEST1", "How did I do?")
        store.add_message(cid, "user", "How did I do?")
        mid = store.add_message(cid, "assistant", "Up 5% [T1].", citations=[{"id": "T1"}], usage={"input_tokens": 7, "output_tokens": 3})
        self.assertIsNone(store.conversation(cid, OWNER))
        self.assertEqual(len(store.messages(cid)), 2)
        self.assertTrue(store.feedback(mid, VIEWER, 1, None))
        self.assertFalse(store.feedback(mid, OWNER, 1, None))
        self.assertGreaterEqual(store.tokens_used_today(), 10)
        self.assertTrue(store.add_inbox(kind="data_quality", account_id="UTEST1", period_key="2026-10-12",
                                        title="t", body="b", citations=[], run_id=None))
        self.assertFalse(store.add_inbox(kind="data_quality", account_id="UTEST1", period_key="2026-10-12",
                                         title="t", body="b", citations=[], run_id=None))
        item = store.inbox(["UTEST1"])[0]["item_id"]
        self.assertFalse(store.decide(item, ["UTEST2"], "approved", "x" * 64))
        self.assertTrue(store.decide(item, ["UTEST1"], "approved", "x" * 64))
        self.assertTrue(store.archive(cid, VIEWER))
        self.assertEqual(store.purge(90), 0)
        store.db.close()


if __name__ == "__main__":
    unittest.main()
