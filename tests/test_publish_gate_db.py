"""The publish gate on a real PostgreSQL, through the real benchmark writer.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Builds its own database with every migration in order, as a new
environment does, and writes as a non-superuser owner so forced row-level security applies
as on Azure. Synthetic data only; the benchmark prices come from a fake provider.
"""
from __future__ import annotations

import dataclasses
import io
import json
import os
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from ibkr_etl import crisis_scenarios, lineage, provisional_benchmark
from ibkr_etl import publish_gate as gate
from ibkr_etl.db import connect
from ibkr_etl.market_calendar import CALENDARS

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
TENANT = layers.TENANT
SUBJECT, OTHER = "U9100001", "U9100002"
START, END = date(2026, 8, 3), date(2026, 10, 16)
SESSIONS = [day for day in (START + timedelta(days=offset) for offset in range((END - START).days + 1))
            if CALENDARS["XNYS"].is_session(day) and CALENDARS["XNAS"].is_session(day)]
NAV = {day.isoformat(): 100000 * (1 + 0.001 * index) for index, day in enumerate(SESSIONS)}
LAST = SESSIONS[-1].isoformat()
SHA = "a" * 64


class Frame:
    """The part of a yfinance history frame the writer reads."""

    def __init__(self, rows):
        self.rows = rows

    def iterrows(self):
        return iter((day, {"Close": close}) for day, close in self.rows)


class Ticker:
    def __init__(self, symbol):
        self.base = 5000 if symbol == "^GSPC" else 16000

    def history(self, **request):
        return Frame([(day.isoformat(), self.base * (1 + 0.0005 * index)) for index, day in enumerate(SESSIONS)])


def _seed(owner) -> None:
    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
    run = owner.execute("INSERT INTO ingestion_runs (run_type, started_at, completed_at, status) VALUES "
                        "('staging-snapshot-import', '2026-10-17', '2026-10-17', 'success') RETURNING run_id"
                        ).fetchone()[0]
    owner.execute("INSERT INTO accounts (account_id, base_currency, first_seen_at, last_seen_at, source_json) "
                  "VALUES (%s, 'USD', '2026-08-01', '2026-10-17', '{}')", (SUBJECT,))
    owner.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, base_currency, "
                  "reporting_currency, history_start_date, lifecycle_status) VALUES (%s, 'physical_account', "
                  "'Synthetic', 'USD', 'USD', %s, 'active')", (SUBJECT, START.isoformat()))
    for day, value in NAV.items():
        owner.execute("INSERT INTO nav_snapshots (account_id, snapshot_date, currency, total_text, row_sha256, "
                      "last_seen_run_id) VALUES (%s, %s, 'USD', %s, md5(%s), %s)",
                      (SUBJECT, day, repr(value), day, run))
    # Positions plus cash equal the NAV on the latest day.
    owner.execute("INSERT INTO position_snapshots (account_id, snapshot_date, conid, currency, quantity_text, "
                  "market_value_text, row_sha256, last_seen_run_id) VALUES (%s, %s, '1001', 'USD', '10', %s, 'p', %s)",
                  (SUBJECT, LAST, repr(NAV[LAST] - 5000), run))
    owner.execute("INSERT INTO cash_snapshots (account_id, snapshot_date, currency, ending_cash_text, row_sha256, "
                  "last_seen_run_id) VALUES (%s, %s, 'USD', '5000', 'c', %s)", (SUBJECT, LAST, run))
    for benchmark, kind in (("GSPC", "primary"), ("IXIC", "secondary")):
        owner.execute("INSERT INTO portfolio_benchmark_mapping (subject_id, benchmark_id, mapping_type, "
                      "effective_from, reporting_currency, created_at) VALUES (%s, %s, %s, '2026-01-01', 'USD', "
                      "'2026-08-01')", (SUBJECT, benchmark, kind))
    owner.commit()


def _rows(result, change):
    return dataclasses.replace(result, rows=tuple(change(index, row) for index, row in enumerate(result.rows)))


# Ways to break the comparison the writer is about to store (GSPC, the primary, only).
def wrong_nav(result):
    return _rows(result, lambda i, row: dataclasses.replace(row, portfolio_value=row.portfolio_value * 1.01)
                 if i == 20 else row)


def wrong_excess(result):
    return _rows(result, lambda i, row: dataclasses.replace(row, excess_return=row.excess_return + 0.001)
                 if i == 20 else row)


def broken_chain(result):
    return _rows(result, lambda i, row: dataclasses.replace(row, normalized_portfolio=row.normalized_portfolio * 1.01)
                 if i == 20 else row)


def no_rows(result):
    return dataclasses.replace(result, rows=())


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class PublishGateDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = f"publish_gate_test_{uuid.uuid4().hex[:10]}"
        try:
            layers._build(cls.database)
            with layers._connect(cls.database) as owner:
                _seed(owner)
        except Exception:
            layers._drop(cls.database)
            raise
        cls.url = layers._url(cls.database, layers.OWNER)

    @classmethod
    def tearDownClass(cls):
        layers._drop(cls.database)

    def setUp(self):
        lineage.reset()
        self.environment = patch.dict(os.environ, {"PORTFOLIO_TENANT_ID": TENANT, gate.MODE_ENV: "off"})
        self.environment.start()
        os.environ.pop("LINEAGE_RECORDING", None)
        with layers._connect(self.database) as owner:
            for table in ("performance_comparison_daily", "crisis_scenario_results", "crisis_replay_daily",
                          "risk_analytics_snapshots", "benchmark_prices"):
                owner.execute(f"DELETE FROM {table}")
            owner.execute("UPDATE cash_snapshots SET ending_cash_text = '5000' WHERE account_id = %s", (SUBJECT,))

    def tearDown(self):
        self.environment.stop()
        lineage.reset()

    def _job(self):
        return connect(Path("/tmp/unused.sqlite3"), self.url, TENANT)

    def _write(self, mode, break_it=None, connection=None):
        """Run the real benchmark writer for SUBJECT; returns (result or BuildHeldBack, gate
        log records, whether the job's session was left outside a transaction)."""
        from psycopg import pq

        job = connection or self._job()
        original = provisional_benchmark.version_website_comparison

        def version(result):
            result = original(result)
            return break_it(result) if break_it and result.benchmark_id == "GSPC" else result

        stdout = io.StringIO()
        try:
            # The writer stamps its rows to the second; tests run faster than that.
            with patch.dict(os.environ, {gate.MODE_ENV: mode}), redirect_stdout(stdout), \
                    patch.object(provisional_benchmark, "version_website_comparison", version), \
                    patch.object(provisional_benchmark, "utc_now_iso",
                                 lambda: datetime.now(timezone.utc).isoformat()):
                try:
                    outcome = provisional_benchmark.refresh_and_materialize(
                        job, account_id=SUBJECT, start_date=START, end_date=END, ticker_factory=Ticker)
                except gate.BuildHeldBack as held:
                    outcome = held
            idle = job._connection.info.transaction_status == pq.TransactionStatus.IDLE
        finally:
            if connection is None:
                job.close()
        records = [json.loads(line.removeprefix(gate.LOG_PREFIX)) for line in stdout.getvalue().splitlines()
                   if line.startswith(gate.LOG_PREFIX)]
        return outcome, records, idle

    def _published(self):
        with layers._connect(self.database) as owner:
            return owner.execute("SELECT benchmark_id, observation_date, portfolio_value, normalized_portfolio, "
                                 "excess_return FROM performance_comparison_daily ORDER BY 1, 2").fetchall()

    @staticmethod
    def _checks(record, table=gate.COMPARISON):
        return {name: check["verdict"] for name, check in record["tables"][table]["checks"].items()}

    # The benchmark comparison, through the real writer -------------------------------------

    def test_off_writes_as_before_and_logs_nothing(self):
        outcome, records, idle = self._write("off")
        self.assertEqual((outcome["status"], outcome["comparison_rows"], records, idle), ("success", 108, [], True))
        self.assertEqual(len(self._published()), 108)

    def test_a_clean_version_passes_every_check_and_is_committed(self):
        for mode in ("shadow", "enforce"):
            with self.subTest(mode=mode):
                outcome, records, _ = self._write(mode)
                self.assertEqual(outcome["status"], "success")
                self.assertEqual(len(records), 1)
                record = records[0]
                self.assertEqual((record["verdict"], record["decision"], record["rows"], record["writer"]),
                                 ("pass", "publish", 108, "provisional_benchmark"))
                self.assertEqual(self._checks(record), {
                    "contract": "pass", "nav_matches_ibkr": "pass", "cash_plus_positions": "pass",
                    "returns_continuity": "pass", "benchmark_coverage": "pass"})
                self.assertEqual(record["tables"][gate.COMPARISON]["checks"]["contract"]["rules"], 8)
                self.assertLess(record["elapsed_ms"], 10000)
                self.assertEqual(len(self._published()), 108)

    def test_each_broken_version_fails_the_right_check(self):
        cases = ((wrong_nav, "nav_matches_ibkr", "differs from the books' NAV"),
                 (wrong_excess, "contract", "rule excess_is_difference (error) found 1 bad row(s)"),
                 (broken_chain, "returns_continuity", "the portfolio index does not chain with the return"),
                 (no_rows, "benchmark_coverage", "U9100001 has no primary comparison against GSPC"))
        for break_it, check, reason in cases:
            with self.subTest(check=check):
                outcome, records, _ = self._write("shadow", break_it)
                self.assertEqual(outcome["status"], "success")  # shadow never stops the writer
                record = records[0]
                self.assertEqual((record["verdict"], record["decision"]), ("fail", "publish"))
                failed = {name for name, verdict in self._checks(record).items() if verdict == "fail"}
                self.assertEqual(failed, {check})
                self.assertTrue(any(reason in text for text in record["reasons"]), record["reasons"])

    def test_enforce_holds_back_a_broken_version_and_keeps_the_last_good_one(self):
        self._write("off")
        good = self._published()
        for break_it in (wrong_nav, wrong_excess, broken_chain, no_rows):
            with self.subTest(broken=break_it.__name__):
                held, records, idle = self._write("enforce", break_it)
                self.assertIsInstance(held, gate.BuildHeldBack)
                self.assertEqual(records[0]["decision"], "hold_back")
                self.assertTrue(idle)  # rolled back, nothing left pending in the job's session
                self.assertEqual(self._published(), good)

    def test_warnings_never_hold_back(self):
        self._write("off")
        with layers._connect(self.database) as owner:
            # A row left from an earlier build inside the range (a Saturday), and a cash
            # balance that no longer ties to the NAV.
            owner.execute("INSERT INTO performance_comparison_daily (subject_id, benchmark_id, observation_date, "
                          "reporting_currency, portfolio_value, benchmark_level, normalized_portfolio, "
                          "normalized_benchmark, portfolio_return, benchmark_return, excess_return, "
                          "calculation_version, quality_status, generated_at) SELECT subject_id, benchmark_id, "
                          "'2026-09-05', reporting_currency, portfolio_value, benchmark_level, normalized_portfolio, "
                          "normalized_benchmark, portfolio_return, benchmark_return, excess_return, "
                          "calculation_version, quality_status, '2026-09-08T08:30:00+00:00' "
                          "FROM performance_comparison_daily WHERE benchmark_id = 'GSPC' "
                          "AND observation_date = '2026-09-04'")
            owner.execute("UPDATE cash_snapshots SET ending_cash_text = '1000' WHERE account_id = %s", (SUBJECT,))
        original = provisional_benchmark.fetch_benchmark_yfinance

        def secondary_unavailable(spec, *args, **kwargs):
            if spec.benchmark_id == "IXIC":
                raise RuntimeError("no new file")
            return original(spec, *args, **kwargs)

        with patch.object(provisional_benchmark, "fetch_benchmark_yfinance", secondary_unavailable):
            outcome, records, _ = self._write("enforce")
        self.assertEqual(outcome["status"], "success")
        record = records[0]
        self.assertEqual((record["verdict"], record["decision"]), ("warn", "publish"))
        self.assertEqual(self._checks(record), {
            "contract": "pass", "nav_matches_ibkr": "pass", "cash_plus_positions": "warn",
            "returns_continuity": "warn", "benchmark_coverage": "warn"})

    def test_only_the_new_version_is_judged(self):
        with layers._connect(self.database) as owner:
            # Another subject's broken row, written earlier: not this build.
            owner.execute("INSERT INTO performance_comparison_daily (subject_id, benchmark_id, observation_date, "
                          "reporting_currency, portfolio_value, benchmark_level, normalized_portfolio, "
                          "normalized_benchmark, portfolio_return, benchmark_return, excess_return, "
                          "calculation_version, quality_status, generated_at) VALUES (%s, 'GSPC', '2026-10-16', "
                          "'USD', 1, 1, 100, 100, 0.1, 0.1, 0.5, 'benchmark-comparison-v1', 'warn', "
                          "'2026-10-16T08:30:00+00:00')", (OTHER,))
        outcome, records, _ = self._write("enforce")
        self.assertEqual((records[0]["verdict"], records[0]["rows"]), ("pass", 108))

    def test_the_checks_leave_the_writers_transaction_as_it_was(self):
        job = self._job()
        try:
            job.execute("INSERT INTO crisis_scenario_results (subject_id, holdings_as_of, scenario_id, "
                        "calculation_version, reporting_currency, crisis_start, crisis_end, available, "
                        "maximum_drawdown, result_json, generated_at) VALUES (?, '2026-10-16', 'lehman_2008', "
                        "'v-test', 'USD', '2008-09-01', '2009-03-09', true, -0.3, '{}', 'x')", (SUBJECT,))
            with patch.dict(os.environ, {gate.MODE_ENV: "shadow"}):
                gate.before_commit(job, {"gold.crisis_scenario_results": {"subject_id": SUBJECT,
                                                                          "calculation_version": "v-test"}},
                                   writer="test", out=lambda text: None)
            settings = job.execute("SELECT current_setting('statement_timeout') AS timeout, "
                                   "current_setting('transaction_read_only') AS read_only").fetchone()
            self.assertEqual((settings["timeout"], settings["read_only"]), ("0", "off"))
            job.execute("UPDATE crisis_scenario_results SET generated_at = 'y' WHERE calculation_version = 'v-test'")
            job.commit()
        finally:
            job.close()
        with layers._connect(self.database) as owner:
            self.assertEqual(owner.execute("SELECT generated_at FROM crisis_scenario_results").fetchone()[0], "y")

    # The other gold tables, on rows written in a job session -------------------------------

    def _scenarios(self, job, holdings_as_of, *, broken=False):
        for scenario in ("lehman_2008", "china_2015", "covid_2020", "rates_2022"):
            available_without_drawdown = broken and scenario == "covid_2020"
            job.execute("INSERT INTO crisis_scenario_results (subject_id, holdings_as_of, scenario_id, "
                        "calculation_version, reporting_currency, crisis_start, crisis_end, available, "
                        "maximum_drawdown, result_json, generated_at) VALUES (?, ?, ?, ?, 'USD', '2020-02-19', "
                        "'2020-03-23', true, ?, ?::jsonb, '2026-10-19T09:20:00+00:00')",
                        (SUBJECT, holdings_as_of, scenario, crisis_scenarios.VERSION,
                         None if available_without_drawdown else -0.25,
                         json.dumps({"scenario_id": scenario, "available": True})))
        return {"gold.crisis_scenario_results": {"subject_id": SUBJECT, "holdings_as_of": holdings_as_of,
                                                 "calculation_version": crisis_scenarios.VERSION}}

    def test_a_held_back_crisis_scenario_version_leaves_the_last_good_one_on_the_page(self):
        job = self._job()
        try:
            self._scenarios(job, "2026-10-15")
            job.commit()
            builds = self._scenarios(job, "2026-10-16", broken=True)
            with patch.dict(os.environ, {gate.MODE_ENV: "enforce"}), self.assertRaises(gate.BuildHeldBack) as held:
                gate.before_commit(job, builds, writer="crisis_scenarios", out=lambda text: None)
            self.assertIn("rule drawdown_matches_availability (error) found 1 bad row(s)", str(held.exception))
            # The page's reader picks the latest holdings date on or before the one asked for.
            shown = crisis_scenarios.load(job, SUBJECT, as_of="2026-10-16", tenant_id=TENANT)
            self.assertEqual(shown["holdings_as_of"], "2026-10-15")
        finally:
            job.close()

    def test_risk_snapshots_and_the_crisis_replay_are_judged_but_never_held_back(self):
        self._write("off")
        job = self._job()
        try:
            for benchmark, verdict in (("GSPC", "pass"), ("HSI", "fail")):
                job.execute("DELETE FROM risk_analytics_snapshots")
                job.execute("INSERT INTO risk_analytics_snapshots (tenant_id, subject_id, as_of_date, "
                            "analytics_version, comparison_version, source_data_sha256, payload_sha256, payload_json, "
                            "quality_status, generated_at, duration_ms, timings_json) VALUES (?, ?, ?, "
                            "'risk-analytics-v2', 'benchmark-comparison-v1', ?, ?, ?::jsonb, 'warn', "
                            "'2026-10-19T08:55:00+00:00', 5, '{}')",
                            (TENANT, SUBJECT, LAST, SHA, SHA, json.dumps({"benchmark_id": benchmark})))
                with patch.dict(os.environ, {gate.MODE_ENV: "enforce"}):
                    record = gate.before_commit(job, {gate.RISK: {"subject_id": SUBJECT, "as_of_date": LAST,
                                                                  "analytics_version": "risk-analytics-v2"}},
                                                writer="risk_snapshots", out=lambda text: None)
                with self.subTest(benchmark=benchmark):
                    self.assertEqual((record["verdict"], record["decision"]), (verdict, "publish"))
            self.assertIn("exact date", record["not_held_back"])
            job.commit()
            for day, value in (("2008-09-01", 0.9), ("2008-09-02", 0.85)):
                job.execute("INSERT INTO crisis_replay_daily (subject_id, holdings_as_of, observation_date, "
                            "normalized_value, drawdown, crisis_start, crisis_end, calculation_version, "
                            "quality_status, generated_at) VALUES (?, ?, ?, ?, ?, '2008-09-01', '2009-03-09', 'v1', "
                            "'warn', 'x')", (SUBJECT, LAST, day, value, value - 0.9))
            partition = {"subject_id": SUBJECT, "holdings_as_of": LAST}
            with patch.dict(os.environ, {gate.MODE_ENV: "enforce"}):
                record = gate.before_commit(job, {"gold.crisis_replay_daily": partition,
                                                  "gold.crisis_replay_components": partition},
                                            writer="crisis_replay", out=lambda text: None)
            self.assertEqual((record["verdict"], record["decision"]), ("fail", "publish"))
            self.assertIn("gold.crisis_replay_daily: contract: rule starts_at_one (error) found 1 bad row(s)",
                          record["reasons"])
            job.commit()
        finally:
            job.close()

    # After a job: the lineage scope, month-end and the dry run --------------------------------

    def test_a_job_run_is_found_by_its_lineage_id(self):
        run_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc) - timedelta(seconds=1)
        job = self._job()
        try:
            job._connection.execute("SELECT set_config(%s, %s, false)", (lineage.SESSION_SETTING, run_id))
            job.commit()
            self._write("off", connection=job)
        finally:
            job.close()
        environ = {"DATABASE_URL": self.url, "PORTFOLIO_TENANT_ID": TENANT, "BENCHMARK_ACCOUNT_IDS": SUBJECT}
        verdict = gate.check("benchmark-daily", since=started, run_id=run_id, environ=environ)
        table = verdict["tables"][gate.COMPARISON]
        self.assertEqual((verdict["verdict"], table["basis"], table["rows"]), ("pass", "lineage_run_id", 108))
        other = gate.check("benchmark-daily", since=started, run_id=str(uuid.uuid4()), environ=environ)
        self.assertEqual(other["tables"][gate.COMPARISON]["rows"], 0)

    def test_month_end_is_reported_and_never_stopped(self):
        started = datetime.now(timezone.utc) - timedelta(seconds=1)
        with layers._connect(self.database) as owner:
            owner.execute("INSERT INTO reporting_snapshots (snapshot_id, subject_id, period_start, period_end, "
                          "as_of_at, reporting_currency, formula_version, source_manifest_json, data_sha256, "
                          "quality_status, status, created_at, snapshot_payload_json) VALUES (gen_random_uuid(), "
                          "%s, '2026-10-01', '2026-10-31', '2026-10-31', 'USD', 'v1', '{}', %s, 'pass', 'frozen', "
                          "%s, '{\"synthetic\": true}')", (SUBJECT, SHA, datetime.now(timezone.utc).isoformat()))
        lines = []
        environ = {"DATABASE_URL": self.url, "PORTFOLIO_TENANT_ID": TENANT, gate.MODE_ENV: "enforce"}
        record = gate.report("month-end-factsheet", since=started, environ=environ, out=lines.append)
        self.assertEqual((record["verdict"], record["decision"], len(lines)), ("fail", "report_only", 1))
        self.assertIn("gold.reporting_snapshots: contract: rule digest_matches_payload (error) found 1 bad row(s)",
                      record["reasons"])

    def test_the_dry_run_reads_every_job_without_error(self):
        self._write("off")
        with patch.dict(os.environ, {"DATABASE_URL": self.url}):
            for job in gate.JOB_TABLES:
                stdout = io.StringIO()
                with self.subTest(job=job), redirect_stdout(stdout):
                    self.assertEqual(gate.main([job, "--since", "2026-01-01"]), 0)
                verdict = json.loads(stdout.getvalue())
                errors = [reason for reason in verdict["reasons"] if "could not" in reason]
                self.assertEqual(errors, [], job)


if __name__ == "__main__":
    unittest.main()
