"""The supervisor's task record (migrations/agent/018) against a real PostgreSQL.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts
one). Skipped otherwise. Creates and drops its own database and uses synthetic data only.
"""
from __future__ import annotations

import importlib.util
import os
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER_ROLE = "supervisor_test_owner"
READER, WRITER = "supervisor_test_reader", "supervisor_test_writer"
TEST_PASSWORD = "supervisor-test-only"  # throwaway roles in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
USER = "22222222-2222-2222-2222-222222222222"


def _dsn(base: str, database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(base)
    params["dbname"] = database
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class SupervisorTasksDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection

        cls.database = "supervisor_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (READER, WRITER):
                if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
                    admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{TEST_PASSWORD}'")
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER_ROLE,)).fetchone() is None:
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
            texts = _script("install_agent_schema").agent_schema_sql(owner)
            for text in texts:
                owner.execute(text)
            # Idempotent: the release reruns the whole folder.
            for text in texts:
                owner.execute(text)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:  # roles are cluster-wide
            admin.execute(f"GRANT agent_reader TO {READER}")
            admin.execute(f"GRANT agent_writer TO {WRITER}")

        from wealthryse_agent.config import DatabaseSettings

        cls.reader = DatabaseSettings("", cls.database, "", "", TENANT, _dsn(ADMIN_DSN, cls.database, READER))
        cls.writer = DatabaseSettings("", cls.database, "", "", TENANT, _dsn(ADMIN_DSN, cls.database, WRITER))

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')

    def _store(self):
        from wealthryse_agent.database import connect
        from wealthryse_agent.store import Store

        store = Store(connect(self.writer, search_path="agent"), TENANT)
        self.addCleanup(store.db.close)
        return store

    def _tree(self, conversation_id, question="How did I do this year, and what drove the risk?"):
        from wealthryse_agent.runtime import Turn
        from wealthryse_agent.supervisor import watch
        from wealthryse_agent.supervisor.routing import route_chat

        recorder = watch.Recorder(type("Inner", (), {"call": lambda self, n, a: {"data": {}},
                                                      "close": lambda self: None})())
        recorder.call("get_performance", {"account_id": "U1"})
        turn = Turn(answer="ok", tool_calls=1, usage={"input_tokens": 10, "output_tokens": 2},
                    citations=[{"id": "F1", "certified": True, "as_of": "2026-10-27",
                                "figure": {"build_id": str(uuid.uuid4())}}])
        return watch.chat_tree(route=route_chat(question), account_id="U1", correlation_id=str(uuid.uuid4()),
                               conversation_id=conversation_id, outcome="answer", turn=turn, recorder=recorder,
                               pinned={"as_of": "2026-10-28", "source": "freshness", "lag_days": 1}, elapsed_ms=900)

    def test_1_the_writer_records_a_tree_and_reads_its_roots(self):
        store = self._store()
        self.assertTrue(store.tasks_ready())
        conversation = store.create_conversation(USER, "U1", "Synthetic")
        rows = self._tree(conversation)
        self.assertTrue(store.add_tasks(rows))
        tree = store.db.execute("SELECT level, role, status, parent_id::text AS parent FROM agent.tasks "
                                "WHERE root_id = ?::uuid ORDER BY level, role", (rows[0]["task_id"],)).fetchall()
        store.db.rollback()
        self.assertEqual(len(tree), 5)
        self.assertEqual(sorted(r["role"] for r in tree),
                         ["answer_checker", "holdings_performance", "planner", "risk", "supervisor"])
        roots = store.task_roots("2000-01-01", "2999-01-01")
        self.assertEqual([r["task_id"] for r in roots], [rows[0]["task_id"]])
        self.assertEqual(roots[0]["detail"]["route"]["lead"], "planner")
        self.assertEqual(str(roots[0]["pinned_as_of"]), "2026-10-28")

    def test_2_a_refused_row_rolls_the_tree_back_and_keeps_the_connection(self):
        store = self._store()
        conversation = store.create_conversation(USER, "U1", "Synthetic")
        rows = self._tree(conversation)
        rows[-1] = {**rows[-1], "role": "Not A Role"}
        self.assertFalse(store.add_tasks(rows))
        self.assertEqual(store.db.execute("SELECT COUNT(*) AS n FROM agent.tasks WHERE root_id = ?::uuid",
                                          (rows[0]["task_id"],)).fetchone()["n"], 0)
        store.db.rollback()
        self.assertTrue(store.add_tasks(self._tree(conversation)))  # the same connection still works

    def test_3_the_writer_cannot_change_or_delete_tasks_and_the_reader_cannot_see_them(self):
        import psycopg

        from wealthryse_agent.database import connect

        store = self._store()
        store.add_tasks(self._tree(store.create_conversation(USER, "U1", "Synthetic")))
        for statement in ("UPDATE agent.tasks SET status = 'done'", "DELETE FROM agent.tasks"):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                store.db.execute(statement)
            store.db.rollback()
        reader = connect(self.reader, search_path="agent")
        self.addCleanup(reader.close)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            reader.execute("SELECT 1 FROM agent.tasks")
        reader.rollback()

    def test_4_purging_a_conversation_removes_its_tasks(self):
        import psycopg

        store = self._store()
        conversation = store.create_conversation(USER, "U1", "Old")
        rows = self._tree(conversation)
        self.assertTrue(store.add_tasks(rows))
        with psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE), autocommit=True) as owner:
            owner.execute("UPDATE agent.conversations SET updated_at = now() - interval '400 days' "
                          "WHERE conversation_id = %s", (conversation,))
        self.assertGreaterEqual(store.purge(90), 1)
        self.assertEqual(store.db.execute("SELECT COUNT(*) AS n FROM agent.tasks WHERE root_id = ?::uuid",
                                          (rows[0]["task_id"],)).fetchone()["n"], 0)
        store.db.rollback()

    def test_5_every_live_tree_the_team_writes_fits_the_table(self):
        from test_supervisor_live import PlannerTests, _final, _Model, _team

        store = self._store()
        conversation = store.create_conversation(USER, "U1", "Synthetic")
        cases = {
            "fast_path": ("What is my volatility this year?", _Model()),
            "planner": (PlannerTests.QUESTION, _Model(plan=PlannerTests.PLAN, fail=("risk",),
                                                      writer=("Up 5.1% [T1]; the risk figures weren't available.",))),
            "analyst": (PlannerTests.QUESTION, _Model(plan=[])),
            "router": ("Write me a poem.", _Model(in_scope=False)),
        }
        for lead, (question, model) in cases.items():
            team = _team(model)
            final = _final(list(team.answer(question)))
            rows = team.tree(outcome=final["type"], turn=final["turn"], conversation_id=conversation, elapsed_ms=1,
                             pinned={"as_of": "2026-10-28", "source": "freshness", "lag_days": 0})
            self.assertTrue(store.add_tasks(rows), lead)
            saved = store.db.execute("SELECT mode, role, status FROM agent.tasks WHERE root_id = ?::uuid "
                                     "ORDER BY level, role", (rows[0]["task_id"],)).fetchall()
            store.db.rollback()
            self.assertEqual({r["mode"] for r in saved}, {"live"}, lead)
            self.assertIn(lead, {r["role"] for r in saved})
        failed = store.db.execute("SELECT detail FROM agent.tasks WHERE mode = 'live' AND role = 'risk' "
                                  "AND status = 'failed'").fetchall()
        store.db.rollback()
        self.assertEqual(len(failed), 1)

    def test_6_the_dispatchers_records_fit_the_table_and_are_found_again(self):
        from datetime import datetime, timezone

        from wealthryse_agent.supervisor import dispatcher

        store = self._store()
        now = datetime(2026, 10, 29, 23, 10, tzinfo=timezone.utc)
        for job, status, mode in (("holdings_explainer", "shadow", "watch"), ("risk_watch", "done", "live"),
                                  ("weekly_brief", "blocked", "live"), ("risk_watch", "failed", "live")):
            event = dispatcher.Event(dispatcher.BY_JOB[job], "U1", "2026-10-30", "2026-W44" if job == "weekly_brief"
                                     else f"2026-10-{30 if status != 'failed' else 29}")
            rows = dispatcher.tree(event, mode_=mode, detected_at=now, status=status,
                                   written=1 if status == "done" else None,
                                   usage={"input_tokens": 5, "output_tokens": 1} if status == "done" else None,
                                   error_code="tool_error" if status == "failed" else None)
            self.assertTrue(store.add_tasks(rows), (job, status))
        self.assertEqual(store.event_keys(10), {"event:holdings_explainer:U1:2026-10-30",
                                                "event:risk_watch:U1:2026-10-30", "event:weekly_brief:U1:2026-W44",
                                                "event:risk_watch:U1:2026-10-29"})
        roots = [r for r in store.task_roots("2000-01-01", "2999-01-01") if r["source"] == "event"]
        self.assertEqual(len(roots), 4)
        self.assertEqual({r["detail"]["minutes_before_fixed_run"] for r in roots if r["detail"]["job"] != "weekly_brief"},
                         {80, 125})


if __name__ == "__main__":
    unittest.main()
