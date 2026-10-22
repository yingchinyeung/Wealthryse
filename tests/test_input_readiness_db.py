"""Input readiness against a real PostgreSQL with every migration applied.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts
one). Skipped otherwise. Creates and drops its own databases and roles and writes
synthetic rows only. Shows the evidence queries judge a cycle that ran as ready and a
late pull as not ready, that shadow and enforce act on that, and that with the mode
unset the gate opens no connection and logs nothing. Also that a subject still in
onboarding does not hold up risk, that an open lineage run of risk or sectors only
delays, that sectors never stop anything, that reports follow month-end's run, that
enforce waits and checks again, and that without migration 071 (no lineage_events) the
check falls back to the evidence it used before.
"""
from __future__ import annotations

import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import input_readiness as readiness
from test_medallion_layers_db import NEEDS_LAYERS

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER = "readiness_test_owner"
NO_READS = "readiness_test_no_reads"
READER = "readiness_test_reader"  # reads the evidence tables but not lineage_events
TEST_PASSWORD = "readiness-test-only-password"  # throwaway roles in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
DAY = "2026-10-19"  # a Monday; the pull asks for data up to Sunday 18 Oct
EVIDENCE_TABLES = ("risk_analytics_snapshots", "subject_calculation_runs", "institutional_work_items",
                   "ingestion_runs")
READER_TABLES = (*EVIDENCE_TABLES, "platform_subjects")
LINEAGE_MIGRATION = "071_lineage_records.sql"
RISK_JOB, SECTOR_JOB = "ibkrv2stg-risk-snapshots", "ibkrv2stg-sector-classification"
REPORTS_JOB = "ibkrv2stg-month-end-factsheet"


def _dsn(database: str, user: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(ADMIN_DSN)
    params["dbname"] = database
    params["options"] = f"-c app.tenant_id={TENANT}"
    if user:
        params["user"] = user
        params["password"] = TEST_PASSWORD
    return make_conninfo(**params)


def _at(day: str, clock: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}:00+00:00")


def _work_items(connection, *, run_date: str, completed: str | None, status: str = "succeeded") -> None:
    """One statement request and one account import, as institutional-daily queues them."""
    for kind, subject in (("flex_statement_request", None), ("account_import", "U0000001")):
        key = f"flex:{uuid.uuid4()}:{run_date}" + (":subject:U0000001:import" if subject else "")
        done = status in ("succeeded", "failed")
        connection.execute(
            "INSERT INTO institutional_work_items(work_item_id, subject_id, work_kind, ordering_key, "
            "idempotency_key, status, payload_json, completed_at) VALUES (%s, %s, %s, 'credential:x', %s, %s, %s, %s)",
            (str(uuid.uuid4()), subject, kind, key, status, json.dumps({"run_date": run_date}),
             completed if done else None))


def _cycle(connection, day: str, *, pull: bool = True) -> None:
    """A synthetic daily cycle on `day`: the pull (data up to the day before), then
    benchmark, derived and risk, at their scheduled times."""
    previous = (datetime.fromisoformat(day).date().toordinal() - 1)
    data_day = datetime.fromordinal(previous).date().isoformat()
    if pull:
        _work_items(connection, run_date=data_day, completed=f"{day}T08:24:00+00:00")
    connection.execute(
        "INSERT INTO ingestion_runs(run_type, started_at, completed_at, status) "
        "VALUES ('staging-yfinance-benchmark', %s, %s, 'success')", (f"{day}T08:30:00+00:00", f"{day}T08:33:00+00:00"))
    connection.execute(
        "INSERT INTO subject_calculation_runs(calculation_run_id, subject_id, definition_id, requested_from, "
        "requested_to, status, started_at, completed_at) VALUES (%s, 'SYN-COMPOSITE', %s, '2025-01-20', %s, "
        "'validated', %s, %s)", (str(uuid.uuid4()), DEFINITION, day, _at(day, "08:50"), _at(day, "08:52")))
    connection.execute(
        "INSERT INTO risk_analytics_snapshots(subject_id, as_of_date, analytics_version, comparison_version, "
        "source_data_sha256, payload_sha256, payload_json, quality_status, generated_at, duration_ms, "
        "timings_json) VALUES ('U0000001', %s, %s, 'v', %s, %s, '{}', 'pass', %s, 1, %s)",
        (data_day, "v-" + day, "e" * 64, "f" * 64, f"{day}T08:56:00+00:00",
         json.dumps({"source_checked_at": f"{day}T08:57:00+00:00", "source_check_status": "current"})))


DEFINITION = "6f0b6c9e-1d1e-4f43-9a55-2b8f3f1d0001"
ONBOARDING_DEFINITION = "6f0b6c9e-1d1e-4f43-9a55-2b8f3f1d0002"


def _calculation(connection, subject: str, status: str, completed: datetime, *, day: str = DAY) -> None:
    definition = ONBOARDING_DEFINITION if subject == "SYN-ONBOARDING" else DEFINITION
    connection.execute(
        "INSERT INTO subject_calculation_runs(calculation_run_id, subject_id, definition_id, requested_from, "
        "requested_to, status, started_at, completed_at) VALUES (%s, %s, %s, '2025-01-20', %s, %s, %s, %s)",
        (str(uuid.uuid4()), subject, definition, day, status, completed - timedelta(minutes=1), completed))


def _lineage(connection, job: str, event: str, at: datetime, *, run_id: str, facets: dict | None = None) -> None:
    connection.execute(
        "INSERT INTO lineage_events(run_id, event_type, event_time, job_namespace, job_name, producer, facets) "
        "VALUES (%s, %s, %s, 'wealthryse', %s, 'synthetic-test', %s)",
        (run_id, event, at, job, json.dumps(facets or {})))


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class _ReadinessDatabase(unittest.TestCase):
    """A disposable database with every migration applied except SKIPPED."""
    SKIPPED: frozenset[str] = frozenset()

    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = "readiness_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (NO_READS, READER):
                admin.execute(f"DROP ROLE IF EXISTS {role}")
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
                # Like Azure: the owner is not a superuser, so forced row-level security applies.
                admin.execute(f"CREATE ROLE {OWNER} LOGIN PASSWORD '{TEST_PASSWORD}'")
            for role in (NO_READS, READER):
                admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER}')
        with psycopg.connect(_dsn(cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        with psycopg.connect(_dsn(cls.database, OWNER)) as owner:
            for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
                if "_medallion_" in path.name or path.name in NEEDS_LAYERS or path.name in cls.SKIPPED:
                    continue  # the layer moves keep these names working (test_medallion_layers_db)
                try:
                    owner.execute(path.read_text(encoding="utf-8"))
                    owner.commit()
                except Exception:
                    owner.rollback()
                    if path.name not in TOLERATED_MIGRATIONS:
                        raise
                if owner.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                        owner.execute("SELECT 1 FROM tenants").fetchone() is None:
                    owner.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                  "VALUES (%s, 'Test', 'active', '2026-01-01')", (TENANT,))
                    owner.commit()
            owner.execute(
                "INSERT INTO platform_subjects(subject_id, subject_type, display_name, lifecycle_status) VALUES "
                "('U0000001', 'physical_account', 'Synthetic account', 'active'), "
                "('SYN-COMPOSITE', 'composite_portfolio', 'Synthetic composite', 'active'), "
                "('SYN-ONBOARDING', 'composite_portfolio', 'Synthetic composite in onboarding', 'onboarding')")
            for definition, subject in ((DEFINITION, "SYN-COMPOSITE"), (ONBOARDING_DEFINITION, "SYN-ONBOARDING")):
                owner.execute(
                    "INSERT INTO subject_calculation_definitions(definition_id, subject_id, subject_type, "
                    "calculation_kind, definition_version, configuration_json, configuration_sha256, "
                    "approval_status, effective_from) VALUES (%s, %s, 'composite_portfolio', "
                    "'fixed_unit_normalized_composite', 1, '{}', %s, 'validated', '2025-01-20')",
                    (definition, subject, "a" * 64))
            for role in (NO_READS, READER):
                owner.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
                owner.execute(f"GRANT EXECUTE ON FUNCTION app_tenant_id() TO {role}")
            owner.execute(f"GRANT SELECT ON {', '.join(READER_TABLES)} TO {READER}")
            owner.commit()

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')
            for role in (NO_READS, READER):
                admin.execute(f"DROP ROLE IF EXISTS {role}")

    def setUp(self):
        import psycopg

        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            for table in EVIDENCE_TABLES:
                owner.execute(f"DELETE FROM {table}")
            if owner.execute("SELECT to_regclass('public.lineage_events')").fetchone()[0]:
                # Lineage records are append-only; the owner lifts that for a clean test.
                owner.execute("ALTER TABLE lineage_events DISABLE TRIGGER lineage_events_append_only")
                owner.execute("DELETE FROM lineage_events")
                owner.execute("ALTER TABLE lineage_events ENABLE TRIGGER lineage_events_append_only")
            _cycle(owner, "2026-10-18")  # yesterday's cycle, complete
            owner.commit()

    def _seed(self, function, *args, **kwargs):
        import psycopg

        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            function(owner, *args, **kwargs)
            owner.commit()

    def _environ(self, mode: str | None, user: str = OWNER, **settings) -> dict:
        environ = {"DATABASE_URL": _dsn(self.database, user), "PORTFOLIO_TENANT_ID": TENANT, **settings}
        if mode is not None:
            environ[readiness.MODE_ENV] = mode
        return environ

    def _gate(self, job: str, clock: str, mode: str | None, user: str = OWNER, sleep=None, **settings):
        lines: list[str] = []
        result = readiness.gate(job, environ=self._environ(mode, user, **settings), now=_at(DAY, clock),
                                out=lines.append, sleep=sleep)
        return result, [json.loads(line.removeprefix(readiness.LOG_PREFIX)) for line in lines]


class InputReadinessDatabaseTests(_ReadinessDatabase):
    def test_ready_when_the_cycle_ran_in_order(self):
        self._seed(_cycle, DAY)
        results = {}
        for job, clock in (("benchmark-daily", "08:30"), ("sector-classification", "08:45"),
                           ("derived-daily", "08:50"), ("risk-snapshots", "08:55"),
                           ("month-end-factsheet", "09:00"), ("analyst-assurance", "09:10"),
                           ("crisis-scenarios-daily", "09:20")):
            with self.subTest(job=job):
                result, records = self._gate(job, clock, "enforce")
                self.assertEqual((result["ready"], result["reasons"], result["decision"]), (True, [], "continue"))
                self.assertEqual(len(records), 1)
                results[job] = result
        result = results["month-end-factsheet"]
        evidence = result["evidence"]
        self.assertEqual(evidence["ingestion"]["through_date"], "2026-10-18")
        self.assertEqual(evidence["ingestion"]["completed_at"], "2026-10-19T08:24:00+00:00")
        self.assertEqual(evidence["benchmarks_fx"]["completed_at"], "2026-10-19T08:33:00+00:00")
        self.assertEqual(evidence["derived"]["completed_at"], "2026-10-19T08:52:00+00:00")
        self.assertEqual(evidence["risk"]["completed_at"], "2026-10-19T08:57:00+00:00")
        self.assertEqual(result["ledger"], {"rows": 0})

    def test_not_ready_when_the_pull_is_late(self):
        # 08:30 and today's pull has not run: the newest statement is yesterday's.
        result, records = self._gate("benchmark-daily", "08:30", "shadow")
        self.assertEqual(result["reasons"], ["ingestion: stale data", "reconciliation: stale data"])
        self.assertEqual((records[0]["ready"], records[0]["decision"]), (False, "continue"))
        with self.assertRaises(SystemExit) as stopped:
            self._gate("benchmark-daily", "08:30", "enforce")
        self.assertEqual(stopped.exception.code, readiness.EXIT_NOT_READY)
        # Queued but not finished. The policy names the nearest missing step.
        self._seed(_work_items, run_date="2026-10-18", completed=None, status="queued")
        result, _ = self._gate("benchmark-daily", "08:31", "shadow")
        self.assertEqual(result["reasons"], ["reconciliation: missing completion evidence"])
        self.assertEqual(result["evidence"]["ingestion"]["state"], "in_progress")
        self.assertEqual(result["evidence"]["reconciliation"]["state"], "in_progress")

    def test_later_steps_are_not_ready_on_yesterdays_outputs(self):
        # The pull landed today, but derived and benchmarks have not run yet.
        self._seed(_work_items, run_date="2026-10-18", completed=f"{DAY}T08:24:00+00:00")
        result, _ = self._gate("risk-snapshots", "08:55", "shadow")
        self.assertEqual(result["reasons"], [
            "benchmarks_fx: reconciliation changed; recalculate", "benchmarks_fx: stale data",
            "derived: reconciliation changed; recalculate", "derived: stale data"])

    def test_a_derived_calculation_still_running_is_not_finished(self):
        import psycopg

        self._seed(_cycle, DAY)
        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            owner.execute(
                "INSERT INTO subject_calculation_runs(calculation_run_id, subject_id, definition_id, "
                "requested_from, requested_to, status, started_at) VALUES (%s, 'SYN-COMPOSITE', %s, "
                "'2025-01-20', %s, 'running', %s)", (str(uuid.uuid4()), DEFINITION, DAY, _at(DAY, "08:53")))
            owner.commit()
        result, _ = self._gate("risk-snapshots", "08:55", "shadow")
        self.assertEqual(result["evidence"]["derived"]["state"], "in_progress")
        self.assertEqual(result["reasons"], ["derived: missing completion evidence"])

    def test_off_does_nothing(self):
        import psycopg

        self._seed(_cycle, DAY)
        calls = []
        real_connect = psycopg.connect

        def counting(*args, **kwargs):
            calls.append(args)
            return real_connect(*args, **kwargs)

        with patch("psycopg.connect", side_effect=counting):
            for mode in (None, "", "off"):
                with self.subTest(mode=mode):
                    self.assertEqual(self._gate("derived-daily", "08:50", mode), (None, []))
            self.assertEqual(calls, [])
            # The same setup in shadow opens exactly one session and logs one line.
            result, records = self._gate("derived-daily", "08:50", "shadow")
        self.assertEqual(len(calls), 1)
        self.assertEqual((len(records), result["ready"]), (1, True))

    def test_a_login_without_reads_is_unavailable_and_shadow_carries_on(self):
        self._seed(_cycle, DAY)
        result, records = self._gate("derived-daily", "08:50", "shadow", user=NO_READS)
        self.assertEqual(records[0]["decision"], "continue")
        self.assertFalse(result["ready"])
        self.assertTrue(all("InsufficientPrivilege" in reason for reason in result["reasons"]), result["reasons"])
        self.assertEqual(len(result["reasons"]), 3)

    def test_the_check_never_writes(self):
        import psycopg

        self._seed(_cycle, DAY)
        from ibkr_etl.layer_freshness import open_read_only

        opened = []

        def recording(url, tenant):
            connection = open_read_only(url, tenant)
            opened.append(connection)
            return connection

        result = readiness.check("month-end-factsheet", environ=self._environ("shadow"), now=_at(DAY, "09:00"),
                                 connect=recording)
        self.assertTrue(result["ready"], result["reasons"])
        self.assertTrue(opened and opened[0].read_only)
        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            counts = [owner.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in EVIDENCE_TABLES]
        self.assertEqual(counts, [2, 2, 4, 2])

    def test_ledger_rows_for_the_cycle_must_agree(self):
        import psycopg
        from psycopg.rows import dict_row

        from ibkr_etl import workflow_ledger

        day = "2026-10-21"  # its own cycle: ledger rows cannot be removed
        self._seed(_cycle, day)
        with psycopg.connect(_dsn(self.database, OWNER), row_factory=dict_row) as owner:
            workflow_ledger.append_evidence(
                owner, tenant_id=TENANT, cycle_key=f"daily:{day}", subject_id=readiness.SUBJECT,
                stage="ingestion", status="failed", validated=False,
                through_date=datetime.fromisoformat("2026-10-20").date(), input_sha256="1" * 64,
                output_sha256="2" * 64, dependency_ids={}, producer="synthetic-test")
            owner.commit()
        lines: list[str] = []
        result = readiness.gate("benchmark-daily", environ=self._environ("shadow"), now=_at(day, "08:40"),
                                out=lines.append)
        self.assertEqual(result["ledger"]["rows"], 1)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reasons"], ["ledger: reconciliation: missing completion evidence"])
        self.assertEqual(result["evidence"]["reconciliation"]["state"], "fresh")

    # Case 1 -----------------------------------------------------------------------------

    def test_a_subject_still_onboarding_does_not_hold_up_risk_or_reports(self):
        self._seed(_cycle, DAY)
        self._seed(_calculation, "SYN-ONBOARDING", "failed", _at(DAY, "08:51"))
        for job, clock in (("risk-snapshots", "08:55"), ("month-end-factsheet", "09:00")):
            with self.subTest(job=job):
                result, records = self._gate(job, clock, "enforce")
                self.assertEqual((result["ready"], result["reasons"], records[-1]["decision"]), (True, [], "continue"))
                derived = result["evidence"]["derived"]
                self.assertEqual((derived["planned"], derived["failed"], derived["waiting"],
                                  derived["waiting_subjects"]), (1, 0, 1, ["SYN-ONBOARDING"]))

    def test_a_live_subjects_blocked_or_failed_calculation_still_stops(self):
        self._seed(_cycle, DAY)
        self._seed(_calculation, "SYN-ONBOARDING", "blocked", _at(DAY, "08:51"))
        for status in ("blocked", "failed"):
            with self.subTest(status=status):
                self._seed(_calculation, "SYN-COMPOSITE", status, _at(DAY, "08:53"))
                result, _ = self._gate("risk-snapshots", "08:55", "shadow")
                self.assertEqual(result["reasons"], ["derived: not validated"])
                self.assertEqual(result["evidence"]["derived"]["waiting"], 1)
        with self.assertRaises(SystemExit) as stopped:
            self._gate("risk-snapshots", "08:55", "enforce")
        self.assertEqual(stopped.exception.code, readiness.EXIT_NOT_READY)

    # Cases 2 and 3 ----------------------------------------------------------------------

    def test_risk_still_running_holds_up_reports_until_its_run_ends(self):
        self._seed(_cycle, DAY)  # snapshots stamped at 08:57 by the run still in progress
        run = str(uuid.uuid4())
        self._seed(_lineage, RISK_JOB, "START", _at(DAY, "08:55"), run_id=run)
        result, _ = self._gate("month-end-factsheet", "09:00", "shadow")
        self.assertFalse(result["ready"])
        self.assertIn("risk: ibkrv2stg-risk-snapshots is still running (started 2026-10-19T08:55:00+00:00)",
                      result["reasons"])
        self.assertEqual(result["evidence"]["risk"]["run"]["run_id"], run)
        self._seed(_lineage, RISK_JOB, "COMPLETE", _at(DAY, "09:01"), run_id=run)
        result, _ = self._gate("month-end-factsheet", "09:02", "enforce")
        self.assertEqual((result["ready"], result["reasons"]), (True, []))
        self.assertEqual(result["evidence"]["risk"]["completed_at"], "2026-10-19T09:01:00+00:00")

    def test_a_risk_run_that_crashed_stops_reports(self):
        self._seed(_cycle, DAY)
        run = str(uuid.uuid4())
        self._seed(_lineage, RISK_JOB, "START", _at(DAY, "08:55"), run_id=run)
        self._seed(_lineage, RISK_JOB, "FAIL", _at(DAY, "08:58"), run_id=run, facets={"error": {"type": "RuntimeError"}})
        result, _ = self._gate("month-end-factsheet", "09:00", "shadow")
        self.assertEqual(result["reasons"], ["risk: ibkrv2stg-risk-snapshots did not finish (RuntimeError)",
                                             "risk: not validated"])

    def test_sectors_still_running_hold_up_derived(self):
        self._seed(_cycle, DAY)
        run = str(uuid.uuid4())
        self._seed(_lineage, SECTOR_JOB, "START", _at(DAY, "08:45"), run_id=run)
        result, _ = self._gate("derived-daily", "08:50", "shadow")
        self.assertIn("benchmarks_fx: ibkrv2stg-sector-classification is still running "
                      "(started 2026-10-19T08:45:00+00:00)", result["reasons"])
        self.assertFalse(result["ready"])
        # Still open when the wait is over: derived carries on and a note says so.
        result, records = self._gate("derived-daily", "08:50", "enforce", sleep=lambda seconds: None,
                                     INPUT_READINESS_WAIT_SECONDS="60", INPUT_READINESS_RECHECK_SECONDS="30")
        self.assertEqual(([r["decision"] for r in records], result["ready"], result["attempts"]),
                         (["wait", "continue"], True, 4))
        self.assertIn("benchmarks_fx: ibkrv2stg-sector-classification still running after the wait (started "
                      "2026-10-19T08:45:00+00:00); sectors not checked", result["notes"])
        self._seed(_lineage, SECTOR_JOB, "FAIL", _at(DAY, "08:48"), run_id=run, facets={"exit_code": 2})
        result, _ = self._gate("derived-daily", "08:50", "enforce")
        self.assertEqual((result["ready"], result["evidence"]["benchmarks_fx"]["sectors"]["state"]), (True, "finished"))

    def test_a_sector_run_that_crashed_never_stops_anything(self):
        self._seed(_cycle, DAY)
        run = str(uuid.uuid4())
        self._seed(_lineage, SECTOR_JOB, "START", _at(DAY, "08:45"), run_id=run)
        self._seed(_lineage, SECTOR_JOB, "FAIL", _at(DAY, "08:47"), run_id=run,
                   facets={"error": {"type": "RuntimeError"}})
        for job, clock in (("derived-daily", "08:50"), ("risk-snapshots", "08:55"),
                           ("month-end-factsheet", "09:00"), ("crisis-scenarios-daily", "09:20")):
            with self.subTest(job=job):
                result, records = self._gate(job, clock, "enforce")
                self.assertEqual((result["ready"], result["reasons"], records[-1]["decision"]),
                                 (True, [], "continue"))
                self.assertIn("benchmarks_fx: ibkrv2stg-sector-classification did not finish (RuntimeError); "
                              "sectors not checked", result["notes"])

    def test_derived_that_finished_before_sectors_ended_carries_on_with_a_note(self):
        self._seed(_cycle, DAY)  # derived completed at 08:52
        run = str(uuid.uuid4())
        self._seed(_lineage, SECTOR_JOB, "START", _at(DAY, "08:45"), run_id=run)
        self._seed(_lineage, SECTOR_JOB, "COMPLETE", _at(DAY, "08:53"), run_id=run)
        result, _ = self._gate("risk-snapshots", "08:55", "enforce")
        self.assertEqual((result["ready"], result["reasons"]), (True, []))
        self.assertIn("derived: finished before ibkrv2stg-sector-classification ended; it used the earlier "
                      "sector classifications", result["notes"])

    def test_lineage_the_login_cannot_read_falls_back_with_a_note(self):
        self._seed(_cycle, DAY)
        self._seed(_lineage, RISK_JOB, "START", _at(DAY, "08:55"), run_id=str(uuid.uuid4()))
        result, _ = self._gate("month-end-factsheet", "09:00", "shadow", user=READER)
        self.assertEqual((result["ready"], result["reasons"]), (True, []))
        self.assertIn("risk: ibkrv2stg-risk-snapshots lineage unavailable (lineage_events not present or not "
                      "readable); judged by public.risk_analytics_snapshots alone", result["notes"])

    # Case 4 -----------------------------------------------------------------------------

    def test_enforce_waits_and_rechecks_until_risk_ends(self):
        self._seed(_cycle, DAY)
        run = str(uuid.uuid4())
        self._seed(_lineage, RISK_JOB, "START", _at(DAY, "08:55"), run_id=run)
        pauses = []

        def sleep(seconds):
            pauses.append(seconds)
            if len(pauses) == 2:  # the risk run ends during the second pause
                self._seed(_lineage, RISK_JOB, "COMPLETE", _at(DAY, "09:01"), run_id=run)

        result, records = self._gate("month-end-factsheet", "09:00", "enforce", sleep=sleep,
                                     INPUT_READINESS_WAIT_SECONDS="600", INPUT_READINESS_RECHECK_SECONDS="60")
        self.assertEqual(pauses, [60.0, 60.0])
        self.assertEqual([record["decision"] for record in records], ["wait", "continue"])
        self.assertEqual((result["ready"], result["attempts"], result["waited_seconds"]), (True, 3, 120.0))

    def test_a_risk_run_still_open_after_the_wait_only_delays_when_the_tables_are_done(self):
        self._seed(_cycle, DAY)  # snapshots for today, but the run never recorded its end
        self._seed(_lineage, RISK_JOB, "START", _at(DAY, "08:55"), run_id=str(uuid.uuid4()))
        pauses = []
        result, records = self._gate("month-end-factsheet", "09:00", "enforce", sleep=pauses.append,
                                     INPUT_READINESS_WAIT_SECONDS="120", INPUT_READINESS_RECHECK_SECONDS="60")
        self.assertEqual(pauses, [60.0, 60.0])
        self.assertEqual([record["decision"] for record in records], ["wait", "continue"])
        self.assertEqual((result["ready"], result["reasons"], result["attempts"]), (True, [], 4))
        self.assertIn("risk: ibkrv2stg-risk-snapshots still running after the wait (started "
                      "2026-10-19T08:55:00+00:00); judged by public.risk_analytics_snapshots alone", result["notes"])
        self.assertEqual(result["first_reasons"], [
            "risk: ibkrv2stg-risk-snapshots is still running (started 2026-10-19T08:55:00+00:00)",
            "risk: missing completion evidence"])

    def test_enforce_stops_when_risk_is_still_running_and_its_tables_are_not_done(self):
        self._seed(_cycle, DAY)
        self._seed(lambda c: c.execute("DELETE FROM risk_analytics_snapshots WHERE analytics_version = %s",
                                       ("v-" + DAY,)))
        self._seed(_lineage, RISK_JOB, "START", _at(DAY, "08:55"), run_id=str(uuid.uuid4()))
        pauses, lines = [], []
        environ = self._environ("enforce", INPUT_READINESS_WAIT_SECONDS="120", INPUT_READINESS_RECHECK_SECONDS="60")
        with self.assertRaises(SystemExit) as stopped:
            readiness.gate("month-end-factsheet", environ=environ, now=_at(DAY, "09:00"), out=lines.append,
                           sleep=pauses.append)
        self.assertEqual((stopped.exception.code, pauses), (readiness.EXIT_NOT_READY, [60.0, 60.0]))
        final = json.loads(lines[-1].removeprefix(readiness.LOG_PREFIX))
        # Yesterday's snapshots: stale, and older than the steps they read.
        self.assertEqual((final["decision"], final["reasons"]), ("stop", [
            "risk: benchmarks_fx changed; recalculate", "risk: derived changed; recalculate", "risk: stale data"]))
        self.assertIn("risk: ibkrv2stg-risk-snapshots still running after the wait (started "
                      "2026-10-19T08:55:00+00:00); judged by public.risk_analytics_snapshots alone", final["notes"])

    # Reports, judged by month-end-factsheet's run -------------------------------------

    def test_assurance_follows_the_month_end_run(self):
        self._seed(_cycle, DAY)
        result, _ = self._gate("analyst-assurance", "09:10", "enforce")
        self.assertEqual((result["ready"], result["evidence"]["reports"]["state"]), (True, "not_checked"))
        self.assertIn("reports: ibkrv2stg-month-end-factsheet no run recorded; reports not checked",
                      result["notes"])
        run = str(uuid.uuid4())
        self._seed(_lineage, REPORTS_JOB, "START", _at(DAY, "09:00"), run_id=run)
        result, _ = self._gate("analyst-assurance", "09:10", "shadow")
        self.assertIn("reports: ibkrv2stg-month-end-factsheet is still running (started 2026-10-19T09:00:00+00:00)",
                      result["reasons"])
        # A partial failure (exit 1) ran to its end: finished.
        self._seed(_lineage, REPORTS_JOB, "FAIL", _at(DAY, "09:05"), run_id=run, facets={"exit_code": 1})
        result, _ = self._gate("analyst-assurance", "09:10", "enforce")
        self.assertEqual((result["ready"], result["reasons"]), (True, []))
        self.assertEqual((result["evidence"]["reports"]["completed_at"], result["evidence"]["reports"]["run"]["state"]),
                         ("2026-10-19T09:05:00+00:00", "finished"))
        crashed = str(uuid.uuid4())
        self._seed(_lineage, REPORTS_JOB, "START", _at(DAY, "09:06"), run_id=crashed)
        self._seed(_lineage, REPORTS_JOB, "FAIL", _at(DAY, "09:07"), run_id=crashed,
                   facets={"error": {"type": "SystemExit"}})
        with self.assertRaises(SystemExit) as stopped:
            self._gate("analyst-assurance", "09:10", "enforce")
        self.assertEqual(stopped.exception.code, readiness.EXIT_NOT_READY)
        result, _ = self._gate("analyst-assurance", "09:10", "shadow")
        self.assertEqual(result["reasons"], ["reports: ibkrv2stg-month-end-factsheet did not finish (SystemExit)",
                                             "reports: not validated"])

    def test_shadow_and_off_never_wait(self):
        self._seed(_cycle, DAY)
        self._seed(_lineage, RISK_JOB, "START", _at(DAY, "08:55"), run_id=str(uuid.uuid4()))
        pauses = []
        result, records = self._gate("month-end-factsheet", "09:00", "shadow", sleep=pauses.append,
                                     INPUT_READINESS_WAIT_SECONDS="600")
        self.assertEqual((pauses, len(records), result["ready"], result["decision"]), ([], 1, False, "continue"))
        self.assertEqual(self._gate("month-end-factsheet", "09:00", None, sleep=pauses.append,
                                    INPUT_READINESS_WAIT_SECONDS="600"), (None, []))
        self.assertEqual(pauses, [])


class WithoutLineageMigrationTests(_ReadinessDatabase):
    """The same checks on a database where migration 071, and so every later one, was never applied."""
    SKIPPED = frozenset(path.name for path in (ROOT / "migrations" / "postgres").glob("*.sql")
                        if path.name >= LINEAGE_MIGRATION)

    def test_the_cycle_is_ready_as_before_and_the_notes_say_lineage_is_missing(self):
        self._seed(_cycle, DAY)
        for job, clock in (("derived-daily", "08:50"), ("month-end-factsheet", "09:00"),
                           ("analyst-assurance", "09:10")):
            with self.subTest(job=job):
                result, records = self._gate(job, clock, "enforce")
                self.assertEqual((result["ready"], result["reasons"], records[-1]["decision"]), (True, [], "continue"))
        self.assertIn("reports: ibkrv2stg-month-end-factsheet lineage unavailable (lineage_events not present or "
                      "not readable); reports not checked", result["notes"])
        self.assertIn("risk: ibkrv2stg-risk-snapshots lineage unavailable (lineage_events not present or not "
                      "readable); judged by public.risk_analytics_snapshots alone", result["notes"])
        self.assertIn("benchmarks_fx: ibkrv2stg-sector-classification lineage unavailable (lineage_events not "
                      "present or not readable); sectors not checked", result["notes"])

    def test_a_subject_still_onboarding_does_not_hold_up_risk(self):
        self._seed(_cycle, DAY)
        self._seed(_calculation, "SYN-ONBOARDING", "failed", _at(DAY, "08:51"))
        result, _ = self._gate("risk-snapshots", "08:55", "enforce")
        self.assertEqual((result["ready"], result["evidence"]["derived"]["waiting"]), (True, 1))

    def test_enforce_waits_for_evidence_that_arrives_in_the_tables(self):
        # At 08:52 derived has not run today; it finishes during the first pause.
        self._seed(_work_items, run_date="2026-10-18", completed=f"{DAY}T08:24:00+00:00")
        self._seed(lambda c: c.execute(
            "INSERT INTO ingestion_runs(run_type, started_at, completed_at, status) "
            "VALUES ('staging-yfinance-benchmark', %s, %s, 'success')", (_at(DAY, "08:30"), _at(DAY, "08:33"))))
        pauses = []

        def sleep(seconds):
            pauses.append(seconds)
            self._seed(_calculation, "SYN-COMPOSITE", "validated", _at(DAY, "08:52"))

        result, records = self._gate("risk-snapshots", "08:51", "enforce", sleep=sleep,
                                     INPUT_READINESS_WAIT_SECONDS="300", INPUT_READINESS_RECHECK_SECONDS="60")
        self.assertEqual(pauses, [60.0])
        self.assertEqual([record["decision"] for record in records], ["wait", "continue"])
        self.assertEqual(result["first_reasons"], ["derived: benchmarks_fx changed; recalculate",
                                                   "derived: reconciliation changed; recalculate",
                                                   "derived: stale data"])


if __name__ == "__main__":
    unittest.main()
