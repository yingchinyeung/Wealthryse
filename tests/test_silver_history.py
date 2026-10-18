"""U8 "as known on" history (docs/MEDALLION_AS_KNOWN_ON.md) without a database server.

The loader's FX event step (IBKR_FX_EVENT_KEY) on SQLite, the backfill's replay of raw statements
and account-sync copies, its comparison with live rows, the FX merge plan and the tool's helpers.
Synthetic data only. tests/test_silver_history_db.py runs the backfill, the check, the FX step and
the undo on PostgreSQL with the held migrations.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import shutil
import sqlite3
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from ibkr_etl import silver_history as history
from ibkr_etl.db import DEFAULT_TENANT_ID, initialize, start_run
from ibkr_etl.loader import fx_event_identity, fx_event_key, fx_transaction_values, load_report
from ibkr_etl.parser import parse_flex_xml
from switch_fixtures import account_snapshot, edit_snapshot, v11_export
from test_as_known_on_draft_db import (
    CANCELLATION, DIVIDEND, FX_FIRST, FX_RESTATED, ORIGINAL_SALE, REBOOKING, statement,
)

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT, OTHER = "U9100001", "U9100002"
T0 = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 18, 4, 0, tzinfo=timezone.utc)
INTEREST = dict(transactionID="C201", reportDate="20261015", dateTime="20261015;160000",
                type="Broker Interest Received", description="USD CREDIT INT", amount="3.10")
TWIN = dict(FX_FIRST, proceeds="128.90", realizedPL="0.90")  # alike apart from the amounts IBKR restates

FIRST = statement(ACCOUNT, "20261014", "20261015", nav={"20261014": "100000.00", "20261015": "100500.00"},
                  trades=[ORIGINAL_SALE], cash=[DIVIDEND], fx=[FX_FIRST])
SECOND = statement(ACCOUNT, "20261014", "20261016",
                   nav={"20261014": "100000.00", "20261015": "100480.00", "20261016": "100700.00"},
                   trades=[ORIGINAL_SALE, CANCELLATION, REBOOKING], cash=[DIVIDEND], fx=[FX_RESTATED])
# The same window again, without the dividend.
THIRD = statement(ACCOUNT, "20261014", "20261016",
                  nav={"20261014": "100000.00", "20261015": "100480.00", "20261016": "100700.00"},
                  trades=[ORIGINAL_SALE, CANCELLATION, REBOOKING], cash=[INTEREST], fx=[FX_RESTATED])


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fx_records(content: bytes):
    return [row for row in parse_flex_xml(content).rows if row.record_type.lower() == "fxtransaction"]


def _event_key(row, occurrence: int = 1) -> str:
    return fx_event_key(fx_event_identity(fx_transaction_values(row, 0)), occurrence)


def _tool():
    spec = importlib.util.spec_from_file_location("medallion_silver_history",
                                                  ROOT / "scripts" / "medallion_silver_history.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FxEventKeyLoaderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "silver.sqlite"
        initialize(path)
        self.connection = sqlite3.connect(path)
        self.addCleanup(self.connection.close)

    def _load(self, content: bytes, setting: str = "") -> dict:
        with patch.dict(os.environ, {"IBKR_FX_EVENT_KEY": setting, "IBKR_FLEX_LEGACY_KEY_COMPAT": ""}):
            run_id = start_run(self.connection, "test", None, None)
            counts = load_report(self.connection, parse_flex_xml(content), run_id)
        self.connection.commit()
        return counts

    def _add_column(self):
        # What the FX step's migration adds on PostgreSQL.
        self.connection.execute("ALTER TABLE fx_transactions ADD COLUMN event_key TEXT")
        self.connection.execute("CREATE UNIQUE INDEX fx_transactions_event_key ON fx_transactions (account_id, "
                                "event_key) WHERE event_key IS NOT NULL")
        self.connection.commit()

    def _fx(self):
        return self.connection.execute("SELECT source_key, proceeds_text, event_key FROM fx_transactions "
                                       "ORDER BY proceeds_text").fetchall()

    def test_off_by_default_it_never_reads_or_writes_event_key(self):
        sent: list[str] = []
        self.connection.set_trace_callback(sent.append)
        counts = {**self._load(FIRST), **self._load(SECOND)}
        self.connection.set_trace_callback(None)
        self.assertTrue(any("INSERT INTO fx_transactions" in sql for sql in sent))
        self.assertEqual([sql for sql in sent if "event_key" in sql], [])
        self.assertNotIn("fx_events_restated", counts)
        # As today: the FX row re-sent with new amounts is a second row.
        self.assertEqual([row[1] for row in self.connection.execute(
            "SELECT source_key, proceeds_text FROM fx_transactions ORDER BY proceeds_text")], ["128.60", "128.70"])

    def test_off_it_leaves_the_column_alone_once_it_exists(self):
        self._add_column()
        self._load(FIRST)
        self._load(SECOND)
        self.assertEqual(self.connection.execute("SELECT count(*), count(event_key) FROM fx_transactions").fetchone(),
                         (2, 0))

    def test_on_without_the_column_it_refuses_before_writing(self):
        with self.assertRaisesRegex(RuntimeError, "IBKR_FX_EVENT_KEY is on but fx_transactions has no event_key"):
            self._load(FIRST, "true")
        self.connection.rollback()
        for table in ("flex_rows", "trades", "fx_transactions", "nav_snapshots"):
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0, table)

    def test_on_a_re_sent_fx_event_updates_its_own_row(self):
        self._add_column()
        self.assertNotIn("fx_events_restated", self._load(FIRST, "true"))
        self.assertEqual(self._load(SECOND, "true").get("fx_events_restated"), 1)
        [restated] = _fx_records(SECOND)
        self.assertEqual(self._fx(), [(restated.source_key, "128.60", _event_key(restated))])
        # The same statement again changes nothing.
        self.assertNotIn("fx_events_restated", self._load(SECOND, "true"))
        self.assertEqual(self._fx(), [(restated.source_key, "128.60", _event_key(restated))])
        # Other tables load as before.
        self.assertEqual(self.connection.execute("SELECT count(*) FROM trades").fetchone()[0], 3)

    def test_on_events_alike_in_one_statement_keep_their_own_rows(self):
        self._add_column()
        self._load(statement(ACCOUNT, "20261015", "20261015", nav={"20261015": "1.00"}, fx=[FX_FIRST, TWIN]), "true")
        self.assertEqual([row[2][-2:] for row in self._fx()], [":1", ":2"])
        counts = self._load(statement(ACCOUNT, "20261015", "20261016", nav={"20261016": "1.00"},
                                      fx=[FX_RESTATED, dict(TWIN, proceeds="128.95")]), "true")
        self.assertEqual(counts.get("fx_events_restated"), 2)
        self.assertEqual([(row[1], row[2][-2:]) for row in self._fx()], [("128.60", ":1"), ("128.95", ":2")])

    def test_the_event_identity_leaves_out_the_restated_amounts_and_treats_empty_as_null(self):
        [first], [restated] = _fx_records(FIRST), _fx_records(SECOND)
        self.assertNotEqual(first.source_key, restated.source_key)
        self.assertEqual(_event_key(first), _event_key(restated))
        self.assertTrue(_event_key(first).startswith("fx-event:v1:"))
        self.assertEqual(fx_event_identity({"account_id": ACCOUNT, "model_code": None}),
                         fx_event_identity({"account_id": ACCOUNT, "model_code": ""}))
        self.assertNotEqual(fx_event_identity({"account_id": ACCOUNT, "cost_text": "-128.00"}),
                            fx_event_identity({"account_id": ACCOUNT, "cost_text": "-128.01"}))


class ReplayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.replay = history.Replay(self.root / "replay")
        self.addCleanup(self.replay.close)

    def _statement(self, run_id: int, content: bytes, minutes: int) -> None:
        self.replay.load(history.Load(history.STATEMENT, T0 + timedelta(minutes=minutes), run_id, _sha(content),
                                      DEFAULT_TENANT_ID), content)

    def _versions(self, table: str, *columns: str):
        at = [self.replay.columns[table].index(name) for name in columns]
        return sorted((*(v.values[i] for i in at), v.opened_by, v.recorded_run_id, v.closed_by)
                      for v in self.replay.versions[table])

    def test_each_change_is_a_version_recorded_when_its_load_ran(self):
        self._statement(101, FIRST, 0)
        self._statement(102, SECOND, 60)
        self.assertEqual(self._versions("nav_snapshots", "snapshot_date", "total_text"), [
            ("2026-10-14", "100000.00", "first", 101, None),
            ("2026-10-15", "100480.00", "restated", 102, None),
            ("2026-10-15", "100500.00", "first", 101, "restated"),
            ("2026-10-16", "100700.00", "first", 102, None),
        ])
        # Sent again unchanged: still one version, though last_seen_run_id moved.
        self.assertEqual(self._versions("cash_transactions", "amount_text"), [("12.50", "first", 101, None)])
        self.assertEqual(self._versions("trades", "proceeds_text"), [
            ("-5000.00", "first", 102, None), ("4980.00", "first", 102, None), ("5000.00", "first", 101, None)])
        # The replay keeps today's loader: two FX rows, no event key.
        self.assertEqual(self._versions("fx_transactions", "proceeds_text"), [
            ("128.60", "first", 102, None), ("128.70", "first", 101, None)])
        restated = [v for v in self.replay.versions["nav_snapshots"] if v.opened_by == "restated"][0]
        closed = [v for v in self.replay.versions["nav_snapshots"] if v.closed_by][0]
        self.assertEqual((restated.recorded_from, closed.recorded_to), (T0 + timedelta(hours=1),) * 2)
        self.assertEqual(restated.valid_date, date(2026, 10, 15))
        seen_at = self.replay.columns["nav_snapshots"].index("last_seen_run_id")
        self.assertEqual(restated.values[seen_at], 102)  # the real run, not the replay's own
        self.assertEqual(self.replay.counts, Counter({"statement": 2}))

    def test_rows_a_later_statement_covering_their_date_lacks_are_counted(self):
        self._statement(101, FIRST, 0)
        self._statement(102, SECOND, 60)
        # The FX row IBKR re-sent with new amounts: the first report is no longer sent.
        self.assertEqual(self.replay.stopped_sending(), Counter({"fx_transactions": 1}))
        self._statement(103, THIRD, 120)
        self.assertEqual(self.replay.stopped_sending(), Counter({"fx_transactions": 1, "cash_transactions": 1}))
        self.assertEqual(self.replay.stopped_sending({OTHER}), Counter())

    def test_account_sync_copies_replay_as_their_import_ran(self):
        content = statement(OTHER, "20261014", "20261015", nav={"20261014": "200000.00", "20261015": "200500.00"},
                            trades=[ORIGINAL_SALE], cash=[DIVIDEND])
        export = v11_export(self.root / "v11", [content], "2026-10-16T00:00:00+00:00")
        first = account_snapshot(export, self.root / "copies" / "first.sqlite", OTHER, "2026-10-01",
                                 "2026-10-16T00:00:00+00:00")
        second = self.root / "copies" / "second.sqlite"
        shutil.copy(first, second)
        edit_snapshot(second, ["UPDATE nav_snapshots SET total_text = '200100.00' WHERE snapshot_date = '2026-10-15'"])
        for run_id, path, days in ((201, first, 0), (202, second, 1)):
            self.replay.load(history.Load(history.SNAPSHOT, T0 + timedelta(days=days), run_id,
                                          _sha(path.read_bytes()), DEFAULT_TENANT_ID), path)
        self.assertEqual(self._versions("nav_snapshots", "snapshot_date", "total_text"), [
            ("2026-10-14", "200000.00", "first", 201, None),
            ("2026-10-15", "200100.00", "restated", 202, None),
            ("2026-10-15", "200500.00", "first", 201, "restated"),
        ])
        self.assertEqual(self._versions("trades", "proceeds_text"), [("5000.00", "first", 201, None)])
        explained = history.v11_explained(self.replay, history.statement_digests([content]))
        self.assertEqual(explained["trades:explained"], 1)
        self.assertEqual(explained["cash_transactions:explained"], 1)
        self.assertFalse([key for key in explained if key.endswith("not_found")])


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.replay = history.Replay(Path(directory.name) / "replay")
        self.addCleanup(self.replay.close)
        for run_id, content, minutes in ((101, FIRST, 0), (102, SECOND, 60)):
            self.replay.load(history.Load(history.STATEMENT, T0 + timedelta(minutes=minutes), run_id, _sha(content),
                                          DEFAULT_TENANT_ID), content)

    def _live(self, table: str, columns: list[str], seen: int = 999) -> dict:
        source = self.replay.columns[table]
        return {key: tuple(seen if name == "last_seen_run_id" else version.values[source.index(name)]
                           for name in columns)
                for key, version in self.replay.current[table].items()}

    def _reconcile(self, table, columns, live, **options):
        options.setdefault("run_times", {})
        options.setdefault("first_seen", {})
        return history.reconcile(self.replay, table, columns, live, {ACCOUNT}, now=NOW, **options)

    def test_live_rows_the_replay_explains_match_whatever_the_column_order(self):
        columns = list(reversed(self.replay.columns["trades"]))
        plan = self._reconcile("trades", columns, self._live("trades", columns))
        self.assertEqual(plan.problems(), 0)
        self.assertEqual({k: v for k, v in plan.counts.items() if v}, {
            "matched": 3, "opened_first": 3, "live_rows": 3, "versions": 3})
        source_key = columns.index("source_key")
        self.assertEqual(sorted(v.values[source_key] for v in plan.versions),
                         sorted(key[1] for key in self.replay.current["trades"]))

    def test_unexplained_rows_are_baselines_known_from_the_earliest_provable_time(self):
        columns = self.replay.columns["cash_transactions"]
        live = self._live("cash_transactions", columns)
        extra = dict(zip(columns, next(iter(live.values()))), source_key="manual-1", last_seen_run_id=101)
        live[(ACCOUNT, "manual-1")] = tuple(extra[name] for name in columns)
        for first_seen, expected in (({(ACCOUNT, "manual-1"): T0 + timedelta(minutes=2)}, T0 + timedelta(minutes=2)),
                                     ({}, T0 + timedelta(minutes=5))):
            plan = self._reconcile("cash_transactions", columns, live, run_times={101: T0 + timedelta(minutes=5)},
                                   first_seen=first_seen)
            self.assertEqual((plan.counts["matched"], plan.counts["unexplained"], plan.problems()), (1, 1, 0))
            [baseline] = [v for v in plan.versions if v.opened_by == "baseline"]
            self.assertEqual((baseline.recorded_from, baseline.recorded_run_id, baseline.opened_note),
                             (expected, 101, history.NOTE_UNEXPLAINED))
        plan = self._reconcile("cash_transactions", columns, live)
        self.assertEqual([v.recorded_from for v in plan.versions if v.opened_by == "baseline"], [NOW])

    def test_differences_stop_the_backfill_unless_accepted(self):
        columns = self.replay.columns["nav_snapshots"]
        live = self._live("nav_snapshots", columns)
        total, day = columns.index("total_text"), columns.index("snapshot_date")
        repaired = next(key for key, values in live.items() if values[day] == "2026-10-14")
        live[repaired] = tuple("99999.00" if i == total else value for i, value in enumerate(live[repaired]))
        dropped = next(key for key, values in live.items() if values[day] == "2026-10-16")
        del live[dropped]

        plan = self._reconcile("nav_snapshots", columns, live)
        self.assertEqual((plan.counts["matched"], plan.counts["differs"], plan.counts["replay_only"], plan.problems()),
                         (1, 1, 1, 2))
        self.assertFalse([v for v in plan.versions if v.opened_by == "baseline" or v.recorded_to == NOW])

        plan = self._reconcile("nav_snapshots", columns, live, accept_differences=True)
        closed = sorted((v.values[day], v.closed_by, v.closed_note) for v in plan.versions if v.recorded_to == NOW)
        self.assertEqual(closed, [("2026-10-14", "restated", history.NOTE_DIFFERS),
                                  ("2026-10-16", "deleted", history.NOTE_NOT_LIVE)])
        [baseline] = [v for v in plan.versions if v.opened_by == "baseline"]
        self.assertEqual((baseline.values[total], baseline.recorded_from, baseline.opened_note),
                         ("99999.00", NOW, history.NOTE_DIFFERS))
        # The replay itself is left as it was.
        self.assertFalse([v for v in self.replay.versions["nav_snapshots"] if v.recorded_to == NOW])

    def test_other_accounts_are_left_out(self):
        columns = self.replay.columns["trades"]
        plan = history.reconcile(self.replay, "trades", columns, {}, {OTHER}, now=NOW, run_times={}, first_seen={})
        self.assertEqual((plan.versions, plan.problems(), plan.counts["live_rows"]), ([], 0, 0))


class FxMergePlanTests(unittest.TestCase):
    def test_a_re_sent_event_merges_into_its_latest_report(self):
        [first], [restated] = _fx_records(FIRST), _fx_records(SECOND)
        plan = history.plan_fx_merge([(ACCOUNT, first.source_key, None), (ACCOUNT, restated.source_key, None)],
                                     history.fx_index([FIRST, SECOND]))
        self.assertEqual(plan.merge, [(ACCOUNT, first.source_key)])
        self.assertEqual(plan.fill, {(ACCOUNT, restated.source_key): _event_key(restated)})
        self.assertEqual({k: v for k, v in plan.counts.items() if v},
                         {"rows": 2, "merged": 1, "events_with_duplicates": 1, "events": 1})

    def test_rows_no_statement_explains_or_with_another_key_are_left(self):
        [restated] = _fx_records(SECOND)
        plan = history.plan_fx_merge([(ACCOUNT, "fx:unknown", None), (ACCOUNT, restated.source_key, "fx-event:v1:x:1")],
                                     history.fx_index([FIRST, SECOND]))
        self.assertEqual((plan.merge, plan.fill), ([], {}))
        self.assertEqual((plan.counts["not_in_any_statement"], plan.counts["event_key_differs"]), (1, 1))

    def test_a_key_another_row_already_holds_stops_the_merge(self):
        [first], [restated] = _fx_records(FIRST), _fx_records(SECOND)
        plan = history.plan_fx_merge([(ACCOUNT, first.source_key, _event_key(first)),
                                      (ACCOUNT, restated.source_key, None)], history.fx_index([FIRST, SECOND]))
        self.assertEqual((plan.merge, plan.fill, plan.counts["event_key_held_by_another_row"]), ([], {}, 1))

    def test_rows_once_reported_together_are_never_merged(self):
        both = statement(ACCOUNT, "20261015", "20261015", nav={"20261015": "1.00"}, fx=[FX_FIRST, TWIN])
        later = statement(ACCOUNT, "20261015", "20261016", nav={"20261016": "1.00"}, fx=[TWIN])
        rows = [(ACCOUNT, row.source_key, None) for row in _fx_records(both)]
        plan = history.plan_fx_merge(rows, history.fx_index([both, later]))
        self.assertEqual((plan.merge, plan.counts["groups_seen_together"]), ([], 1))


class SmallPartsTests(unittest.TestCase):
    def test_each_run_replays_once_in_the_order_it_reached_silver(self):
        a = history.Load(history.STATEMENT, T0 + timedelta(hours=1), 5, "a", DEFAULT_TENANT_ID)
        b = history.Load(history.SNAPSHOT, T0, 7, "b", DEFAULT_TENANT_ID)
        again = history.Load(history.STATEMENT, T0 + timedelta(hours=2), 9, "a", DEFAULT_TENANT_ID)  # reloaded
        self.assertEqual(history.order_loads([again, a, b, a]), [b, a, again])

    def test_dates_and_stored_times(self):
        self.assertEqual(history.valid_date("2026-10-15"), date(2026, 10, 15))
        for value in ("20261015", "2026-02-30", "", None):
            self.assertIsNone(history.valid_date(value))
        for value in ("2026-10-16T01:00:00+00:00", "2026-10-16T09:00:00+08:00", "2026-10-16T01:00:00",
                      "2026-10-16T01:00:00Z"):
            self.assertEqual(history.parse_time(value), T0)
        self.assertIsNone(history.parse_time(""))

    def test_reports_name_no_tenant_but_the_default_one(self):
        self.assertEqual(history.tenant_labels([DEFAULT_TENANT_ID, "t-x", "t-y", "t-x"]),
                         {"default": DEFAULT_TENANT_ID, "tenant#1": "t-x", "tenant#2": "t-y"})


class ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = _tool()

    def test_a_kept_blob_url_names_its_container_and_blob(self):
        self.assertEqual(self.tool._blob("https://example.blob.core.windows.net/ibkr-raw/daily/2026-10-16/a%20b.xml"),
                         ("https://example.blob.core.windows.net", "ibkr-raw", "daily/2026-10-16/a b.xml"))
        for value in ("/var/raw/a.xml", "", None, "https://example.blob.core.windows.net/ibkr-raw"):
            self.assertIsNone(self.tool._blob(value))

    def test_files_are_found_by_sha256_and_checked_before_use(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "raw" / "2026" / "10"
            folder.mkdir(parents=True)
            (folder / "one.xml").write_bytes(b"one")
            files = self.tool.Files([Path(directory) / "raw"], Path(directory))
            self.assertEqual(files.get(_sha(b"one")), folder / "one.xml")
            self.assertIsNone(files.get(_sha(b"two")))
            self.assertIsNone(files.get(_sha(b"two"), ("", "ibkr-raw", "x.xml")))  # no storage account: no download
            (folder / "one.xml").write_bytes(b"changed")
            with self.assertRaisesRegex(history.HistoryError, "does not match"):
                files.get(_sha(b"one"))

    def test_errors_show_no_account_number_and_no_url(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"DATABASE_ADMIN_URL": "postgresql://someone:secret@127.0.0.1:1/x"}), \
                patch.object(self.tool, "_connect", return_value=MagicMock()), \
                patch.object(self.tool.history, "check", side_effect=history.HistoryError("U9100001 is broken")), \
                redirect_stdout(output):
            self.assertEqual(self.tool.main(["check"]), 2)
        self.assertIn("ERROR HistoryError", output.getvalue())
        self.assertNotIn("U9100001", output.getvalue())
        output = io.StringIO()
        with patch.dict(os.environ, {"DATABASE_ADMIN_URL": "secret-not-a-url"}), redirect_stdout(output):
            self.assertEqual(self.tool.main(["check"]), 2)
        self.assertNotIn("secret-not-a-url", output.getvalue())

    def test_the_summary_holds_counts_and_table_names(self):
        lines = self.tool._summary("check", {"status": "PASS", "tenants": {"default": {"accounts": 2, "tables": {
            "trades": {"problems": {"overlapping_versions": 0}, "versions": {"opened_first": 3}}}}}})
        self.assertEqual(lines, ["medallion silver history check: PASS", "  default trades: overlapping_versions=0"])


if __name__ == "__main__":
    unittest.main()
