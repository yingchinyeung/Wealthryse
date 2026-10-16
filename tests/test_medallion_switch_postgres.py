"""The medallion switch checks against PostgreSQL after the real account-sync import.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN. Skipped otherwise. Each class creates and drops its own
database. Synthetic data only.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from switch_fixtures import (
    ACCOUNT_A, ACCOUNT_B, ACCOUNT_C, FIRST_RUN, account_snapshot, edit_snapshot, scenario, settings_for,
    statement_for_run,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER_ROLE = "medallion_switch_test_owner"
TEST_PASSWORD = "medallion-switch-test-only"  # throwaway role in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
START = "2026-03-01"
# %% so the same text works in psycopg (with parameters) and SQLite LIKE.
LEGACY = ("UPDATE {table} SET source_key = 'legacy:' || source_key "
          "WHERE (source_key LIKE 'cash:%%' OR source_key LIKE 'fx:%%')")


def _url(database: str, owner: bool = True) -> str:
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(ADMIN_DSN)
    user, password = (OWNER_ROLE, TEST_PASSWORD) if owner else (params["user"], params["password"])
    return f"postgresql://{user}:{password}@{params['host']}:{params['port']}/{database}"


class _Database(unittest.TestCase):
    """A fresh migrated database holding account-sync's import of two synthetic accounts."""

    snapshot_edits: dict[str, list[str]] = {}
    statement_options: dict = {}

    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.db import PostgresConnection
        from ibkr_etl.pilot_import import import_snapshot

        cls.database = "medallion_switch_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER_ROLE,)).fetchone() is None:
                admin.execute(f"CREATE ROLE {OWNER_ROLE} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER_ROLE}')
        with psycopg.connect(_url(cls.database, owner=False), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        cls.url = _url(cls.database)
        connection = PostgresConnection(psycopg.connect(cls.url))
        connection.execute("SELECT set_config('app.tenant_id', ?, false)", (TENANT,))
        connection.commit()
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

        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.days, cls.statements, cls.snapshots = scenario(cls.root, statement_options=cls.statement_options)
        for account, edits in cls.snapshot_edits.items():
            edit_snapshot(cls.snapshots[account], edits)
        for account, path in cls.snapshots.items():
            import_snapshot(database_url=cls.url, local_path=path, account_id=account, start_date=START,
                            tenant_id=TENANT, source_reference=f"test/{path.name}")

    @classmethod
    def tearDownClass(cls):
        import psycopg

        cls.directory.cleanup()
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')

    def execute(self, sql, params=()):
        import psycopg

        with psycopg.connect(self.url) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
            result = connection.execute(sql, params)
            rows = result.fetchall() if result.description else None
            connection.commit()
        return rows

    def counts(self, account):
        from ibkr_etl.pilot_snapshot import ACCOUNT_TABLES

        return {table: self.execute(f"SELECT COUNT(*) FROM {table} WHERE account_id = %s", (account,))[0][0]
                for table in ACCOUNT_TABLES}

    def dry_run(self, statements, lookback_days=14):
        from ibkr_etl.switch_dry_run import open_read_only, read_snapshot, run_dry_run, statement_from_bytes

        live = open_read_only(self.url, TENANT)
        try:
            with tempfile.TemporaryDirectory() as work:
                return run_dry_run(
                    [statement_from_bytes(f"s{i}.xml", content) for i, content in enumerate(statements)],
                    [read_snapshot(path.name, path) for path in self.snapshots.values()],
                    work_dir=Path(work), tenant_id=TENANT, live_connection=live, lookback_days=lookback_days)
        finally:
            live.rollback()
            live.close()


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class LiveCheckTests(_Database):
    def test_matching_live_data_passes(self):
        report = self.dry_run(self.statements[-8:])
        self.assertEqual(report["status"], "PASS", json.dumps(report.get("live_check"), default=str)[:2000])
        for account in (ACCOUNT_A, ACCOUNT_B):
            item = report["live_check"]["accounts"][account]
            self.assertEqual(item["days_checked"], 14)
            self.assertEqual(item["nav_base_currency_rows"], 0)
            self.assertEqual(item["flex_row_sections"], {})  # account-sync loads no flex_rows
            for table, result in item["tables"].items():
                self.assertEqual(result.get("would_insert", 0), 0, table)
                self.assertEqual(result["extra_in_v2"] if "extra_in_v2" in result else 0, 0, table)
            self.assertGreater(item["tables"]["cash_transactions"]["matched"], 0)

    def test_rekeyed_live_rows_fail(self):
        self.execute(LEGACY.format(table="cash_transactions") + " AND account_id = %s", (ACCOUNT_A,))
        self.addCleanup(self.execute, "UPDATE cash_transactions SET source_key = substr(source_key, 8) "
                                      "WHERE source_key LIKE 'legacy:%%'")
        live = self.dry_run(self.statements[-8:])["live_check"]
        result = live["accounts"][ACCOUNT_A]["tables"]["cash_transactions"]
        self.assertEqual(live["status"], "FAIL")
        self.assertGreater(result["rekeyed"], 0)
        self.assertEqual(result["extra_in_v2"], 0)
        self.assertEqual(live["accounts"][ACCOUNT_B]["status"], "PASS")

    def test_base_nav_rows_and_short_coverage_fail(self):
        self.execute("INSERT INTO nav_snapshots(account_id, snapshot_date, currency, total_text, row_sha256, "
                     "last_seen_run_id) SELECT account_id, snapshot_date, 'BASE', total_text, row_sha256, "
                     "last_seen_run_id FROM nav_snapshots WHERE account_id = %s ORDER BY snapshot_date LIMIT 1",
                     (ACCOUNT_B,))
        self.addCleanup(self.execute, "DELETE FROM nav_snapshots WHERE currency = 'BASE'")
        live = self.dry_run(self.statements[-8:])["live_check"]
        self.assertIn("NAV rows in currency BASE", " ".join(live["accounts"][ACCOUNT_B]["failures"]))
        short = self.dry_run(self.statements[-2:])["live_check"]
        self.assertEqual(short["status"], "FAIL")
        self.assertIn("statements cover 8 of the last 14 days", short["accounts"][ACCOUNT_A]["failures"])

    def test_empty_key_lists_and_registry_query(self):
        from ibkr_etl.db import initialize
        from ibkr_etl.flow_backfill import registry_accounts
        from ibkr_etl.switch_dry_run import live_reference, open_read_only

        path = self.root / "empty.sqlite"
        initialize(path)
        v2 = sqlite3.connect(path)
        v2.row_factory = sqlite3.Row
        live = open_read_only(self.url, TENANT)
        try:
            rows, _, extra = live_reference(live, tenant_id=TENANT, account_id=ACCOUNT_C, v2=v2,
                                            window={"2026-03-10", "2026-03-11"})
            self.assertEqual(registry_accounts(live, TENANT), [])
        finally:
            live.rollback()
            live.close()
            v2.close()
        self.assertEqual(sum(len(value) for value in rows.values()), 0)
        self.assertEqual(extra["flex_row_sections"], {})


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class FirstPullTests(_Database):
    """v1.1 keyed some hash rows differently: what the first 14-day v2 pull does to live."""

    snapshot_edits = {ACCOUNT_A: [LEGACY.format(table="cash_transactions"), LEGACY.format(table="fx_transactions")]}
    # A historical deposit in B, before the first 14-day window (FIRST_RUN - 5 onwards).
    statement_options = {"deposits": {(ACCOUNT_B, FIRST_RUN - timedelta(days=7)): "5000"}}

    def ingest(self, content, compat):
        from ibkr_etl.loader import LEGACY_KEY_COMPAT_ENV
        from ibkr_etl.service import ingest_content

        settings = replace(settings_for(self.root / "pull"), database_url=self.url, tenant_id=TENANT)
        environment = {"IBKR_RUN_MIGRATIONS": "false", LEGACY_KEY_COMPAT_ENV: "1" if compat else "0"}
        with patch.dict(os.environ, environment):
            return ingest_content(settings, content, run_type="institutional-flex-daily")

    def reimport(self, label):
        from ibkr_etl.pilot_import import import_snapshot

        # account-sync's next day: a new snapshot of the same data, so a new digest.
        path = account_snapshot(self.root / "v11" / "v11.sqlite", self.root / f"{label}.sqlite", ACCOUNT_A, START,
                                f"{self.days[-1].isoformat()}T16:0{len(label) % 10}:00+08:00")
        edit_snapshot(path, self.snapshot_edits[ACCOUNT_A])
        return import_snapshot(database_url=self.url, local_path=path, account_id=ACCOUNT_A, start_date=START,
                               tenant_id=TENANT, source_reference=f"test/{label}")

    def test_1_first_pull_with_and_without_the_compat_step(self):
        pull = statement_for_run(self.days[-1], lookback=14, **self.statement_options)
        before = self.counts(ACCOUNT_A)
        legacy = self.execute("SELECT COUNT(*) FROM cash_transactions WHERE source_key LIKE 'legacy:%%'")[0][0]
        self.assertGreater(legacy, 0)

        result = self.ingest(pull, compat=True)
        self.assertGreater(result["reconciliation"]["record_types"]["legacy_source_keys_reused"], 0)
        self.assertEqual(self.counts(ACCOUNT_A), before)
        self.assertEqual(self.reimport("after-compat")["status"], "PASS")

        self.ingest(pull, compat=False)
        after = self.counts(ACCOUNT_A)
        self.assertGreater(after["cash_transactions"], before["cash_transactions"])
        with self.assertRaisesRegex(RuntimeError, "Reconciliation failed"):
            self.reimport("after-plain")

    def test_2_flow_backfill(self):
        from ibkr_etl.flow_backfill import apply, load_candidates, plan
        from ibkr_etl.switch_dry_run import open_read_only, statement_from_bytes

        import psycopg

        self.execute(
            "INSERT INTO reporting_snapshots(snapshot_id, subject_id, period_start, period_end, as_of_at, "
            "reporting_currency, formula_version, source_manifest_json, data_sha256, quality_status, status, "
            "created_at) VALUES (%s, %s, '2026-03-01', '2026-03-31', '2026-04-01', 'USD', 'test', '{}', %s, "
            "'pass', 'frozen', '2026-04-01')", (str(uuid.uuid4()), ACCOUNT_B, "0" * 64))
        statements = [statement_from_bytes(f"s{i}.xml", content) for i, content in enumerate(self.statements)]
        candidates = load_candidates(statements, lambda s: s.content, accounts=[ACCOUNT_A, ACCOUNT_B],
                                     work_dir=self.root / "backfill")
        live = open_read_only(self.url, TENANT)
        try:
            planned = plan(candidates, accounts=[ACCOUNT_A, ACCOUNT_B], live=live, tenant_id=TENANT)
        finally:
            live.rollback()
            live.close()
        a, b = planned["accounts"][ACCOUNT_A], planned["accounts"][ACCOUNT_B]
        # The 14-day pull already stored A's recent rows under the same keys.
        self.assertGreater(a["already_live_same"], 0)
        self.assertEqual(a["already_live_different"], 0)
        self.assertEqual(a["flows_added"], {})
        self.assertEqual(b["flows_added"], {(FIRST_RUN - timedelta(days=7)).isoformat(): 5000.0})
        self.assertEqual(b["frozen_periods_that_would_differ_from_live"], ["2026-03-31"])
        self.assertTrue(planned["flows_added"])

        with psycopg.connect(self.url) as connection:
            from ibkr_etl.flow_backfill import BackfillError

            with self.assertRaises(BackfillError):
                apply(connection, planned, tenant_id=TENANT)
            done = apply(connection, planned, tenant_id=TENANT, accept_flow_changes=True)
        self.assertEqual(done["inserted"], planned["new_rows"])
        run = self.execute("SELECT run_type, status, row_count FROM ingestion_runs WHERE run_id = %s",
                           (done["run_id"],))
        self.assertEqual(run, [("v11-archive-flow-backfill", "success", planned["new_rows"])])

        live = open_read_only(self.url, TENANT)
        try:
            again = plan(candidates, accounts=[ACCOUNT_A, ACCOUNT_B], live=live, tenant_id=TENANT)
        finally:
            live.rollback()
            live.close()
        self.assertEqual(again["new_rows"], 0)
        self.assertFalse(again["flows_added"])

    def test_3_backfill_command(self):
        import medallion_flow_backfill as command

        files = []
        for index, content in enumerate(self.statements):
            path = self.root / f"cmd-{index}.xml"
            path.write_bytes(content)
            files += ["--statement-file", str(path)]
        report = self.root / "backfill.json"
        with patch.dict(os.environ, {"DATABASE_URL": self.url, "PORTFOLIO_TENANT_ID": TENANT}):
            output = io.StringIO()
            with redirect_stdout(output):
                code = command.main(files + ["--account", ACCOUNT_A, "--account", ACCOUNT_B, "--live",
                                             "--report", str(report)])
            self.assertEqual(code, 0, output.getvalue())
            self.assertNotIn(ACCOUNT_B, output.getvalue() + report.read_text())
            self.assertEqual(json.loads(report.read_text())["new_rows"], 0)


if __name__ == "__main__":
    unittest.main()
