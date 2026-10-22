"""U8 "as known on" history end to end on a real PostgreSQL, as docs/MEDALLION_AS_KNOWN_ON.md runs it.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped
otherwise. Builds its own database with every migration (074_silver_history.sql among them,
recording off), sets up the logins with scripts/bootstrap_postgres.py, and loads made-up statements
and account-sync copies as the jobs do. Then, in order: the history as 074 leaves it, the backfill
(dry run, refusal, apply), the past read back, recording, the FX step (the held file under the next
free number with --only-migration), and the undo. Synthetic data only.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from switch_fixtures import account_snapshot, edit_snapshot, v11_export
from ibkr_etl.silver_history import parse_time
from test_as_known_on_draft_db import (
    CANCELLATION, DIVIDEND, FX_FIRST, FX_RESTATED, ORIGINAL_SALE, REBOOKING, statement,
)

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
DRAFTS = ROOT / "infra" / "medallion" / "drafts"
UNDO = ROOT / "infra" / "medallion" / "undo"
TENANT, OTHER_TENANT = layers.TENANT, layers.OTHER_TENANT
ACCOUNT = "U9100001"  # v2's own statements
SYNCED = "U9100002"  # account-sync's copies of v1.1
OTHER_ACCOUNT = "U9200001"  # another tenant
APP, WEB, APPROVAL = "asof_test_app", "asof_test_web", "asof_test_approval"
TRACKED = ("trades", "cash_transactions", "fx_transactions", "fx_rates", "nav_snapshots")
ACCOUNT_IDS = re.compile(r"U9[12]\d{5}")
# 074 is in migrations/postgres (it ran in production on 21 Oct 2026). On its day the held FX
# file gets the next free number there.
NEXT = max(int(path.name[:3]) for path in layers.MIGRATIONS.glob("*.sql")) + 1
PART_A, PART_B = "074_silver_history.sql", f"{NEXT:03d}_fx_event_key.sql"
FX_LATER = dict(FX_FIRST, proceeds="128.50", realizedPL="0.50")

S1 = statement(ACCOUNT, "20261014", "20261015", nav={"20261014": "100000.00", "20261015": "100500.00"},
               trades=[ORIGINAL_SALE], cash=[DIVIDEND], fx=[FX_FIRST])
S2 = statement(ACCOUNT, "20261014", "20261016",
               nav={"20261014": "100000.00", "20261015": "100480.00", "20261016": "100700.00"},
               trades=[ORIGINAL_SALE, CANCELLATION, REBOOKING], cash=[DIVIDEND], fx=[FX_RESTATED])
S3 = statement(ACCOUNT, "20261015", "20261017",
               nav={"20261015": "100480.00", "20261016": "100650.00", "20261017": "100900.00"},
               trades=[ORIGINAL_SALE, CANCELLATION, REBOOKING], fx=[FX_RESTATED])
S4 = statement(ACCOUNT, "20261016", "20261018",
               nav={"20261016": "100650.00", "20261017": "100950.00", "20261018": "101000.00"})
S5 = statement(ACCOUNT, "20261015", "20261018", nav={"20261018": "101000.00"}, fx=[FX_LATER])
SYNCED_V11 = statement(SYNCED, "20261014", "20261015", nav={"20261014": "200000.00", "20261015": "200500.00"},
                       trades=[ORIGINAL_SALE], cash=[DIVIDEND])
OTHER = statement(OTHER_ACCOUNT, "20261014", "20261015", nav={"20261014": "300000.00"}, trades=[ORIGINAL_SALE])


def setUpModule():
    from ibkr_etl import db

    # The held FX file as its day will number it; nothing else changes.
    directory = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(directory.cleanup)
    migrations = Path(directory.name) / "postgres"
    migrations.mkdir()
    for path in layers.MIGRATIONS.glob("*.sql"):
        shutil.copy(path, migrations / path.name)
    shutil.copy(DRAFTS / "u8_fx_event_key.sql", migrations / PART_B)
    # CI's database job runs against the installed package: read the schema from this checkout.
    for name, value in (("POSTGRES_MIGRATIONS_DIR", migrations),
                        ("SQLITE_MIGRATION", ROOT / "migrations" / "001_init.sql")):
        patcher = patch.object(db, name, value)
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class SilverHistoryTests(unittest.TestCase):
    """One database through every step, in order (the tests share it, as the proof's do)."""

    @classmethod
    def setUpClass(cls):
        import psycopg

        from ibkr_etl.pilot_import import import_snapshot

        cls.environment = patch.dict(os.environ, {"IBKR_RUN_MIGRATIONS": "false", "IBKR_FLEX_LEGACY_KEY_COMPAT": "",
                                                  "IBKR_FX_EVENT_KEY": ""})
        cls.environment.start()
        cls.files = tempfile.TemporaryDirectory()
        cls.root = Path(cls.files.name)
        cls.copies = cls.root / "copies"
        cls.database = f"asof_build_{uuid.uuid4().hex[:10]}"
        cls.marks: list[tuple[str, object]] = []
        cls.runs: dict[str, int] = {}
        try:
            with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
                for role in (APP, WEB, APPROVAL):
                    admin.execute(f"DROP ROLE IF EXISTS {role}")
            applied = layers._build(cls.database)
            # The logins as today's bootstrap leaves them; it applies nothing.
            cls._bootstrap("--only-migration", applied[-1], "--allow-pending", PART_B)
            cls.app_url = layers._url(cls.database, APP)
            # Before the backfill, recording off: v2's loads and account-sync's imports, as production had them.
            export = v11_export(cls.root / "v11", [SYNCED_V11], "2026-10-16T00:00:00+00:00")
            first = account_snapshot(export, cls.copies / "c1.sqlite", SYNCED, "2026-10-01",
                                     "2026-10-16T00:00:00+00:00")
            second = cls._copy(first, "c2.sqlite", "200100.00")
            cls.runs["s1"] = cls._load(S1)
            cls._mark("s1")
            import_snapshot(database_url=cls.app_url, local_path=first, account_id=SYNCED, start_date="2026-10-01",
                            tenant_id=TENANT, source_reference="test/c1.sqlite")
            cls._mark("c1")
            cls.runs["s2"] = cls._load(S2)
            cls._mark("s2")
            import_snapshot(database_url=cls.app_url, local_path=second, account_id=SYNCED, start_date="2026-10-01",
                            tenant_id=TENANT, source_reference="test/c2.sqlite")
            cls._mark("c2")
            cls._load(OTHER, tenant=OTHER_TENANT)
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        layers._drop(cls.database, roles=(APP, WEB, APPROVAL))
        cls.files.cleanup()
        cls.environment.stop()

    # --- helpers ---

    @classmethod
    def _bootstrap(cls, *args) -> str:
        environment = {
            "DATABASE_ADMIN_URL": layers._url(cls.database, layers.OWNER), "PORTFOLIO_TENANT_ID": TENANT,
            "DATABASE_APP_ROLE": APP, "DATABASE_APP_PASSWORD": layers.PASSWORD,
            "DATABASE_WEB_ROLE": WEB, "DATABASE_WEB_PASSWORD": layers.PASSWORD,
            "DATABASE_APPROVAL_ROLE": APPROVAL, "DATABASE_APPROVAL_PASSWORD": layers.PASSWORD,
        }
        output = io.StringIO()
        with patch.dict(os.environ, environment), contextlib.redirect_stdout(output):
            layers._script("bootstrap_postgres").main(list(args))
        return output.getvalue()

    @classmethod
    def _copy(cls, source: Path, name: str, total: str) -> Path:
        """Another day's copy in which v1.1 learned a new NAV for 15 Oct."""
        target = cls.copies / name
        shutil.copy(source, target)
        edit_snapshot(target, [f"UPDATE nav_snapshots SET total_text = '{total}' WHERE snapshot_date = '2026-10-15'"])
        return target

    @classmethod
    def _load(cls, content: bytes, tenant: str = TENANT) -> int:
        from ibkr_etl.config import Settings
        from ibkr_etl.service import ingest_content

        settings = Settings(project_root=cls.root, flex_token="", flex_query_id="",
                            database_path=cls.root / "unused.sqlite3", raw_report_dir=cls.root / "raw",
                            log_dir=cls.root / "logs", timezone_name="Asia/Hong_Kong", daily_lookback_days=14,
                            user_agent="test", request_timeout_seconds=10, max_wait_seconds=20,
                            database_url=cls.app_url, tenant_id=tenant)
        result = ingest_content(settings, content, run_type="asof-test")
        cls.last_counts = result["reconciliation"]["record_types"]
        return result["run_id"]

    @classmethod
    def _owner(cls, tenant: str = TENANT):
        return layers._connect(cls.database, tenant=tenant)

    @classmethod
    def _mark(cls, name: str) -> None:
        """What each table held after a load, and when; the next load starts in a later second."""
        cls.marks.append((name, cls._now(), cls._state()))
        time.sleep(1.1)  # the runs keep their times to the second

    @classmethod
    def _now(cls):
        with cls._owner() as owner:
            return owner.execute("SELECT clock_timestamp()").fetchone()[0]

    @classmethod
    def _state(cls, at=None, accounts=(ACCOUNT, SYNCED)) -> dict:
        """Each table's rows for the accounts, apart from last_seen_run_id; at: as known then."""
        found = {}
        with cls._owner() as owner:
            for table in TRACKED:
                columns = [row[0] for row in owner.execute(
                    "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 AND NOT "
                    "attisdropped AND attname NOT IN ('last_seen_run_id', 'event_key') ORDER BY attnum",
                    (f"silver.{table}",))]
                source = f"silver.{table}" if at is None else f"silver.{table}_as_known(%s)"
                parameters = (list(accounts),) if at is None else (at, list(accounts))
                found[table] = sorted(owner.execute(
                    f"SELECT {', '.join(columns)} FROM {source} WHERE account_id = ANY(%s) "
                    "AND source_key IS DISTINCT FROM 'manual-1'" if "source_key" in columns else
                    f"SELECT {', '.join(columns)} FROM {source} WHERE account_id = ANY(%s)",
                    parameters).fetchall(), key=repr)
        return found

    def _rows(self, sql: str, *parameters, tenant: str = TENANT):
        with self._owner(tenant) as owner:
            return owner.execute(sql, parameters).fetchall()

    def _tool(self, *args, report: Path | None = None) -> tuple[int, str]:
        """Run scripts/medallion_silver_history.py as the owner; its output never names an account."""
        options = ["--tenant", TENANT, "--tenant", OTHER_TENANT, "--raw-dir", str(self.root / "raw"),
                   "--snapshot-dir", str(self.copies), "--lock-seconds", "5"]
        if report:
            options += ["--report", str(report)]
        output = io.StringIO()
        with patch.dict(os.environ, {"DATABASE_ADMIN_URL": layers._url(self.database, layers.OWNER)}), \
                contextlib.redirect_stdout(output):
            code = layers._script("medallion_silver_history").main([*args, *options])
        text = output.getvalue() + (report.read_text(encoding="utf-8") if report and report.exists() else "")
        self.assertIsNone(ACCOUNT_IDS.search(text), "the tool printed an account number")
        self.assertNotIn(layers.PASSWORD, text)
        return code, output.getvalue()

    def _history_rows(self) -> int:
        with self._owner() as owner:
            return sum(owner.execute(f"SELECT count(*) FROM silver.{table}_history").fetchone()[0]
                       for table in TRACKED)

    def _recording(self) -> bool:
        return self._rows("SELECT recording FROM public.silver_history_settings")[0][0]

    def _run_sql(self, path: Path) -> None:
        with self._owner() as owner:
            owner.execute(path.read_text(encoding="utf-8"))

    # --- in order ---

    def test_1_the_migration_leaves_recording_off_and_logins_only_read_the_history(self):
        self.assertFalse(self._recording())
        self.assertEqual(self._history_rows(), 0)
        objects = [f"silver.{table}_history" for table in TRACKED] + ["public.silver_history_settings"]

        def rights():
            with self._owner() as owner:
                return {(role, name): [row[0] for row in owner.execute(
                    "SELECT p FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p "
                    "WHERE has_table_privilege(%s, %s::regclass, p) ORDER BY 1", (role, name))]
                    for role in (APP, WEB, APPROVAL) for name in objects}

        # Each history table holds its table's columns, in order, after its own; a later migration
        # that adds a column to the table must add it here too.
        with self._owner() as owner:
            for table in TRACKED:
                columns = {name: owner.execute(
                    "SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid = "
                    "%s::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum", (name,)).fetchall()
                    for name in (f"silver.{table}", f"silver.{table}_history")}
                own = len(columns[f"silver.{table}_history"]) - len(columns[f"silver.{table}"])
                self.assertEqual(columns[f"silver.{table}_history"][own:], columns[f"silver.{table}"], table)
                self.assertEqual(owner.execute(f"SELECT count(*) FROM silver.{table}_as_known(now())").fetchone(),
                                 (0,))
        before = rights()
        for (role, name), granted in before.items():
            self.assertFalse(set(granted) - {"SELECT"}, f"{role} can write {name}")
        self.assertEqual(before[(APP, "silver.trades_history")], ["SELECT"])
        # Bootstrap's schema-wide grants run again on every deployment; the history stays read-only.
        output = self._bootstrap("--only-migration", PART_A, "--allow-pending", PART_B)
        self.assertIn("Migrations applied: none.", output)  # 074 is applied and recorded
        self.assertEqual(rights(), before)
        # Recording is off until the backfill: loads in between leave no history.
        self.runs["s3"] = self._load(S3)
        self._mark("s3")
        self.assertEqual(self._history_rows(), 0)
        with self._owner() as owner:
            # A row no kept file explains, and a row someone repaired by hand.
            owner.execute("INSERT INTO silver.cash_transactions (account_id, source_key, effective_date, "
                          "currency, amount_text, row_sha256, last_seen_run_id) VALUES (%s, 'manual-1', "
                          "'2026-10-14', 'USD', '1.00', 'synthetic', %s)", (ACCOUNT, self.runs["s1"]))
            owner.execute("UPDATE silver.nav_snapshots SET total_text = '99999.00' WHERE account_id = %s "
                          "AND snapshot_date = '2026-10-14'", (ACCOUNT,))

    def test_2_the_backfill_reports_refuses_then_applies_and_checks(self):
        report = self.root / "dry-run.json"
        code, output = self._tool("backfill", report=report)
        self.assertEqual(code, 1, output)
        self.assertIn("backfill: NOT READY", output)
        self.assertRegex(output, r"default nav_snapshots: .*differs=1")
        self.assertRegex(output, r"default cash_transactions: .*unexplained=1")
        self.assertRegex(output, r"tenant#1 trades: .*matched=1")
        self.assertEqual((self._history_rows(), self._recording()), (0, False))

        code, output = self._tool("backfill", "--apply")
        self.assertEqual(code, 2, output)
        self.assertIn("ERROR HistoryError: nav_snapshots", output)
        self.assertEqual((self._history_rows(), self._recording()), (0, False))

        code, output = self._tool("backfill", "--apply", "--accept-differences")
        self.assertEqual(code, 0, output)
        self.assertIn("backfill: APPLIED", output)
        self.assertTrue(self._recording())
        code, output = self._tool("check")
        self.assertEqual(code, 0, output)
        self.assertIn("check: PASS", output)
        self.assertRegex(output, r"tenant#1 trades: current_versions_not_in_table=0")

    def test_3_the_past_reads_back_as_each_load_left_it(self):
        for name, moment, state in self.marks:
            with self.subTest(name):
                self.assertEqual(self._state(moment), state)
        self.assertEqual(self._state(self.marks[0][1].replace(year=2025)), {table: [] for table in TRACKED})
        # The row no file explains: known from when its run completed.
        [(opened, since, note)] = self._rows(
            "SELECT opened_by, recorded_from, opened_note FROM silver.cash_transactions_history "
            "WHERE source_key = 'manual-1'")
        [(completed,)] = self._rows("SELECT completed_at FROM public.ingestion_runs WHERE run_id = %s",
                                    self.runs["s1"])
        self.assertEqual((opened, since, note),
                         ("baseline", parse_time(completed), "U8 backfill: not explained by a kept raw file"))
        # The repaired row: the loaded value until the backfill, then the repair as a baseline.
        self.assertEqual(self._rows(
            "SELECT total_text, opened_by, closed_by FROM silver.nav_snapshots_history WHERE account_id = %s "
            "AND snapshot_date = '2026-10-14' ORDER BY version_id", ACCOUNT),
            [("100000.00", "first", "restated"), ("99999.00", "baseline", None)])
        # Account-sync's restated NAV is a version recorded when its copy was imported.
        self.assertEqual(self._rows(
            "SELECT total_text, opened_by, recorded_run_id IS NOT NULL FROM silver.nav_snapshots_history "
            "WHERE account_id = %s AND snapshot_date = '2026-10-15' ORDER BY version_id", SYNCED),
            [("200500.00", "first", True), ("200100.00", "restated", True)])

    def test_4_writes_after_the_switch_on_are_recorded(self):
        import psycopg
        from ibkr_etl.pilot_import import import_snapshot

        before = self._now()
        self._load(S4)
        import_snapshot(database_url=self.app_url, local_path=self._copy(self.copies / "c1.sqlite", "c3.sqlite",
                                                                         "200050.00"),
                        account_id=SYNCED, start_date="2026-10-01", tenant_id=TENANT, source_reference="test/c3")
        self.assertEqual(self._rows(
            "SELECT h.total_text, h.opened_by FROM silver.nav_snapshots_history h WHERE h.recorded_from > %s "
            "AND h.account_id = ANY(%s) ORDER BY h.account_id, h.snapshot_date", before, [ACCOUNT, SYNCED]),
            [("100950.00", "restated"), ("101000.00", "first"), ("200050.00", "restated")])
        self.assertEqual(self._state(self._now()), self._state())
        self.assertEqual(self._tool("check")[0], 0)
        # Only the trigger writes the history, and a table cannot be emptied while recording.
        with layers._connect(self.database, APP) as app:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                app.execute("DELETE FROM silver.trades_history")
        with self._owner() as owner:
            with self.assertRaisesRegex(psycopg.Error, "TRUNCATE is refused while recording is on"):
                owner.execute("TRUNCATE silver.trades")
        with self._owner() as owner:
            with self.assertRaisesRegex(psycopg.Error, "append-only"):
                owner.execute("UPDATE silver.nav_snapshots_history SET total_text = '1.00'")

    def test_5_the_fx_step_keeps_one_row_per_fx_event(self):
        output = self._bootstrap("--only-migration", PART_B)
        self.assertIn(f"Migrations applied: {PART_B}.", output)
        fx = "SELECT proceeds_text, event_key IS NOT NULL FROM silver.fx_transactions WHERE account_id = %s ORDER BY 1"
        self.assertEqual(self._rows(fx, ACCOUNT), [("128.60", False), ("128.70", False)])
        code, output = self._tool("fx-merge", report=self.root / "fx.json")
        self.assertEqual(code, 0, output)
        self.assertEqual(self._rows(fx, ACCOUNT), [("128.60", False), ("128.70", False)])  # a dry run
        code, output = self._tool("fx-merge", "--apply")
        self.assertEqual(code, 0, output)
        self.assertEqual(self._rows(fx, ACCOUNT), [("128.60", True)])
        self.assertEqual(self._rows(
            "SELECT proceeds_text, closed_by, closed_note FROM silver.fx_transactions_history WHERE account_id = %s "
            "AND closed_by = 'merged'", ACCOUNT),
            [("128.70", "merged", "U8 FX step: the same FX event re-sent with new amounts")])
        self.assertEqual(self._tool("check")[0], 0)
        with patch.dict(os.environ, {"IBKR_FX_EVENT_KEY": "true"}):
            self._load(S5)
        self.assertEqual(self.last_counts.get("fx_events_restated"), 1)
        self.assertEqual(self._rows(fx, ACCOUNT), [("128.50", True)])
        self.assertEqual(self._tool("check")[0], 0)

    def test_6_the_undo_puts_the_tables_back(self):
        import psycopg

        with self.assertRaisesRegex(psycopg.Error, "fx-unmerge"):
            self._run_sql(DRAFTS / "u8_fx_event_key_undo.sql")
        code, output = self._tool("fx-unmerge", "--apply")
        self.assertEqual(code, 0, output)
        self.assertEqual(self._rows("SELECT proceeds_text FROM silver.fx_transactions WHERE account_id = %s "
                                    "ORDER BY 1", ACCOUNT), [("128.50",), ("128.70",)])
        tables = self._state(accounts=(ACCOUNT, SYNCED, OTHER_ACCOUNT))
        with self.assertRaisesRegex(psycopg.Error, "u8_fx_event_key_undo"):
            self._run_sql(UNDO / "074_silver_history_undo.sql")
        self._run_sql(DRAFTS / "u8_fx_event_key_undo.sql")
        with patch.dict(os.environ, {"IBKR_FX_EVENT_KEY": "true"}), self.assertRaisesRegex(RuntimeError, "event_key"):
            self._load(S5)
        self._run_sql(UNDO / "074_silver_history_undo.sql")
        gone = [f"silver.{table}_history" for table in TRACKED] + ["public.silver_history_settings"]
        self.assertEqual(self._rows("SELECT count(to_regclass(name)) FROM unnest(%s::text[]) name", gone), [(0,)])
        self.assertEqual(self._state(accounts=(ACCOUNT, SYNCED, OTHER_ACCOUNT)), tables)
        self.assertEqual(self._rows("SELECT count(*) FROM public.fx_transactions WHERE account_id = %s", ACCOUNT),
                         [(2,)])
        self._load(S5)  # loads carry on as before
