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


def _agent_schema_sql(connection) -> list[str]:
    """The agent schema files as scripts/install_agent_schema.py applies them."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("install_agent_schema", ROOT / "scripts" / "install_agent_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.agent_schema_sql(connection)


def _dsn(base: str, database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(base)
    params["dbname"] = database
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class AgentDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection

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
        # Read from the checkout: an installed package resolves elsewhere.
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
        # Raw psycopg: the SQL files use the jsonb "?" operator family and $$ bodies.
        with psycopg.connect(_dsn(ADMIN_DSN, cls.database, "agent_test_owner"),
                             options=f"-c app.tenant_id={TENANT}") as owner:
            # Every agent schema file in name order, as scripts/install_agent_schema.py applies them;
            # twice, because the installer must be safe to re-run.
            for _ in range(2):
                for text in _agent_schema_sql(owner):
                    owner.execute(text)
            owner.execute((ROOT / "tests/fixtures/agent_seed.sql").read_text(encoding="utf-8"))
            cls._seed_month_end(owner)
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

    SNAPSHOT = "aaaaaaaa-0000-4000-8000-000000000001"
    TAMPERED = "aaaaaaaa-0000-4000-8000-000000000002"

    @classmethod
    def _seed_month_end(cls, owner):
        import hashlib
        import json

        payload = json.dumps({"subject_id": "UTEST1", "period_end": "2026-03-31"})
        rows = [(cls.SNAPSHOT, "2026-03-31", payload, hashlib.sha256(payload.encode()).hexdigest()),
                (cls.TAMPERED, "2026-02-28", payload, "0" * 64)]
        for snapshot_id, period_end, text, digest in rows:
            owner.execute(
                "INSERT INTO reporting_snapshots(snapshot_id, tenant_id, subject_id, period_start, period_end, as_of_at, "
                "reporting_currency, formula_version, source_manifest_json, data_sha256, quality_status, status, "
                "created_at, snapshot_payload_json, publication_allowed) VALUES (%s, %s, 'UTEST1', '2026-01-02', %s, "
                "'2026-04-01', 'USD', 'test', '{}', %s, 'pass', 'frozen', '2026-04-01', %s, true)",
                (snapshot_id, TENANT, period_end, digest, text))
        for report_type, status, sha in (("factsheet", "failed", None), ("factsheet", "awaiting_approval", "a" * 64),
                                         ("presentation", "released_internal", "b" * 64)):
            owner.execute(
                "INSERT INTO report_generation_runs(tenant_id, snapshot_id, report_type, status, output_reference, "
                "output_sha256, started_at, completed_at) VALUES (%s, %s, %s, %s, %s, %s, '2026-04-02', '2026-04-02')",
                (TENANT, cls.SNAPSHOT, report_type, status, f"internal-reports/review/UTEST1/2026-03-31/{report_type}",
                 sha))
        owner.execute("INSERT INTO agent.service_grants(tenant_id, job_name, subject_id, granted_by) "
                      "VALUES (%s, 'month_team', 'UTEST1', 'test')", (TENANT,))

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

    def test_an_all_accounts_grant_covers_accounts_onboarded_later(self):
        import psycopg

        with psycopg.connect(_dsn(ADMIN_DSN, self.database, "agent_test_owner"), autocommit=True) as owner:
            owner.execute("INSERT INTO agent.service_grants(tenant_id, job_name, subject_id, granted_by) "
                          "VALUES (%s, 'weekly_brief', '*', 'test')", (TENANT,))
            try:
                self.assertEqual(self._accounts(job="weekly_brief"), ["UTEST1", "UTEST2"])
                owner.execute(
                    "INSERT INTO platform_subjects(tenant_id, subject_id, subject_type, display_name, lifecycle_status) "
                    "VALUES (%s, 'UTEST3', 'composite_portfolio', 'Test three', 'onboarding'), "
                    "(%s, 'BTEST1', 'benchmark', 'Test benchmark', 'active')", (TENANT, TENANT))
                self.assertEqual(self._accounts(job="weekly_brief"), ["UTEST1", "UTEST2"])
                owner.execute("UPDATE platform_subjects SET lifecycle_status = 'active' WHERE subject_id = 'UTEST3'")
                self.assertEqual(self._accounts(job="weekly_brief"), ["UTEST1", "UTEST2", "UTEST3"])
                self.assertEqual(self._accounts(job="live_market"), [])
            finally:
                owner.execute("DELETE FROM platform_subjects WHERE subject_id IN ('UTEST3', 'BTEST1')")
                owner.execute("DELETE FROM agent.service_grants WHERE subject_id = '*'")

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

    def test_stress_test_revalues_the_latest_holdings(self):
        def run(shocks):
            result = self._call("run_stress_test", {"account_id": "UTEST1", "shocks": shocks}, user=VIEWER)
            return result["structuredContent"]["data"]

        hk = run([{"kind": "market", "match": "hong kong", "change": -0.1}])
        self.assertAlmostEqual(hk["total_impact"], -541.8)
        self.assertAlmostEqual(hk["impact_of_nav"], -541.8 / 12705)
        self.assertEqual([r["symbol"] for r in hk["largest_impacts"]], ["700"])
        both = run([{"kind": "market", "match": "hong kong", "change": -0.1},
                    {"kind": "currency", "match": "HKD", "change": -0.05},
                    {"kind": "symbol", "match": "MSFT", "change": 0.2}])
        self.assertAlmostEqual(both["total_impact"], 5418 * (0.9 * 0.95 - 1))
        self.assertEqual(both["shocks_matching_nothing"], [{"kind": "symbol", "match": "MSFT", "change": 0.2}])
        self.assertAlmostEqual(run([{"kind": "currency", "match": "USD", "change": -0.5}])["total_impact"], 0.0)
        refused = self._call("run_stress_test", {"account_id": "UTEST1", "shocks": [
            {"kind": "market", "match": "atlantis", "change": -0.1}]}, user=VIEWER)
        self.assertTrue(refused["isError"])

    def test_live_snapshot_only_when_the_live_view_is_active(self):
        import psycopg

        def snapshot():
            return self._call("get_live_snapshot", {"account_id": "UTEST1"}, user=VIEWER)["structuredContent"]["data"]

        self.assertFalse(snapshot()["available"])
        with psycopg.connect(_dsn(ADMIN_DSN, self.database, "agent_test_owner"),
                             options=f"-c app.tenant_id={TENANT}") as owner:
            owner.execute("INSERT INTO live_gateway_status(tenant_id, observed_at, gateway_state, collector_version) "
                          "VALUES (%s, now(), 'connected', 'test')", (TENANT,))
            owner.execute("INSERT INTO live_account_values(tenant_id, account_id, observed_at, base_currency, "
                          "net_liquidation, total_cash, daily_pnl) VALUES (%s, 'UTEST1', now(), 'USD', 10200, 500, 200)",
                          (TENANT,))
            owner.execute("INSERT INTO live_positions(tenant_id, account_id, observed_at, conid, symbol, sec_type, "
                          "currency, quantity, market_value, daily_pnl) SELECT %s, 'UTEST1', observed_at, '265598', "
                          "'AAPL', 'STK', 'USD', 15, 3300, 180 FROM live_account_values WHERE account_id = 'UTEST1'",
                          (TENANT,))
            owner.execute("INSERT INTO live_quotes(tenant_id, conid, observed_at, symbol, currency, last_price, "
                          "prior_close, market_data_type) VALUES (%s, '265598', now(), 'AAPL', 'USD', 220, 200, 1)",
                          (TENANT,))
            self.assertFalse(snapshot()["available"])  # stored, but the switch is off
            owner.execute("INSERT INTO subject_feature_states(tenant_id, subject_id, feature_key, feature_state, reason) "
                          "VALUES (%s, 'UTEST1', 'live_view', 'preview', 'test')", (TENANT,))
            owner.commit()
            self.assertFalse(snapshot()["available"])  # the pilot stays the owner's own website view
            owner.execute("UPDATE subject_feature_states SET feature_state = 'active' WHERE subject_id = 'UTEST1' "
                          "AND feature_key = 'live_view'")
            owner.commit()
        data = snapshot()
        self.assertTrue(data["available"])
        self.assertAlmostEqual(data["daily_return"], 200 / 10000)
        self.assertEqual(data["largest_movers"][0]["symbol"], "AAPL")
        self.assertAlmostEqual(data["largest_movers"][0]["change_since_prior_close"], 0.1)

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

    def test_value_history_and_risk(self):
        result = self._call("get_nav_history", {"account_id": "UTEST1", "frequency": "daily"}, user=VIEWER)
        data = result["structuredContent"]["data"]
        points = {p["date"]: p for p in data["points"]}
        self.assertEqual(list(points), ["2026-01-02", "2026-01-30", "2026-02-27", "2026-03-31"])
        expected = 1.05 * (1 + 600 / 11000) * (12705 / 12100) - 1
        self.assertAlmostEqual(points["2026-03-31"]["cumulative_return"], expected)
        self.assertEqual(points["2026-02-27"]["net_flows"], 1000.0)
        self.assertAlmostEqual(points["2026-03-31"]["benchmark_cumulative_return"], 0.05)
        self.assertIsNone(points["2026-01-30"]["benchmark_cumulative_return"])  # no level within a week
        risk = self._call("get_risk_metrics", {"account_id": "UTEST1"}, user=VIEWER)
        self.assertTrue(risk["isError"])
        self.assertEqual(risk["structuredContent"]["error"]["code"], "not_enough_history")

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

    # Export agent ---------------------------------------------------------------

    def test_store_export_checks_and_packs(self):
        import psycopg

        from wealthryse_agent.database import connect
        from wealthryse_agent.store import Store

        store = Store(connect(self.writer_settings, search_path="agent"), TENANT)
        plan = {"title": "Dividends", "datasets": [{"dataset": "cash-activity"}]}
        passed = store.add_export_check(user=VIEWER, account_id="UTEST1", plan=plan, fingerprint="a" * 64, status="pass",
                                        note="n", note_checked=True, concerns=[], checks=[{"id": "x"}],
                                        pack_item_id=None, correlation_id="c")
        failed = store.add_export_check(user=VIEWER, account_id="UTEST1", plan=plan, fingerprint="b" * 64, status="fail",
                                        note="", note_checked=False, concerns=["c"], checks=[], pack_item_id=None,
                                        correlation_id="c")
        record = store.export_check(passed, VIEWER)
        self.assertEqual((record["plan"], record["checks"], record["released_at"]), (plan, [{"id": "x"}], None))
        self.assertIsNone(store.export_check(passed, OWNER))
        self.assertFalse(store.release_export_check(passed, OWNER))
        self.assertTrue(store.release_export_check(passed, VIEWER))
        self.assertIsNotNone(store.export_check(passed, VIEWER)["released_at"])
        self.assertFalse(store.release_export_check(failed, VIEWER))  # a failed export is never released

        pack = store.save_export_pack(user=VIEWER, account_id="UTEST1", name="Tax pack", schedule="monthly", plan=plan)
        self.assertEqual([p["pack_id"] for p in store.export_packs(VIEWER, "UTEST1")], [pack])
        self.assertEqual(store.export_packs(OWNER), [])
        self.assertEqual(store.export_pack(pack, VIEWER)["plan"], plan)
        self.assertIn(pack, [p["pack_id"] for p in store.active_export_packs()])
        self.assertTrue(store.add_inbox(kind="export_pack", account_id="UTEST1", period_key=f"{pack}:2026-03-31",
                                        title="t", body="b", citations=[{"export_pack": pack}], run_id=None))
        self.assertFalse(store.archive_export_pack(pack, OWNER))
        self.assertTrue(store.archive_export_pack(pack, VIEWER))
        self.assertIsNone(store.export_pack(pack, VIEWER))
        with self.assertRaises(psycopg.errors.CheckViolation):
            store.save_export_pack(user=VIEWER, account_id="UTEST1", name="x", schedule="daily", plan=plan)
        store.db.rollback()
        store.db.close()

    def test_export_source_reads_and_ties_to_the_books(self):
        import contextlib

        import psycopg
        from psycopg.rows import dict_row

        from ibkr_etl import export_agent as xa
        from ibkr_etl.export_agent_source import PlatformExportSource

        dsn = _dsn(ADMIN_DSN, self.database, "agent_test_owner")
        with psycopg.connect(dsn, options=f"-c app.tenant_id={TENANT}") as owner:
            # Positions on 2026-03-31 are 3,300 USD plus 42,000 HKD at 0.129, so 8,718 USD; with 3,987 USD of cash
            # they make the account's 12,705.
            owner.execute("INSERT INTO cash_snapshots(account_id, snapshot_date, currency, ending_cash_text, settled_cash_text, "
                          "fx_rate_to_base_text, row_sha256, last_seen_run_id) VALUES "
                          "('UTEST1', '2026-02-27', 'USD', '5000', '5000', '1', 'c1', 1), "
                          "('UTEST1', '2026-03-31', 'USD', '3987', '3987', '1', 'c2', 1), "
                          "('UTEST1', '2026-03-31', 'BASE_SUMMARY', '3987', '3987', '1', 'c3', 1) ON CONFLICT DO NOTHING")
            owner.execute("INSERT INTO cash_transactions(account_id, source_key, event_datetime, effective_date, "
                          "transaction_type, description, amount_text, currency, conid, row_sha256, last_seen_run_id) VALUES "
                          "('UTEST1', 'd1', '2026-03-16', '2026-03-16', 'Dividends', 'AAPL dividend', '12', 'USD', '265598', 'd1', 1) "
                          "ON CONFLICT DO NOTHING")

        @contextlib.contextmanager
        def database():
            with psycopg.connect(dsn, row_factory=dict_row, options=f"-c app.tenant_id={TENANT}") as connection:
                yield connection

        access = {"subject_id": "UTEST1", "display_name": "Test one", "reporting_currency": "USD",
                  "subject_type": "physical_account", "history_start_date": "2026-01-02"}
        source = PlatformExportSource(database=database, tenant_id=TENANT, subject_id="UTEST1", access=access,
                                      cash_filter=lambda start, end: (start, end),
                                      trade_filter=lambda start, end, symbol, side: (start, end, symbol, side))
        facts = source.facts("2026-04-01")
        self.assertEqual((facts["latest_date"], facts["cash_transaction_types"]), ("2026-03-31", ["Dividends"]))
        self.assertEqual(facts["currencies"], ["HKD", "USD"])
        self.assertEqual(source.nav_on("2026-03-31"), xa.Decimal("12705"))
        value = source.holdings_value("2026-03-31")
        self.assertEqual((value["positions"], value["cash"], value["nav"]),
                         (xa.Decimal("8718.000"), xa.Decimal("3987"), xa.Decimal("12705")))
        roll = source.cash_roll("2026-03-01", "2026-03-31")
        self.assertEqual({k: str(v) for k, v in roll["USD"].items()},
                         {"opening": "5000", "activity": "12", "trades": "0", "closing": "3987"})
        plan, _ = xa.validate_plan({"title": "Quarter", "datasets": [
            {"dataset": "holdings", "to_date": "2026-03-31"},
            {"dataset": "performance", "from_date": "2026-01-01"},
            {"dataset": "cash-activity", "from_date": "2026-03-01", "to_date": "2026-03-31", "group_by": ["currency"]}]},
            facts)
        built = xa.build(plan, source.fetch, source.subject_type)
        checks = {c["id"]: c for c in xa.run_checks(built, source.books(), source.subject_type)}
        self.assertEqual([len(d.rows) for d in built], [2, 4, 1])
        self.assertEqual(checks["holdings_tie_1"]["status"], "pass")
        self.assertEqual(checks["nav_matches_books_2"]["status"], "pass")
        self.assertEqual(checks["cash_roll_3"]["status"], "info")  # 1,025 USD went to the AAPL purchase
        # The seed has month-end values only, so the value history's gaps are the one thing to review.
        self.assertEqual([k for k, c in checks.items() if c["status"] not in ("pass", "info")], ["nav_gaps_2"])

    # Month-end team -----------------------------------------------------------

    def test_month_end_tools_are_for_the_team_only(self):
        package = self._call("get_month_end_package", {"account_id": "UTEST1", "period_end": "2026-03-31"},
                             job="month_team")["structuredContent"]["data"]
        self.assertEqual(package["snapshot_id"], self.SNAPSHOT)
        self.assertEqual([(f["report_type"], f["status"]) for f in package["files"]],
                         [("factsheet", "awaiting_approval"), ("presentation", "released_internal")])
        snapshot = self._call("get_month_end_snapshot", {"account_id": "UTEST1", "snapshot_id": self.SNAPSHOT},
                              job="month_team")["structuredContent"]["data"]
        self.assertEqual(snapshot["payload"]["period_end"], "2026-03-31")
        tampered = self._call("get_month_end_snapshot", {"account_id": "UTEST1", "snapshot_id": self.TAMPERED},
                              job="month_team")
        self.assertEqual(tampered["structuredContent"]["error"]["code"], "snapshot_integrity_failed")
        months = self._call("list_month_end_packages", {"account_id": "UTEST1"}, job="month_team")
        self.assertEqual(months["structuredContent"]["data"]["months"][0]["files"]["presentation"]["status"],
                         "released_internal")
        # A person, even the owner, can neither call nor see them.
        refused = self._call("get_month_end_package", {"account_id": "UTEST1", "period_end": "2026-03-31"}, user=OWNER)
        self.assertEqual(refused["structuredContent"]["error"]["code"], "unknown_tool")
        listed = self.client.post("/mcp", headers={"X-WR-User": OWNER},
                                  json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
        self.assertNotIn("get_month_end_snapshot", [t["name"] for t in listed])
        # Nor can another job, even one granted the account.
        other = self._call("get_month_end_package", {"account_id": "UTEST1", "period_end": "2026-03-31"},
                           job="holdings_explainer")
        self.assertEqual(other["structuredContent"]["error"]["code"], "unknown_tool")

    def test_month_store_round_trip(self):
        from wealthryse_agent.database import connect
        from wealthryse_agent.month_team.store import MonthStore
        from wealthryse_agent.store import Store

        connection = connect(self.writer_settings, search_path="agent")
        store = MonthStore(connection, TENANT)
        run = store.run("UTEST1", "2026-03-31", "month")
        self.assertEqual((run["stage"], run["attempts"], run["live"]), ("data", 0, False))
        self.assertEqual(store.run("UTEST1", "2026-03-31", "month")["month_run_id"], run["month_run_id"])
        self.assertNotEqual(store.run("UTEST1", "2026-03-31", "replay")["month_run_id"], run["month_run_id"])
        store.update_run(run["month_run_id"], stage="ready", live=True, figures={"figures": [{"id": "F1"}]},
                         commentary_status="agreed", verdicts={"reviewer": "objection"}, attempts=3, last_error="x")
        step = store.add_step(run["month_run_id"], "reviewer", "objection", "One objection.", evidence={"n": 1})
        store.add_objection(run["month_run_id"], step, raised_by="visual_qa", target="factsheet", message="Clipped",
                            page=1, region={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1})
        store.add_objection(run["month_run_id"], step, raised_by="reviewer", target="commentary", message="Wrong way")
        self.assertEqual(store.settle_objections(run["month_run_id"], ["commentary"], "overruled", "Kept"), 1)
        listed = store.runs(["UTEST1", "UTEST2"], "2026-03-31")
        self.assertEqual([(r["subject_id"], r["open_objections"]) for r in listed], [("UTEST1", 1)])
        detail = store.run_detail("UTEST1", "2026-03-31")
        self.assertEqual((detail["figures"]["figures"][0]["id"], detail["live"]), ("F1", True))
        self.assertEqual(detail["objections"][0]["region"]["w"], 0.3)
        self.assertEqual([s["agent_name"] for s in detail["steps"]], ["reviewer"])
        self.assertIsNone(store.run_detail("UTEST2", "2026-03-31"))
        self.assertEqual(store.retry_stopped("month", "2026-03-31", "2026-03-31"), 1)
        # Cards: one per kind, account and month; a refresh can reopen it.
        store.put_card(kind="month_end_report", account_id="UTEST1", period_key="2026-03-31", title="t", body="b")
        self.assertEqual(store.card_status("month_end_report", "UTEST1", "2026-03-31"), "new")
        main = Store(connection, TENANT)
        item = next(i for i in main.inbox(["UTEST1"]) if i["kind"] == "month_end_report")
        self.assertTrue(main.decide(item["item_id"], ["UTEST1"], "read", "x" * 64))
        store.put_card(kind="month_end_report", account_id="UTEST1", period_key="2026-03-31", title="t2", body="b2")
        self.assertEqual(store.card_status("month_end_report", "UTEST1", "2026-03-31"), "read")
        store.put_card(kind="month_end_report", account_id="UTEST1", period_key="2026-03-31", title="t3", body="b3",
                       reopen=True)
        self.assertEqual(store.card_status("month_end_report", "UTEST1", "2026-03-31"), "new")
        # The team's tokens count against its own monthly budget, not the daily chat budget.
        before = main.tokens_used_today()
        connection.execute("INSERT INTO agent.runs(tenant_id, job_name, correlation_id, status, usage) VALUES "
                           "(?::uuid, 'month_team', 'c', 'succeeded', '{\"input_tokens\": 900, \"output_tokens\": 100}')",
                           (TENANT,))
        connection.commit()
        self.assertEqual(main.tokens_used_today(), before)
        self.assertEqual(store.tokens_used_this_month(), 1000)
        self.assertEqual(store.team_status()["latest_job"]["status"], "succeeded")
        connection.close()

    def test_each_replay_round_checks_a_month_afresh_and_rounds_compare(self):
        from wealthryse_agent.database import connect
        from wealthryse_agent.month_team.store import MonthStore

        connection = connect(self.writer_settings, search_path="agent")
        store = MonthStore(connection, TENANT)
        first = store.run("UTEST2", "2026-02-28", "replay")
        self.assertEqual(first["replay_round"], 1)
        self.assertEqual(store.run("UTEST2", "2026-02-28", "replay", 1)["month_run_id"], first["month_run_id"])
        second = store.run("UTEST2", "2026-02-28", "replay", 2)
        self.assertNotEqual(second["month_run_id"], first["month_run_id"])
        self.assertEqual((second["stage"], second["replay_round"]), ("data", 2))
        self.assertEqual(store.run("UTEST2", "2026-02-28", "month")["replay_round"], 0)
        with self.assertRaises(ValueError):
            store.run("UTEST2", "2026-02-28", "month", 2)
        # Round 1 found an objection; round 2 finished clean.
        store.update_run(first["month_run_id"], stage="ready", data_verdict="clean")
        step = store.add_step(first["month_run_id"], "visual_qa", "objection", "Clipped.")
        store.add_objection(first["month_run_id"], step, raised_by="visual_qa", target="factsheet", message="Clipped")
        store.update_run(second["month_run_id"], stage="ready", data_verdict="clean")
        # Website reads show the newest round unless another is asked for.
        self.assertEqual(store.latest_round(), 2)
        self.assertEqual([r["replay_round"] for r in store.runs(["UTEST2"], "2026-02-28", "replay")], [2])
        self.assertEqual(store.runs(["UTEST2"], "2026-02-28", "replay", 1)[0]["open_objections"], 1)
        self.assertEqual(store.run_detail("UTEST2", "2026-02-28", "replay", 1)["objections"][0]["message"], "Clipped")
        self.assertEqual(store.run_detail("UTEST2", "2026-02-28", "replay")["objections"], [])
        rounds = {r["replay_round"]: r for r in store.round_summary(["UTEST2"])}
        self.assertEqual((rounds[1]["months"], rounds[1]["objections"], rounds[1]["no_objections"]), (1, 1, 0))
        self.assertEqual(rounds[1]["objections_by_target"], {"factsheet": 1})
        self.assertEqual((rounds[2]["months"], rounds[2]["objections"], rounds[2]["no_objections"]), (1, 0, 1))
        self.assertEqual(store.round_summary(["UTEST1"]), [])  # only the accounts asked about
        # A retry clears stopped runs in its own round only.
        store.update_run(first["month_run_id"], attempts=3)
        store.update_run(second["month_run_id"], attempts=3)
        self.assertEqual(store.retry_stopped("replay", "2026-02-28", "2026-02-28", 2), 1)
        self.assertEqual(store.run("UTEST2", "2026-02-28", "replay")["attempts"], 3)
        connection.close()


if __name__ == "__main__":
    unittest.main()
