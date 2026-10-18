"""Database tests for the agents' figure tools on certified figures with the fallback, the month-end
job's read tools and the comparison for a restored copy (scripts/compare_agent_certified_figures.py).

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts
one). Skipped otherwise. Creates and drops its own database; synthetic data only
(tests/fixtures/agent_seed.sql), with certified figures written from the tools' own answers: some
equal, some different, the rest not published.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import unittest
import uuid
from unittest import mock

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from test_wealthryse_agent_db import (  # noqa: E402
    ADMIN_DSN, OWNER, ROOT, TENANT, TEST_PASSWORD, TOLERATED_MIGRATIONS, _agent_schema_sql, _dsn)

SETTING = "AGENT_CERTIFIED_FIGURE_TOOLS"
SERVED = {"AGENT_CERTIFIED_FIGURES": "on", "CERTIFIED_FIGURES_PAGES": "overview=on,holdings=on"}
OFF = {SETTING: "", "AGENT_CERTIFIED_FIGURES": "", "CERTIFIED_FIGURES_PAGES": "", "AGENT_CERTIFIED_MONTH_END": ""}
AS_OF = "2026-03-31"
CALLS = (("get_performance", {}), ("get_sector_allocation", {}), ("get_nav_history", {"frequency": "daily"}),
         ("compare_to_benchmark", {}))


def _strip(result):
    return {k: v for k, v in result.items() if k != "generated_at"}


def _url(database, user="agent_test_owner"):
    """A postgresql:// URL, as DATABASE_URL and the comparison's --url-env hold one."""
    from urllib.parse import quote

    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(ADMIN_DSN)
    return (f"postgresql://{quote(user)}:{quote(TEST_PASSWORD)}@{params.get('host', '127.0.0.1')}:"
            f"{params.get('port', 5432)}/{database}")


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class CertifiedFallbackDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = "certfallback_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in ("agent_test_reader", "agent_test_writer"):
                if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
                    admin.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{TEST_PASSWORD}'")
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'agent_test_owner'").fetchone() is None:
                admin.execute(f"CREATE ROLE agent_test_owner LOGIN CREATEROLE PASSWORD '{TEST_PASSWORD}'")
            admin.execute("GRANT agent_test_reader, agent_test_writer TO agent_test_owner WITH ADMIN OPTION")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER agent_test_owner')
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        cls._migrate()
        with cls._owner() as owner:
            for text in _agent_schema_sql(owner):
                owner.execute(text)
            owner.execute((ROOT / "tests/fixtures/agent_seed.sql").read_text(encoding="utf-8"))
            owner.execute("INSERT INTO agent.service_grants(tenant_id, job_name, subject_id, granted_by) "
                          "VALUES (%s, 'month_end_drafter', 'UTEST1', 'test')", (TENANT,))
            owner.commit()
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute("GRANT agent_reader TO agent_test_reader")
            admin.execute("GRANT agent_writer TO agent_test_writer")

        from fastapi.testclient import TestClient

        from wealthryse_agent.config import DatabaseSettings, ServiceAuthSettings
        from wealthryse_agent.tool_server import ToolService, create_app

        reader = DatabaseSettings("", cls.database, "", "", TENANT, _dsn(ADMIN_DSN, cls.database, "agent_test_reader"))
        auth = ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True)
        cls.client = TestClient(create_app(ToolService(reader, auth, frozenset(), frozenset())))
        # Today's answers, before any certified figure exists.
        with mock.patch.dict(os.environ, OFF):
            cls.before = {name: cls._call(name, {"account_id": "UTEST1", **extra}) for name, extra in CALLS}
        with cls._owner() as owner:
            cls._seed_certified(owner)
            owner.commit()

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')

    @classmethod
    def _owner(cls):
        import psycopg

        return psycopg.connect(_dsn(ADMIN_DSN, cls.database, "agent_test_owner"), options=f"-c app.tenant_id={TENANT}")

    @classmethod
    def _migrate(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection

        connection = PostgresConnection(psycopg.connect(_dsn(ADMIN_DSN, cls.database, "agent_test_owner")))
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

    @classmethod
    def _seed_certified(cls, owner):
        """A published build: some figures equal to today's, some different, the rest missing."""
        from ibkr_etl import certified_figures as register

        build_id = str(uuid.uuid4())
        owner.execute("INSERT INTO gold.certified_builds(build_id, run_kind, started_at, status, checks) "
                      "VALUES (%s, 'daily', '2026-04-01T09:00:00Z', 'shadow', '{}')", (build_id,))

        def figure(subject, figure_id, as_of, value, period="", item=""):
            entry = register.figure(figure_id)
            owner.execute(
                "INSERT INTO gold.certified_figures(build_id, subject_id, figure_id, period, item, as_of_date, value, "
                "unit, currency, status, definition_id, definition_version, calculation_version, input_sha256, "
                "source_table, source_key) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'ok', %s, %s, 'test-v1', %s, "
                "'silver.subject_nav_series', '{}')",
                (build_id, subject, figure_id, period, item, as_of, value, entry.unit,
                 "USD" if entry.unit == "money" else None, entry.definition_id, entry.definition_version, "c" * 64))

        performance = cls.before["get_performance"]["structuredContent"]["data"]
        ytd = next(p for p in performance["periods"] if p["key"] == "ytd")
        figure("UTEST1", "overview.nav", AS_OF, performance["latest_nav"])
        figure("UTEST1", "overview.twr", AS_OF, ytd["twr"], period="ytd")
        figure("UTEST1", "overview.irr_annualised", AS_OF, ytd["irr_annualized"] + 0.01, period="ytd")
        sectors = cls.before["get_sector_allocation"]["structuredContent"]["data"]["allocation"]
        cls.sectors = {row["sector"]: row["weight"] for row in sectors}
        figure("UTEST1", "holdings.sector_allocation", AS_OF, cls.sectors["Technology"], item="Technology")
        figure("UTEST1", "holdings.sector_allocation", AS_OF, cls.sectors["Communication Services"] + 0.05,
               item="Communication Services")
        points = cls.before["get_nav_history"]["structuredContent"]["data"]["points"]
        figure("UTEST1", "overview.twr", points[1]["date"], points[1]["cumulative_return"], period="since_inception")
        figure("UTEST2", "overview.nav", AS_OF, 1.0)  # another account: never read for UTEST1
        owner.execute("UPDATE gold.certified_builds SET status = 'published', "
                      "published_at = '2026-04-01T09:52:11Z' WHERE build_id = %s", (build_id,))
        cls.build_id = build_id

    @classmethod
    def _call(cls, name, arguments, *, user=OWNER, job=None):
        headers = {"X-WR-Job": job} if job else {"X-WR-User": user}
        response = cls.client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        assert response.status_code == 200, response.status_code
        return response.json()["result"]

    def _tool(self, name, extra=None, **env):
        with mock.patch.dict(os.environ, {**OFF, **env}):
            return self._call(name, {"account_id": "UTEST1", **(extra or {})})["structuredContent"]

    # ---------------------------------------------------------------------------------------

    def test_off_answers_exactly_as_before(self):
        for name, extra in CALLS:
            self.assertEqual(_strip(self._tool(name, extra)), _strip(self.before[name]["structuredContent"]), name)

    def test_fallback_uses_the_equal_figures_and_flags_the_different_ones(self):
        with self.assertLogs("wealthryse_agent.tools", "INFO") as logged:
            result = self._tool("get_performance", **{SETTING: "fallback"}, **SERVED)
        before = self.before["get_performance"]["structuredContent"]
        self.assertEqual(result["data"], before["data"])  # equal figures, and today's where they differ
        index = next(i for i, p in enumerate(result["data"]["periods"]) if p["key"] == "ytd")
        self.assertEqual(result["figures_source"], {f"/data/periods/{index}/irr_annualized": "fallback"})
        figures = result["certified"]["figures"]
        self.assertEqual({(f["figure_id"], f.get("period_key"), f["at"]) for f in figures},
                         {("overview.nav", None, "/data/latest_nav"),
                          ("overview.twr", "ytd", f"/data/periods/{index}/twr")})
        self.assertEqual({f["lineage"]["build_id"] for f in figures}, {self.build_id})
        [line] = [line for line in logged.output if "CERTIFIED_FIGURE_TOOLS" in line]
        payload = json.loads(line.split("CERTIFIED_FIGURE_TOOLS ", 1)[1])
        self.assertEqual((payload["status"], payload["differ"], payload["used"]),
                         ("differ", {"overview.irr_annualised": 1}, 2))
        self.assertNotIn("UTEST1", line)
        self.assertNotIn(str(before["data"]["latest_nav"]).split(".")[0], line)

    def test_sector_weights_and_nav_history(self):
        result = self._tool("get_sector_allocation", **{SETTING: "fallback"}, **SERVED)
        self.assertEqual({r["sector"]: r["weight"] for r in result["data"]["allocation"]}, self.sectors)
        self.assertEqual([f["item"] for f in result["certified"]["figures"]], ["Technology"])
        self.assertEqual(list(result["figures_source"].values()), ["fallback"])
        history = self._tool("get_nav_history", {"frequency": "daily"}, **{SETTING: "fallback"}, **SERVED)
        self.assertEqual(history["data"], self.before["get_nav_history"]["structuredContent"]["data"])
        # Every point is compared; only the last is listed (its NAV here: the return on 30 Jan matched too).
        self.assertEqual([(f["figure_id"], f["at"]) for f in history["certified"]["figures"]],
                         [("overview.nav", "/data/points/3/value")])
        self.assertNotIn("figures_source", history)

    def test_on_uses_the_certified_figure(self):
        result = self._tool("get_sector_allocation", **{SETTING: "on"}, **SERVED)
        weights = {r["sector"]: r["weight"] for r in result["data"]["allocation"]}
        self.assertAlmostEqual(weights["Communication Services"], self.sectors["Communication Services"] + 0.05)
        self.assertNotIn("figures_source", result)

    def test_a_failing_read_keeps_the_tool_working(self):
        from wealthryse_agent import certified_fallback

        broken = certified_fallback.READ_SQL.replace("FROM certified_figures", "FROM certified_figures_missing")
        with mock.patch.object(certified_fallback, "READ_SQL", broken), \
                self.assertLogs("wealthryse_agent.tools", "WARNING") as logged:
            result = self._tool("get_performance", **{SETTING: "fallback"}, **SERVED)
        self.assertEqual(result["data"], self.before["get_performance"]["structuredContent"]["data"])
        self.assertEqual(set(result["figures_source"].values()), {"fallback"})
        self.assertIn('"status":"error"', logged.output[0])

    def test_the_month_end_job_gets_the_read_tools_only_when_switched_on(self):
        def listed(**env):
            with mock.patch.dict(os.environ, {**OFF, **SERVED, **env}):
                response = self.client.post("/mcp", headers={"X-WR-Job": "month_end_drafter"},
                                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            return {t["name"] for t in response.json()["result"]["tools"]}

        self.assertFalse({"get_certified_figures", "trace_figure"} & listed())
        self.assertTrue({"get_certified_figures", "trace_figure"} <= listed(AGENT_CERTIFIED_MONTH_END="on"))
        with mock.patch.dict(os.environ, {**OFF, **SERVED, "AGENT_CERTIFIED_MONTH_END": "on"}):
            result = self._call("get_certified_figures", {"account_id": "UTEST1", "figures": ["overview.nav"]},
                                job="month_end_drafter")["structuredContent"]
        self.assertEqual([f["value"] for f in result["figures"]], [12705.0])
        with mock.patch.dict(os.environ, {**OFF, **SERVED}):
            refused = self._call("get_certified_figures", {"account_id": "UTEST1", "figures": ["overview.nav"]},
                                 job="month_end_drafter")["structuredContent"]
        self.assertEqual(refused["error"]["code"], "unknown_tool")

    def test_the_website_analysts_tools_read_gold(self):
        import psycopg
        from psycopg.rows import dict_row

        from ibkr_etl.agent_tools import AgentToolContext, execute_tool

        owner = _url(self.database)
        context = AgentToolContext(TENANT, "test", frozenset({"UTEST1"}), "c")
        with mock.patch.dict(os.environ, {**OFF, "DATABASE_URL": owner}), \
                psycopg.connect(owner, row_factory=dict_row) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
            arguments = {"subject_id": "UTEST1", "as_of": AS_OF}
            today = execute_tool(connection, context, "get_sector_allocation", arguments)
            fallback = execute_tool(connection, context, "get_sector_allocation", arguments, certified_mode="fallback")
        self.assertEqual(fallback["data"]["allocation"], today["data"]["allocation"])
        self.assertEqual(list(fallback["data"]["figures_source"].values()), ["fallback"])
        self.assertNotIn("figures_source", today["data"])

    def test_the_comparison_for_a_restored_copy_prints_counts_only(self):
        spec = importlib.util.spec_from_file_location("compare_agent_certified_figures",
                                                      ROOT / "scripts" / "compare_agent_certified_figures.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        owner = _url(self.database)
        output = io.StringIO()
        with mock.patch.dict(os.environ, {**OFF, "COPY_URL": owner}), contextlib.redirect_stdout(output):
            code = module.main(["--url-env", "COPY_URL", "--as-of", AS_OF])
        text = output.getvalue()
        self.assertEqual(code, 1)  # two figures differ
        rows = {tuple(line.split()[:3]): [int(x) for x in line.split()[3:]] for line in text.splitlines()
                if line.startswith(("agent ", "website "))}
        self.assertEqual(rows[("agent", "get_performance", "overview.irr_annualised")][:2], [0, 1])
        self.assertEqual(rows[("agent", "get_performance", "overview.nav")][:2], [1, 1])  # UTEST1 equal, UTEST2 not
        self.assertEqual(rows[("agent", "get_sector_allocation", "holdings.sector_allocation")][:2], [1, 1])
        self.assertEqual(rows[("website", "get_sector_allocation", "holdings.sector_allocation")][:2], [1, 1])
        self.assertIn("Values the fallback changed beyond the certified tolerance: 0", text)
        self.assertIn("2 subjects", text)
        for secret in ("UTEST", "12705", str(self.sectors["Technology"])[:6]):
            self.assertNotIn(secret, text)
        with mock.patch.dict(os.environ, {**OFF, "COPY_URL": owner}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(["--url-env", "COPY_URL", "--subject", "U9300001"]), 0)  # none such
        with mock.patch.dict(os.environ, {**OFF}), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(["--url-env", "COPY_URL"]), 2)


if __name__ == "__main__":
    unittest.main()
