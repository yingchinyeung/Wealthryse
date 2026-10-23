"""Input readiness: the decision logic with synthetic evidence, the three modes, the
enforce wait and the job wiring. tests/test_input_readiness_db.py runs the queries
against PostgreSQL."""
from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import re
import time
import types
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
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
    "analyst-assurance": ("infra/staging-portfolio-analyst-assurance.bicep",
                          "scripts/run_registry_portfolio_assurance.py"),
}


def _row(completed_at, *, through=None, planned=1, succeeded=None, failed=0):
    return {"row": {"completed_at": completed_at, "through_date": through, "planned": planned,
                    "succeeded": planned - failed if succeeded is None else succeeded, "failed": failed}}


RISK_JOB, SECTOR_JOB = "ibkrv2stg-risk-snapshots", "ibkrv2stg-sector-classification"
REPORTS_JOB = "ibkrv2stg-month-end-factsheet"
LOADS_JOB = "ibkrv2stg-institutional-daily"


def _run(started, ended=None, *, end=None, exit_code=None, error=None):
    """A job's newest lineage run, as LINEAGE_RUN_SQL returns it."""
    return {"run_id": "00000000-0000-4000-8000-000000000001", "started_at": started, "ended_at": ended,
            "end_type": end or ("COMPLETE" if ended else None), "exit_code": exit_code, "error_type": error}


def _with_runs(evidence, **runs):
    names = {"risk": RISK_JOB, "sectors": SECTOR_JOB, "reports": REPORTS_JOB, "loads": LOADS_JOB}
    return {**evidence, readiness.LINEAGE_KEY: {"runs": {names[key]: run for key, run in runs.items()}}}


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

    def test_assurance_without_a_reports_record_still_checks_the_steps_before(self):
        decision = readiness.decide("assurance", healthy(), now=NOW)
        self.assertEqual((decision["ready"], decision["evidence"]["reports"]["state"]), (True, "not_checked"))
        self.assertIn("reports: ibkrv2stg-month-end-factsheet lineage not read; reports not checked",
                      decision["notes"])
        late = healthy()
        late["risk"] = _row("2026-10-18T08:57:00+00:00", planned=5)
        stale = readiness.decide("assurance", late, now=NOW)
        self.assertFalse(stale["ready"])
        self.assertIn("risk: stale data", stale["reasons"])

    def test_ledger_rows_when_present_must_agree(self):
        decision = readiness.decide("derived", healthy(), now=NOW,
                                    ledger={"rows": 1, "ready": False, "reasons": ["ingestion: not validated"]})
        self.assertEqual(decision["reasons"], ["ledger: ingestion: not validated"])
        self.assertTrue(readiness.decide("derived", healthy(), now=NOW, ledger={"rows": 0})["ready"])
        broken = readiness.decide("derived", healthy(), now=NOW, ledger={"rows": 0, "error": "UndefinedTable"})
        self.assertEqual(broken["reasons"], ["ledger: unavailable (UndefinedTable)"])


class NotLiveSubjectTests(unittest.TestCase):
    """Case 1: a derived subject that is not live yet does not hold up risk and reports."""

    def _derived(self, **counts):
        evidence = healthy()
        evidence["derived"] = {"row": {"completed_at": "2026-10-19T08:52:00+00:00", "through_date": None,
                                       **counts}}
        return evidence

    def test_a_failed_subject_that_is_not_live_is_waiting_not_counted(self):
        evidence = self._derived(planned=2, succeeded=2, failed=0, waiting=1, waiting_subjects=["SYN-ONBOARDING"])
        decision = readiness.decide("reports", evidence, now=NOW)
        self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
        derived = decision["evidence"]["derived"]
        self.assertEqual((derived["status"], derived["waiting"], derived["waiting_subjects"]),
                         ("succeeded", 1, ["SYN-ONBOARDING"]))
        self.assertIn("derived: 1 subject(s) not live (onboarding or not active) failed or blocked; "
                      "waiting, not counted", decision["notes"])

    def test_only_subjects_that_are_not_live_leaves_nothing_to_wait_for(self):
        evidence = self._derived(planned=0, succeeded=0, failed=0, waiting=2, waiting_subjects=["A", "B"])
        decision = readiness.decide("risk", evidence, now=NOW)
        self.assertEqual((decision["ready"], decision["evidence"]["derived"]["status"]), (True, "succeeded"))

    def test_a_live_subject_that_failed_or_was_blocked_still_fails(self):
        evidence = self._derived(planned=2, succeeded=1, failed=1, waiting=1, waiting_subjects=["SYN-ONBOARDING"])
        decision = readiness.decide("risk", evidence, now=NOW)
        self.assertEqual(decision["reasons"], ["derived: not validated"])

    def test_the_query_counts_only_live_subjects(self):
        sql = readiness.EVIDENCE["derived"][2]
        self.assertIn("lifecycle_status IS DISTINCT FROM 'active'", sql)
        self.assertIn("count(*) FILTER (WHERE NOT waiting AND status IN ('failed', 'blocked')) AS failed", sql)


class RiskRunTests(unittest.TestCase):
    """Case 2: the risk job's lineage run decides whether it has finished."""
    at = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)

    def test_a_risk_run_with_a_start_and_no_end_is_not_ready(self):
        # Stamps from the run in progress look fresh; the open run says it is not done.
        evidence = _with_runs(healthy(), risk=_run("2026-10-19T08:55:00+00:00"))
        decision = readiness.decide("reports", evidence, now=self.at)
        self.assertFalse(decision["ready"])
        self.assertEqual(decision["reasons"], [
            "risk: ibkrv2stg-risk-snapshots is still running (started 2026-10-19T08:55:00+00:00)",
            "risk: missing completion evidence"])
        risk = decision["evidence"]["risk"]
        self.assertEqual((risk["state"], risk["status"], risk["run"]["state"]), ("in_progress", "running", "running"))

    def test_a_finished_risk_run_is_ready_and_dated_by_its_end(self):
        evidence = _with_runs(healthy(), risk=_run("2026-10-19T08:55:00+00:00", "2026-10-19T08:58:30+00:00"))
        decision = readiness.decide("reports", evidence, now=self.at)
        self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
        self.assertEqual(decision["evidence"]["risk"]["completed_at"], "2026-10-19T08:58:30+00:00")

    def test_a_risk_run_that_crashed_or_was_stopped_is_not_validated(self):
        for error, end in (("RuntimeError", "FAIL"), ("SystemExit", "FAIL"), (None, "ABORT")):
            with self.subTest(error=error, end=end):
                run = _run("2026-10-19T08:55:00+00:00", "2026-10-19T08:56:00+00:00", end=end, error=error)
                decision = readiness.decide("reports", _with_runs(healthy(), risk=run), now=self.at)
                self.assertIn("risk: not validated", decision["reasons"])
                self.assertIn(f"risk: ibkrv2stg-risk-snapshots did not finish ({error or end})", decision["reasons"])

    def test_a_non_zero_return_is_finished_and_the_snapshots_judge_it(self):
        run = _run("2026-10-19T08:55:00+00:00", "2026-10-19T08:58:00+00:00", end="FAIL", exit_code="1")
        decision = readiness.decide("reports", _with_runs(healthy(), risk=run), now=self.at)
        self.assertEqual((decision["ready"], decision["evidence"]["risk"]["run"]["state"]), (True, "finished"))
        blocked = healthy()
        blocked["risk"] = _row("2026-10-19T08:57:00+00:00", planned=5, failed=1)
        decision = readiness.decide("reports", _with_runs(blocked, risk=run), now=self.at)
        self.assertEqual(decision["reasons"], ["risk: not validated"])

    def test_without_lineage_the_snapshots_alone_decide_and_a_note_says_so(self):
        cases = {
            "lineage not read": healthy(),
            "lineage unavailable (lineage_events not present or not readable)": {
                **healthy(), readiness.LINEAGE_KEY: {"error": "lineage_events not present or not readable"}},
            "no run recorded": _with_runs(healthy(), risk=None),
            "newest run ended before this cycle": _with_runs(
                healthy(), risk=_run("2026-10-18T08:55:00+00:00", "2026-10-18T08:58:00+00:00")),
            "no end recorded within 3600 seconds": _with_runs(healthy(), risk=_run("2026-10-19T07:59:00+00:00")),
        }
        for why, evidence in cases.items():
            with self.subTest(why=why):
                decision = readiness.decide("reports", evidence, now=self.at)
                self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
                self.assertIn(f"risk: ibkrv2stg-risk-snapshots {why}; judged by "
                              "public.risk_analytics_snapshots alone", decision["notes"])
                self.assertEqual(decision["evidence"]["risk"]["completed_at"], "2026-10-19T08:57:00+00:00")

    def test_an_unreadable_run_is_not_used(self):
        evidence = _with_runs(healthy(), risk=_run("not a time"))
        decision = readiness.decide("reports", evidence, now=self.at)
        self.assertTrue(decision["ready"])
        self.assertIn("unreadable record", " ".join(decision["notes"]))


class SectorRunTests(unittest.TestCase):
    """Case 3: sector classification's lineage run is part of benchmarks and FX."""

    def test_sectors_still_running_hold_up_derived(self):
        evidence = _with_runs(healthy(), sectors=_run("2026-10-19T08:45:00+00:00"))
        decision = readiness.decide("derived", evidence, now=datetime(2026, 10, 19, 8, 50, tzinfo=timezone.utc))
        self.assertEqual(decision["reasons"], [
            "benchmarks_fx: ibkrv2stg-sector-classification is still running (started 2026-10-19T08:45:00+00:00)",
            "benchmarks_fx: missing completion evidence"])
        self.assertEqual(decision["evidence"]["benchmarks_fx"]["sectors"]["state"], "running")

    def test_finished_sectors_are_ready(self):
        evidence = _with_runs(healthy(), sectors=_run("2026-10-19T08:45:00+00:00", "2026-10-19T08:48:00+00:00"))
        for stage in ("derived", "risk", "reports"):
            with self.subTest(stage=stage):
                decision = readiness.decide(stage, evidence, now=NOW)
                self.assertEqual((decision["ready"], decision["reasons"]), (True, []))

    def test_derived_that_finished_before_sectors_ended_is_only_a_note(self):
        evidence = _with_runs(healthy(), sectors=_run("2026-10-19T08:45:00+00:00", "2026-10-19T08:53:00+00:00"))
        decision = readiness.decide("risk", evidence, now=NOW)
        self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
        self.assertIn("derived: finished before ibkrv2stg-sector-classification ended; it used the earlier sector "
                      "classifications", decision["notes"])

    def test_sectors_that_ended_before_the_pull_are_only_a_note(self):
        evidence = _with_runs(healthy(), sectors=_run("2026-10-19T08:20:00+00:00", "2026-10-19T08:23:00+00:00"))
        decision = readiness.decide("derived", evidence, now=NOW)
        self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
        self.assertIn("benchmarks_fx: ibkrv2stg-sector-classification ended before reconciliation; it classified "
                      "the earlier positions", decision["notes"])

    def test_sectors_that_failed_or_never_ended_never_stop_anything(self):
        crashed = _run("2026-10-19T08:45:00+00:00", "2026-10-19T08:46:00+00:00", end="FAIL", error="RuntimeError")
        aborted = _run("2026-10-19T08:45:00+00:00", "2026-10-19T08:46:00+00:00", end="ABORT")
        lost = _run("2026-10-19T07:40:00+00:00")  # no end after its 1800 s replicaTimeout
        for run, why in ((crashed, "did not finish (RuntimeError)"), (aborted, "did not finish (ABORT)"),
                         (lost, "no end recorded within 1800 seconds")):
            for stage in ("derived", "risk", "reports"):
                with self.subTest(why=why, stage=stage):
                    decision = readiness.decide(stage, _with_runs(healthy(), sectors=run), now=NOW)
                    self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
                    self.assertIn(f"benchmarks_fx: ibkrv2stg-sector-classification {why}; sectors not checked",
                                  decision["notes"])

    def test_unclassified_holdings_exit_2_count_as_finished(self):
        run = _run("2026-10-19T08:45:00+00:00", "2026-10-19T08:48:00+00:00", end="FAIL", exit_code="2")
        decision = readiness.decide("derived", _with_runs(healthy(), sectors=run), now=NOW)
        self.assertEqual((decision["ready"], decision["evidence"]["benchmarks_fx"]["sectors"]["exit_code"]),
                         (True, "2"))

    def test_without_a_record_for_this_cycle_sectors_are_not_checked(self):
        for evidence in (healthy(), _with_runs(healthy(), sectors=None),
                         _with_runs(healthy(), sectors=_run("2026-10-18T08:45:00+00:00", "2026-10-18T08:48:00+00:00"))):
            decision = readiness.decide("derived", evidence, now=NOW)
            self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
            self.assertTrue(any(note.startswith("benchmarks_fx: ibkrv2stg-sector-classification")
                                and note.endswith("sectors not checked") for note in decision["notes"]))

    def test_stages_that_do_not_read_benchmarks_read_no_sector_lineage(self):
        loads = _run("2026-10-19T08:15:00+00:00", "2026-10-19T08:34:00+00:00")
        decision = readiness.decide("benchmarks_fx", _with_runs(healthy(), loads=loads), now=NOW)
        self.assertEqual(decision["notes"], [])


class AccountLoadTests(unittest.TestCase):
    """institutional-daily loads the Kiwoom accounts after the IBKR work items, so its
    open run holds benchmark-daily and everything after it, but never fails them."""
    at = datetime(2026, 10, 19, 8, 31, tzinfo=timezone.utc)

    def test_an_open_institutional_run_holds_the_stages_after_reconciliation(self):
        evidence = _with_runs(healthy(), loads=_run("2026-10-19T08:15:00+00:00"))
        evidence["benchmarks_fx"] = _row("2026-10-18T08:33:00+00:00")
        decision = readiness.decide("benchmarks_fx", evidence, now=self.at)
        self.assertFalse(decision["ready"])
        self.assertIn("reconciliation: ibkrv2stg-institutional-daily is still running "
                      "(started 2026-10-19T08:15:00+00:00)", decision["reasons"])
        self.assertEqual(readiness.open_runs(decision), [LOADS_JOB])
        for stage in ("derived", "risk"):
            with self.subTest(stage=stage):
                held = readiness.decide(stage, _with_runs(healthy(), loads=_run("2026-10-19T08:15:00+00:00")),
                                        now=NOW)
                self.assertFalse(held["ready"])

    def test_after_the_wait_an_open_run_is_only_a_note(self):
        evidence = _with_runs(healthy(), loads=_run("2026-10-19T08:15:00+00:00"))
        decision = readiness.decide("benchmarks_fx", evidence, now=self.at, hold_open_runs=False)
        self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
        self.assertIn("reconciliation: ibkrv2stg-institutional-daily still running after the wait (started "
                      "2026-10-19T08:15:00+00:00); loads not checked", decision["notes"])

    def test_a_run_that_just_ended_holds_for_the_next_step(self):
        # The IBKR worker's run ended 30 seconds ago; the Kiwoom loads' START may follow.
        evidence = _with_runs(healthy(), loads=_run("2026-10-19T08:15:00+00:00", "2026-10-19T08:30:30+00:00"))
        decision = readiness.decide("benchmarks_fx", evidence, now=self.at)
        self.assertFalse(decision["ready"])
        self.assertIn("reconciliation: ibkrv2stg-institutional-daily ended at 2026-10-19T08:30:30+00:00; "
                      "waiting 120 seconds for its next step", decision["reasons"])
        self.assertEqual(readiness.open_runs(decision), [LOADS_JOB])
        later = readiness.decide("benchmarks_fx", evidence, now=self.at + timedelta(seconds=91))
        self.assertEqual((later["ready"], later["reasons"]), (True, []))
        final = readiness.decide("benchmarks_fx", evidence, now=self.at, hold_open_runs=False)
        self.assertEqual((final["ready"], final["reasons"]), (True, []))

    def test_a_finished_or_failed_run_never_stops_anything(self):
        finished = _run("2026-10-19T08:15:00+00:00", "2026-10-19T08:29:00+00:00")
        crashed = _run("2026-10-19T08:15:00+00:00", "2026-10-19T08:29:00+00:00", end="FAIL", error="RuntimeError")
        for run in (finished, crashed):
            with self.subTest(end=run["end_type"]):
                decision = readiness.decide("benchmarks_fx", _with_runs(healthy(), loads=run), now=self.at)
                self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
        decision = readiness.decide("benchmarks_fx", _with_runs(healthy(), loads=crashed), now=self.at)
        self.assertIn("reconciliation: ibkrv2stg-institutional-daily did not finish (RuntimeError); loads not checked",
                      decision["notes"])


class OpenRunTests(unittest.TestCase):
    """A START with no end only delays: judged without it, the tables decide."""
    at = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)

    def test_without_holding_open_runs_the_tables_decide_and_a_note_says_so(self):
        evidence = _with_runs(healthy(), risk=_run("2026-10-19T08:55:00+00:00"),
                              sectors=_run("2026-10-19T08:45:00+00:00"))
        held = readiness.decide("reports", evidence, now=self.at)
        self.assertFalse(held["ready"])
        self.assertEqual(readiness.open_runs(held), [RISK_JOB, SECTOR_JOB])
        decision = readiness.decide("reports", evidence, now=self.at, hold_open_runs=False)
        self.assertEqual((decision["ready"], decision["reasons"], readiness.open_runs(decision)), (True, [], []))
        self.assertIn("risk: ibkrv2stg-risk-snapshots still running after the wait (started "
                      "2026-10-19T08:55:00+00:00); judged by public.risk_analytics_snapshots alone", decision["notes"])
        self.assertIn("benchmarks_fx: ibkrv2stg-sector-classification still running after the wait (started "
                      "2026-10-19T08:45:00+00:00); sectors not checked", decision["notes"])

    def test_tables_that_are_not_done_still_stop(self):
        evidence = _with_runs(healthy(), risk=_run("2026-10-19T08:55:00+00:00"))
        evidence["risk"] = _row("2026-10-18T08:57:00+00:00", planned=5)
        decision = readiness.decide("reports", evidence, now=self.at, hold_open_runs=False)
        self.assertFalse(decision["ready"])
        self.assertIn("risk: stale data", decision["reasons"])

    def test_a_run_that_ended_in_failure_keeps_stopping(self):
        run = _run("2026-10-19T08:55:00+00:00", "2026-10-19T08:56:00+00:00", end="FAIL", error="RuntimeError")
        decision = readiness.decide("reports", _with_runs(healthy(), risk=run), now=self.at, hold_open_runs=False)
        self.assertIn("risk: not validated", decision["reasons"])


class ReportsRunTests(unittest.TestCase):
    """Reports are judged by month-end-factsheet's lineage run alone."""
    at = datetime(2026, 10, 19, 9, 10, tzinfo=timezone.utc)

    def test_a_finished_month_end_run_lets_assurance_start(self):
        for run in (_run("2026-10-19T09:00:00+00:00", "2026-10-19T09:04:00+00:00"),
                    _run("2026-10-19T09:00:00+00:00", "2026-10-19T09:04:00+00:00", end="FAIL", exit_code="1")):
            with self.subTest(end=run["end_type"]):
                decision = readiness.decide("assurance", _with_runs(healthy(), reports=run), now=self.at)
                self.assertEqual((decision["ready"], decision["reasons"]), (True, []))
                reports = decision["evidence"]["reports"]
                self.assertEqual((reports["status"], reports["completed_at"]),
                                 ("succeeded", "2026-10-19T09:04:00+00:00"))

    def test_a_month_end_run_still_running_or_crashed_is_not_ready(self):
        running = _with_runs(healthy(), reports=_run("2026-10-19T09:00:00+00:00"))
        self.assertEqual(readiness.decide("assurance", running, now=self.at)["reasons"], [
            "reports: ibkrv2stg-month-end-factsheet is still running (started 2026-10-19T09:00:00+00:00)",
            "reports: missing completion evidence"])
        self.assertTrue(readiness.decide("assurance", running, now=self.at, hold_open_runs=False)["ready"])
        crashed = _run("2026-10-19T09:00:00+00:00", "2026-10-19T09:01:00+00:00", end="FAIL", error="RuntimeError")
        decision = readiness.decide("assurance", _with_runs(healthy(), reports=crashed), now=self.at)
        self.assertEqual(decision["reasons"], ["reports: ibkrv2stg-month-end-factsheet did not finish (RuntimeError)",
                                               "reports: not validated"])

    def test_a_month_end_run_that_ended_before_risk_must_recalculate(self):
        run = _run("2026-10-19T08:50:00+00:00", "2026-10-19T08:52:00+00:00")
        decision = readiness.decide("assurance", _with_runs(healthy(), reports=run), now=self.at)
        self.assertEqual(decision["reasons"], ["reports: risk changed; recalculate"])


class LineageSettingsTests(unittest.TestCase):
    BICEP = {SECTOR_JOB: "infra/staging-sector-classification-job.bicep",
             RISK_JOB: "infra/staging-risk-snapshot-job.bicep",
             REPORTS_JOB: "infra/staging-month-end-factsheet-job.bicep",
             LOADS_JOB: "infra/staging-institutional-daily.bicep"}

    def test_each_time_limit_is_the_jobs_replica_timeout(self):
        for stage, jobs in readiness.LINEAGE_JOBS.items():
            for key, job, limit in jobs:
                with self.subTest(job=job):
                    text = (ROOT / self.BICEP[job]).read_text(encoding="utf-8")
                    self.assertIn(f"param jobName string = '{job}'", text)
                    self.assertEqual(int(re.search(r"replicaTimeout: (\d+)", text).group(1)), limit)

    def test_job_names_and_namespace_are_the_ones_lineage_records(self):
        from ibkr_etl import lineage

        self.assertEqual(readiness.LINEAGE_NAMESPACE, lineage.NAMESPACE)
        recorded = set()
        for path in (ROOT / "scripts").glob("*.py"):
            recorded |= set(re.findall(r'lineage\.run_main\("([^"]+)"', path.read_text(encoding="utf-8")))
        for jobs in readiness.LINEAGE_JOBS.values():
            for _, job, _ in jobs:
                self.assertIn(job, recorded)


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


class WaitTests(unittest.TestCase):
    """Case 4: enforce waits and checks again before it stops; off and shadow never wait."""

    def setUp(self):
        self.late = healthy()
        self.late["reconciliation"] = _row("2026-10-19T08:24:00+00:00", through="2026-10-18", planned=3, succeeded=2)
        self.sleeps = []

    def _gate(self, mode, sequence, **settings):
        lines = []
        environ = {**CheckTests.environ, readiness.MODE_ENV: mode, **settings}
        results = iter(sequence)
        last = [None]

        def observe(*args, **kwargs):
            last[0] = next(results, last[0])
            return last[0], {"rows": 0}

        with patch.object(readiness, "observe", side_effect=observe) as observed:
            try:
                result = readiness.gate("derived-daily", environ=environ, now=NOW, out=lines.append,
                                        sleep=self.sleeps.append, connect=lambda url, tenant: FakeSession())
            except SystemExit as stopped:
                result = stopped
        return result, [json.loads(line.removeprefix(readiness.LOG_PREFIX)) for line in lines], observed.call_count

    def test_the_wait_settings(self):
        cases = {
            (): (0.0, 60.0, []),
            (("600", "30"),): (600.0, 30.0, []),
            (("600", "1"),): (600.0, 5.0, []),
            (("10m", ""),): (0.0, 60.0, ["INPUT_READINESS_WAIT_SECONDS not understood; no wait"]),
            (("-5", ""),): (0.0, 60.0, ["INPUT_READINESS_WAIT_SECONDS not understood; no wait"]),
            (("nan", ""),): (0.0, 60.0, ["INPUT_READINESS_WAIT_SECONDS not understood; no wait"]),
            (("99999", ""),): (1800.0, 60.0, ["INPUT_READINESS_WAIT_SECONDS above 1800; waits 1800 seconds"]),
            (("300", "soon"),): (300.0, 60.0, ["INPUT_READINESS_RECHECK_SECONDS not understood; "
                                                 "rechecks every 60 seconds"]),
        }
        for values, expected in cases.items():
            environ = {} if not values else {readiness.WAIT_ENV: values[0][0], readiness.RECHECK_ENV: values[0][1]}
            with self.subTest(environ=environ):
                self.assertEqual(readiness.wait_policy(environ), expected)

    def test_enforce_waits_and_continues_when_the_evidence_arrives(self):
        result, records, checks = self._gate("enforce", [self.late, self.late, healthy()],
                                             INPUT_READINESS_WAIT_SECONDS="600", INPUT_READINESS_RECHECK_SECONDS="60")
        self.assertEqual(self.sleeps, [60.0, 60.0])
        self.assertEqual(checks, 3)
        self.assertEqual([record["decision"] for record in records], ["wait", "continue"])
        self.assertEqual((records[0]["wait_seconds"], records[0]["recheck_seconds"], records[0]["ready"]),
                         (600.0, 60.0, False))
        final = records[-1]
        self.assertEqual((final["ready"], final["attempts"], final["waited_seconds"]), (True, 3, 120.0))
        self.assertEqual(final["first_reasons"], ["reconciliation: missing completion evidence"])
        self.assertEqual(final["checked_at"], (NOW + timedelta(seconds=120)).isoformat())
        self.assertEqual(result["decision"], "continue")

    def test_enforce_stops_when_the_wait_is_over(self):
        result, records, checks = self._gate("enforce", [self.late],
                                             INPUT_READINESS_WAIT_SECONDS="150", INPUT_READINESS_RECHECK_SECONDS="60")
        self.assertEqual(result.code, readiness.EXIT_NOT_READY)
        self.assertEqual(self.sleeps, [60.0, 60.0, 30.0])
        self.assertEqual(checks, 4)
        self.assertEqual([record["decision"] for record in records], ["wait", "stop"])
        self.assertEqual((records[-1]["attempts"], records[-1]["waited_seconds"]), (4, 150.0))

    def test_the_wait_also_ends_on_the_clock_when_checks_are_slow(self):
        clock = [0.0]

        def slow_sleep(seconds):
            self.sleeps.append(seconds)
            clock[0] += seconds * 5  # each pause and check took far longer than planned

        fake_time = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=slow_sleep)
        lines = []
        environ = {**CheckTests.environ, readiness.MODE_ENV: "enforce", readiness.WAIT_ENV: "300",
                   readiness.RECHECK_ENV: "60"}
        with patch.object(readiness, "monotonic_time", fake_time), \
                patch.object(readiness, "observe", return_value=(self.late, {"rows": 0})), \
                self.assertRaises(SystemExit):
            readiness.gate("derived-daily", environ=environ, now=NOW, out=lines.append,
                           connect=lambda url, tenant: FakeSession())
        self.assertEqual(self.sleeps, [60.0])

    def test_enforce_ready_at_once_does_not_wait(self):
        result, records, checks = self._gate("enforce", [healthy()], INPUT_READINESS_WAIT_SECONDS="600")
        self.assertEqual((self.sleeps, checks, len(records)), ([], 1, 1))
        self.assertEqual((result["attempts"], result["waited_seconds"]), (1, 0.0))
        self.assertNotIn("first_reasons", result)

    def test_enforce_without_a_wait_stops_at_once_as_before(self):
        for settings in ({}, {"INPUT_READINESS_WAIT_SECONDS": "0"}, {"INPUT_READINESS_WAIT_SECONDS": "ten"}):
            with self.subTest(settings=settings):
                self.sleeps.clear()
                result, records, checks = self._gate("enforce", [self.late], **settings)
                self.assertEqual(result.code, readiness.EXIT_NOT_READY)
                self.assertEqual((self.sleeps, checks, [r["decision"] for r in records]), ([], 1, ["stop"]))
        self.assertIn("INPUT_READINESS_WAIT_SECONDS not understood; no wait", records[0]["notes"])

    def test_shadow_never_waits(self):
        result, records, checks = self._gate("shadow", [self.late], INPUT_READINESS_WAIT_SECONDS="600")
        self.assertEqual((self.sleeps, checks, len(records)), ([], 1, 1))
        self.assertEqual((result["decision"], result["ready"]), ("continue", False))
        self.assertNotIn("attempts", result)

    def test_an_open_run_only_delays_then_the_tables_decide(self):
        running = _with_runs(healthy(), risk=_run("2026-10-19T08:55:00+00:00"))
        lines = []
        environ = {**CheckTests.environ, readiness.MODE_ENV: "enforce", readiness.WAIT_ENV: "120",
                   readiness.RECHECK_ENV: "60"}
        with patch.object(readiness, "observe", return_value=(running, {"rows": 0})) as observed:
            result = readiness.gate("month-end-factsheet", environ=environ, now=NOW, out=lines.append,
                                    sleep=self.sleeps.append, connect=lambda url, tenant: FakeSession())
        records = [json.loads(line.removeprefix(readiness.LOG_PREFIX)) for line in lines]
        self.assertEqual(self.sleeps, [60.0, 60.0])
        self.assertEqual(observed.call_count, 4)  # the first check, two rechecks, one without the open run
        self.assertEqual([record["decision"] for record in records], ["wait", "continue"])
        self.assertEqual((result["ready"], result["reasons"], result["attempts"]), (True, [], 4))
        self.assertIn("still running after the wait", " ".join(result["notes"]))
        self.assertIn("risk: ibkrv2stg-risk-snapshots is still running (started 2026-10-19T08:55:00+00:00)",
                      result["first_reasons"])

    def test_without_a_wait_an_open_run_does_not_stop_either(self):
        running = _with_runs(healthy(), sectors=_run("2026-10-19T08:45:00+00:00"))
        result, records, checks = self._gate("enforce", [running])
        self.assertEqual((result["decision"], result["ready"], checks, self.sleeps), ("continue", True, 2, []))
        self.assertEqual(len(records), 1)

    def test_an_open_run_with_other_reasons_still_stops(self):
        late = _with_runs(self.late, sectors=_run("2026-10-19T08:45:00+00:00"))
        result, records, checks = self._gate("enforce", [late])
        self.assertEqual((result.code, checks), (readiness.EXIT_NOT_READY, 2))
        self.assertEqual(records[-1]["reasons"], ["reconciliation: missing completion evidence"])

    def test_shadow_logs_the_open_run_as_not_ready_and_checks_once(self):
        running = _with_runs(healthy(), risk=_run("2026-10-19T08:55:00+00:00"))
        lines = []
        with patch.object(readiness, "observe", return_value=(running, {"rows": 0})) as observed:
            result = readiness.gate("month-end-factsheet", environ={**CheckTests.environ, readiness.MODE_ENV: "shadow"},
                                    now=NOW, out=lines.append, sleep=self.sleeps.append,
                                    connect=lambda url, tenant: FakeSession())
        self.assertEqual((observed.call_count, len(lines), result["ready"], self.sleeps), (1, 1, False, []))

    def test_off_never_waits(self):
        def fail(*args, **kwargs):
            raise AssertionError("off must not check")

        with patch.object(readiness, "check", side_effect=fail):
            self.assertIsNone(readiness.gate("derived-daily", environ={readiness.WAIT_ENV: "600"},
                                             sleep=self.sleeps.append, out=self.sleeps.append))
        self.assertEqual(self.sleeps, [])


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
