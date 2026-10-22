"""U8's FX step (docs/MEDALLION_AS_KNOWN_ON.md, "The FX step in October") without a database server.

The merge plan for rows account-sync kept under v1.1's key, the read-only FX check's counts, a
second merge finding nothing, and the loader (IBKR_FX_EVENT_KEY on) once the merge has run: a
re-send the next day never adds a second row. Synthetic data only. tests/test_fx_event_key_db.py
runs the same on PostgreSQL with the held migrations as 074 and 075.
"""
from __future__ import annotations

import importlib.util
import io
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from ibkr_etl import silver_history as history
from ibkr_etl.db import initialize, start_run
from ibkr_etl.loader import FX_COLUMNS, load_report
from ibkr_etl.parser import parse_flex_xml
from test_as_known_on_draft_db import FX_FIRST, FX_RESTATED, statement

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT, OTHER = "U9300001", "U9300002"
NAV = {"20261015": "1.00"}
FX_LATER = dict(FX_FIRST, proceeds="128.50", realizedPL="0.50")
TWIN = dict(FX_FIRST, proceeds="128.90", realizedPL="0.90")  # alike apart from the amounts IBKR restates
SAME_AMOUNTS = dict(FX_FIRST, quantity="1,000")  # another raw record, the same silver row
NEW_COST = dict(FX_RESTATED, cost="-128.10", realizedPL="0.50")  # a re-send that changes an identity field
OTHER_EVENT = dict(FX_FIRST, dateTime="20261015;170000", quantity="2000", cost="-256.00", proceeds="257.40",
                   realizedPL="1.40")
ACCOUNT_IDS = re.compile(r"U93\d{5}")


def _statement(*fx, account: str = ACCOUNT, to_date: str = "20261015") -> bytes:
    return statement(account, "20261015", to_date, nav={to_date: "1.00"}, fx=list(fx))


FIRST = _statement(FX_FIRST, OTHER_EVENT)
SECOND = _statement(FX_RESTATED, OTHER_EVENT, to_date="20261016")
THIRD = _statement(FX_LATER, OTHER_EVENT, to_date="20261017")


def _records(content: bytes):
    return [row for row in parse_flex_xml(content).rows if row.record_type.lower() == "fxtransaction"]


def _tool():
    spec = importlib.util.spec_from_file_location("medallion_silver_history_fx",
                                                  ROOT / "scripts" / "medallion_silver_history.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Silver:
    """A SQLite silver as the loader writes it; event_key and its index as the FX step's migration adds them."""

    def __init__(self, directory: str, test: unittest.TestCase) -> None:
        path = Path(directory) / "silver.sqlite"
        initialize(path)
        self.connection = sqlite3.connect(path)
        test.addCleanup(self.connection.close)

    def load(self, content: bytes, setting: str = "") -> dict:
        with patch.dict(os.environ, {"IBKR_FX_EVENT_KEY": setting, "IBKR_FLEX_LEGACY_KEY_COMPAT": ""}):
            counts = load_report(self.connection, parse_flex_xml(content), start_run(self.connection, "test", None, None))
        self.connection.commit()
        return counts

    def add_event_key(self) -> None:
        self.connection.execute("ALTER TABLE fx_transactions ADD COLUMN event_key TEXT")
        self.connection.execute("CREATE UNIQUE INDEX fx_transactions_event_key ON fx_transactions (account_id, "
                                "event_key) WHERE event_key IS NOT NULL")
        self.connection.commit()

    def values(self) -> dict:
        names = [name for name in FX_COLUMNS if name != "last_seen_run_id"]
        has_key = "event_key" in [row[1] for row in self.connection.execute("PRAGMA table_info(fx_transactions)")]
        rows = self.connection.execute(f"SELECT {', '.join(names)}, {'event_key' if has_key else 'NULL'} "
                                       "FROM fx_transactions").fetchall()
        return {(row[0], row[1]): {**dict(zip(names, row)), "event_key": row[-1]} for row in rows}

    def plan(self, index: history.FxIndex) -> history.FxPlan:
        return history.plan_fx_merge([(*key, row["event_key"], row["row_sha256"])
                                      for key, row in self.values().items()], index)

    def merge(self, index: history.FxIndex) -> history.FxPlan:
        """What apply_fx_merge writes on PostgreSQL (there the trigger keeps the removed rows)."""
        plan = self.plan(index)
        self.connection.executemany("DELETE FROM fx_transactions WHERE account_id = ? AND source_key = ?",
                                    plan.merge)
        self.connection.executemany("UPDATE fx_transactions SET event_key = ? WHERE account_id = ? AND source_key = ?",
                                    [(key, *member) for member, key in plan.fill.items()])
        self.connection.commit()
        return plan

    def proceeds(self) -> list[tuple]:
        return sorted((row["proceeds_text"], row["event_key"] is not None) for row in self.values().values())


class MergePlanTests(unittest.TestCase):
    def test_a_row_kept_under_v11s_key_is_found_by_its_raw_record(self):
        [first, _], [restated, _] = _records(FIRST), _records(SECOND)
        rows = [(ACCOUNT, "legacy:" + first.source_key, None, first.row_sha256),
                (ACCOUNT, restated.source_key, None, restated.row_sha256)]
        plan = history.plan_fx_merge(rows, history.fx_index([FIRST, SECOND]))
        self.assertEqual(plan.merge, [(ACCOUNT, "legacy:" + first.source_key)])
        self.assertEqual(list(plan.fill), [(ACCOUNT, restated.source_key)])
        self.assertEqual((plan.counts["found_by_raw_record"], plan.counts["not_in_any_statement"]), (1, 0))
        # Without its raw record it is left, as before.
        plan = history.plan_fx_merge([row[:3] for row in rows], history.fx_index([FIRST, SECOND]))
        self.assertEqual((plan.merge, plan.counts["not_in_any_statement"]), ([], 1))

    def test_the_same_raw_record_under_two_keys_is_left_and_counted(self):
        [first, _] = _records(FIRST)
        rows = [(ACCOUNT, "legacy:" + first.source_key, None, first.row_sha256),
                (ACCOUNT, first.source_key, None, first.row_sha256)]
        plan = history.plan_fx_merge(rows, history.fx_index([FIRST]))
        self.assertEqual((plan.merge, plan.counts["same_record_under_two_keys"]), ([], 1))

    def test_a_second_merge_finds_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            silver = Silver(directory, self)
            silver.load(FIRST)
            silver.load(SECOND)
            silver.add_event_key()
            index = history.fx_index([FIRST, SECOND])
            first = silver.merge(index)
            self.assertEqual((first.counts["merged"], first.counts["events"]), (1, 2))
            again = silver.merge(index)
            self.assertEqual((again.merge, again.counts["merged"], again.counts["events"]), ([], 0, 2))
            self.assertEqual(again.fill, first.fill)
            self.assertEqual(silver.proceeds(), [("128.60", True), ("257.40", True)])

    def test_statements_without_report_dates_are_counted(self):
        undated = {key: value for key, value in FX_FIRST.items() if key != "reportDate"}
        index = history.fx_index([_statement(undated, OTHER_EVENT)])
        self.assertEqual((index.records, index.without_report_date), (2, 1))


class FxCheckCountTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.silver = Silver(directory.name, self)

    def _counts(self, *statements: bytes):
        index = history.fx_index(statements)
        return history.fx_counts(self.silver.values(), self.silver.plan(index), index)

    def test_before_the_merge_a_re_sent_event_has_two_current_rows(self):
        self.silver.load(FIRST)
        self.silver.load(SECOND)
        counts = self._counts(FIRST, SECOND)
        self.assertEqual({k: v for k, v in counts.items() if v}, {
            "rows": 3, "look_alike_groups": 1, "rows_in_look_alike_groups": 2,
            "events_with_more_than_one_current_row": 1, "rows_beyond_one_per_event": 1,
            "rows_the_merge_removes": 1, "accounts_the_merge_changes": 1,
            "events_where_proceeds_text_changes": 1, "events_where_realized_pnl_text_changes": 1,
            "events_whose_amounts_change": 1, "account_currency_totals_that_change": 1})

    def test_after_the_merge_and_the_next_days_load_one_row_per_event(self):
        self.silver.load(FIRST)
        self.silver.load(SECOND)
        self.silver.add_event_key()
        self.silver.merge(history.fx_index([FIRST, SECOND]))
        self.silver.load(SECOND, "true")
        self.silver.load(THIRD, "true")
        counts = self._counts(FIRST, SECOND, THIRD)
        self.assertEqual({k: v for k, v in counts.items() if v},
                         {"rows": 2, "event_key_filled": 2})

    def test_look_alike_events_in_one_statement_stay_apart(self):
        content = _statement(FX_FIRST, TWIN)
        self.silver.load(content)
        counts = self._counts(content)
        self.assertEqual((counts["look_alike_groups"], counts["events_with_more_than_one_current_row"],
                          counts["rows_the_merge_removes"]), (1, 0, 0))

    def test_a_copy_with_the_same_amounts_is_removed_without_changing_any_amount(self):
        self.silver.load(FIRST)
        again = _statement(SAME_AMOUNTS, OTHER_EVENT, to_date="20261016")
        self.silver.load(again)
        counts = self._counts(FIRST, again)
        self.assertEqual((counts["rows_the_merge_removes"], counts["removed_rows_with_the_same_amounts"],
                          counts["events_whose_amounts_change"], counts["account_currency_totals_that_change"]),
                         (1, 1, 0, 1))

    def test_a_re_send_that_changes_the_cost_is_counted_not_merged(self):
        self.silver.load(FIRST)
        changed = _statement(NEW_COST, OTHER_EVENT, to_date="20261016")
        self.silver.load(changed)
        counts = self._counts(FIRST, changed)
        self.assertEqual((counts["events_with_more_than_one_current_row"], counts["rows_the_merge_removes"],
                          counts["possible_resends_outside_the_rule"],
                          counts["possible_resends_outside_the_rule_differ_in_cost_text"],
                          counts["possible_resends_outside_the_rule_differ_in_report_date"]), (0, 0, 1, 1, 0))

    def test_rows_no_statement_explains_are_counted(self):
        self.silver.load(FIRST)
        counts = self._counts()
        self.assertEqual((counts["rows"], counts["not_in_any_statement"], counts["events_with_more_than_one_current_row"]),
                         (2, 2, 0))


class LoaderAfterTheMergeTests(unittest.TestCase):
    """IBKR_FX_EVENT_KEY on, after fx-merge: whatever IBKR sends next, one row per FX event."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.silver = Silver(directory.name, self)
        self.silver.load(FIRST)
        self.silver.load(SECOND)
        self.assertEqual(len(self.silver.values()), 3)  # today: the re-send is a second row
        self.silver.add_event_key()
        self.silver.merge(history.fx_index([FIRST, SECOND]))

    def test_the_same_statement_again_changes_nothing(self):
        before = self.silver.values()
        counts = self.silver.load(SECOND, "true")
        self.assertNotIn("fx_events_restated", counts)
        self.assertEqual(self.silver.values(), before)

    def test_new_amounts_the_next_day_update_the_events_row(self):
        counts = self.silver.load(THIRD, "true")
        self.assertEqual(counts.get("fx_events_restated"), 1)
        self.assertEqual(self.silver.proceeds(), [("128.50", True), ("257.40", True)])
        [later, _] = _records(THIRD)
        self.assertIn((ACCOUNT, later.source_key), self.silver.values())  # names the latest bronze record

    def test_an_older_report_loaded_again_never_adds_a_row(self):
        self.silver.load(FIRST, "true")
        self.assertEqual(self.silver.proceeds(), [("128.70", True), ("257.40", True)])
        self.silver.load(THIRD, "true")
        self.assertEqual(self.silver.proceeds(), [("128.50", True), ("257.40", True)])

    def test_a_row_kept_under_v11s_key_takes_the_parsers_key_on_its_next_re_send(self):
        self.silver.connection.execute("UPDATE fx_transactions SET source_key = 'legacy:' || source_key")
        self.silver.connection.commit()
        self.silver.load(SECOND, "true")
        self.assertEqual(len(self.silver.values()), 2)
        self.assertFalse([key for key in self.silver.values() if key[1].startswith("legacy:")])

    def test_a_row_the_merge_could_not_place_gets_its_key_on_its_next_re_send(self):
        self.silver.connection.execute("UPDATE fx_transactions SET event_key = NULL WHERE quantity_text = '2000'")
        self.silver.connection.commit()
        self.silver.load(SECOND, "true")
        self.assertEqual(self.silver.proceeds(), [("128.60", True), ("257.40", True)])

    def test_with_the_setting_off_again_the_loader_is_todays(self):
        self.silver.load(THIRD)
        self.assertEqual(self.silver.proceeds(), [("128.50", False), ("128.60", True), ("257.40", True)])


class FxCheckToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = _tool()

    def _run(self, *args, result=None):
        output = io.StringIO()
        with patch.dict(os.environ, {"DATABASE_ADMIN_URL": "postgresql://someone:secret@127.0.0.1:1/x"}), \
                patch.object(self.tool, "_connect", return_value=MagicMock()), \
                patch.object(self.tool, "_fx_contents", return_value=iter(())), \
                patch.object(self.tool.history, "fx_check", return_value=result) as check, \
                redirect_stdout(output):
            code = self.tool.main(["fx-check", *args])
        return code, output.getvalue(), check

    def test_it_prints_counts_and_exits_0_only_when_every_event_has_one_row(self):
        report = {"event_key_column": True, "statements_read": 3, "fx_records_read": 9,
                  "fx_records_without_report_date": 0, "ok": False, "tenants": {"default": {"accounts": 2, "tables": {
                      "fx_transactions": {"rows": 4, "events_with_more_than_one_current_row": 1}}}}}
        code, output, _ = self._run(result=report)
        self.assertEqual(code, 1)
        self.assertIn("fx-check: DUPLICATES", output)
        self.assertIn("statements: fx_records_read=9, fx_records_without_report_date=0, statement_files_missing=0, "
                      "statements_read=3", output)
        self.assertIn("default fx_transactions: events_with_more_than_one_current_row=1, rows=4", output)
        code, output, _ = self._run(result={**report, "ok": True})
        self.assertEqual(code, 0)
        self.assertIn("fx-check: PASS", output)

    def test_it_only_reads(self):
        code, output, check = self._run("--apply", result={})
        self.assertEqual(code, 2)
        self.assertIn("fx-check only reads", output)
        check.assert_not_called()

    def test_the_merge_waits_for_missing_raw_statements_unless_told_to_go_ahead(self):
        def run(*args):
            output = io.StringIO()
            with patch.dict(os.environ, {"DATABASE_ADMIN_URL": "postgresql://someone:secret@127.0.0.1:1/x"}), \
                    patch.object(self.tool, "_connect", return_value=MagicMock()), \
                    patch.object(self.tool, "_inputs", return_value=([], {"statements": 2, "snapshots": 0}, {}, [], [])), \
                    patch.object(self.tool.history, "apply_fx_merge", return_value={"tenants": {}}) as merge, \
                    redirect_stdout(output):
                code = self.tool.main(["fx-merge", *args])
            return code, output.getvalue(), merge

        code, output, merge = run("--apply")
        self.assertEqual(code, 2)
        self.assertIn("2 raw statements are missing, so nothing was merged", output)
        merge.assert_not_called()
        code, output, merge = run()  # the dry run reports them
        self.assertEqual(code, 0)
        self.assertIn("statement_files_missing=2", output)
        code, output, merge = run("--apply", "--accept-differences")
        self.assertEqual(code, 0)
        self.assertTrue(merge.call_args.kwargs["dry_run"] is False)

    def test_the_fx_merge_dry_run_prints_its_counts(self):
        lines = self.tool._summary("fx-merge", {"status": "READY", "statements_read": 2, "tenants": {"default": {
            "accounts": 1, "tables": {"fx_transactions": {"merged": 1, "events": 2}}}}})
        self.assertEqual(lines, ["medallion silver history fx-merge: READY", "  statements: statements_read=2",
                                 "  default fx_transactions: events=2, merged=1"])

    def test_the_check_on_real_counts_names_no_account(self):
        with tempfile.TemporaryDirectory() as directory:
            silver = Silver(directory, self)
            silver.load(FIRST)
            silver.load(SECOND)
            silver.load(_statement(FX_FIRST, account=OTHER))
            index = history.fx_index([FIRST, SECOND])
            counts = history.fx_counts(silver.values(), silver.plan(index), index)
        lines = self.tool._summary("fx-check", {"status": "DUPLICATES", "tenants": {"default": {
            "tables": {"fx_transactions": dict(counts)}}}})
        text = "\n".join(lines)
        self.assertIsNone(ACCOUNT_IDS.search(text))
        self.assertNotIn("128.", text)
        self.assertIn("events_with_more_than_one_current_row=1", text)


if __name__ == "__main__":
    unittest.main()
