"""Input readiness against a real PostgreSQL with every migration applied.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts
one). Skipped otherwise. Creates and drops its own database and roles and writes
synthetic rows only. Shows the evidence queries judge a cycle that ran as ready and a
late pull as not ready, that shadow and enforce act on that, and that with the mode
unset the gate opens no connection and logs nothing.
"""
from __future__ import annotations

import json
import os
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import input_readiness as readiness

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER = "readiness_test_owner"
NO_READS = "readiness_test_no_reads"
TEST_PASSWORD = "readiness-test-only-password"  # throwaway roles in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
DAY = "2026-10-19"  # a Monday; the pull asks for data up to Sunday 18 Oct
EVIDENCE_TABLES = ("risk_analytics_snapshots", "subject_calculation_runs", "institutional_work_items",
                   "ingestion_runs")


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


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class InputReadinessDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = "readiness_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP ROLE IF EXISTS {NO_READS}")
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
                # Like Azure: the owner is not a superuser, so forced row-level security applies.
                admin.execute(f"CREATE ROLE {OWNER} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f"CREATE ROLE {NO_READS} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER}')
        with psycopg.connect(_dsn(cls.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        with psycopg.connect(_dsn(cls.database, OWNER)) as owner:
            for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
                if "_medallion_" in path.name:
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
                "('SYN-COMPOSITE', 'composite_portfolio', 'Synthetic composite', 'active')")
            owner.execute(
                "INSERT INTO subject_calculation_definitions(definition_id, subject_id, subject_type, "
                "calculation_kind, definition_version, configuration_json, configuration_sha256, approval_status, "
                "effective_from) VALUES (%s, 'SYN-COMPOSITE', 'composite_portfolio', "
                "'fixed_unit_normalized_composite', 1, '{}', %s, 'validated', '2025-01-20')",
                (DEFINITION, "a" * 64))
            owner.execute(f"GRANT USAGE ON SCHEMA public TO {NO_READS}")
            owner.execute(f"GRANT EXECUTE ON FUNCTION app_tenant_id() TO {NO_READS}")
            owner.commit()

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')
            admin.execute(f"DROP ROLE IF EXISTS {NO_READS}")

    def setUp(self):
        import psycopg

        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            for table in EVIDENCE_TABLES:
                owner.execute(f"DELETE FROM {table}")
            _cycle(owner, "2026-10-18")  # yesterday's cycle, complete
            owner.commit()

    def _seed(self, function, *args, **kwargs):
        import psycopg

        with psycopg.connect(_dsn(self.database, OWNER)) as owner:
            function(owner, *args, **kwargs)
            owner.commit()

    def _environ(self, mode: str | None, user: str = OWNER) -> dict:
        environ = {"DATABASE_URL": _dsn(self.database, user), "PORTFOLIO_TENANT_ID": TENANT}
        if mode is not None:
            environ[readiness.MODE_ENV] = mode
        return environ

    def _gate(self, job: str, clock: str, mode: str | None, user: str = OWNER):
        lines: list[str] = []
        result = readiness.gate(job, environ=self._environ(mode, user), now=_at(DAY, clock), out=lines.append)
        return result, [json.loads(line.removeprefix(readiness.LOG_PREFIX)) for line in lines]

    def test_ready_when_the_cycle_ran_in_order(self):
        self._seed(_cycle, DAY)
        results = {}
        for job, clock in (("benchmark-daily", "08:30"), ("sector-classification", "08:45"),
                           ("derived-daily", "08:50"), ("risk-snapshots", "08:55"),
                           ("month-end-factsheet", "09:00"), ("crisis-scenarios-daily", "09:20")):
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


if __name__ == "__main__":
    unittest.main()
