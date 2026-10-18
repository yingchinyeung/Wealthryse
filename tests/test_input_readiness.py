"""Input readiness: the decision logic with synthetic evidence, the three modes and the
job wiring. tests/test_input_readiness_db.py runs the queries against PostgreSQL."""
from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import input_readiness as readiness

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 19, 8, 58, tzinfo=timezone.utc)  # a Monday
# Each daily job after ingestion: its Bicep file and the script the job runs.
WIRED = {
    "benchmark-daily": ("infra/staging-benchmark-job.bicep", "scripts/refresh_provisional_benchmark.py"),
    "sector-classification": ("infra/staging-sector-classification-job.bicep",
                              "scripts/refresh_staging_sector_classifications.py"),
    "derived-daily": ("infra/staging-derived-refresh.bicep", "scripts/run_self_service_derived_refresh.py"),
    "risk-snapshots": ("infra/staging-risk-snapshot-job.bicep", "scripts/build_risk_snapshots.py"),
    "crisis-scenarios-daily": ("infra/staging-crisis-scenarios-job.bicep", "scripts/refresh_crisis_scenarios.py"),
    "month-end-factsheet": ("infra/staging-month-end-factsheet-job.bicep",
                            "scripts/run_registry_month_end_packages.py"),
}


def _row(completed_at, *, through=None, planned=1, succeeded=None, failed=0):
    return {"row": {"completed_at": completed_at, "through_date": through, "planned": planned,
                    "succeeded": planned - failed if succeeded is None else succeeded, "failed": failed}}


def healthy():
    """Synthetic evidence of a cycle that ran in order on Mon 19 Oct 2026."""
    return {
        "ingestion": _row("2026-10-19T08:22:00+00:00", through="2026-10-18"),
        "reconciliation": _row("2026-10-19T08:24:00+00:00", through="2026-10-18", planned=3),
        "benchmarks_fx": _row("2026-10-19T08:33:00+00:00"),
        "derived": _row("2026-10-19T08:52:00+00:00", planned=2),
        "risk": _row("2026-10-19T08:57:00+00:00", planned=5),
    }


class ModeTests(unittest.TestCase):
    def test_unset_and_off_words_are_off(self):
        for environ in ({}, {readiness.MODE_ENV: ""}, {readiness.MODE_ENV: " Off "},
                        {readiness.MODE_ENV: "false"}, {readiness.MODE_ENV: "0"}):
            with self.subTest(environ=environ):
                self.assertEqual(readiness.mode(environ), ("off", None))

    def test_shadow_and_enforce(self):
        self.assertEqual(readiness.mode({readiness.MODE_ENV: "SHADOW"}), ("shadow", None))
        self.assertEqual(readiness.mode({readiness.MODE_ENV: " enforce"}), ("enforce", None))

    def test_an_unknown_value_runs_as_shadow_and_says_so(self):
        selected, note = readiness.mode({readiness.MODE_ENV: "on"})
        self.assertEqual(selected, "shadow")
        self.assertIn("unknown", note)


class DecisionTests(unittest.TestCase):
    def test_every_wired_stage_is_ready_after_a_cycle_that_ran_in_order(self):
        for job, stage in readiness.JOB_STAGES.items():
            with self.subTest(job=job):
                decision = readiness.decide(stage, healthy(), now=NOW)
                self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
                self.assertEqual(set(decision["evidence"]), set(readiness.prerequisites(stage)))

    def test_ingestion_and_reconciliation_must_cover_yesterday_and_the_rest_today(self):
        decision = readiness.decide("reports", healthy(), now=NOW)
        expected = {name: detail["expected"] for name, detail in decision["evidence"].items()}
        self.assertEqual(expected, {"ingestion": "2026-10-18", "reconciliation": "2026-10-18",
                                    "benchmarks_fx": "2026-10-19", "derived": "2026-10-19", "risk": "2026-10-19"})
        self.assertRegex(decision["date_policy_sha256"], "^[0-9a-f]{64}$")

    def test_a_late_pull_leaves_yesterdays_statement_as_the_newest(self):
        evidence = healthy()
        evidence["ingestion"] = _row("2026-10-18T08:23:00+00:00", through="2026-10-17")
        evidence["reconciliation"] = _row("2026-10-18T08:25:00+00:00", through="2026-10-17", planned=3)
        decision = readiness.decide("benchmarks_fx", evidence, now=datetime(2026, 10, 19, 8, 30, tzinfo=timezone.utc))
        self.assertFalse(decision["ready"])
        self.assertEqual(decision["reasons"], ["ingestion: stale data", "reconciliation: stale data"])
        self.assertEqual(decision["evidence"]["ingestion"]["state"], "late")

    def test_a_pull_still_running_is_not_ready(self):
        evidence = healthy()
        evidence["ingestion"] = _row(None, through="2026-10-18", planned=2, succeeded=1)
        evidence["reconciliation"] = _row(None, through="2026-10-18", planned=3, succeeded=0)
        decision = readiness.decide("benchmarks_fx", evidence, now=NOW)
        # The policy names the nearest missing step; the log has every step's state.
        self.assertEqual(decision["reasons"], ["reconciliation: missing completion evidence"])
        self.assertEqual(decision["evidence"]["ingestion"]["state"], "in_progress")
        evidence["reconciliation"] = _row("2026-10-19T08:24:00+00:00", through="2026-10-18", planned=3)
        decision = readiness.decide("benchmarks_fx", evidence, now=NOW)
        self.assertEqual(decision["reasons"], ["ingestion: missing completion evidence"])

    def test_a_failed_step_is_not_validated(self):
        evidence = healthy()
        evidence["benchmarks_fx"] = _row("2026-10-19T08:34:00+00:00", failed=1)
        decision = readiness.decide("derived", evidence, now=NOW)
        self.assertEqual(decision["reasons"], ["benchmarks_fx: not validated"])

    def test_a_step_that_ran_before_the_step_it_reads_must_recalculate(self):
        evidence = healthy()
        evidence["benchmarks_fx"] = _row("2026-10-19T08:21:00+00:00")  # before reconciliation
        decision = readiness.decide("derived", evidence, now=NOW)
        self.assertEqual(decision["reasons"], ["benchmarks_fx: reconciliation changed; recalculate"])

    def test_yesterdays_derived_run_is_stale_for_risk(self):
        evidence = healthy()
        evidence["derived"] = _row("2026-10-18T08:52:00+00:00", planned=2)
        decision = readiness.decide("risk", evidence, now=NOW)
        self.assertIn("derived: stale data", decision["reasons"])
        self.assertFalse(decision["ready"])

    def test_missing_and_unreadable_evidence(self):
        evidence = healthy()
        evidence["risk"] = {"row": None}
        evidence["derived"] = {"error": "InsufficientPrivilege (42501)"}
        decision = readiness.decide("reports", evidence, now=NOW)
        self.assertEqual(decision["reasons"], ["derived: evidence unavailable (InsufficientPrivilege (42501))",
                                               "risk: missing completion evidence"])

    def test_a_future_completion_is_flagged(self):
        evidence = healthy()
        evidence["benchmarks_fx"] = _row("2026-10-19T10:00:00+00:00")
        decision = readiness.decide("derived", evidence, now=NOW)
        self.assertIn("benchmarks_fx: completion time is in the future", decision["reasons"])

    def test_assurance_has_no_reports_evidence_yet(self):
        decision = readiness.decide("assurance", healthy(), now=NOW)
        self.assertEqual(decision["evidence"]["reports"], {"state": "no_source"})
        self.assertEqual(decision["reasons"], ["reports: missing completion evidence"])

    def test_ledger_rows_when_present_must_agree(self):
        decision = readiness.decide("derived", healthy(), now=NOW,
                                    ledger={"rows": 1, "ready": False, "reasons": ["ingestion: not validated"]})
        self.assertEqual(decision["reasons"], ["ledger: ingestion: not validated"])
        self.assertTrue(readiness.decide("derived", healthy(), now=NOW, ledger={"rows": 0})["ready"])
        broken = readiness.decide("derived", healthy(), now=NOW, ledger={"rows": 0, "error": "UndefinedTable"})
        self.assertEqual(broken["reasons"], ["ledger: unavailable (UndefinedTable)"])


class FakeSession:
    def __init__(self):
        self.closed = False

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class CheckTests(unittest.TestCase):
    environ = {"DATABASE_URL": "postgresql://example.invalid/db", "PORTFOLIO_TENANT_ID": readiness.DEFAULT_TENANT_ID}

    def test_reads_through_one_session_and_closes_it(self):
        session = FakeSession()
        with patch.object(readiness, "observe", return_value=(healthy(), {"rows": 0})) as observe:
            decision = readiness.check("derived-daily", environ=self.environ, now=NOW,
                                       connect=lambda url, tenant: session)
        self.assertTrue(decision["ready"])
        self.assertTrue(session.closed)
        self.assertEqual(observe.call_args.kwargs["tenant_id"], readiness.DEFAULT_TENANT_ID)

    def test_a_connection_failure_is_a_reason_not_a_crash(self):
        def refuse(url, tenant):
            raise ConnectionError("refused")

        decision = readiness.check("derived-daily", environ=self.environ, now=NOW, connect=refuse)
        self.assertFalse(decision["ready"])
        self.assertIn("ingestion: evidence unavailable (ConnectionError)", decision["reasons"])

    def test_a_hung_database_is_cut_off_at_the_time_budget(self):
        started = time.monotonic()
        decision = readiness.check("derived-daily", environ=self.environ, now=NOW, time_budget_seconds=0.2,
                                   connect=lambda url, tenant: time.sleep(5))
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn("ingestion: evidence unavailable (TimeoutError (time budget of 0.2 seconds))",
                      decision["reasons"])

    def test_no_database_url(self):
        decision = readiness.check("derived-daily", environ={}, now=NOW)
        self.assertIn("ingestion: evidence unavailable (database not configured)", decision["reasons"])

    def test_an_unknown_job_is_a_reason(self):
        decision = readiness.check("nightly-misc", environ=self.environ, now=NOW)
        self.assertEqual(decision["ready"], False)
        self.assertIn("Unknown job or stage", decision["reasons"][0])


class GateTests(unittest.TestCase):
    def _gate(self, mode, evidence, **kwargs):
        lines = []
        environ = {**CheckTests.environ, readiness.MODE_ENV: mode}
        with patch.object(readiness, "observe", return_value=(evidence, {"rows": 0})):
            result = readiness.gate("derived-daily", environ=environ, now=NOW, out=lines.append,
                                    connect=lambda url, tenant: FakeSession(), **kwargs)
        return result, lines

    def test_off_does_nothing(self):
        def fail(*args, **kwargs):
            raise AssertionError("off must not measure anything")

        lines = []
        with patch.object(readiness, "check", side_effect=fail), \
                patch("threading.Thread", side_effect=fail):
            for environ in ({}, {readiness.MODE_ENV: "off"}, {"DATABASE_URL": "postgresql://x/y"}):
                self.assertIsNone(readiness.gate("derived-daily", environ=environ, out=lines.append))
        self.assertEqual(lines, [])

    def test_off_with_the_real_environment_prints_nothing(self):
        stdout = io.StringIO()
        with patch.dict(os.environ, {}, clear=False), redirect_stdout(stdout):
            os.environ.pop(readiness.MODE_ENV, None)
            self.assertIsNone(readiness.gate("derived-daily"))
        self.assertEqual(stdout.getvalue(), "")

    def test_off_imports_nothing_new(self):
        # A fresh interpreter, as a job starts: importing the module and passing the
        # gate with the variable unset loads no database driver or policy module.
        code = ("import sys; from ibkr_etl import input_readiness as r; before = set(sys.modules); "
                "result = r.gate('derived-daily'); print(sorted(set(sys.modules) - before), result)")
        environ = {key: value for key, value in os.environ.items() if key != readiness.MODE_ENV}
        environ["PYTHONPATH"] = str(ROOT / "src")
        output = subprocess.run([sys.executable, "-c", code], env=environ, capture_output=True, text=True,
                                check=True, timeout=60).stdout
        self.assertEqual(output.strip(), "[] None")

    def test_shadow_logs_one_line_and_always_continues(self):
        late = healthy()
        late["ingestion"] = _row("2026-10-18T08:23:00+00:00", through="2026-10-17")
        for evidence, ready in ((healthy(), True), (late, False)):
            with self.subTest(ready=ready):
                result, lines = self._gate("shadow", evidence)
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith("INPUT_READINESS {"))
                record = json.loads(lines[0].removeprefix(readiness.LOG_PREFIX))
                self.assertEqual((record["mode"], record["decision"], record["ready"]), ("shadow", "continue", ready))
                self.assertEqual((record["job"], record["stage"]), ("derived-daily", "derived"))
                self.assertEqual(result["ready"], ready)

    def test_shadow_survives_a_broken_check_and_a_broken_log(self):
        with patch.object(readiness, "decide", side_effect=RuntimeError("bug")):
            result, lines = self._gate("shadow", healthy())
        self.assertEqual(result["decision"], "continue")
        self.assertIn("check failed: RuntimeError: bug", result["reasons"])

        def broken(text):
            raise OSError("stdout closed")

        with patch.object(readiness, "observe", return_value=(healthy(), {"rows": 0})):
            result = readiness.gate("derived-daily", environ={**CheckTests.environ, readiness.MODE_ENV: "shadow"},
                                    now=NOW, out=broken, connect=lambda url, tenant: FakeSession())
        self.assertEqual(result["decision"], "continue")

    def test_enforce_stops_with_exit_75_when_not_ready(self):
        late = healthy()
        late["reconciliation"] = _row("2026-10-19T08:24:00+00:00", through="2026-10-18", planned=3, failed=1)
        lines = []
        environ = {**CheckTests.environ, readiness.MODE_ENV: "enforce"}
        with patch.object(readiness, "observe", return_value=(late, {"rows": 0})), \
                self.assertRaises(SystemExit) as stopped:
            readiness.gate("derived-daily", environ=environ, now=NOW, out=lines.append,
                           connect=lambda url, tenant: FakeSession())
        self.assertEqual(stopped.exception.code, 75)
        record = json.loads(lines[0].removeprefix(readiness.LOG_PREFIX))
        self.assertEqual((record["decision"], record["reasons"]), ("stop", ["reconciliation: not validated"]))

    def test_enforce_continues_when_ready(self):
        result, lines = self._gate("enforce", healthy())
        self.assertEqual((result["decision"], result["ready"]), ("continue", True))
        self.assertEqual(len(lines), 1)

    def test_enforce_stops_when_evidence_cannot_be_read(self):
        environ = {readiness.MODE_ENV: "enforce"}
        with self.assertRaises(SystemExit) as stopped:
            readiness.gate("derived-daily", environ=environ, now=NOW, out=lambda text: None)
        self.assertEqual(stopped.exception.code, readiness.EXIT_NOT_READY)


class WiringTests(unittest.TestCase):
    def test_each_daily_job_after_ingestion_checks_first(self):
        self.assertEqual(set(WIRED), set(readiness.JOB_STAGES))
        for job, (bicep, script) in WIRED.items():
            with self.subTest(job=job):
                # The job definition runs this script.
                self.assertIn(f"args: ['{script}']", (ROOT / bicep).read_text(encoding="utf-8"))
                tree = ast.parse((ROOT / script).read_text(encoding="utf-8"))
                main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
                first = main.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    first = main.body[1]  # a docstring
                self.assertEqual(ast.unparse(first), f"input_readiness.gate('{job}')")

    def test_nothing_switches_it_on(self):
        for path in (ROOT / "infra").rglob("*"):
            if path.is_file() and path.suffix in {".bicep", ".json"}:
                self.assertNotIn(readiness.MODE_ENV, path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
