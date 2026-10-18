"""Publish gate: the judges with synthetic evidence, the three modes, the hold-back and the
wiring. tests/test_publish_gate_db.py runs the checks against PostgreSQL."""
from __future__ import annotations

import ast
import inspect
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
import unittest
from contextlib import closing, contextmanager, redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import publish_gate as gate

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
TENANT = gate.DEFAULT_TENANT_ID
COMPARISON = gate.COMPARISON
SCENARIOS = "gold.crisis_scenario_results"
PARTITION = {COMPARISON: {"subject_id": "U9100001", "generated_at": "2026-10-19T08:30:00+00:00"}}


def clean_observation(table=COMPARISON):
    """What a good comparison version looks like when measured."""
    checks = {"contract": {"version": "1.1.0", "results": [
        {"rule": "excess_is_difference", "severity": "error", "must_be": 0, "count": 0}]}}
    if table == COMPARISON:
        checks.update({
            "nav_matches_ibkr": {"checked": 54, "problems": {}, "ambiguous_ibkr_total": {"dates": 0}},
            "cash_plus_positions": {"subjects": [{"subject_id": "U9100001", "date": "2026-10-16", "currency": "USD",
                                                  "nav": "1000", "positions": "900", "cash": "100"}]},
            "returns_continuity": {"series": 2, "problems": {}, "missing_days": {"days": 0},
                                   "left_over_rows": {"rows": 0}},
            "benchmark_coverage": {"comparisons": [
                {"subject_id": "U9100001", "benchmark_id": "GSPC", "mapping_type": "primary",
                 "latest_nav": "2026-10-16", "latest_comparison": "2026-10-16"}]},
        })
    if table == gate.RISK:
        checks["benchmark_coverage"] = {"snapshots": [{"subject_id": "U9100001", "as_of_date": "2026-10-16",
                                                       "benchmark_id": "GSPC", "latest_comparison": "2026-10-16"}]}
    return {"basis": "partition", "rows": 108, "checks": checks}


def broken_observation():
    observation = clean_observation()
    observation["checks"]["nav_matches_ibkr"]["problems"] = {"mismatch": {"rows": 1, "samples": []}}
    return observation


class FakeCursor:
    def __init__(self, rows=(), names=()):
        self.rows = list(rows)
        self.description = [type("Column", (), {"name": name})() for name in names]

    def fetchall(self):
        return self.rows


class FakeRaw:
    """A psycopg connection as the gate uses it: settings, savepoints and one query."""

    def __init__(self):
        self.info = object()
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if "current_setting('app.tenant_id'" in sql:
            return FakeCursor([(TENANT,)], ["current_setting"])
        return FakeCursor()

    @contextmanager
    def transaction(self):
        import psycopg

        self.statements.append("SAVEPOINT")
        try:
            yield
        except psycopg.Rollback:
            self.statements.append("ROLLBACK TO SAVEPOINT")


class FakeJobConnection:
    """db.PostgresConnection around FakeRaw."""

    def __init__(self):
        self._connection = FakeRaw()
        self.rolled_back = 0

    def rollback(self):
        self.rolled_back += 1

    def commit(self):
        raise AssertionError("the gate never commits")


class ModeTests(unittest.TestCase):
    def test_unset_and_off_words_are_off(self):
        for environ in ({}, {gate.MODE_ENV: ""}, {gate.MODE_ENV: " Off "}, {gate.MODE_ENV: "false"},
                        {gate.MODE_ENV: "0"}):
            with self.subTest(environ=environ):
                self.assertEqual(gate.mode(environ), ("off", None))

    def test_shadow_and_enforce(self):
        self.assertEqual(gate.mode({gate.MODE_ENV: "SHADOW"}), ("shadow", None))
        self.assertEqual(gate.mode({gate.MODE_ENV: " enforce"}), ("enforce", None))

    def test_an_unknown_value_runs_as_shadow_and_says_so(self):
        self.assertEqual(gate.mode({gate.MODE_ENV: "hold"}), ("shadow", "unknown PUBLISH_GATE_MODE value; ran as shadow"))


class RulesTests(unittest.TestCase):
    def test_the_shipped_rules_match_the_contracts(self):
        expected = gate.contract_rules(ROOT / "infra" / "medallion" / "contracts")
        shipped = json.loads(gate.RULES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(shipped, expected, "run python -m ibkr_etl.publish_gate --write-rules")

    def test_every_gold_table_has_rules_and_each_rule_can_be_scoped(self):
        rules = gate.load_rules()
        self.assertEqual(set(rules), set(gate.TABLES))
        for name, contract in rules.items():
            self.assertTrue(contract["rules"], name)
            for rule in contract["rules"]:
                with self.subTest(table=name, rule=rule["name"]):
                    self.assertIn("(SELECT * FROM " + name + " WHERE x)", gate.scoped(rule["query"], name, "x"))

    def test_each_job_names_known_tables(self):
        for job, tables in gate.JOB_TABLES.items():
            self.assertTrue(set(tables) <= set(gate.TABLES), job)
        self.assertEqual(set().union(*gate.JOB_TABLES.values()), set(gate.TABLES))


class ScopedTests(unittest.TestCase):
    def test_each_reference_is_narrowed_and_keeps_or_gains_an_alias(self):
        query = ("SELECT COUNT(*) FROM gold.crisis_replay_components c WHERE NOT EXISTS (SELECT 1 FROM "
                 "gold.crisis_replay_daily d WHERE d.subject_id = c.subject_id)")
        self.assertEqual(
            gate.scoped(query, "gold.crisis_replay_components", "k = 1"),
            "SELECT COUNT(*) FROM (SELECT * FROM gold.crisis_replay_components WHERE k = 1) c WHERE NOT EXISTS "
            "(SELECT 1 FROM gold.crisis_replay_daily d WHERE d.subject_id = c.subject_id)")
        self.assertEqual(gate.scoped("SELECT COUNT(*) FROM gold.risk_analytics_snapshots WHERE x > 0",
                                     gate.RISK, "k = 1"),
                         "SELECT COUNT(*) FROM (SELECT * FROM gold.risk_analytics_snapshots WHERE k = 1) AS "
                         "risk_analytics_snapshots WHERE x > 0")
        self.assertEqual(gate.scoped("SELECT 1 FROM gold.risk_analytics_snapshots", gate.RISK, "k"),
                         "SELECT 1 FROM (SELECT * FROM gold.risk_analytics_snapshots WHERE k) AS "
                         "risk_analytics_snapshots")

    def test_a_longer_name_is_not_a_reference(self):
        with self.assertRaises(ValueError):
            gate.scoped("SELECT 1 FROM gold.crisis_replay_daily_old", "gold.crisis_replay_daily", "k")


class ScopeTests(unittest.TestCase):
    def test_a_partition_picks_the_new_version(self):
        scope = gate.Scope(partition=(("subject_id", "U9100001"), ("generated_at", "2026-10-19T08:30:00+00:00")))
        self.assertEqual(scope.condition(gate.TABLES[COMPARISON], "v"),
                         ("partition", "v.subject_id = %(p0)s AND v.generated_at = %(p1)s",
                          {"p0": "U9100001", "p1": "2026-10-19T08:30:00+00:00"}))
        self.assertEqual(scope.describe(), {"partition": {"subject_id": "U9100001",
                                                          "generated_at": "2026-10-19T08:30:00+00:00"}})
        with self.assertRaises(ValueError):
            gate.Scope(partition=(("subject_id; DROP", "x"),)).condition(gate.TABLES[COMPARISON])

    def test_a_lineage_id_is_used_only_where_the_table_has_it(self):
        scope = gate.Scope(since=NOW, run_id="0b7e7d1c-0000-4000-8000-000000000001")
        self.assertEqual(scope.condition(gate.TABLES[gate.RISK])[0], "lineage_run_id")
        basis, sql, params = scope.condition(gate.TABLES["gold.crisis_replay_components"])
        self.assertEqual(basis, "written_since")
        self.assertIn("fetched_at", sql)
        self.assertEqual(params, {"since": NOW})

    def test_without_a_start_time_there_is_no_scope(self):
        with self.assertRaises(ValueError):
            gate.Scope().condition(gate.TABLES["gold.reporting_snapshots"])


class JudgeTests(unittest.TestCase):
    def test_contract(self):
        def results(*items):
            return {"version": "1.1.0", "results": list(items)}

        ok = {"rule": "a", "severity": "error", "must_be": 0, "count": 0}
        self.assertEqual(gate.judge_contract(results(ok))["verdict"], "pass")
        self.assertEqual(gate.judge_contract(results(ok, {**ok, "count": 2}))["verdict"], "fail")
        self.assertEqual(gate.judge_contract(results({**ok, "severity": "warning", "count": 1}))["verdict"], "warn")
        unavailable = gate.judge_contract(results({**ok, "error": "QueryCanceled"}))
        self.assertEqual(unavailable["verdict"], "warn")
        self.assertIn("could not run", unavailable["reasons"][0])
        self.assertEqual(gate.judge_contract(results())["verdict"], "skipped")

    def test_nav(self):
        def nav(problems=None, ambiguous=0, checked=10):
            return gate.judge_nav({"checked": checked, "problems": problems or {},
                                   "ambiguous_ibkr_total": {"dates": ambiguous}})

        self.assertEqual(nav()["verdict"], "pass")
        for problem, verdict in (("missing", "fail"), ("mismatch", "fail"), ("ambiguous", "warn"),
                                 ("unreadable", "warn")):
            with self.subTest(problem=problem):
                self.assertEqual(nav({problem: {"rows": 1}})["verdict"], verdict)
        # The v_daily_nav overlap: IBKR sent TOTAL with ALL or blank on a day. Only a warning.
        self.assertEqual(nav(ambiguous=2)["verdict"], "warn")
        self.assertEqual(nav(checked=0)["verdict"], "skipped")

    def test_holdings_never_fail(self):
        def holdings(**item):
            return gate.judge_holdings({"subjects": [{"subject_id": "U9100001", "date": "2026-10-16",
                                                      "currency": "USD", **item}]})

        self.assertEqual(holdings(nav="1000", positions="900", cash="99.5")["verdict"], "pass")  # within 1 unit
        self.assertEqual(holdings(nav="100000", positions="90000", cash="9500")["verdict"], "pass")  # within 1%
        self.assertEqual(holdings(nav="100000", positions="90000", cash="8000")["verdict"], "warn")
        self.assertEqual(holdings(error="QueryCanceled")["verdict"], "warn")
        self.assertEqual(holdings(reason="The books have no cash balances on 2026-10-16.")["verdict"], "skipped")
        self.assertEqual(holdings(not_applicable="composite")["verdict"], "skipped")

    def test_continuity(self):
        base = {"series": 2, "problems": {}, "missing_days": {"days": 0}, "left_over_rows": {"rows": 0}}
        self.assertEqual(gate.judge_continuity(base)["verdict"], "pass")
        for problem in gate.CONTINUITY_FAILURES:
            with self.subTest(problem=problem):
                self.assertEqual(gate.judge_continuity({**base, "problems": {problem: {"rows": 1}}})["verdict"],
                                 "fail")
        self.assertEqual(gate.judge_continuity({**base, "missing_days": {"days": 3}})["verdict"], "warn")
        self.assertEqual(gate.judge_continuity({**base, "left_over_rows": {"rows": 1}})["verdict"], "warn")
        noted = gate.judge_continuity({**base, "problems": {"long_gap": {"rows": 1}}})
        self.assertEqual((noted["verdict"], noted["long_gaps"]), ("pass", 1))
        self.assertEqual(gate.judge_continuity({**base, "series": 0})["verdict"], "skipped")

    def test_coverage_fails_only_without_a_primary(self):
        def comparison(mapping_type, latest, nav="2026-10-16"):
            return {"subject_id": "U9100001", "benchmark_id": "X", "mapping_type": mapping_type,
                    "latest_nav": nav, "latest_comparison": latest}

        def judge(*items):
            return gate.judge_coverage({"comparisons": list(items)})["verdict"]

        self.assertEqual(judge(comparison("primary", "2026-10-16"), comparison("secondary", "2026-10-15")), "pass")
        self.assertEqual(judge(comparison("primary", None)), "fail")
        self.assertEqual(judge(comparison("primary", "2026-10-16"), comparison("secondary", None)), "warn")
        # A long holiday (Golden Week, Lunar New Year): the writers hold the primary to its
        # exchange calendar, so a day count only warns.
        self.assertEqual(judge(comparison("primary", "2026-10-01", nav="2026-10-09")), "warn")
        self.assertEqual(judge(comparison("primary", None, nav=None)), "skipped")  # no NAV yet: onboarding

    def test_risk_coverage(self):
        def judge(benchmark, latest, as_of="2026-10-16"):
            return gate.judge_coverage({"snapshots": [{"subject_id": "U9100001", "as_of_date": as_of,
                                                       "benchmark_id": benchmark, "latest_comparison": latest}]})

        self.assertEqual(judge("GSPC", "2026-10-16")["verdict"], "pass")
        self.assertEqual(judge("GSPC", None)["verdict"], "fail")
        self.assertEqual(judge("HSI", "2026-10-09")["verdict"], "warn")
        self.assertEqual(judge(None, None)["verdict"], "skipped")  # HKD/HIBOR snapshots have no benchmark

    def test_a_check_that_could_not_run_warns_and_a_table_fails_on_any_failure(self):
        table = gate.TABLES[COMPARISON]
        self.assertEqual(gate.judge_table(table, clean_observation())["verdict"], "pass")
        self.assertEqual(gate.judge_table(table, broken_observation())["verdict"], "fail")
        partial = clean_observation()
        del partial["checks"]["returns_continuity"]
        self.assertEqual(gate.judge_table(table, partial)["verdict"], "warn")
        self.assertEqual(gate.judge_table(table, {"error": "TimeoutError"})["verdict"], "warn")
        with patch.dict(gate.JUDGES, {"nav_matches_ibkr": lambda evidence: 1 / 0}):
            self.assertEqual(gate.judge_table(table, clean_observation())["checks"]["nav_matches_ibkr"]["verdict"],
                             "unavailable")

    def test_decide(self):
        decision = gate.decide("risk-snapshots", {gate.RISK: clean_observation(gate.RISK)})
        self.assertEqual((decision["verdict"], decision["rows"], decision["policy_version"]),
                         ("pass", 108, gate.POLICY_VERSION))
        self.assertEqual(gate.decide("benchmark-daily", {})["verdict"], "warn")  # nothing measured
        with self.assertRaises(ValueError):
            gate.decide("nightly-misc", {})


class ToleranceTests(unittest.TestCase):
    """The gate's tolerances are the ones the code already uses for the same figures."""

    def test_build_comparison_thresholds(self):
        from ibkr_etl.benchmark_analytics import build_comparison

        defaults = inspect.signature(build_comparison).parameters
        self.assertEqual(defaults["maximum_absolute_portfolio_return"].default, gate.MAXIMUM_PORTFOLIO_RETURN)
        self.assertEqual(defaults["maximum_absolute_benchmark_return"].default, gate.MAXIMUM_BENCHMARK_RETURN)
        self.assertEqual(defaults["maximum_staleness_days"].default, gate.STALENESS_DAYS)
        self.assertIn("days > 7", inspect.getsource(build_comparison))
        self.assertEqual(gate.GAP_DAYS, 7)

    def test_source_rounding_and_holdings_tie(self):
        from ibkr_etl import comparison_source_evidence
        from ibkr_etl.export_agent import HOLDINGS_TIE_TOLERANCE

        self.assertIn('max(Decimal("0.00000001"), abs(expected) * Decimal("0.0000000001"))',
                      inspect.getsource(comparison_source_evidence))
        self.assertEqual(Decimal("0.00000001"), Decimal(str(gate.ROUNDING_ABSOLUTE)))
        self.assertEqual(Decimal("0.0000000001"), Decimal(str(gate.ROUNDING_RELATIVE)))
        self.assertEqual(HOLDINGS_TIE_TOLERANCE, Decimal("0.01"))

    def test_number_reads_what_float_reads(self):
        import re

        for text in ("12", "-12.50", "12.", ".5", "+1e3", " 7 ", "1E-4"):
            with self.subTest(text=text):
                self.assertTrue(re.match(gate.NUMBER, text))
                float(text)
        for text in ("", "abc", "1,000", "nan", "inf", "1.2.3"):
            with self.subTest(text=text):
                self.assertIsNone(re.match(gate.NUMBER, text))


class BeforeCommitTests(unittest.TestCase):
    def _run(self, mode, observation, *, builds=PARTITION, rollback=True, **kwargs):
        connection, lines = FakeJobConnection(), []
        with patch.object(gate, "_measure", return_value=observation) as measure:
            try:
                result = gate.before_commit(connection, builds, writer="test", rollback=rollback,
                                            environ={gate.MODE_ENV: mode}, out=lines.append, now=NOW, **kwargs)
            except gate.BuildHeldBack as held:
                result = held
        return result, connection, lines, measure

    def test_off_touches_nothing(self):
        connection, lines = FakeJobConnection(), []
        for environ in ({}, {gate.MODE_ENV: "off"}, {"DATABASE_URL": "postgresql://x/y"}):
            self.assertIsNone(gate.before_commit(connection, PARTITION, writer="test", environ=environ,
                                                 out=lines.append))
        self.assertEqual((connection._connection.statements, connection.rolled_back, lines), ([], 0, []))

    def test_off_with_the_real_environment_prints_nothing(self):
        stdout = io.StringIO()
        with patch.dict(os.environ, {}, clear=False), redirect_stdout(stdout):
            os.environ.pop(gate.MODE_ENV, None)
            self.assertIsNone(gate.before_commit(object(), PARTITION, writer="test"))
            self.assertIsNone(gate.report("month-end-factsheet"))
        self.assertEqual(stdout.getvalue(), "")

    def test_off_imports_nothing_new(self):
        # A fresh interpreter, as a job starts: importing a writer and passing the gate with
        # the variable unset loads no database driver or other module.
        code = ("import sys; from ibkr_etl import publish_gate as g; before = set(sys.modules); "
                "a = g.before_commit(object(), {}, writer='x'); b = g.report('month-end-factsheet'); "
                "print(sorted(set(sys.modules) - before), a, b)")
        environ = {key: value for key, value in os.environ.items() if key != gate.MODE_ENV}
        environ["PYTHONPATH"] = str(ROOT / "src")
        output = subprocess.run([sys.executable, "-c", code], env=environ, capture_output=True, text=True,
                                check=True, timeout=60).stdout
        self.assertEqual(output.strip(), "[] None None")

    def test_shadow_logs_one_line_and_lets_the_writer_commit(self):
        for observation, verdict in ((clean_observation(), "pass"), (broken_observation(), "fail")):
            with self.subTest(verdict=verdict):
                result, connection, lines, measure = self._run("shadow", observation)
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith("PUBLISH_GATE {"))
                record = json.loads(lines[0].removeprefix(gate.LOG_PREFIX))
                self.assertEqual((record["mode"], record["decision"], record["verdict"], record["writer"]),
                                 ("shadow", "publish", verdict, "test"))
                self.assertEqual(record["scope"], PARTITION)
                self.assertEqual(result["verdict"], verdict)
                self.assertEqual(connection.rolled_back, 0)
                # Measured inside a savepoint that is rolled back, read only, with timeouts.
                statements = connection._connection.statements
                self.assertEqual(statements[0], "SAVEPOINT")
                self.assertEqual(statements[-1], "ROLLBACK TO SAVEPOINT")
                self.assertIn("SET LOCAL transaction_read_only = on", statements)
                scope = measure.call_args.args[3]
                self.assertEqual(scope.partition, tuple(PARTITION[COMPARISON].items()))
                self.assertEqual(measure.call_args.kwargs["tenant_id"], TENANT)

    def test_enforce_holds_back_a_failing_version(self):
        held, connection, lines, _ = self._run("enforce", broken_observation())
        self.assertIsInstance(held, gate.BuildHeldBack)
        self.assertIn("1 row(s) where the NAV differs from the books' NAV", str(held))
        self.assertEqual(held.verdict["decision"], "hold_back")
        self.assertEqual(connection.rolled_back, 1)
        self.assertEqual(json.loads(lines[0].removeprefix(gate.LOG_PREFIX))["decision"], "hold_back")

    def test_enforce_inside_a_callers_savepoint_leaves_the_rollback_to_it(self):
        held, connection, _, _ = self._run("enforce", broken_observation(), rollback=False)
        self.assertIsInstance(held, gate.BuildHeldBack)
        self.assertEqual(connection.rolled_back, 0)

    def test_enforce_publishes_a_passing_or_warning_version(self):
        warning = clean_observation()
        warning["checks"]["returns_continuity"]["left_over_rows"] = {"rows": 2}
        for observation, verdict in ((clean_observation(), "pass"), (warning, "warn")):
            with self.subTest(verdict=verdict):
                result, connection, lines, _ = self._run("enforce", observation)
                self.assertEqual((result["verdict"], result["decision"]), (verdict, "publish"))
                self.assertEqual(connection.rolled_back, 0)

    def test_enforce_never_holds_back_a_version_readers_cannot_fall_back_from(self):
        builds = {gate.RISK: {"subject_id": "U9100001", "as_of_date": "2026-10-16",
                              "analytics_version": "risk-analytics-v2"}}
        observation = clean_observation(gate.RISK)
        observation["checks"]["contract"]["results"][0]["count"] = 1
        result, connection, _, _ = self._run("enforce", observation, builds=builds)
        self.assertEqual((result["verdict"], result["decision"]), ("fail", "publish"))
        self.assertIn("exact date", result["not_held_back"])
        self.assertEqual(connection.rolled_back, 0)
        # Quant analytics readers fall back to the latest earlier date, so a held-back day is safe.
        self.assertEqual({name for name, table in gate.TABLES.items() if table.holds_back},
                         {COMPARISON, SCENARIOS, gate.QUANT})

    def test_a_slow_or_broken_check_never_holds_back(self):
        result, connection, _, measure = self._run("enforce", clean_observation(), time_budget_seconds=0)
        self.assertEqual((result["verdict"], result["decision"]), ("warn", "publish"))
        self.assertIn("TimeoutError", result["reasons"][0])
        measure.assert_not_called()
        with patch.object(gate, "_measure", side_effect=RuntimeError("bug")):
            result = gate.before_commit(FakeJobConnection(), PARTITION, writer="test",
                                        environ={gate.MODE_ENV: "enforce"}, out=lambda text: None)
        self.assertEqual((result["verdict"], result["decision"]), ("warn", "publish"))
        with patch.object(gate, "examine", side_effect=RuntimeError("bug")):
            result = gate.before_commit(FakeJobConnection(), PARTITION, writer="test",
                                        environ={gate.MODE_ENV: "enforce"}, out=lambda text: None)
        self.assertEqual(result["decision"], "publish")

    def test_a_broken_log_and_a_non_postgres_connection(self):
        def broken(text):
            raise OSError("stdout closed")

        with patch.object(gate, "_measure", return_value=clean_observation()):
            result = gate.before_commit(FakeJobConnection(), PARTITION, writer="test",
                                        environ={gate.MODE_ENV: "shadow"}, out=broken)
        self.assertEqual(result["decision"], "publish")
        with closing(sqlite3.connect(":memory:")) as local:
            result = gate.before_commit(local, PARTITION, writer="test", environ={gate.MODE_ENV: "enforce"},
                                        out=lambda text: None)
        self.assertEqual((result["verdict"], result["decision"]), ("warn", "publish"))
        self.assertIn("not a PostgreSQL connection", result["reasons"][0])

    def test_an_unknown_table_is_a_reason(self):
        result = gate.before_commit(FakeJobConnection(), {"gold.nothing": {"subject_id": "x"}}, writer="test",
                                    environ={gate.MODE_ENV: "enforce"}, out=lambda text: None)
        self.assertEqual(result["decision"], "publish")
        self.assertIn("not a gold table the gate knows", result["reasons"][0])

    def test_the_job_is_named_when_running_inside_a_lineage_run(self):
        from ibkr_etl import lineage

        with patch.dict(os.environ, {"LINEAGE_RECORDING": ""}), lineage.run("ibkrv2stg-benchmark-daily"):
            result, _, lines, _ = self._run("shadow", clean_observation())
        self.assertEqual(result["job"], "ibkrv2stg-benchmark-daily")


class FakeSession:
    def __init__(self):
        self.closed = False

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class ReportTests(unittest.TestCase):
    environ = {"DATABASE_URL": "postgresql://example.invalid/db", "PORTFOLIO_TENANT_ID": TENANT}

    def test_reads_through_one_session_and_closes_it(self):
        session = FakeSession()

        def observe(session, job, scope, *, results, **kwargs):
            results[gate.RISK] = clean_observation(gate.RISK)

        with patch.object(gate, "observe", side_effect=observe):
            verdict = gate.check("risk-snapshots", since=NOW, environ=self.environ, now=NOW,
                                 connect=lambda url, tenant: session)
        self.assertEqual(verdict["verdict"], "pass")
        self.assertTrue(session.closed)

    def test_a_connection_failure_or_hung_database_warns(self):
        def refuse(url, tenant):
            raise ConnectionError("refused")

        verdict = gate.check("risk-snapshots", since=NOW, environ=self.environ, now=NOW, connect=refuse)
        self.assertEqual(verdict["verdict"], "warn")
        self.assertIn("could not be checked (ConnectionError)", verdict["reasons"][0])
        started = time.monotonic()
        verdict = gate.check("risk-snapshots", since=NOW, environ=self.environ, now=NOW, time_budget_seconds=0.2,
                             connect=lambda url, tenant: time.sleep(5))
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn("TimeoutError (time budget of 0.2 seconds)", verdict["reasons"][0])

    def test_no_database_and_an_unknown_job(self):
        self.assertIn("database not configured", gate.check("risk-snapshots", since=NOW, environ={})["reasons"][0])
        self.assertIn("Unknown job", gate.check("nightly-misc", since=NOW, environ=self.environ)["reasons"][0])

    def test_report_logs_one_line_and_never_stops_even_in_enforce(self):
        failing = {"verdict": "fail", "reasons": ["gold.reporting_snapshots: contract: bad"]}
        for mode in ("shadow", "enforce"):
            with self.subTest(mode=mode):
                lines = []
                with patch.object(gate, "check", return_value=dict(failing)):
                    result = gate.report("month-end-factsheet", since=NOW, environ={gate.MODE_ENV: mode},
                                         out=lines.append)
                self.assertEqual((result["decision"], len(lines)), ("report_only", 1))
        with patch.object(gate, "check", side_effect=RuntimeError("bug")):
            self.assertEqual(gate.report("month-end-factsheet", environ={gate.MODE_ENV: "enforce"},
                                         out=lambda text: None)["decision"], "report_only")

    def test_dry_run_prints_the_verdict(self):
        stdout = io.StringIO()
        with patch.object(gate, "check", return_value={"verdict": "pass"}) as check, redirect_stdout(stdout):
            self.assertEqual(gate.main(["risk-snapshots", "--since", "2026-10-19T08:00:00"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"verdict": "pass"})
        self.assertEqual(check.call_args.kwargs["since"], datetime(2026, 10, 19, 8, tzinfo=timezone.utc))


class WiringTests(unittest.TestCase):
    @staticmethod
    def _function(path, name):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)

    def _gated_commits(self, function):
        """Each commit() in the function, and whether the statement before it is the gate."""
        found = []
        for node in ast.walk(function):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or isinstance(node, ast.If) and ast.unparse(node.test) == "commit":
                continue
            for previous, statement in zip([None, *body], body):
                text = ast.unparse(statement)
                if text in ("connection.commit()", "if commit:\n    connection.commit()"):
                    found.append(previous is not None and "before_commit(" in ast.unparse(previous))
        return found

    def test_each_writer_calls_the_gate_right_before_it_commits_new_gold(self):
        self.assertEqual(self._gated_commits(self._function("src/ibkr_etl/provisional_benchmark.py",
                                                            "_refresh_and_materialize")), [True])
        self.assertEqual(self._gated_commits(self._function("src/ibkr_etl/crisis_scenarios.py", "refresh")), [True])
        # The risk writer also commits when nothing changed; only the write is gated.
        self.assertEqual(sorted(self._gated_commits(self._function("src/ibkr_etl/risk_snapshots.py",
                                                                   "build_risk_snapshot"))), [False, True])
        for name in ("refresh_crisis_replay", "refresh_composite_crisis_replay"):
            self.assertEqual(self._gated_commits(self._function("src/ibkr_etl/crisis_replay.py", name)), [True])

    def test_benchmark_daily_carries_on_past_a_held_back_subject(self):
        main = ast.unparse(self._function("scripts/refresh_provisional_benchmark.py", "main"))
        self.assertIn("except publish_gate.BuildHeldBack", main)
        self.assertIn("publish_gate.EXIT_REJECTED if held_back else 0", main)

    def test_month_end_only_reports_after_readiness(self):
        bicep = (ROOT / "infra" / "staging-month-end-factsheet-job.bicep").read_text(encoding="utf-8")
        self.assertIn("args: ['scripts/run_registry_month_end_packages.py']", bicep)
        main = self._function("scripts/run_registry_month_end_packages.py", "main")
        self.assertEqual(ast.unparse(main.body[0]), "input_readiness.gate('month-end-factsheet')")
        self.assertEqual(ast.unparse(main.body[1]), "started = datetime.now(timezone.utc)")
        text = ast.unparse(main)
        self.assertIn("publish_gate.report('month-end-factsheet', since=started)", text)
        self.assertNotIn("before_commit", text)

    def test_nothing_switches_it_on(self):
        for path in (ROOT / "infra").rglob("*"):
            if path.is_file() and path.suffix in {".bicep", ".json"}:
                self.assertNotIn(gate.MODE_ENV, path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
