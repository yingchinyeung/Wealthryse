"""The S5 one-day check: v2's parser over v1.1's own statements against v1.1's snapshot.

Synthetic accounts and values only. The v1.1 side is simulated with the same
ETL loaded daily; edits to its snapshot stand in for v1.1 behaving differently.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path

from switch_fixtures import ACCOUNT_A, ACCOUNT_B, ACCOUNT_C, FIRST_RUN, edit_snapshot, scenario
from ibkr_etl.switch_dry_run import (
    AccountMasker, DryRunError, key_kind, masked_report, read_snapshot, run_dry_run,
    statement_from_bytes, summary_lines,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import medallion_switch_dry_run as command  # noqa: E402


def _run(root, statements, snapshots, **options):
    return run_dry_run(
        [statement_from_bytes(f"statement-{index}.xml", content) for index, content in enumerate(statements)],
        [read_snapshot(path.name, path) for path in snapshots],
        work_dir=root / "work", **options,
    )


class SwitchDryRunTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_v2_reproduces_v11_snapshot_exactly(self):
        _, statements, snapshots = scenario(self.root)
        report = _run(self.root, statements[-2:], snapshots.values())
        self.assertEqual(report["status"], "PASS")
        for account in (ACCOUNT_A, ACCOUNT_B):
            item = report["accounts"][account]
            self.assertEqual(item["failures"], [])
            self.assertEqual(item["coverage"]["days"], 8)
            for table in ("nav_snapshots", "position_snapshots", "cash_snapshots", "fx_rates",
                          "trades", "cash_transactions", "fx_transactions"):
                result = item["tables"][table]
                self.assertGreater(result["matched"], 0, table)
                self.assertEqual(result["matched"], result["v2_rows"], table)
                self.assertEqual(result["reference_rows_in_scope"], result["v2_rows"], table)
            # Older history in the snapshot is outside the statements and not compared.
            self.assertGreater(item["tables"]["nav_snapshots"]["reference_outside_scope"], 0)
            self.assertEqual(item["window_stability"]["counts"], {})
            self.assertEqual(item["flows"]["window_flows"], {})
            self.assertEqual(item["flows"]["transfers_sections"], {"present_empty": 2})
        self.assertIn(ACCOUNT_C, report["accounts_not_compared"])

    def test_cash_transaction_under_another_key_is_reported_as_rekeyed(self):
        _, statements, snapshots = scenario(self.root)
        edit_snapshot(snapshots[ACCOUNT_A], [
            "UPDATE cash_transactions SET source_key = 'legacy:' || source_key WHERE source_key LIKE 'cash:%'"])
        report = _run(self.root, statements[-1:], snapshots.values())
        result = report["accounts"][ACCOUNT_A]["tables"]["cash_transactions"]
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(result["rekeyed"], 0)
        self.assertEqual(result["extra_in_v2"], 0)
        self.assertEqual(result["missing_in_v2"], 0)
        sample = result["samples"]["rekeyed"][0]
        self.assertTrue(sample["v2_key"].startswith("cash:"))
        self.assertEqual(sample["reference_key"], "legacy:" + sample["v2_key"])
        self.assertIn("cash_transactions", " ".join(report["accounts"][ACCOUNT_A]["failures"]))
        self.assertEqual(report["accounts"][ACCOUNT_B]["status"], "PASS")

    def test_value_missing_and_extra_rows_fail(self):
        days, statements, snapshots = scenario(self.root)
        last = (days[-1] - timedelta(days=1)).isoformat()
        edit_snapshot(snapshots[ACCOUNT_A], [
            f"UPDATE nav_snapshots SET total_text = '1.00' WHERE snapshot_date = '{last}'",
            f"DELETE FROM trades WHERE trade_date = (SELECT MAX(trade_date) FROM trades)",
            "INSERT INTO cash_transactions(account_id, source_key, effective_date, amount_text, currency, row_sha256)"
            f" VALUES ('{ACCOUNT_A}', 'transactionID:ONLY-V11', '{last}', '5', 'USD', 'x')",
        ])
        tables = _run(self.root, statements[-1:], snapshots.values())["accounts"][ACCOUNT_A]["tables"]
        self.assertEqual(tables["nav_snapshots"]["different"], 1)
        self.assertEqual(tables["nav_snapshots"]["samples"]["different"][0]["reference_then_v2"]["total_text"][0], "1.00")
        self.assertEqual(tables["trades"]["extra_in_v2"], 1)
        self.assertEqual(tables["cash_transactions"]["missing_in_v2"], 1)

    def test_nav_currency_fallback_shows_as_parallel_rows(self):
        _, statements, snapshots = scenario(self.root)
        _, base_statements, _ = scenario(self.root / "base", statement_options={"nav_without_currency": True},
                                         snapshot_accounts=())
        report = _run(self.root, base_statements[-1:], snapshots.values())
        nav = report["accounts"][ACCOUNT_A]["tables"]["nav_snapshots"]
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(nav["extra_in_v2"], 7)
        self.assertEqual(nav["missing_in_v2"], 7)
        self.assertEqual(nav["samples"]["extra_in_v2"][0]["key"][1], "BASE")

    def test_content_keys_that_change_with_the_period_fail(self):
        _, statements, snapshots = scenario(self.root, statement_options={"period_dependent_fx": True})
        report = _run(self.root, statements[-2:], snapshots.values())
        item = report["accounts"][ACCOUNT_A]
        self.assertEqual(item["status"], "FAIL")
        self.assertGreater(item["window_stability"]["counts"]["fx_transactions:hash_key_changed"], 0)
        self.assertTrue(any("change with the statement period" in text for text in item["failures"]))

    def test_deposit_in_window_fails_and_earlier_deposit_warns(self):
        early = FIRST_RUN - timedelta(days=5)
        late = FIRST_RUN + timedelta(days=8)
        deposits = {(ACCOUNT_A, early): "5000", (ACCOUNT_A, late): "2500"}
        _, statements, snapshots = scenario(self.root, statement_options={"deposits": deposits})
        report = _run(self.root, statements[-1:], snapshots.values())
        flows = report["accounts"][ACCOUNT_A]["flows"]
        self.assertEqual(flows["window_flows"], {late.isoformat(): 2500.0})
        self.assertEqual([item["date"] for item in flows["history_external_cash_transactions"]], [early.isoformat()])
        self.assertEqual(report["accounts"][ACCOUNT_A]["status"], "FAIL")
        self.assertTrue(any("earlier deposits" in text for text in report["accounts"][ACCOUNT_A]["warnings"]))
        self.assertEqual(report["accounts"][ACCOUNT_B]["status"], "PASS")

    def test_earlier_deposit_alone_is_a_warning(self):
        deposits = {(ACCOUNT_A, FIRST_RUN - timedelta(days=5)): "5000"}
        _, statements, snapshots = scenario(self.root, statement_options={"deposits": deposits})
        report = _run(self.root, statements[-1:], snapshots.values())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["accounts"][ACCOUNT_A]["flows"]["history_external_cash_transactions"]), 1)

    def test_inputs_must_pair_up(self):
        _, statements, snapshots = scenario(self.root)
        with self.assertRaises(DryRunError):
            _run(self.root, statements[-1:], [snapshots[ACCOUNT_A], snapshots[ACCOUNT_A]])
        _, other, _ = scenario(self.root / "other", accounts=(ACCOUNT_B,), snapshot_accounts=())
        with self.assertRaises(DryRunError):
            _run(self.root, other[-1:], [snapshots[ACCOUNT_A]])

    def test_masking(self):
        masker = AccountMasker(["U1111234", "U2221234", "U9999999"])
        self.assertEqual(masker("U9999999"), "***999")
        self.assertNotEqual(masker("U1111234"), masker("U2221234"))
        self.assertEqual(masker.scrub("U9999999 and U1111234"), "***999 and " + masker("U1111234"))
        self.assertEqual(key_kind("transactionID:1"), "id")
        self.assertEqual(key_kind("cash:abc"), "hash")

    def test_summary_has_counts_only(self):
        deposits = {(ACCOUNT_A, FIRST_RUN + timedelta(days=8)): "2500"}
        _, statements, snapshots = scenario(self.root, statement_options={"deposits": deposits})
        report, _ = masked_report(_run(self.root, statements[-1:], snapshots.values()))
        text = "\n".join(summary_lines(report)) + json.dumps(report)
        for account in (ACCOUNT_A, ACCOUNT_B, ACCOUNT_C):
            self.assertNotIn(account, text)
        summary = "\n".join(summary_lines(report))
        self.assertIn("***001", summary)
        self.assertNotIn("2500", summary)

    def test_command_exit_codes_and_read_only_inputs(self):
        _, statements, snapshots = scenario(self.root)
        files = []
        for index, content in enumerate(statements[-2:]):
            path = self.root / f"in-{index}.xml"
            path.write_bytes(content)
            files += ["--statement-file", str(path)]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in snapshots.values()}
        arguments = files + sum((["--snapshot-file", str(path)] for path in snapshots.values()), [])
        report_path = self.root / "report.json"
        output = io.StringIO()
        with redirect_stdout(output):
            code = command.main(arguments + ["--report", str(report_path)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertNotIn(ACCOUNT_A, output.getvalue())
        self.assertIn('"status": "PASS"', output.getvalue())
        self.assertEqual(json.loads(report_path.read_text())["status"], "PASS")
        self.assertNotIn(ACCOUNT_A, report_path.read_text())
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in snapshots.values()})

        edit_snapshot(snapshots[ACCOUNT_B], ["UPDATE fx_rates SET rate_text = '0.2'"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(command.main(arguments + ["--report", str(report_path)]), 1)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(command.main(["--snapshot-file", str(self.root / "missing.sqlite")]), 2)
        self.assertIn("ERROR", output.getvalue())

    def test_blob_arguments_expand_to_archive_day_folders(self):
        arguments = command._arguments(["--as-of", "2026-10-20", "--days", "3", "--tenant-id", "t1"])
        self.assertEqual(command._statement_prefixes(arguments), [
            "v11-archive/t1/2026/10/20/", "v11-archive/t1/2026/10/19/", "v11-archive/t1/2026/10/18/"])


if __name__ == "__main__":
    unittest.main()
