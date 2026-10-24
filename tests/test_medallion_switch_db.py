"""The medallion switch checks against PostgreSQL after the real account-sync import.

Needs a disposable PostgreSQL server: set AGENT_TEST_PG_ADMIN_DSN to a
superuser DSN. Skipped otherwise. Each class creates and drops its own
database. Synthetic data only. CI's database job runs this against the
installed package, so the SQLite schema is read from this checkout.
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
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
OWNER_ROLE = "medallion_switch_test_owner"
TEST_PASSWORD = "medallion-switch-test-only"  # throwaway role in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
START = "2026-03-01"
# %% so the same text works in psycopg (with parameters) and SQLite LIKE.
LEGACY = ("UPDATE {table} SET source_key = 'legacy:' || source_key "
          "WHERE (source_key LIKE 'cash:%%' OR source_key LIKE 'fx:%%')")
OTHER_TENANT_ACCOUNT = "U9100008"  # belongs to another tenant
UNKNOWN_ACCOUNT = "U9100009"  # in no tenant
LATE_ACCOUNT = "U9100007"  # in this tenant, used for the concurrent-writer case


def _url(database: str, owner: bool = True) -> str:
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(ADMIN_DSN)
    user, password = (OWNER_ROLE, TEST_PASSWORD) if owner else (params.get("user", "postgres"),
                                                                params.get("password", ""))
    return (f"postgresql://{user}:{password}@{params.get('host', 'localhost')}:{params.get('port', '5432')}"
            f"/{database}")


class _Database(unittest.TestCase):
    """A fresh migrated database holding account-sync's import of two synthetic accounts."""

    snapshot_edits: dict[str, list[str]] = {}
    statement_options: dict = {}

    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl import db as db_module
        from ibkr_etl.db import PostgresConnection
        from ibkr_etl.pilot_import import import_snapshot

        # The installed package's db.py looks for the schema next to site-packages.
        for name, path in (("SQLITE_MIGRATION", ROOT / "migrations" / "001_init.sql"),
                           ("POSTGRES_MIGRATIONS_DIR", ROOT / "migrations" / "postgres")):
            patcher = patch.object(db_module, name, path)
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        cls.database = "medallion_switch_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER_ROLE,)).fetchone() is None:
                admin.execute(f"CREATE ROLE {OWNER_ROLE} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {OWNER_ROLE}')
        cls.addClassCleanup(cls._drop)
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
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.days, cls.statements, cls.snapshots = scenario(cls.root, statement_options=cls.statement_options)
        for account, edits in cls.snapshot_edits.items():
            edit_snapshot(cls.snapshots[account], edits)
        for account, path in cls.snapshots.items():
            import_snapshot(database_url=cls.url, local_path=path, account_id=account, start_date=START,
                            tenant_id=TENANT, source_reference=f"test/{path.name}")

    @classmethod
    def _drop(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')

    def execute(self, sql, params=(), *, admin=False):
        import psycopg

        with psycopg.connect(_url(self.database, owner=False) if admin else self.url) as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
            result = connection.execute(sql, params)
            rows = result.fetchall() if result.description else None
            connection.commit()
        return rows

    def counts(self, account):
        from ibkr_etl.pilot_snapshot import ACCOUNT_TABLES

        return {table: self.execute(f"SELECT COUNT(*) FROM {table} WHERE account_id = %s", (account,))[0][0]
                for table in ACCOUNT_TABLES}

    def add_account(self, account, tenant=TENANT):
        self.execute("INSERT INTO tenants(tenant_id, name, status, created_at) VALUES (%s, 'Other', 'active', "
                     "'2026-01-01') ON CONFLICT DO NOTHING", (tenant,), admin=True)
        self.execute("INSERT INTO accounts(account_id, base_currency, first_seen_at, last_seen_at, source_json, "
                     "tenant_id) VALUES (%s, 'USD', '2026-03-01', '2026-03-01', '{}', %s)", (account, tenant),
                     admin=True)

    def dry_run(self, statements, lookback_days=14, allow_unchecked=(ACCOUNT_C,)):
        from ibkr_etl.switch_dry_run import open_read_only, read_snapshot, run_dry_run, statement_from_bytes

        with tempfile.TemporaryDirectory() as work:
            return run_dry_run(
                [statement_from_bytes(f"s{i}.xml", content) for i, content in enumerate(statements)],
                [read_snapshot(path.name, path) for path in self.snapshots.values()],
                work_dir=Path(work), tenant_id=TENANT, open_live=lambda: open_read_only(self.url, TENANT),
                lookback_days=lookback_days, allow_unchecked=allow_unchecked)


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
        # The account without a snapshot is live-checked too; live has nothing of it.
        unchecked = report["live_check"]["accounts"][ACCOUNT_C]
        self.assertEqual((unchecked["status"], unchecked["checked_against_snapshot"], unchecked["new_in_live"]),
                         ("PASS", False, True))
        self.assertGreater(unchecked["tables"]["trades"]["new_rows"], 0)

    def test_a_second_live_key_for_a_date_fails(self):
        # Live (not the snapshot) holds the last NAV day under another currency and a position
        # under another model code: the first pull would add a second NAV row and double holdings.
        last = self.execute("SELECT MAX(snapshot_date) FROM nav_snapshots WHERE account_id = %s", (ACCOUNT_A,))[0][0]
        self.execute("UPDATE nav_snapshots SET currency = 'HKD' WHERE account_id = %s AND snapshot_date = %s",
                     (ACCOUNT_A, last))
        self.addCleanup(self.execute, "UPDATE nav_snapshots SET currency = 'USD' WHERE currency = 'HKD'")
        position_day, model = self.execute(
            "SELECT snapshot_date, model_code FROM position_snapshots WHERE account_id = %s "
            "ORDER BY snapshot_date DESC LIMIT 1", (ACCOUNT_A,))[0]
        self.execute("UPDATE position_snapshots SET model_code = 'LEGACYMODEL' WHERE account_id = %s "
                     "AND snapshot_date = %s", (ACCOUNT_A, position_day))
        self.addCleanup(self.execute, "UPDATE position_snapshots SET model_code = %s "
                                      "WHERE model_code = 'LEGACYMODEL'", (model,))
        report = self.dry_run(self.statements[-8:])
        item = report["live_check"]["accounts"][ACCOUNT_A]
        self.assertEqual((report["status"], item["status"]), ("FAIL", "FAIL"))
        nav, positions = item["tables"]["nav_snapshots"], item["tables"]["position_snapshots"]
        self.assertEqual((nav["would_insert"], nav["missing_in_v2"], nav["dates_with_another_live_key"]), (1, 1, 1))
        self.assertEqual(positions["dates_with_another_live_key"], 1)
        self.assertEqual(sum("under another key" in text for text in item["failures"]), 2)
        self.assertEqual(report["live_check"]["accounts"][ACCOUNT_B]["status"], "PASS")

    def test_unchecked_accounts_fail_unless_allowed_and_are_checked_when_live_has_them(self):
        self.assertEqual(self.dry_run(self.statements[-8:], allow_unchecked=())["status"], "FAIL")
        # Live already holds a NAV row of the account without a snapshot, under another currency.
        self.add_account(ACCOUNT_C)
        self.addCleanup(self.execute, "DELETE FROM accounts WHERE account_id = %s", (ACCOUNT_C,), admin=True)
        day = (FIRST_RUN + timedelta(days=5)).isoformat()
        self.execute("INSERT INTO nav_snapshots(account_id, snapshot_date, currency, total_text, row_sha256, "
                     "last_seen_run_id) SELECT %s, snapshot_date, 'HKD', total_text, row_sha256, last_seen_run_id "
                     "FROM nav_snapshots WHERE account_id = %s AND snapshot_date = %s", (ACCOUNT_C, ACCOUNT_A, day))
        self.addCleanup(self.execute, "DELETE FROM nav_snapshots WHERE account_id = %s", (ACCOUNT_C,))
        item = self.dry_run(self.statements[-8:])["live_check"]["accounts"][ACCOUNT_C]
        self.assertEqual((item["status"], item["new_in_live"]), ("FAIL", False))
        self.assertEqual(item["tables"]["nav_snapshots"]["dates_with_another_live_key"], 1)

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

    def candidates(self, accounts, label, statements=None):
        from ibkr_etl.flow_backfill import load_candidates
        from ibkr_etl.switch_dry_run import statement_from_bytes

        parsed = [statement_from_bytes(f"s{i}.xml", content)
                  for i, content in enumerate(statements or self.statements)]
        return load_candidates(parsed, lambda s: s.content, accounts=accounts, work_dir=self.root / label)

    def plan_live(self, candidates, accounts):
        from ibkr_etl.flow_backfill import plan
        from ibkr_etl.switch_dry_run import open_read_only

        live = open_read_only(self.url, TENANT)
        try:
            return plan(candidates, accounts=accounts, live=live, tenant_id=TENANT)
        finally:
            live.rollback()
            live.close()

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
        import psycopg

        from ibkr_etl.flow_backfill import BackfillError, apply

        self.execute(
            "INSERT INTO reporting_snapshots(snapshot_id, subject_id, period_start, period_end, as_of_at, "
            "reporting_currency, formula_version, source_manifest_json, data_sha256, quality_status, status, "
            "created_at) VALUES (%s, %s, '2026-03-01', '2026-03-31', '2026-04-01', 'USD', 'test', '{}', %s, "
            "'pass', 'frozen', '2026-04-01')", (str(uuid.uuid4()), ACCOUNT_B, "0" * 64))
        candidates = self.candidates([ACCOUNT_A, ACCOUNT_B], "backfill")
        planned = self.plan_live(candidates, [ACCOUNT_A, ACCOUNT_B])
        a, b = planned["accounts"][ACCOUNT_A], planned["accounts"][ACCOUNT_B]
        # The 14-day pull already stored A's recent rows under the same keys.
        self.assertGreater(a["already_live_same"], 0)
        self.assertEqual(a["already_live_different"], 0)
        self.assertEqual(a["flows_added"], {})
        self.assertEqual(b["flows_added"], {(FIRST_RUN - timedelta(days=7)).isoformat(): 5000.0})
        self.assertEqual(b["frozen_periods_that_would_differ_from_live"], ["2026-03-31"])
        self.assertTrue(planned["flows_added"])

        with psycopg.connect(self.url) as connection:
            with self.assertRaises(BackfillError):
                apply(connection, planned, tenant_id=TENANT)
            done = apply(connection, planned, tenant_id=TENANT, accept_flow_changes=True)
        self.assertEqual(done["inserted"], planned["new_rows"])
        run = self.execute("SELECT run_type, status, row_count FROM ingestion_runs WHERE run_id = %s",
                           (done["run_id"],))
        self.assertEqual(run, [("v11-archive-flow-backfill", "success", planned["new_rows"])])

        again = self.plan_live(candidates, [ACCOUNT_A, ACCOUNT_B])
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

    def test_4_backfill_refuses_accounts_outside_the_tenant(self):
        import psycopg

        import medallion_flow_backfill as command
        from ibkr_etl.flow_backfill import BackfillError, apply

        self.add_account(OTHER_TENANT_ACCOUNT, OTHER_TENANT)
        accounts = [OTHER_TENANT_ACCOUNT, UNKNOWN_ACCOUNT]
        statements = [statement_for_run(day, accounts) for day in self.days]
        planned = self.plan_live(self.candidates(accounts, "foreign", statements), accounts)
        self.assertGreater(planned["new_rows"], 0)
        runs = self.execute("SELECT COUNT(*) FROM ingestion_runs")[0][0]
        with psycopg.connect(self.url) as connection:
            with self.assertRaisesRegex(BackfillError, "not in this tenant"):
                apply(connection, planned, tenant_id=TENANT, accept_flow_changes=True)
        self.assertEqual(self.execute("SELECT COUNT(*) FROM flex_rows WHERE account_id = ANY(%s)", (accounts,),
                                      admin=True)[0][0], 0)
        self.assertEqual(self.execute("SELECT COUNT(*) FROM ingestion_runs")[0][0], runs)

        path = self.root / "foreign.xml"
        path.write_bytes(statements[-1])
        output = io.StringIO()
        with patch.dict(os.environ, {"DATABASE_URL": self.url, "PORTFOLIO_TENANT_ID": TENANT}), \
                redirect_stdout(output):
            code = command.main(["--statement-file", str(path), "--account", OTHER_TENANT_ACCOUNT, "--apply",
                                 "--accept-flow-changes", "--report", str(self.root / "foreign.json")])
        self.assertEqual(code, 2)
        self.assertIn("not in this tenant", output.getvalue())
        self.assertNotIn(OTHER_TENANT_ACCOUNT, output.getvalue())

    def test_5_a_concurrent_writer_rolls_the_whole_backfill_back(self):
        import psycopg

        from ibkr_etl.flow_backfill import BackfillError, apply

        self.add_account(LATE_ACCOUNT)
        statements = [statement_for_run(day, (LATE_ACCOUNT,)) for day in self.days]
        planned = self.plan_live(self.candidates([LATE_ACCOUNT], "late", statements), [LATE_ACCOUNT])
        rows = planned["accounts"][LATE_ACCOUNT]["rows"]
        self.assertGreater(len(rows), 1)
        # Another job (not taking the ibkr-daily lock) stores one of the planned rows first.
        first = rows[0]
        run_id = self.execute("INSERT INTO ingestion_runs(run_type, started_at, status) "
                              "VALUES ('institutional-flex-daily', 'now', 'success') RETURNING run_id")[0][0]
        self.execute("INSERT INTO flex_rows(account_id, section, record_type, source_key, report_date, conid, "
                     "currency, row_sha256, data_json, first_seen_run_id, last_seen_run_id) "
                     "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                     (first["account_id"], first["section"], first["record_type"], first["source_key"],
                      first["report_date"], first["conid"], first["currency"], first["row_sha256"],
                      first["data_json"], run_id, run_id))
        with psycopg.connect(self.url) as connection:
            with self.assertRaisesRegex(BackfillError, "rolled back"):
                apply(connection, planned, tenant_id=TENANT)
        self.assertEqual(self.execute("SELECT COUNT(*) FROM flex_rows WHERE account_id = %s",
                                      (LATE_ACCOUNT,))[0][0], 1)
        self.assertEqual(self.execute("SELECT COUNT(*) FROM ingestion_runs WHERE run_type = "
                                      "'v11-archive-flow-backfill' AND status = 'running'")[0][0], 0)

    def test_6_nav_evidence_backfill_traces_every_silver_nav_day_and_changes_no_figure(self):
        import medallion_flow_backfill as command

        files = []
        for index, content in enumerate(self.statements):
            path = self.root / f"nav-{index}.xml"
            path.write_bytes(content)
            files += ["--statement-file", str(path)]
        accounts = ["--account", ACCOUNT_A, "--account", ACCOUNT_B]
        silver = {table: self.execute(f"SELECT md5(string_agg(row_sha256, ',' ORDER BY row_sha256)) FROM {table}")
                  for table in ("nav_snapshots", "position_snapshots", "cash_snapshots", "cash_transactions")}
        flows = self.execute("SELECT COUNT(*) FROM flex_rows WHERE section IN ('Transfers', 'CashTransactions')")

        def run(*extra):
            report = self.root / "nav-backfill.json"
            output = io.StringIO()
            with patch.dict(os.environ, {"DATABASE_URL": self.url, "PORTFOLIO_TENANT_ID": TENANT}), \
                    redirect_stdout(output):
                code = command.main(files + accounts + ["--sections", "nav", "--report", str(report), *extra])
            text = output.getvalue() + report.read_text()
            self.assertNotIn(ACCOUNT_A, text)
            self.assertNotIn(ACCOUNT_B, text)
            return code, json.loads(report.read_text()), output.getvalue()

        code, report, output = run("--live")
        self.assertEqual(code, 0, output)
        self.assertEqual(report["sections"], "nav")
        self.assertGreater(report["new_rows"], 0)
        for trace in report["nav_trace"].values():
            # account-sync's history has no bronze rows; the archive covers every silver day.
            self.assertGreater(trace["before"]["untraced"], 0)
            self.assertEqual((trace["after"]["traced"], trace["after"]["differs"], trace["after"]["untraced"]),
                             (trace["silver_days"], 0, 0))
        self.assertIn("medallion nav evidence backfill: dry_run", output)

        code, applied, _ = run("--apply")
        self.assertEqual((code, applied["applied"]["inserted"]), (0, report["new_rows"]))
        run_type = self.execute("SELECT run_type FROM ingestion_runs WHERE run_id = %s",
                                (applied["applied"]["run_id"],))
        self.assertEqual(run_type, [("v11-archive-nav-evidence-backfill",)])
        code, again, _ = run("--live")
        self.assertEqual((code, again["new_rows"]), (0, 0))
        for trace in again["nav_trace"].values():
            self.assertEqual(trace["before"]["traced"], trace["silver_days"])
        # Bronze only: silver and the flow sections are untouched.
        self.assertEqual({table: self.execute(f"SELECT md5(string_agg(row_sha256, ',' ORDER BY row_sha256)) "
                                              f"FROM {table}") for table in silver}, silver)
        self.assertEqual(self.execute("SELECT COUNT(*) FROM flex_rows WHERE section IN "
                                      "('Transfers', 'CashTransactions')"), flows)

        # A silver day whose total differs from IBKR's record is reported, and the dry run exits 1.
        day, total = self.execute("SELECT snapshot_date, total_text FROM nav_snapshots WHERE account_id = %s "
                                  "ORDER BY snapshot_date LIMIT 1", (ACCOUNT_A,))[0]
        change = "UPDATE nav_snapshots SET total_text = %s WHERE account_id = %s AND snapshot_date = %s"
        self.execute(change, ("1.00", ACCOUNT_A, day))
        try:
            code, differing, _ = run("--live")
        finally:
            self.execute(change, (total, ACCOUNT_A, day))
        self.assertEqual(code, 1)
        changed = [t for t in differing["nav_trace"].values() if t["after"]["differs"]]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["after"]["differing_dates"], [str(day)[:10]])


if __name__ == "__main__":
    unittest.main()
