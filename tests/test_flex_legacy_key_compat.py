"""The loader's optional legacy-key step (IBKR_FLEX_LEGACY_KEY_COMPAT).

Rows that reached the database by another path under a different key, with
identical raw IBKR attributes, are updated instead of duplicated. Off by
default. Synthetic data only.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from switch_fixtures import ACCOUNT_A, FIRST_RUN, settings_for, statement_for_run
from ibkr_etl.db import initialize
from ibkr_etl.loader import LEGACY_KEY_COMPAT_ENV, legacy_key_compat_enabled, load_report
from ibkr_etl.parser import parse_flex_xml
from ibkr_etl.service import ingest_content

EVENT_TABLES = ("trades", "cash_transactions", "corporate_actions", "fx_transactions")


class LegacyKeyCompatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.settings = settings_for(self.root)
        self.statement = statement_for_run(FIRST_RUN, (ACCOUNT_A,))
        ingest_content(self.settings, self.statement, run_type="pilot-stand-in")
        # Every event row as if another importer had used a different key format.
        connection = sqlite3.connect(self.settings.database_path)
        for table in EVENT_TABLES:
            connection.execute(f"UPDATE {table} SET source_key = 'legacy:' || source_key")
        connection.commit()
        self.before = self.counts(connection)
        connection.close()

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def counts(connection):
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in EVENT_TABLES}

    def ingest(self, enabled):
        environment = {LEGACY_KEY_COMPAT_ENV: "1"} if enabled else {}
        with patch.dict(os.environ, environment, clear=False):
            if not enabled:
                os.environ.pop(LEGACY_KEY_COMPAT_ENV, None)
            return ingest_content(self.settings, self.statement, run_type="flex")

    def test_off_by_default_duplicates_as_today(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(LEGACY_KEY_COMPAT_ENV, None)
            self.assertFalse(legacy_key_compat_enabled())
        result = self.ingest(False)
        connection = sqlite3.connect(self.settings.database_path)
        after = self.counts(connection)
        connection.close()
        self.assertEqual(after, {table: 2 * count for table, count in self.before.items()})
        self.assertNotIn("legacy_source_keys_reused", result["reconciliation"]["record_types"])

    def test_on_updates_the_existing_rows_in_place(self):
        result = self.ingest(True)
        connection = sqlite3.connect(self.settings.database_path)
        after = self.counts(connection)
        last_seen = {
            table: {row[0] for row in connection.execute(f"SELECT last_seen_run_id FROM {table}")}
            for table in EVENT_TABLES if self.before[table]
        }
        keys = {row[0] for row in connection.execute("SELECT source_key FROM cash_transactions")}
        connection.close()
        self.assertEqual(after, self.before)
        self.assertEqual(result["reconciliation"]["record_types"]["legacy_source_keys_reused"], sum(self.before.values()))
        self.assertTrue(all(values == {result["run_id"]} for values in last_seen.values()))
        self.assertTrue(all(key.startswith("legacy:") for key in keys))
        # Idempotent on the next day's load.
        self.ingest(True)
        connection = sqlite3.connect(self.settings.database_path)
        self.assertEqual(self.counts(connection), self.before)
        connection.close()

    def test_ambiguous_or_taken_legacy_keys_are_not_used(self):
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM trades LIMIT 1").fetchone())
        # A second legacy row with the same raw attributes makes the match ambiguous.
        connection.execute(
            f"INSERT INTO trades({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
            [*(value if name != "source_key" else "other:" + value for name, value in row.items())],
        )
        connection.commit()
        report = parse_flex_xml(self.statement)
        trade_rows = [r for r in report.rows if r.record_type == "Trade"]
        run_id = connection.execute(
            "INSERT INTO ingestion_runs(run_type, started_at, status) VALUES ('t', 'now', 'running')"
        ).lastrowid
        counts = load_report(connection, report, run_id, legacy_key_compat=True)
        connection.commit()
        trades = connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        connection.close()
        # The ambiguous record is inserted under the parser's key; the others reuse their legacy key.
        self.assertEqual(counts["legacy_source_keys_reused"], sum(self.before.values()) - 1)
        self.assertEqual(trades, len(trade_rows) + 2)

    def test_a_legacy_key_used_by_the_report_is_not_taken_over(self):
        connection = sqlite3.connect(self.settings.database_path)
        report = parse_flex_xml(self.statement)
        trade = next(r for r in report.rows if r.record_type == "Trade")
        other = next(r for r in report.rows if r.record_type == "Trade" and r.source_key != trade.source_key)
        # The legacy row for `trade` happens to carry a key this report gives `other`.
        connection.execute("UPDATE trades SET source_key = ? WHERE source_key = ?",
                           (other.source_key, "legacy:" + trade.source_key))
        connection.execute("DELETE FROM trades WHERE source_key = ?", ("legacy:" + other.source_key,))
        connection.commit()
        run_id = connection.execute(
            "INSERT INTO ingestion_runs(run_type, started_at, status) VALUES ('t', 'now', 'running')"
        ).lastrowid
        load_report(connection, report, run_id, legacy_key_compat=True)
        connection.commit()
        keys = {row[0] for row in connection.execute("SELECT source_key FROM trades")}
        connection.close()
        self.assertIn(trade.source_key, keys)
        self.assertIn(other.source_key, keys)


if __name__ == "__main__":
    unittest.main()
