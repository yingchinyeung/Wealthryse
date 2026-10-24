"""Database tests for the agents' certified figures views (migrations/agent/017_certified_figures.sql
and 021_certified_latest.sql).

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts
one). Skipped otherwise. Creates and drops its own database; synthetic data only.

It installs the agent schema as scripts/install_agent_schema.py does, first on a database
without migration 073 (017 then does nothing) and again after 073, and shows that every
existing grant is unchanged: 017 and 021 only add their own functions and views, for agent_reader.
"""
from __future__ import annotations

import json
import os
import unittest
import uuid
from unittest import mock

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from test_wealthryse_agent_db import (  # noqa: E402
    ADMIN_DSN, OWNER, ROOT, TENANT, TEST_PASSWORD, TOLERATED_MIGRATIONS, VIEWER, _agent_schema_sql, _dsn)

OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
CERTIFIED_MIGRATION = "073_certified_figures.sql"
NEW_OBJECTS = {"certified_figures", "certified_builds", "scoped_certified_figures()", "scoped_certified_builds()",
               "certified_figure_trace(text,text,text,text,text,date)",
               "certified_latest", "scoped_certified_latest()"}
FIGURE_RUN = "aaaaaaaa-1111-4000-8000-000000000001"
SOURCE_RUN = "aaaaaaaa-1111-4000-8000-000000000002"
RAW_PATH = "raw/flex/UTEST1/2026-03-31/statement.xml"  # a storage path, which must never reach an agent
ON = {"AGENT_CERTIFIED_FIGURES": "on", "CERTIFIED_FIGURES_PAGES": "overview=on,risk-snapshot=on"}

# Every grant on every relation and function, except the platform's own new 073 objects.
GRANTS_SQL = """
SELECT n.nspname || '.' || c.relname AS name, COALESCE(c.relacl::text, '') AS acl
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname IN ('public', 'bronze', 'silver', 'gold', 'agent') AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
UNION ALL
SELECT n.nspname || '.' || p.proname || '(' || oidvectortypes(p.proargtypes) || ')', COALESCE(p.proacl::text, '')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname IN ('public', 'agent')
"""


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class CertifiedAgentViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = "certagent_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in ("agent_test_reader", "agent_test_writer"):
                if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
                    admin.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{TEST_PASSWORD}'")
            # Like Azure: the schema owner is not a superuser, so forced row-level security applies.
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'agent_test_owner'").fetchone() is None:
                admin.execute(f"CREATE ROLE agent_test_owner LOGIN CREATEROLE PASSWORD '{TEST_PASSWORD}'")
            admin.execute("GRANT agent_test_reader, agent_test_writer TO agent_test_owner WITH ADMIN OPTION")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER agent_test_owner')
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        paths = sorted((ROOT / "migrations" / "postgres").glob("*.sql"))
        cls._migrate([path for path in paths if path.name != CERTIFIED_MIGRATION])
        # The agent schema on a database without 073: 017 must do nothing.
        with cls._owner() as owner:
            for text in _agent_schema_sql(owner):
                owner.execute(text)
            owner.execute((ROOT / "tests/fixtures/agent_seed.sql").read_text(encoding="utf-8"))
            owner.commit()
            cls.before = dict(owner.execute(GRANTS_SQL).fetchall())
        # Then 073 (as production applies it, after the other migrations) and the installer, twice.
        cls._migrate([path for path in paths if path.name == CERTIFIED_MIGRATION])
        with cls._owner() as owner:
            for _ in range(2):
                for text in _agent_schema_sql(owner):
                    owner.execute(text)
            owner.commit()
            cls.after = dict(owner.execute(GRANTS_SQL).fetchall())
            cls._seed(owner)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute("GRANT agent_reader TO agent_test_reader")
            admin.execute("GRANT agent_writer TO agent_test_writer")

        from fastapi.testclient import TestClient

        from wealthryse_agent.config import DatabaseSettings, ServiceAuthSettings
        from wealthryse_agent.tool_server import ToolService, create_app

        cls.reader_settings = DatabaseSettings("", cls.database, "", "", TENANT,
                                               _dsn(ADMIN_DSN, cls.database, "agent_test_reader"))
        cls.writer_settings = DatabaseSettings("", cls.database, "", "", TENANT,
                                               _dsn(ADMIN_DSN, cls.database, "agent_test_writer"))
        auth = ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True)
        cls.service = ToolService(cls.reader_settings, auth, frozenset(), frozenset())
        cls.client = TestClient(create_app(cls.service))

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
    def _migrate(cls, paths):
        from ibkr_etl.db import PostgresConnection

        import psycopg

        connection = PostgresConnection(psycopg.connect(_dsn(ADMIN_DSN, cls.database, "agent_test_owner")))
        connection.execute("SELECT set_config('app.tenant_id', ?, false)", (TENANT,))
        connection.commit()
        for path in paths:
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
    def _seed(cls, owner):
        """Two published accounts, a held build that must not show, another tenant, and lineage."""
        from ibkr_etl import certified_figures as register

        def build(started_at="2026-04-01T09:00:00Z"):
            build_id = str(uuid.uuid4())
            owner.execute("INSERT INTO gold.certified_builds(build_id, run_kind, started_at, status, checks) "
                          "VALUES (%s, 'daily', %s, 'shadow', '{\"failed\": [\"overview.nav\"]}')",
                          (build_id, started_at))
            return build_id

        def figure(build_id, subject, figure_id, value, unit="number", period="", currency=None):
            item = register.figure(figure_id)
            owner.execute(
                "INSERT INTO gold.certified_figures(build_id, subject_id, figure_id, period, as_of_date, value, "
                "value_text, unit, currency, status, period_start, period_end, observations, definition_id, "
                "definition_version, calculation_version, input_sha256, source_table, source_key) VALUES "
                "(%s, %s, %s, %s, '2026-03-31', %s, NULL, %s, %s, 'ok', '2026-01-02', '2026-03-31', 4, %s, %s, "
                "'test-v1', %s, 'gold.risk_analytics_snapshots', '{\"as_of_date\": \"2026-03-31\"}')",
                (build_id, subject, figure_id, period, value, unit, currency, item.definition_id,
                 item.definition_version, "c" * 64))

        def publish(build_id, status="published", reason=None):
            owner.execute("UPDATE gold.certified_builds SET status = %s, reason = %s, "
                          "published_at = CASE WHEN %s = 'published' THEN '2026-04-01T09:52:11Z'::timestamptz END "
                          "WHERE build_id = %s", (status, reason, status, build_id))

        # The job runs that made the figure: the build read the risk table, which a run wrote from a raw file.
        events = [(SOURCE_RUN, "START", "2026-04-01T08:00:00Z", "risk-analytics", {"ingestion_run_ids": [1]}),
                  (SOURCE_RUN, "COMPLETE", "2026-04-01T08:10:00Z", "risk-analytics", {}),
                  (FIGURE_RUN, "START", "2026-04-01T09:00:00Z", "certified-figures", {}),
                  (FIGURE_RUN, "COMPLETE", "2026-04-01T09:50:00Z", "certified-figures", {})]
        for run_id, kind, at, job, facets in events:
            owner.execute("INSERT INTO lineage_events(run_id, event_type, event_time, job_namespace, job_name, "
                          "code_version, producer, facets) VALUES (%s, %s, %s, 'wealthryse', %s, 'abc123', 'test', %s)",
                          (run_id, kind, at, job, json.dumps(facets)))
        io = [(SOURCE_RUN, "input", "blob", RAW_PATH, None, "ab" * 32),
              (SOURCE_RUN, "output", "postgres", "gold.risk_analytics_snapshots", 1, None),
              (FIGURE_RUN, "input", "postgres", "gold.risk_analytics_snapshots", 1, None),
              (FIGURE_RUN, "output", "postgres", "gold.certified_figures", 3, None)]
        for run_id, direction, namespace, name, rows, sha in io:
            owner.execute("INSERT INTO lineage_io(run_id, direction, namespace, name, row_count, sha256) "
                          "VALUES (%s, %s, %s, %s, %s, %s)", (run_id, direction, namespace, name, rows, sha))

        owner.execute("SELECT set_config('lineage.run_id', %s, true)", (FIGURE_RUN,))
        published = build()
        figure(published, "UTEST1", "overview.nav", 12705.0, "money", currency="USD")
        figure(published, "UTEST1", "risk.sharpe_ratio", 1.12)
        figure(published, "UTEST1", "overview.twr", 0.1626, "ratio", period="ytd")
        figure(published, "UTEST1", "overview.twr", 0.0123, "ratio", period="mtd")
        figure(published, "UTEST2", "overview.nav", 55000.0, "money", currency="USD")
        # Batch 4 and 5 kinds for the second account: a month's Attribution on its last date
        # (per_period_item), a series one point a date, and RSI per holding, each with its detail.
        attribution_and_charts = [
            ("attribution.portfolio_return", "month_2026-02", "", "2026-02-27", 0.021, {}),
            ("attribution.holding_contribution", "month_2026-02", "756733", "2026-02-27", 0.015,
             {"symbol": "SYN", "sector": "Technology"}),
            ("attribution.sector_contribution", "month_2026-02", "Technology", "2026-02-27", 0.015, {}),
            ("attribution.portfolio_return", "month_2026-03", "", "2026-03-31", -0.004, {}),
            ("attribution.holding_contribution", "month_2026-03", "756733", "2026-03-31", -0.003,
             {"symbol": "SYN", "sector": "Technology"}),
            ("series.rolling_volatility_63d", "", "", "2026-02-27", 0.15, {}),
            ("series.rolling_volatility_63d", "", "", "2026-03-31", 0.16, {}),
            ("indicator.rsi_14", "", "756733", "2026-03-31", 61.5, {"signal": "neutral", "observations": 20})]
        for figure_id, period, item, day, value, detail in attribution_and_charts:
            entry = register.figure(figure_id)
            owner.execute(
                "INSERT INTO gold.certified_figures(build_id, subject_id, figure_id, period, item, as_of_date, "
                "value, unit, status, detail, period_start, period_end, observations, definition_id, "
                "definition_version, calculation_version, input_sha256, source_table, source_key) VALUES "
                "(%s, 'UTEST2', %s, %s, %s, %s, %s, %s, 'ok', %s::jsonb, '2026-01-02', %s, 20, %s, %s, 'test-v1', "
                "%s, 'silver.subject_position_series', '{}')",
                (published, figure_id, period, item, day, value, entry.unit, json.dumps(detail), day,
                 entry.definition_id, entry.definition_version, "d" * 64))
        publish(published)
        held = build("2026-04-02T09:00:00Z")
        figure(held, "UTEST1", "overview.nav", 99999.0, "money", currency="USD")
        publish(held, "held", "A check failed.")
        owner.commit()
        cls.published_build = published
        # The same account id in another tenant: never visible to this tenant's agents.
        owner.execute("SELECT set_config('app.tenant_id', %s, true)", (OTHER_TENANT,))
        owner.execute("INSERT INTO tenants(tenant_id, name, status, created_at) VALUES (%s, 'Other', 'active', "
                      "'2026-01-01')", (OTHER_TENANT,))
        other = build()
        figure(other, "UTEST1", "overview.nav", 1.0, "money", currency="USD")
        publish(other)
        owner.commit()

    def _call(self, name, arguments, *, user=None, job=None):
        headers = {"X-WR-User": user} if user else {"X-WR-Job": job}
        response = self.client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        self.assertEqual(response.status_code, 200)
        return response.json()["result"]

    def _listed(self, **who):
        headers = {"X-WR-User": who["user"]} if "user" in who else {"X-WR-Job": who["job"]}
        response = self.client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        return {t["name"] for t in response.json()["result"]["tools"]}

    # Install ---------------------------------------------------------------------------------

    def test_without_073_the_file_installs_nothing(self):
        self.assertFalse([name for name in self.before if "certified" in name])

    def test_no_existing_grant_changes(self):
        changed = {name: (acl, self.after.get(name)) for name, acl in self.before.items() if self.after.get(name) != acl}
        self.assertEqual(changed, {})

    def test_new_objects_are_for_agent_reader_only(self):
        added = {name: acl for name, acl in self.after.items() if name.startswith("agent.") and name not in self.before}
        self.assertEqual({name.removeprefix("agent.").replace(", ", ",") for name in added}, NEW_OBJECTS)
        for name, acl in added.items():
            grantees = {entry.split("=", 1)[0] for entry in acl.strip("{}").split(",") if entry}
            self.assertEqual(grantees, {"agent_test_owner", "agent_reader"}, name)  # "" would be PUBLIC

    # Access ----------------------------------------------------------------------------------

    def test_the_reader_sees_only_the_account_in_scope_and_never_the_tables(self):
        import psycopg

        from wealthryse_agent.database import connect, scoped

        reader = connect(self.reader_settings, search_path="agent")
        try:
            with scoped(reader, ["UTEST1"]):
                rows = reader.execute("SELECT subject_id, figure_id, value, build_status FROM certified_figures "
                                      "ORDER BY figure_id, value").fetchall()
                self.assertEqual([(r["subject_id"], r["figure_id"], r["value"], r["build_status"]) for r in rows],
                                 [("UTEST1", "overview.nav", 12705.0, "published"),
                                  ("UTEST1", "overview.twr", 0.0123, "published"),
                                  ("UTEST1", "overview.twr", 0.1626, "published"),
                                  ("UTEST1", "risk.sharpe_ratio", 1.12, "published")])
                self.assertEqual(sorted(r["status"] for r in reader.execute(
                    "SELECT status FROM certified_builds").fetchall()), ["held", "published"])
            with scoped(reader, []):
                self.assertEqual(reader.execute("SELECT COUNT(*) AS n FROM certified_figures").fetchone()["n"], 0)
                self.assertEqual(reader.execute("SELECT COUNT(*) AS n FROM certified_builds").fetchone()["n"], 0)
            # Only through the scoped functions: not the tables, the view or their public names.
            self.assertFalse(reader.execute("SELECT has_schema_privilege(current_user, 'gold', 'USAGE') AS p")
                             .fetchone()["p"])
            for table in ("public.v_certified_figures", "public.certified_figures", "public.certified_builds"):
                row = reader.execute("SELECT has_table_privilege(current_user, ?, 'SELECT') AS p", (table,)).fetchone()
                self.assertFalse(row["p"], table)
            reader.rollback()
            for statement in ("SELECT checks FROM gold.certified_builds", "SELECT 1 FROM gold.v_certified_figures"):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=statement):
                    reader.execute(statement)
                reader.rollback()
        finally:
            reader.close()

    def test_the_newest_published_date_for_each_account_in_scope(self):
        """agent.certified_latest (021): the newest as-of date a published build covers, and that build.
        The held build on the same date, the other tenant's build and accounts out of scope never count."""
        from wealthryse_agent.database import connect, scoped

        reader = connect(self.reader_settings, search_path="agent")
        try:
            def latest(subjects):
                with scoped(reader, subjects):
                    return [(r["subject_id"], str(r["build_id"]), str(r["as_of_date"])) for r in reader.execute(
                        "SELECT subject_id, build_id, as_of_date FROM certified_latest ORDER BY subject_id").fetchall()]

            self.assertEqual(latest(["UTEST1", "UTEST2", "UNOBUILD"]),
                             [("UTEST1", self.published_build, "2026-03-31"),
                              ("UTEST2", self.published_build, "2026-03-31")])
            self.assertEqual(latest(["UTEST2"]), [("UTEST2", self.published_build, "2026-03-31")])
            self.assertEqual(latest([]), [])
        finally:
            reader.close()

    def test_the_dispatcher_reads_it_through_the_tool_server(self):
        import psycopg

        with psycopg.connect(_dsn(ADMIN_DSN, self.database, "agent_test_owner"), autocommit=True,
                             options=f"-c app.tenant_id={TENANT}") as owner:
            owner.execute("INSERT INTO agent.service_grants(tenant_id, job_name, subject_id, granted_by) "
                          "VALUES (%s, 'risk_watch', 'UTEST1', 'test')", (TENANT,))
            try:
                result = self._call("get_settled_dates", {"account_id": "UTEST1"}, job="risk_watch")
                self.assertFalse(result.get("isError"), result)
                self.assertEqual(result["structuredContent"]["data"]["certified"],
                                 {"view": True, "as_of_date": "2026-03-31", "build_id": self.published_build})
            finally:
                owner.execute("DELETE FROM agent.service_grants WHERE job_name = 'risk_watch'")

    def test_the_writer_and_public_cannot_read_it(self):
        import psycopg

        from wealthryse_agent.database import connect

        writer = connect(self.writer_settings, search_path="agent")
        try:
            for statement in ("SELECT 1 FROM agent.certified_figures", "SELECT 1 FROM agent.certified_builds",
                              "SELECT 1 FROM agent.certified_latest",
                              "SELECT agent.certified_figure_trace('UTEST1', 'risk.sharpe_ratio', '', '', '', "
                              "'2026-03-31')"):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=statement):
                    writer.execute(statement)
                writer.rollback()
        finally:
            writer.close()

    # Tools -----------------------------------------------------------------------------------

    def test_hidden_until_switched_on(self):
        with mock.patch.dict(os.environ, {"AGENT_CERTIFIED_FIGURES": "", "CERTIFIED_FIGURES_PAGES": ""}):
            self.assertFalse({"get_certified_figures", "trace_figure"} & self._listed(user=OWNER))
            result = self._call("get_certified_figures", {"account_id": "UTEST1", "figures": ["overview.nav"]},
                                user=OWNER)
            self.assertEqual(result["structuredContent"]["error"]["code"], "unknown_tool")
        with mock.patch.dict(os.environ, ON):
            self.assertTrue({"get_certified_figures", "trace_figure"} <= self._listed(user=OWNER))
            self.assertFalse({"get_certified_figures", "trace_figure"} & self._listed(job="month_end_drafter"))
            result = self._call("get_certified_figures", {"account_id": "UTEST1", "figures": ["overview.nav"]},
                                job="month_end_drafter")
            self.assertEqual(result["structuredContent"]["error"]["code"], "unknown_tool")

    def test_get_certified_figures_through_the_tool_server(self):
        with mock.patch.dict(os.environ, ON):
            result = self._call("get_certified_figures",
                                {"account_id": "UTEST1", "figures": ["overview.nav", "risk.sharpe_ratio", "overview.twr"],
                                 "period": "ytd"}, user=VIEWER)["structuredContent"]
            self.assertEqual(result["as_of"], "2026-03-31")
            # The period picks ytd from the per-period figures and leaves the others as they are.
            self.assertEqual({f["figure_id"]: f["value"] for f in result["figures"]},
                             {"overview.nav": 12705.0, "overview.twr": 0.1626, "risk.sharpe_ratio": 1.12})
            nav = next(f for f in result["figures"] if f["figure_id"] == "overview.nav")
            self.assertEqual(nav["lineage"]["build_id"], self.published_build)
            self.assertEqual(nav["freshness"]["status"], "fresh")
            self.assertEqual(nav["display"], "12,705.00 USD")
            everything = self._call("get_certified_figures", {"account_id": "UTEST1", "page": "risk-snapshot"},
                                    user=OWNER)["structuredContent"]
            self.assertEqual([f["value"] for f in everything["figures"]], [1.12])
            refused = self._call("get_certified_figures", {"account_id": "UTEST2", "figures": ["overview.nav"]},
                                 user=VIEWER)["structuredContent"]
            self.assertEqual(refused["error"]["code"], "account_not_allowed")
            moved = self._call("get_certified_figures", {"account_id": "UTEST1", "page": "holdings"},
                               user=OWNER)["structuredContent"]
            self.assertEqual(moved["error"]["code"], "page_not_moved")

    def test_trace_goes_back_to_the_raw_file_without_its_path(self):
        with mock.patch.dict(os.environ, ON):
            result = self._call("trace_figure", {"account_id": "UTEST1", "figure_id": "risk.sharpe_ratio"},
                                user=OWNER)["structuredContent"]
            data = result["data"]
            self.assertEqual(data["build"]["build_id"], self.published_build)
            self.assertEqual([(r["depth"], r["basis"], r["job"]) for r in data["runs"]],
                             [(0, "row", "certified-figures"), (1, "dataset-time", "risk-analytics")])
            self.assertEqual(data["runs"][1]["inputs"], [{"dataset": "raw file", "row_count": None, "sha256": "ab" * 32}])
            self.assertEqual([s["run_id"] for s in data["ibkr_statements"]], [1])
            self.assertNotIn(RAW_PATH, json.dumps(result))
            self.assertNotIn("checks", data["build"])
            missing = self._call("trace_figure", {"account_id": "UTEST2", "figure_id": "risk.sharpe_ratio"},
                                 user=OWNER)["structuredContent"]
            self.assertEqual(missing["error"]["code"], "no_certified_figures")

    def test_attribution_and_the_charts_through_the_tool_server(self):
        """Batch 4 and 5 pages for the agents: a past month's Attribution is found on its last date
        (per_period_item: period and item), a chart's series point and table rows on their date, the
        row's detail with them, and the refusal names the tools that still answer a page not on."""
        pages = {**ON, "CERTIFIED_FIGURES_PAGES": "overview=on,attribution=on,charts=on"}
        with mock.patch.dict(os.environ, pages):
            february = self._call("get_certified_figures", {"account_id": "UTEST2", "page": "attribution",
                                                            "period": "month_2026-02"}, user=OWNER)["structuredContent"]
            self.assertEqual(february["as_of"], "2026-02-27")
            self.assertEqual(sorted((f["figure_id"], f["period_key"], f.get("item"), f["value"])
                                    for f in february["figures"]),
                             [("attribution.holding_contribution", "month_2026-02", "756733", 0.015),
                              ("attribution.portfolio_return", "month_2026-02", None, 0.021),
                              ("attribution.sector_contribution", "month_2026-02", "Technology", 0.015)])
            holding = next(f for f in february["figures"] if f["figure_id"] == "attribution.holding_contribution")
            self.assertEqual(holding["detail"], {"symbol": "SYN", "sector": "Technology"})
            newest = self._call("get_certified_figures", {"account_id": "UTEST2", "page": "attribution"},
                                user=OWNER)["structuredContent"]
            self.assertEqual(newest["as_of"], "2026-03-31")
            self.assertEqual({f["period_key"] for f in newest["figures"]}, {"month_2026-03"})
            traced = self._call("trace_figure", {"account_id": "UTEST2", "figure_id": "attribution.holding_contribution",
                                                 "period": "month_2026-02", "item": "756733"},
                                user=OWNER)["structuredContent"]
            self.assertEqual((traced["as_of"], traced["data"]["build"]["build_id"]), ("2026-02-27", self.published_build))
            charts = self._call("get_certified_figures", {"account_id": "UTEST2", "page": "charts"},
                                user=OWNER)["structuredContent"]
            self.assertEqual(charts["as_of"], "2026-03-31")
            self.assertEqual({(f["figure_id"], f.get("item")): f["value"] for f in charts["figures"]},
                             {("series.rolling_volatility_63d", None): 0.16, ("indicator.rsi_14", "756733"): 61.5})
            rsi = next(f for f in charts["figures"] if f["figure_id"] == "indicator.rsi_14")
            self.assertEqual(rsi["detail"], {"signal": "neutral", "observations": 20})
            earlier = self._call("get_certified_figures", {"account_id": "UTEST2", "figures": [
                "series.rolling_volatility_63d"], "as_of": "2026-03-01"}, user=OWNER)["structuredContent"]
            self.assertEqual([(f["as_of_date"], f["value"]) for f in earlier["figures"]], [("2026-02-27", 0.15)])
        with mock.patch.dict(os.environ, {**ON, "CERTIFIED_FIGURES_PAGES": "overview=on,charts=shadow"}):
            for page, words in (("attribution", "get_holdings_changes"), ("charts", "get_nav_history")):
                refused = self._call("get_certified_figures", {"account_id": "UTEST2", "page": page},
                                     user=OWNER)["structuredContent"]
                self.assertEqual(refused["error"]["code"], "page_not_moved")
                self.assertIn(words, refused["error"]["message"])


if __name__ == "__main__":
    unittest.main()
