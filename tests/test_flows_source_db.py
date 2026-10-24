"""FLOWS_SOURCE on a real PostgreSQL (ibkr_etl.flows_source).

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Builds its own database with every migration (the layer moves included),
loads made-up daily Flex statements with today's loader, and checks:

- fallback inside a READ ONLY transaction, as the website and the agents read: silver when it
  equals bronze, transfers included (silver.transfers, 084), bronze and one log line when it
  does not;
- a login that cannot read silver cash_transactions (as the agent tool server's today): bronze,
  one log line, and the caller's transaction still usable afterwards;
- the comparison command: read-only, every subject of each tenant named, counts only;
- the fill command: read-only without --apply, and with it silver.transfers as the loader wrote it.

Synthetic accounts and amounts only.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from ibkr_etl import flows_source
from ibkr_etl.config import Settings
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.provisional_benchmark import _bronze_external_flows, _external_flows
from ibkr_etl.service import ingest_content
from test_flows_source import ACCOUNT, FIRST, MID_MONTH, OTHER, daily_statements, standard_cash, transfer

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
TENANT, OTHER_TENANT = layers.TENANT, layers.OTHER_TENANT
ELSEWHERE = "U9300003"  # an account of the other tenant
PORTFOLIO = "flows-test-composite"
READER = "flows_test_reader"  # reads bronze and the lookups, not silver cash_transactions
DAYS = 25


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class FlowsSourceDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = f"flows_test_{uuid.uuid4().hex[:10]}"
        cls.raw_dir = tempfile.TemporaryDirectory()
        try:
            layers._build(cls.database)
            with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
                if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER,)).fetchone() is None:
                    admin.execute(f"CREATE ROLE {READER} LOGIN PASSWORD '{layers.PASSWORD}'")
            cls.url = layers._url(cls.database, layers.OWNER)
            with patch.dict(os.environ, {"IBKR_RUN_MIGRATIONS": "false", "IBKR_FLEX_LEGACY_KEY_COMPAT": "",
                                         "IBKR_FX_EVENT_KEY": "", "FLOWS_SOURCE": ""}):
                for content in daily_statements(ACCOUNT, cash=standard_cash(), days=DAYS):
                    cls._load(content, TENANT)
                for content in daily_statements(OTHER, cash=standard_cash(), days=DAYS,
                                                transfers=[transfer(FIRST + timedelta(days=4), "3000")]):
                    cls._load(content, TENANT)
                for content in daily_statements(ELSEWHERE, cash=standard_cash(), days=DAYS):
                    cls._load(content, OTHER_TENANT)
            with layers._connect(cls.database) as owner:
                for tenant, subject, kind in ((TENANT, ACCOUNT, "physical_account"),
                                              (TENANT, OTHER, "physical_account"),
                                              (TENANT, PORTFOLIO, "composite_portfolio"),
                                              (OTHER_TENANT, ELSEWHERE, "physical_account")):
                    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
                    owner.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, "
                                  "base_currency, reporting_currency, history_start_date, inception_date, "
                                  "lifecycle_status) VALUES (%s, %s, 'Synthetic', 'USD', 'USD', %s, %s, 'active')",
                                  (subject, kind, FIRST, FIRST))
                owner.execute(f"GRANT USAGE ON SCHEMA bronze, silver, public TO {READER}")
                for table in ("bronze.flex_rows", "public.flex_rows", "silver.accounts", "public.accounts",
                              "silver.fx_rates", "public.fx_rates", "public.platform_subjects"):
                    owner.execute(f"GRANT SELECT ON {table} TO {READER}")
                owner.commit()
        except Exception:
            cls.raw_dir.cleanup()
            layers._drop(cls.database, roles=(READER,))
            raise

    @classmethod
    def tearDownClass(cls):
        cls.raw_dir.cleanup()
        layers._drop(cls.database, roles=(READER,))

    @classmethod
    def _load(cls, content: bytes, tenant: str) -> None:
        root = Path(cls.raw_dir.name)
        settings = Settings(project_root=root, flex_token="", flex_query_id="", database_path=root / "unused.sqlite3",
                            raw_report_dir=root / "raw", log_dir=root / "logs", timezone_name="Asia/Hong_Kong",
                            daily_lookback_days=14, user_agent="test", request_timeout_seconds=10,
                            max_wait_seconds=20, database_url=cls.url, tenant_id=tenant)
        ingest_content(settings, content, run_type="flows-source-test")

    def setUp(self):
        self.lines: list[str] = []
        emitter = patch.object(flows_source, "emit", self.lines.append)
        emitter.start()
        self.addCleanup(emitter.stop)
        self.at(MID_MONTH)

    def at(self, moment):
        clock = patch.object(flows_source, "_now", return_value=moment)
        clock.start()
        self.addCleanup(clock.stop)

    @contextlib.contextmanager
    def read_only(self, user: str = layers.OWNER):
        """A READ ONLY transaction on a placeholder-translating connection, as the website reads."""
        import psycopg

        raw = psycopg.connect(layers._dsn(self.database, user), row_factory=_hybrid_row_factory)
        try:
            raw.execute("SET TRANSACTION READ ONLY")
            yield raw, PostgresConnection(raw)
        finally:
            raw.rollback()
            raw.close()

    @contextlib.contextmanager
    def without_silver_transfers(self):
        """A transaction, rolled back, in which silver.transfers is empty, as before the fill."""
        import psycopg

        raw = psycopg.connect(layers._dsn(self.database, layers.OWNER), row_factory=_hybrid_row_factory)
        try:
            raw.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT,))
            self.assertEqual(raw.execute("DELETE FROM silver.transfers").rowcount, 1)
            yield raw, PostgresConnection(raw)
        finally:
            raw.rollback()
            raw.close()

    def flows(self, connection, subject: str, mode: str) -> dict[str, float]:
        with patch.dict(os.environ, {"FLOWS_SOURCE": mode}):
            return _external_flows(connection, subject)

    def logged(self) -> list[dict]:
        return [json.loads(line[len(flows_source.LOG_PREFIX):]) for line in self.lines]

    def test_fallback_uses_silver_when_it_equals_bronze(self):
        with self.read_only() as (raw, connection):
            bronze = _bronze_external_flows(connection, ACCOUNT)
            self.assertEqual(len(bronze), 4)
            silver = flows_source.silver_external_flows(connection, ACCOUNT)
            for mode in ("off", "fallback", "on"):
                with self.subTest(mode=mode):
                    self.assertEqual(self.flows(connection, ACCOUNT, mode), bronze if mode == "off" else silver)
            self.assertEqual(silver, bronze)
            self.assertEqual(raw.info.transaction_status.name, "INTRANS")
        self.assertEqual(self.lines, [])

    def test_a_transfer_is_read_from_silver(self):
        with self.read_only() as (_raw, connection):
            bronze = _bronze_external_flows(connection, OTHER)
            self.assertEqual(bronze[(FIRST + timedelta(days=4)).isoformat()], 3000.0)
            silver = flows_source.silver_external_flows(connection, OTHER)
            self.assertEqual(silver, bronze)
            for mode in ("fallback", "on"):
                with self.subTest(mode=mode):
                    self.assertEqual(self.flows(connection, OTHER, mode), silver)
        self.assertEqual(self.lines, [])

    def test_a_transfer_not_in_silver_keeps_bronze_and_logs_one_line(self):
        with self.without_silver_transfers() as (_raw, connection):
            bronze = _bronze_external_flows(connection, OTHER)
            self.assertEqual(self.flows(connection, OTHER, "fallback"), bronze)
        self.assertEqual(self.logged(), [{"dates_differing": 1, "decision": "bronze", "kinds":
                                          {"transfers_not_in_silver": 1}, "mode": "fallback",
                                          "reason": "silver_differs", "subject": "u…02"}])

    def test_portfolios_are_not_affected(self):
        with self.read_only() as (_raw, connection), \
                patch.object(flows_source, "silver_external_flows", side_effect=AssertionError("silver read")):
            self.assertEqual(self.flows(connection, PORTFOLIO, "fallback"), {})
        self.assertEqual(self.lines, [])

    def test_a_login_without_silver_keeps_bronze_and_its_transaction(self):
        with self.read_only(READER) as (raw, connection):
            bronze = _bronze_external_flows(connection, ACCOUNT)
            self.assertEqual(self.flows(connection, ACCOUNT, "fallback"), bronze)
            # The failed silver read was inside a savepoint: the READ ONLY transaction carries on.
            self.assertEqual(raw.info.transaction_status.name, "INTRANS")
            self.assertEqual(connection.execute("SELECT count(*) FROM platform_subjects").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT current_setting('transaction_read_only')").fetchone()[0], "on")
        self.assertEqual(self.logged(), [{"decision": "bronze", "error": "InsufficientPrivilege", "mode": "fallback",
                                          "reason": "silver_unreadable", "subject": "u…01"}])
        with self.read_only(READER) as (_raw, connection), self.assertRaises(Exception):
            self.flows(connection, ACCOUNT, "on")

    def test_the_month_end_check_keeps_bronze_and_the_transaction(self):
        self.at(datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc))
        with self.without_silver_transfers() as (raw, connection):
            bronze = _bronze_external_flows(connection, OTHER)
            self.assertEqual(self.flows(connection, OTHER, "on"), bronze)
        with self.read_only(READER) as (raw, connection):
            self.assertEqual(self.flows(connection, ACCOUNT, "on"), _bronze_external_flows(connection, ACCOUNT))
            self.assertEqual(raw.info.transaction_status.name, "INTRANS")
        self.assertEqual([(line["reason"], line.get("guard"), line["mode"]) for line in self.logged()],
                         [("silver_differs", "month_end", "on"), ("silver_unreadable", "month_end", "on")])

    def test_the_website_reader_goes_through_the_switch(self):
        from ibkr_etl import web

        access = {"subject_type": "physical_account", "base_currency": "USD", "reporting_currency": "USD"}
        with self.without_silver_transfers() as (raw, connection):
            bronze = _bronze_external_flows(connection, OTHER)
            with patch.dict(os.environ, {"FLOWS_SOURCE": "fallback"}):
                self.assertEqual(web._subject_external_flows(raw, OTHER, access), bronze)
                self.assertEqual(web._subject_external_flows(raw, ACCOUNT, access),
                                 flows_source.silver_external_flows(connection, ACCOUNT))
        self.assertEqual([line["subject"] for line in self.logged()], ["u…02"])

    def test_the_comparison_is_read_only_and_prints_counts(self):
        def checksum() -> list:
            with layers._connect(self.database) as owner:
                return [owner.execute(f"SELECT count(*), md5(string_agg(t::text, ',' ORDER BY t::text)) FROM {table} t")
                        .fetchone() for table in ("bronze.flex_rows", "silver.cash_transactions",
                                                  "silver.transfers", "public.platform_subjects",
                                                  "public.ingestion_runs")]

        before = checksum()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(flows_source.main(["compare", "--database-url", self.url, "--tenant", TENANT,
                                                "--tenant", OTHER_TENANT]), 0)
        self.assertEqual(checksum(), before)
        text = out.getvalue()
        self.assertNotRegex(text, r"U9\d{5}")
        report = json.loads(text)
        self.assertEqual({key: report[key] for key in ("tenants", "subjects", "accounts", "portfolios_not_affected",
                                                       "matching", "differing", "errors")},
                         {"tenants": 2, "subjects": 4, "accounts": 3, "portfolios_not_affected": 1, "matching": 3,
                          "differing": 0, "errors": 0})
        self.assertEqual(report["differences"], {})
        self.assertEqual(report["flow_dates"], {"bronze": 13, "silver": 13})
        self.assertEqual(report["notes"]["bronze_transfer_rows"], {"subjects": 1, "rows": 1})
        self.assertEqual(report["notes"]["silver_transfer_rows"], {"subjects": 1, "rows": 1})
        self.assertEqual(report["differing_subjects"], [])
        self.assertEqual(self.lines, [])

    def test_the_fill_counts_read_only_and_writes_with_apply(self):
        def rows() -> list:
            with layers._connect(self.database) as owner:
                owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
                return [tuple(row) for row in owner.execute("SELECT * FROM silver.transfers ORDER BY 1, 2")]

        def fill(*extra: str) -> dict:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(flows_source.main(["fill-transfers", "--database-url", self.url, "--tenant", TENANT,
                                                    "--tenant", OTHER_TENANT, *extra]), 0)
            self.assertNotRegex(out.getvalue(), r"U9\d{5}")
            return json.loads(out.getvalue())

        written = rows()
        self.assertEqual(len(written), 1)
        report = fill()
        self.assertEqual({key: report[key] for key in ("applied", "tenants", "accounts", "bronze_rows", "inserted",
                                                       "updated", "unchanged", "only_in_silver")},
                         {"applied": False, "tenants": 2, "accounts": 1, "bronze_rows": 1, "inserted": 0,
                          "updated": 0, "unchanged": 1, "only_in_silver": 0})
        with layers._connect(self.database) as owner:
            owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
            owner.execute("DELETE FROM silver.transfers")
            owner.commit()
        try:
            self.assertEqual(fill()["inserted"], 1)
            self.assertEqual(rows(), [])  # nothing written without --apply
            report = fill("--apply")
            self.assertEqual((report["applied"], report["inserted"]), (True, 1))
        finally:
            self.assertEqual(rows(), written)  # as the loader wrote it, under the same run
        self.assertEqual(fill()["unchanged"], 1)

    def test_the_comparison_names_only_the_tenants_given(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.dict(os.environ, {"DATABASE_URL": self.url,
                                                                      "PORTFOLIO_TENANT_ID": OTHER_TENANT}):
            flows_source.main(["compare"])
        report = json.loads(out.getvalue())
        self.assertEqual((report["tenants"], report["subjects"], report["matching"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
