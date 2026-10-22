"""The operations agent's and go-live coordinator's database side.

The operations agent's views, its table, and how the platform shows its
diagnoses; the first-month watch's view of live cases; the readiness pack;
memory and weekly briefs; and the connection agent's first checks.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN (CI starts one). Skipped otherwise. Creates and drops its own
database and uses synthetic data only.
"""
from __future__ import annotations

import importlib.util
import os
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER_ROLE = "ops_agent_test_owner"
READER, WRITER, PLATFORM = "ops_agent_test_reader", "ops_agent_test_writer", "ops_agent_test_app"
TEST_PASSWORD = "ops-agent-test-only"  # throwaway roles in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}


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
class OperationsAgentDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection

        cls.database = "ops_agent_test_" + uuid.uuid4().hex[:8]
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
            for text in _script("install_agent_schema").agent_schema_sql(owner):
                owner.execute(text)
            # The website and administrator's own access, as the platform role has it.
            owner.execute(f"GRANT SELECT, UPDATE ON system_incidents TO {PLATFORM}")
            owner.execute(f"GRANT SELECT, INSERT, UPDATE ON admin_chat_conversations, admin_chat_messages TO {PLATFORM}")
            today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            owner.execute(
                "INSERT INTO system_incidents(incident_key, check_id, severity, status, description, evidence_json, "
                "first_seen_at, last_seen_at) VALUES "
                "('synthetic_open', 'risk_free_freshness', 'warning', 'open', 'Synthetic open incident', "
                "'{\"days\": 4}', '2026-10-14T01:00:00Z', %s), "
                "('synthetic_done', 'portal', 'critical', 'resolved', 'Synthetic resolved incident', '{}', "
                "'2026-10-01T01:00:00Z', '2026-10-01T02:00:00Z')", (today,))
            owner.execute(
                "INSERT INTO ingestion_runs(run_type, started_at, status, error_message) VALUES "
                "('flex', %s, 'failed', 'synthetic failure'), ('flex', '2020-01-01T00:00:00Z', 'success', NULL)",
                (today,))
            owner.execute("INSERT INTO autonomous_agent_runs(correlation_id, started_at, status) "
                          "VALUES ('synthetic', %s, 'degraded')", (today,))
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

    def _call(self, name, job="operations"):
        return self.service.call(caller_id="", user=None, job=job, correlation_id="test", name=name, arguments={})

    def _platform(self):
        """The platform role's raw connection, as the administrator and the website open it."""
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(_dsn(ADMIN_DSN, self.database, PLATFORM), row_factory=dict_row)
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
        self.addCleanup(connection.close)
        return connection

    def _grant_platform_reads(self):
        install = _script("install_agent_schema")
        self.assertEqual(install.main(["--database-url", _dsn(ADMIN_DSN, self.database, OWNER_ROLE),
                                       "--existing-logins", "--platform-reader", PLATFORM]), 0)

    def _investigation(self, store, status="diagnosed", pull_request=None):
        from wealthryse_agent.operations import Investigation

        incident = self._call("list_open_incidents")["data"]["incidents"][0]
        diagnosis = {"summary": "Synthetic summary.", "likely_cause": "Synthetic cause.", "confidence": "high",
                     "evidence": [{"source_id": "platform:imports", "finding": "one failed run"}],
                     "owner_steps": ["Look at it."], "code_fix": {"needed": False, "reason": "", "files": []}}
        store.save(incident, Investigation(status, diagnosis, [{"source_id": "platform:imports"}], pull_request,
                                           {"input_tokens": 40, "output_tokens": 2}), "test")
        return incident

    def test_1_the_agent_sees_open_incidents_and_recent_runs_only_through_its_own_job(self):
        incidents = self._call("list_open_incidents")["data"]["incidents"]
        self.assertEqual([i["incident_key"] for i in incidents], ["synthetic_open"])
        self.assertEqual(incidents[0]["evidence"], {"days": 4})
        history = self._call("get_operations_history")["data"]
        self.assertEqual([r["error_message"] for r in history["ingestion_runs"]], ["synthetic failure"])
        self.assertEqual(len(history["admin_runs"]), 1)
        from wealthryse_agent.tools import ToolError

        with self.assertRaises(ToolError):  # no other job is offered the tool
            self._call("list_open_incidents", job="onboarding")

    def test_2_the_agent_writes_only_its_own_table_once_per_episode(self):
        import psycopg

        from wealthryse_agent.database import connect
        from wealthryse_agent.operations import OpsStore

        store = OpsStore(connect(self.writer, search_path="agent"), TENANT)
        self.addCleanup(store.db.close)
        incident = self._investigation(store)
        self.assertTrue(store.done(incident))
        self.assertFalse(store.done(dict(incident, first_seen_at="2026-11-01T00:00:00Z")))
        self._investigation(store)  # the same episode again: ignored
        self.assertEqual(store.tokens_used_this_month(), 42)
        self.assertEqual(store.pull_requests_today(), 0)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            store.db.execute("UPDATE public.system_incidents SET status = 'resolved'")
        store.db.rollback()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            store.db.execute("DELETE FROM agent.ops_investigations")
        store.db.rollback()

    def test_3_the_platform_shows_diagnoses_once_granted_and_posts_each_once(self):
        import psycopg

        from ibkr_etl.ops_agent_posts import open_incidents

        admin = _script("run_autonomous_administrator")
        platform = self._platform()

        def publish():
            admin._publish_admin_chat_updates(platform, findings=[], new_keys=set(), incident_ids={}, diagnosis=None,
                                              correlation_id=str(uuid.uuid4()))
            platform.commit()
            return platform.execute(
                "SELECT message_kind, response_json, incident_id FROM admin_chat_messages "
                "WHERE response_json ? 'investigation_id' ORDER BY sequence_no").fetchall()

        # Before the grant: nothing from the agent, and the administrator's own work carries on.
        self.assertEqual(publish(), [])
        before = open_incidents(platform)
        self.assertEqual([(i["incident_key"], i["diagnosis"]) for i in before], [("synthetic_open", None)])
        platform.rollback()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            platform.execute("SELECT 1 FROM agent.ops_investigations")
        platform.rollback()

        self._grant_platform_reads()
        posted = publish()
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["message_kind"], "proactive_update")
        self.assertEqual(posted[0]["response_json"]["title"], "Incident diagnosis")
        self.assertIsNotNone(posted[0]["incident_id"])
        self.assertEqual(len(publish()), 1)  # not posted twice
        shown = open_incidents(platform)
        self.assertEqual(shown[0]["diagnosis"]["summary"], "Synthetic summary.")
        self.assertEqual(shown[0]["diagnosis"]["owner_steps"], ["Look at it."])
        platform.rollback()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            platform.execute("DELETE FROM agent.ops_investigations")
        platform.rollback()

    def _owner(self):
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE), row_factory=dict_row,
                                     options=f"-c app.tenant_id={TENANT}")
        self.addCleanup(connection.close)
        return connection

    def _live_composite(self, owner, case_id, subject_id):
        owner.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, base_currency, "
                      "lifecycle_status) VALUES (%s, 'composite_portfolio', 'Synthetic live', 'HKD', 'active')",
                      (subject_id,))
        owner.execute(
            "INSERT INTO broker_onboarding_cases (case_id, source_kind, definition_json, definition_sha256, "
            "target_subject_id, display_name, description, reporting_currency, history_start_date, subject_id, "
            "stage, status, went_live_at) VALUES (%s, 'composite_portfolio', '{}'::jsonb, %s, %s, 'Synthetic live', "
            "'A synthetic composite for tests.', 'HKD', '2025-01-02', %s, 'first_month_end', 'live', now())",
            (case_id, "0" * 64, subject_id, subject_id))
        owner.execute("INSERT INTO broker_onboarding_checks (case_id, check_id, status, message, evidence_json) "
                      "VALUES (%s, 'history_built', 'pass', 'Built.', '{\"recorded_by\": \"agent\"}'::jsonb)",
                      (case_id,))
        owner.commit()

    def test_4_the_watch_sees_live_first_month_cases_as_dates_and_statuses(self):
        owner = self._owner()
        case_id = str(uuid.uuid4())
        self._live_composite(owner, case_id, "synthetic-live")
        cases = self._call("list_live_onboarding_cases", job="onboarding")["data"]["cases"]
        self.assertEqual([c["case_id"] for c in cases], [case_id])
        case = cases[0]
        self.assertEqual((case["subject_id"], case["month_team_has_access"], case["latest_data_date"]),
                         ("synthetic-live", False, None))
        self.assertIsNotNone(case["went_live_at"])
        from wealthryse_agent.tools import ToolError

        with self.assertRaises(ToolError):  # only the onboarding job is offered it
            self._call("list_live_onboarding_cases")

    def test_5_the_readiness_pack_lists_every_check_and_what_go_live_switches_on(self):
        from ibkr_etl.onboarding_readiness import readiness_pack

        owner = self._owner()
        case_id = str(uuid.uuid4())
        self._live_composite(owner, case_id, "synthetic-pack")
        pack = readiness_pack(owner, TENANT, case_id)
        owner.rollback()
        built = next(c for s in pack["stages"] for c in s["checks"] if c["check_id"] == "history_built")
        self.assertEqual((built["status"], built["recorded_by"]), ("pass", "agent"))
        self.assertFalse(pack["ready"])  # already past go-live
        self.assertTrue(any("derived calculation" in effect for effect in pack["go_live_switches_on"]))
        self.assertEqual({r["state"] for r in pack["terms_vs_documents"]}, {"no_draft"})
        self.assertRegex(pack["pack_sha256"], "^[0-9a-f]{64}$")

    def test_6_memory_belongs_to_each_person_and_briefs_reach_the_inbox(self):
        import psycopg

        from wealthryse_agent.database import connect
        from wealthryse_agent.store import Store

        store = Store(connect(self.writer, search_path="agent"), TENANT)
        self.addCleanup(store.db.close)
        me, other = "33333333-3333-3333-3333-333333333333", "44444444-4444-4444-4444-444444444444"
        store.remember(me, "tone", "plain", "settings")
        store.remember(me, "tone", "technical", "chat")
        store.remember(other, "focus", "drawdowns", "settings")
        self.assertEqual([(m["key"], m["value"], m["source"]) for m in store.memories(me)],
                         [("tone", "technical", "chat")])
        self.assertEqual(store.forget(me, "focus"), 0)
        self.assertEqual(store.forget(me), 1)
        self.assertEqual(store.memories(me), [])
        self.assertEqual(len(store.memories(other)), 1)
        with self.assertRaises(psycopg.errors.CheckViolation):
            store.remember(me, "password", "x", "chat")
        store.db.rollback()
        self.assertTrue(store.add_inbox(kind="weekly_brief", account_id="U0000901", period_key="2026-W42",
                                        title="Your week", body="Synthetic.", citations=[], run_id=None))

    def test_7_the_connection_agent_queues_only_a_first_check_and_reports_each_result_once(self):
        import psycopg

        from wealthryse_agent.connection_watch import watch_connections
        from wealthryse_agent.database import connect
        from wealthryse_agent.store import Store

        owner = self._owner()
        login, source = str(uuid.uuid4()), str(uuid.uuid4())
        secret = "ibkr-flex-source-" + uuid.uuid4().hex
        owner.execute("INSERT INTO ibkr_flex_credentials (credential_id, display_name, key_vault_secret_name, "
                      "token_fingerprint, status) VALUES (%s, 'Synthetic login', %s, %s, 'pending_validation')",
                      (login, secret, "a" * 16))
        owner.execute("INSERT INTO ibkr_flex_queries (flex_source_id, credential_id, query_id, display_name, status) "
                      "VALUES (%s, %s, '123456', 'Synthetic login daily', 'pending_validation')", (source, login))
        owner.execute("INSERT INTO broker_connections (connection_id, broker, environment, display_name, "
                      "key_vault_secret_name, credential_fingerprint, credential_expires_on, status) "
                      "VALUES (%s, 'ibkr_flex', 'live', 'Synthetic login', %s, %s, '2027-01-01', 'pending_check')",
                      (login, secret, "a" * 16))
        owner.commit()

        store = Store(connect(self.writer, search_path="agent"), TENANT)
        self.addCleanup(store.db.close)

        def call(name, arguments):
            return self.service.call(caller_id="", user=None, job="onboarding", correlation_id="test", name=name,
                                     arguments=arguments)

        self.assertEqual(call("list_broker_logins", {})["data"]["logins"][0]["checks_requested"], 0)
        self.assertEqual(watch_connections(store, call, None), 1)  # queued, and said so
        requests = owner.execute("SELECT validation_request_id, status, requested_by_identity_sha256 "
                                 "FROM ibkr_flex_source_validation_requests WHERE flex_source_id = %s",
                                 (source,)).fetchall()
        owner.commit()
        self.assertEqual([(r["status"], r["requested_by_identity_sha256"]) for r in requests], [("pending", None)])
        self.assertEqual(watch_connections(store, call, None), 0)  # never a second request or report

        # The discovery job fails the check: the agent says why, once, and does not retry.
        owner.execute("UPDATE ibkr_flex_source_validation_requests SET status = 'blocked', error_phase = "
                      "'send_request', error_code = '1012', completed_at = now() WHERE flex_source_id = %s", (source,))
        owner.commit()
        self.assertEqual(watch_connections(store, call, None), 1)
        self.assertEqual(watch_connections(store, call, None), 0)
        self.assertIsNone(store.queue_first_connection_check(source))
        reports = owner.execute("SELECT state, body, owner_steps FROM agent.connection_reports ORDER BY report_id"
                                ).fetchall()
        owner.commit()
        self.assertEqual([r["state"] for r in reports], ["queued", "blocked"])
        self.assertIn("the token has expired (IBKR error 1012)", reports[1]["body"])
        self.assertNotIn(secret, str(reports))
        owner.execute("UPDATE ibkr_flex_source_validation_requests SET result_json = "
                      "'{\"recipe\": {\"status\": \"warn\", \"problems\": []}}'::jsonb WHERE flex_source_id = %s",
                      (source,))
        owner.commit()
        self.assertEqual(call("list_broker_logins", {})["data"]["logins"][0]["latest_recipe"],
                         {"status": "warn", "problems": []})

        # The writer can queue checks only through the function, and cannot read the platform tables.
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            store.db.execute("SELECT 1 FROM public.ibkr_flex_source_validation_requests")
        store.db.rollback()

        self._grant_platform_reads()
        admin = _script("run_autonomous_administrator")
        platform = self._platform()
        for _ in range(2):
            admin._publish_admin_chat_updates(platform, findings=[], new_keys=set(), incident_ids={}, diagnosis=None,
                                              correlation_id=str(uuid.uuid4()))
            platform.commit()
        posted = platform.execute("SELECT response_json FROM admin_chat_messages "
                                  "WHERE response_json ? 'connection_report_id' ORDER BY sequence_no").fetchall()
        self.assertEqual([p["response_json"]["connection_state"] for p in posted], ["queued", "blocked"])
        self.assertIn("Generate a new Flex Web Service token", posted[1]["response_json"]["message"])


if __name__ == "__main__":
    unittest.main()
