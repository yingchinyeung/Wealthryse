"""Migration 071 (lineage records) and ibkr_etl.lineage on a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Builds its own database with every migration in order, as a new
environment does, as a non-superuser owner so forced row-level security applies as on Azure.
Synthetic data only.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from ibkr_etl import lineage
from ibkr_etl.db import connect

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
TENANT, OTHER_TENANT = layers.TENANT, layers.OTHER_TENANT
STAMPED = ("crisis_scenario_results", "performance_comparison_daily", "risk_analytics_snapshots")
STATEMENT_SHA = "1" * 64
PRICES_SHA = "2" * 64


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class LineageDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = f"lineage_test_{uuid.uuid4().hex[:10]}"
        try:
            cls.applied = layers._build(cls.database)
        except Exception:
            layers._drop(cls.database)
            raise
        cls.url = layers._url(cls.database, layers.OWNER)

    @classmethod
    def tearDownClass(cls):
        layers._drop(cls.database)

    def setUp(self):
        lineage.reset()
        self.environment = patch.dict(os.environ, {"LINEAGE_RECORDING": "true", "DATABASE_URL": self.url,
                                                   "PORTFOLIO_TENANT_ID": TENANT})
        self.environment.start()
        for name in ("LINEAGE_CODE_VERSION", "LINEAGE_BUDGET_SECONDS", "CONTAINER_APP_JOB_EXECUTION_NAME"):
            os.environ.pop(name, None)

    def tearDown(self):
        self.environment.stop()
        lineage.reset()

    def _owner(self, tenant=TENANT):
        return layers._connect(self.database, tenant=tenant)

    def _job_session(self):
        return connect(Path("/tmp/unused.sqlite3"), self.url, TENANT)

    @staticmethod
    def _insert_scenario(connection, subject: str, scenario: str = "gfc-2008") -> None:
        connection.execute(
            "INSERT INTO crisis_scenario_results(subject_id, holdings_as_of, scenario_id, calculation_version, "
            "reporting_currency, crisis_start, crisis_end, available, result_json, generated_at) "
            "VALUES (?, '2026-10-16', ?, 'v1', 'USD', '2008-09-01', '2009-03-09', true, '{}'::jsonb, "
            "'2026-10-16T09:20:00Z') "
            "ON CONFLICT (tenant_id, subject_id, holdings_as_of, scenario_id, calculation_version) "
            "DO UPDATE SET generated_at = excluded.generated_at",
            (subject, scenario))
        connection.commit()

    def _stamp(self, subject: str, scenario: str = "gfc-2008"):
        with self._owner() as owner:
            row = owner.execute("SELECT lineage_run_id FROM crisis_scenario_results WHERE subject_id = %s "
                                "AND scenario_id = %s", (subject, scenario)).fetchone()
        return None if row is None or row[0] is None else str(row[0])

    def test_the_migration_is_applied_in_order(self):
        self.assertIn("071_lineage_records.sql", self.applied)

    def test_a_run_leaves_start_end_and_its_datasets(self):
        with patch.dict(os.environ, {"LINEAGE_CODE_VERSION": "ibkr-platform@sha256:" + "a" * 64,
                                     "CONTAINER_APP_JOB_EXECUTION_NAME": "ibkrv2stg-risk-free-daily-x1"}):
            with lineage.run("ibkrv2stg-risk-free-daily") as record:
                record.output("silver.risk_free_rates", rows=4)
                record.input("blob://bronze-market-data/treasury-par-yield/2026/10/16/y.xml", sha256="b" * 64)
        self.assertIsNone(lineage.disabled_reason())
        with self._owner() as owner:
            events = owner.execute("SELECT event_type FROM lineage_events WHERE run_id = %s ORDER BY event_time",
                                   (record.run_id,)).fetchall()
            self.assertEqual([row[0] for row in events], ["START", "COMPLETE"])
            run = owner.execute("SELECT status, job_namespace, job_name, code_version, execution, ended_at >= "
                                "started_at, facets ? 'code' FROM lineage_runs WHERE run_id = %s",
                                (record.run_id,)).fetchone()
            self.assertEqual(run, ("COMPLETE", "wealthryse", "ibkrv2stg-risk-free-daily",
                                   "ibkr-platform@sha256:" + "a" * 64, "ibkrv2stg-risk-free-daily-x1", True, True))
            io_rows = owner.execute("SELECT direction, namespace, name, row_count, sha256 FROM lineage_io "
                                    "WHERE run_id = %s ORDER BY 1, 2, 3", (record.run_id,)).fetchall()
            self.assertEqual(io_rows, [
                ("input", "blob", "bronze-market-data/treasury-par-yield/2026/10/16/y.xml", None, "b" * 64),
                ("output", "postgres", "silver.risk_free_rates", 4, None)])

    def test_a_failed_run_is_recorded_as_failed(self):
        with self.assertRaises(ZeroDivisionError):
            with lineage.run("ibkrv2stg-sector-classification") as record:
                1 / 0
        with self._owner() as owner:
            status, facets = owner.execute("SELECT status, facets FROM lineage_runs WHERE run_id = %s",
                                           (record.run_id,)).fetchone()
        self.assertEqual((status, facets["error"]), ("FAIL", {"type": "ZeroDivisionError"}))

    def test_records_are_append_only_one_end_per_run_and_tenant_scoped(self):
        import psycopg

        with lineage.run("ibkrv2stg-benchmark-daily") as record:
            record.output("silver.benchmark_prices", rows=1)
        with self._owner() as owner:
            for statement in ("UPDATE lineage_events SET job_name = 'x'", "DELETE FROM lineage_events",
                              "TRUNCATE lineage_events", "UPDATE lineage_io SET row_count = 0",
                              "DELETE FROM lineage_io", "TRUNCATE lineage_io"):
                with self.subTest(statement=statement), self.assertRaises(psycopg.Error):
                    owner.execute(statement)
                owner.rollback()
            with self.assertRaises(psycopg.errors.UniqueViolation):
                owner.execute("INSERT INTO lineage_events(run_id, event_type, event_time, job_namespace, job_name, "
                              "producer) VALUES (%s, 'FAIL', now(), 'wealthryse', 'x', 'test')", (record.run_id,))
            owner.rollback()
            for bad in ("INSERT INTO lineage_io(run_id, direction, namespace, name, sha256) "
                        "VALUES (gen_random_uuid(), 'input', 'blob', 'c/x', 'NOT-HEX')",
                        "INSERT INTO lineage_io(run_id, direction, namespace, name) "
                        "VALUES (gen_random_uuid(), 'sideways', 'postgres', 'silver.trades')",
                        "INSERT INTO lineage_events(run_id, event_type, event_time, job_namespace, job_name, producer) "
                        "VALUES (gen_random_uuid(), 'RUNNING', now(), 'n', 'j', 'p')"):
                with self.subTest(bad=bad), self.assertRaises(psycopg.Error):
                    owner.execute(bad)
                owner.rollback()
        with self._owner(OTHER_TENANT) as other:
            self.assertEqual(other.execute("SELECT count(*) FROM lineage_events").fetchone()[0], 0)
            self.assertEqual(other.execute("SELECT count(*) FROM lineage_io").fetchone()[0], 0)
            self.assertEqual(other.execute("SELECT count(*) FROM lineage_runs").fetchone()[0], 0)

    def test_gold_rows_name_the_run_that_last_wrote_them(self):
        with lineage.run("ibkrv2stg-crisis-scenarios-daily") as record:
            session = self._job_session()
            try:
                self.assertTrue(record.bind(session))
                self._insert_scenario(session, "stamp-a")
            finally:
                session.close()
        self.assertEqual(self._stamp("stamp-a"), record.run_id)
        # A session that was never marked writes no run id, and so does a malformed setting.
        session = self._job_session()
        try:
            self._insert_scenario(session, "stamp-b")
            self._insert_scenario(session, "stamp-a")
            session.execute("SELECT set_config('lineage.run_id', 'not-a-uuid', false)")
            self._insert_scenario(session, "stamp-c")
        finally:
            session.close()
        self.assertIsNone(self._stamp("stamp-b"))
        self.assertIsNone(self._stamp("stamp-a"), "the last writer was not recorded")
        self.assertIsNone(self._stamp("stamp-c"))

    def test_recording_off_leaves_the_session_and_rows_alone(self):
        with patch.dict(os.environ, {"LINEAGE_RECORDING": "false"}):
            with lineage.run("ibkrv2stg-crisis-scenarios-daily") as record:
                session = self._job_session()
                try:
                    self.assertFalse(record.bind(session))
                    self._insert_scenario(session, "off-a")
                    setting = session.execute("SELECT current_setting('lineage.run_id', true)").fetchone()[0]
                finally:
                    session.close()
        self.assertIn(setting, (None, ""))
        self.assertIsNone(self._stamp("off-a"))
        with self._owner() as owner:
            self.assertEqual(owner.execute("SELECT count(*) FROM lineage_events WHERE run_id = %s",
                                           (record.run_id,)).fetchone()[0], 0)

    def test_each_stamped_table_has_the_column_trigger_and_a_matching_public_view(self):
        with self._owner() as owner:
            for table in STAMPED:
                with self.subTest(table=table):
                    schema = owner.execute(
                        "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.relname = %s AND c.relkind = 'r'", (table,)).fetchone()[0]
                    self.assertEqual(schema, "gold")
                    columns = [row[0] for row in owner.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s "
                        "AND table_name = %s ORDER BY ordinal_position", (schema, table))]
                    view = [row[0] for row in owner.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                        "AND table_name = %s ORDER BY ordinal_position", (table,))]
                    self.assertEqual(columns[-1], "lineage_run_id")
                    self.assertEqual(view, columns)
                    nullable = owner.execute(
                        "SELECT is_nullable FROM information_schema.columns WHERE table_schema = %s "
                        "AND table_name = %s AND column_name = 'lineage_run_id'", (schema, table)).fetchone()[0]
                    self.assertEqual(nullable, "YES")
                    self.assertEqual(owner.execute(
                        "SELECT count(*) FROM pg_trigger WHERE tgrelid = %s::regclass AND tgname = 'lineage_stamp_run'",
                        (f"{schema}.{table}",)).fetchone()[0], 1)

    def test_trace_walks_from_a_gold_row_to_the_runs_and_raw_files(self):
        with self._owner() as owner:
            ingestion_run = owner.execute(
                "INSERT INTO ingestion_runs(run_type, started_at, status, requested_from, requested_to, raw_sha256, "
                "raw_path) VALUES ('institutional-flex-daily', '2026-10-16T08:21:00Z', 'success', '2026-10-03', "
                "'2026-10-16', %s, 'https://acct.blob.core.windows.net/ibkr-raw/t/2026/10/16/flex.xml') "
                "RETURNING run_id", (STATEMENT_SHA,)).fetchone()[0]
            owner.execute(
                "INSERT INTO benchmark_master(benchmark_id, symbol, name, currency, return_convention, created_at, "
                "updated_at) VALUES ('lineage-test-index', 'LTI', 'Lineage test index', 'USD', 'price', "
                "'2026-10-16', '2026-10-16') ON CONFLICT DO NOTHING")
            owner.execute(
                "INSERT INTO benchmark_prices(benchmark_id, observation_date, level, currency, observation_status, "
                "provider, fetched_at, raw_sha256) VALUES ('lineage-test-index', '2026-10-16', 101.5, 'USD', "
                "'provisional_close', 'test', '2026-10-16T08:31:00Z', %s)", (PRICES_SHA,))
            owner.commit()
        # The daily chain: IBKR statement into silver, a benchmark run reading its raw file,
        # and a gold builder reading silver.
        with lineage.run("ibkrv2stg-institutional-daily") as institutional:
            institutional.append("ingestion_run_ids", ingestion_run)
            institutional.input("blob://ibkr-raw/t/2026/10/16/flex.xml", sha256=STATEMENT_SHA,
                                ingestion_run_id=ingestion_run)
            institutional.output("silver.nav_snapshots", rows=12)
            institutional.output("silver.position_snapshots", rows=30)
        with lineage.run("ibkrv2stg-benchmark-daily") as benchmark:
            benchmark.input("blob://bronze-market-data/yahoo-benchmark-history/2026/10/16/lti.csv", sha256=PRICES_SHA)
            benchmark.output("silver.benchmark_prices", rows=1)
        with lineage.run("ibkrv2stg-crisis-scenarios-daily") as crisis:
            session = self._job_session()
            try:
                crisis.bind(session)
                self._insert_scenario(session, "trace-a")
                crisis.output("gold.crisis_scenario_results", rows=1)
                found = lineage.trace(session, "crisis_scenario_results",
                                      {"subject_id": "trace-a", "holdings_as_of": "2026-10-16",
                                       "scenario_id": "gfc-2008", "calculation_version": "v1"})
            finally:
                session.close()
        # From inside the run: the row names it, but its datasets are stored when it ends.
        self.assertEqual(found["reference"], crisis.run_id)
        self.assertEqual([(step["run_id"], step["basis"], step["status"]) for step in found["runs"]],
                         [(crisis.run_id, "row", "STARTED")])

        # After the run ends the walk reaches the IBKR statement, here from a plain connection.
        with self._owner() as owner:
            found = lineage.trace(owner, "gold.crisis_scenario_results",
                                  {"subject_id": "trace-a", "holdings_as_of": "2026-10-16",
                                   "scenario_id": "gfc-2008", "calculation_version": "v1"})
            self.assertEqual([(step["run_id"], step["basis"], step["status"]) for step in found["runs"]],
                             [(crisis.run_id, "row", "COMPLETE"),
                              (institutional.run_id, "dataset-time", "COMPLETE")])
            self.assertIn("gold.crisis_scenario_results", [item["name"] for item in found["runs"][0]["outputs"]])
            self.assertEqual([(item["dataset"], item["sha256"]) for item in found["raw_files"]],
                             [("blob://ibkr-raw/t/2026/10/16/flex.xml", STATEMENT_SHA)])
            self.assertEqual([(row["run_id"], row["raw_sha256"]) for row in found["ibkr_statements"]],
                             [(ingestion_run, STATEMENT_SHA)])
            prices = lineage.trace(owner, "benchmark_prices", {"benchmark_id": "lineage-test-index",
                                                                "observation_date": "2026-10-16",
                                                                "observation_status": "provisional_close"})
            self.assertEqual([step["run_id"] for step in prices["runs"]][:1], [benchmark.run_id])
            self.assertEqual(prices["raw_files"][0]["dataset"],
                             "blob://bronze-market-data/yahoo-benchmark-history/2026/10/16/lti.csv")
            missing = lineage.trace(owner, "crisis_scenario_results",
                                    {"subject_id": "nobody", "holdings_as_of": "2026-10-16",
                                     "scenario_id": "gfc-2008", "calculation_version": "v1"})
            self.assertEqual((missing["found"], missing["note"]), (False, "no such row"))
            with self.assertRaises(ValueError):
                lineage.trace(owner, "silver.trades", {})
        # Another tenant sees neither the row nor the runs.
        with self._owner(OTHER_TENANT) as other:
            hidden = lineage.trace(other, "crisis_scenario_results",
                                   {"subject_id": "trace-a", "holdings_as_of": "2026-10-16",
                                    "scenario_id": "gfc-2008", "calculation_version": "v1"})
            self.assertFalse(hidden["found"])

    def test_the_command_line_prints_the_trace(self):
        with lineage.run("ibkrv2stg-crisis-scenarios-daily") as record:
            session = self._job_session()
            try:
                record.bind(session)
                self._insert_scenario(session, "cli-a")
            finally:
                session.close()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = lineage.main(["trace", "crisis_scenario_results", "subject_id=cli-a",
                                 "holdings_as_of=2026-10-16", "scenario_id=gfc-2008", "calculation_version=v1"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["runs"][0]["run_id"], record.run_id)

    def test_an_unreachable_database_never_fails_the_job(self):
        broken = self.url.replace(f"/{self.database}", "/no_such_database_for_lineage")
        with patch.dict(os.environ, {"DATABASE_URL": broken}), self.assertLogs("ibkr_etl.lineage", "WARNING"):
            self.assertEqual(lineage.run_main("ibkrv2stg-risk-free-daily", lambda: 0), 0)
        self.assertIsNotNone(lineage.disabled_reason())


if __name__ == "__main__":
    unittest.main()
