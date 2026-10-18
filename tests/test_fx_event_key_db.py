"""U8's FX step on a real PostgreSQL, in its go-live order (docs/MEDALLION_AS_KNOWN_ON.md, "The FX step in
October"): the history migration as 074 with recording on, the read-only FX check, the FX migration as
075 by itself, the merge (dry run, apply, apply again), the next days' loads with IBKR_FX_EVENT_KEY on,
and the undo.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one). Skipped
otherwise. The held files are numbered as their day will number them, unless migrations/postgres already
has them. Synthetic data only.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from test_as_known_on_draft_db import FX_FIRST, FX_RESTATED, statement

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
DRAFTS = ROOT / "infra" / "medallion" / "drafts"
TENANT = layers.TENANT
ACCOUNT = "U9300001"
APP, WEB, APPROVAL = "fxkey_test_app", "fxkey_test_web", "fxkey_test_approval"
ACCOUNT_IDS = re.compile(r"U9\d{6}")
AMOUNTS = re.compile(r"\b(128|256|257|512|515)\.\d\d\b")


def _held(name: str, draft: str) -> tuple[str, Path]:
    """The migration's name on its day, and its file: migrations/postgres's own once it has moved there."""
    moved = sorted(layers.MIGRATIONS.glob(f"*_{name}.sql"))
    return (moved[0].name, moved[0]) if moved else (f"{'074' if name == 'silver_history' else '075'}_{name}.sql",
                                                    DRAFTS / draft)


HISTORY, HISTORY_FILE = _held("silver_history", "u8_silver_history.sql")
FX_STEP, FX_STEP_FILE = _held("fx_event_key", "u8_fx_event_key.sql")

FX_LATER = dict(FX_FIRST, proceeds="128.50", realizedPL="0.50")
# Another conversion, which account-sync copied from v1.1 under v1.1's key before IBKR re-sent it.
SYNCED_FIRST = dict(FX_FIRST, dateTime="20261015;170000", quantity="4000", cost="-512.00", proceeds="515.00",
                    realizedPL="3.00")
SYNCED_RESTATED = dict(SYNCED_FIRST, proceeds="514.80", realizedPL="2.80")
STEADY = dict(FX_FIRST, dateTime="20261015;180000", quantity="2000", cost="-256.00", proceeds="257.40",
              realizedPL="1.40")


def _statement(to_date: str, *fx) -> bytes:
    return statement(ACCOUNT, "20261015", to_date, nav={to_date: "100000.00"}, fx=list(fx))


S1 = _statement("20261015", FX_FIRST, SYNCED_FIRST, STEADY)
S2 = _statement("20261016", FX_RESTATED, SYNCED_RESTATED, STEADY)
S3 = _statement("20261017", FX_LATER, SYNCED_RESTATED, STEADY)


def setUpModule():
    from ibkr_etl import db

    directory = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(directory.cleanup)
    migrations = Path(directory.name) / "postgres"
    migrations.mkdir()
    for path in layers.MIGRATIONS.glob("*.sql"):
        shutil.copy(path, migrations / path.name)
    shutil.copy(HISTORY_FILE, migrations / HISTORY)
    shutil.copy(FX_STEP_FILE, migrations / FX_STEP)
    for name, value in (("POSTGRES_MIGRATIONS_DIR", migrations),
                        ("SQLITE_MIGRATION", ROOT / "migrations" / "001_init.sql")):
        patcher = patch.object(db, name, value)
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class FxEventKeyTests(unittest.TestCase):
    """One database through the FX step, in order."""

    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.environment = patch.dict(os.environ, {"IBKR_RUN_MIGRATIONS": "false", "IBKR_FLEX_LEGACY_KEY_COMPAT": "",
                                                  "IBKR_FX_EVENT_KEY": ""})
        cls.environment.start()
        cls.files = tempfile.TemporaryDirectory()
        cls.root = Path(cls.files.name)
        cls.database = f"fxkey_{uuid.uuid4().hex[:10]}"
        try:
            with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
                for role in (APP, WEB, APPROVAL):
                    admin.execute(f"DROP ROLE IF EXISTS {role}")
            applied = layers._build(cls.database)
            cls.pending = [name for name in (HISTORY, FX_STEP) if name not in applied]
            cls._bootstrap("--only-migration", applied[-1], *cls._pending_except(None))
            cls.app_url = layers._url(cls.database, APP)
            # Today, with the setting off: the re-sent rows are second rows.
            cls._load(S1)
            with cls._owner() as owner:
                owner.execute("UPDATE silver.fx_transactions SET source_key = 'v11:' || source_key "
                              "WHERE account_id = %s AND quantity_text = '4000'", (ACCOUNT,))
            cls._load(S2)
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
    def _pending_except(cls, name: str | None) -> list[str]:
        others = [pending for pending in cls.pending if pending != name]
        return ["--allow-pending", ",".join(others)] if others else []

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
    def _load(cls, content: bytes, setting: str = "") -> dict:
        from ibkr_etl.config import Settings
        from ibkr_etl.service import ingest_content

        settings = Settings(project_root=cls.root, flex_token="", flex_query_id="",
                            database_path=cls.root / "unused.sqlite3", raw_report_dir=cls.root / "raw",
                            log_dir=cls.root / "logs", timezone_name="Asia/Hong_Kong", daily_lookback_days=14,
                            user_agent="test", request_timeout_seconds=10, max_wait_seconds=20,
                            database_url=cls.app_url, tenant_id=TENANT)
        with patch.dict(os.environ, {"IBKR_FX_EVENT_KEY": setting}):
            return ingest_content(settings, content, run_type="fxkey-test")["reconciliation"]["record_types"]

    @classmethod
    def _owner(cls):
        return layers._connect(cls.database, tenant=TENANT)

    def _rows(self, sql: str, *parameters):
        with self._owner() as owner:
            return owner.execute(sql, parameters).fetchall()

    def _fx(self) -> list[tuple]:
        return self._rows("SELECT quantity_text, proceeds_text FROM silver.fx_transactions WHERE account_id = %s "
                          "ORDER BY 1, 2", ACCOUNT)

    def _versions(self) -> int:
        return self._rows("SELECT count(*) FROM silver.fx_transactions_history")[0][0]

    def _tool(self, *args) -> tuple[int, str, dict]:
        """scripts/medallion_silver_history.py as the owner; neither output nor report names an account or amount."""
        report = self.root / f"report-{uuid.uuid4().hex[:6]}.json"
        output = io.StringIO()
        with patch.dict(os.environ, {"DATABASE_ADMIN_URL": layers._url(self.database, layers.OWNER)}), \
                contextlib.redirect_stdout(output):
            code = layers._script("medallion_silver_history").main(
                [*args, "--tenant", TENANT, "--raw-dir", str(self.root / "raw"), "--lock-seconds", "5",
                 "--report", str(report)])
        written = report.read_text(encoding="utf-8") if report.exists() else "{}"
        for text in (output.getvalue(), written):
            self.assertIsNone(ACCOUNT_IDS.search(text), "the tool printed an account number")
            self.assertIsNone(AMOUNTS.search(text), "the tool printed an amount")
        return code, output.getvalue(), json.loads(written)

    def _fx_counts(self, report: dict) -> dict:
        return report["tenants"]["default"]["tables"]["fx_transactions"]

    # --- in order ---

    def test_1_with_recording_on_the_check_counts_the_duplicates(self):
        import psycopg

        if HISTORY in self.pending:
            # The FX migration refuses, changing nothing, before the history migration.
            with self._owner() as owner, self.assertRaisesRegex(psycopg.Error, "needs the history migration"):
                owner.execute(FX_STEP_FILE.read_text(encoding="utf-8"))
            self.assertIn(f"Migrations applied: {HISTORY}.",
                          self._bootstrap("--only-migration", HISTORY, *self._pending_except(HISTORY)))
        self.assertEqual(self._fx(), [("1000", "128.60"), ("1000", "128.70"), ("2000", "257.40"),
                                      ("4000", "514.80"), ("4000", "515.00")])
        code, output, _ = self._tool("backfill", "--apply", "--accept-differences")
        self.assertEqual(code, 0, output)
        code, output, report = self._tool("fx-check")
        self.assertEqual(code, 1, output)
        self.assertIn("fx-check: DUPLICATES", output)
        self.assertFalse(report["event_key_column"])
        counts = self._fx_counts(report)
        self.assertEqual({name: counts[name] for name in (
            "rows", "events_with_more_than_one_current_row", "rows_beyond_one_per_event",
            "events_with_more_than_one_current_row_after_merge", "rows_the_merge_removes",
            "events_whose_amounts_change", "account_currency_totals_that_change", "found_by_raw_record",
            "possible_resends_outside_the_rule", "history_rows_closed_as_merged")},
            {"rows": 5, "events_with_more_than_one_current_row": 2, "rows_beyond_one_per_event": 2,
             "events_with_more_than_one_current_row_after_merge": 0, "rows_the_merge_removes": 2,
             "events_whose_amounts_change": 2, "account_currency_totals_that_change": 1, "found_by_raw_record": 1,
             "possible_resends_outside_the_rule": 0, "history_rows_closed_as_merged": 0})
        self.assertEqual((report["fx_records_without_report_date"], report["statement_files_missing"],
                          report["statements_read"]), (0, 0, 2))

    def test_2_the_fx_step_merges_once_and_a_second_run_finds_nothing(self):
        output = self._bootstrap("--only-migration", FX_STEP, *self._pending_except(FX_STEP))
        self.assertIn(f"Migrations applied: {FX_STEP}.", output)
        before, versions = self._fx(), self._versions()
        code, output, report = self._tool("fx-merge")
        self.assertEqual(code, 0, output)
        self.assertIn("fx-merge: READY", output)
        self.assertRegex(output, r"default fx_transactions: .*merged=2")
        self.assertEqual((self._fx(), self._versions()), (before, versions))  # the dry run wrote nothing

        code, output, report = self._tool("fx-merge", "--apply")
        self.assertEqual(code, 0, output)
        self.assertEqual(self._fx(), [("1000", "128.60"), ("2000", "257.40"), ("4000", "514.80")])
        self.assertEqual(self._rows("SELECT count(*), count(event_key) FROM silver.fx_transactions")[0], (3, 3))
        self.assertEqual(self._rows(
            "SELECT quantity_text, proceeds_text, closed_note FROM silver.fx_transactions_history "
            "WHERE closed_by = 'merged' ORDER BY 1"),
            [("1000", "128.70", "U8 FX step: the same FX event re-sent with new amounts"),
             ("4000", "515.00", "U8 FX step: the same FX event re-sent with new amounts")])
        merged, versions = self._fx(), self._versions()
        code, output, report = self._tool("fx-merge", "--apply")
        self.assertEqual(code, 0, output)
        self.assertNotIn("merged=", output)
        self.assertEqual(self._fx_counts(report)["events"], 3)
        self.assertEqual((self._fx(), self._versions()), (merged, versions))
        self.assertEqual(self._tool("check")[0], 0)

    def test_3_the_next_days_loads_keep_one_row_per_event(self):
        versions = self._versions()
        counts = self._load(S2, "true")  # the same window again
        self.assertNotIn("fx_events_restated", counts)
        self.assertEqual(self._fx(), [("1000", "128.60"), ("2000", "257.40"), ("4000", "514.80")])
        self.assertEqual(self._versions(), versions)
        counts = self._load(S3, "true")  # IBKR restates the first conversion again
        self.assertEqual(counts.get("fx_events_restated"), 1)
        self.assertEqual(self._fx(), [("1000", "128.50"), ("2000", "257.40"), ("4000", "514.80")])
        self.assertEqual(self._rows(
            "SELECT proceeds_text, opened_by, closed_by FROM silver.fx_transactions_history "
            "WHERE quantity_text = '1000' ORDER BY version_id"),
            [("128.70", "first", "merged"), ("128.60", "first", "restated"), ("128.60", "restated", "restated"),
             ("128.50", "restated", None)])
        code, output, report = self._tool("fx-check")
        self.assertEqual(code, 0, output)
        self.assertIn("fx-check: PASS", output)
        counts = self._fx_counts(report)
        self.assertEqual((counts["rows"], counts["event_key_filled"], counts["events_with_more_than_one_current_row"],
                          counts["rows_the_merge_removes"], counts["history_rows_closed_as_merged"]), (3, 3, 0, 0, 2))
        self.assertEqual(self._tool("check")[0], 0)

    def test_4_the_undo_puts_back_todays_rows_and_loads_carry_on(self):
        code, output, report = self._tool("fx-unmerge")
        self.assertEqual(code, 0, output)
        self.assertEqual(self._fx_counts(report), {"rows_to_put_back": 2})
        self.assertEqual(self._tool("fx-unmerge", "--apply")[0], 0)
        self.assertEqual(self._tool("fx-unmerge", "--apply")[2]["tenants"]["default"]["tables"]["fx_transactions"],
                         {"rows_to_put_back": 0})
        self.assertEqual(self._fx(), [("1000", "128.50"), ("1000", "128.70"), ("2000", "257.40"),
                                      ("4000", "514.80"), ("4000", "515.00")])
        with self._owner() as owner:
            owner.execute((DRAFTS / "u8_fx_event_key_undo.sql").read_text(encoding="utf-8"))
        code, output, report = self._tool("fx-check")
        self.assertEqual(code, 1, output)
        self.assertFalse(report["event_key_column"])
        self.assertEqual(self._fx_counts(report)["events_with_more_than_one_current_row"], 2)
        self._load(S3)  # loads carry on as today
        self.assertEqual(len(self._fx()), 5)
        self.assertEqual(self._tool("check")[0], 0)


if __name__ == "__main__":
    unittest.main()
