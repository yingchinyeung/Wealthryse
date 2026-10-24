"""Which figures change when v2's Flex pull adds Transfers and CashTransactions rows.

account-sync imports no flex_rows, so the external-flow reader sees no flows
for v1.1-imported accounts. These tests compare that state with v2's own load
of the same statements, and check the history backfill. Synthetic data only.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from switch_fixtures import ACCOUNT_A, ACCOUNT_B, FIRST_RUN, settings_for, statement_for_run
from ibkr_etl.derived_portfolio import _flow_adjusted_nav
from ibkr_etl.flow_backfill import SECTIONS, BackfillError, _equity_totals, apply, load_candidates, plan, without_rows
from ibkr_etl.performance_periods import performance_periods, period_return, validated_flows, validated_nav
from ibkr_etl.provisional_benchmark import _external_flows
from ibkr_etl.service import ingest_content
from ibkr_etl.switch_dry_run import statement_from_bytes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import medallion_flow_backfill as command  # noqa: E402

RUNS = 30


def _statements(**options):
    return [statement_for_run(FIRST_RUN + timedelta(days=day), (ACCOUNT_A, ACCOUNT_B), **options)
            for day in range(RUNS)]


class _Databases:
    """v2's load of the statements, and the same data as account-sync leaves it (no flex_rows)."""

    def __init__(self, root: Path, statements: list[bytes]):
        self.settings = settings_for(root / "v2")
        for content in statements:
            ingest_content(self.settings, content, run_type="institutional-flex-daily")
        self.v2 = sqlite3.connect(self.settings.database_path)
        self.v2.row_factory = sqlite3.Row
        self.pilot = sqlite3.connect(":memory:")
        self.v2.backup(self.pilot)
        self.pilot.row_factory = sqlite3.Row
        self.pilot.execute("DELETE FROM flex_rows")
        self.pilot.commit()

    def nav(self, connection, account=ACCOUNT_A):
        return {row[0]: float(row[1]) for row in connection.execute(
            "SELECT snapshot_date, total_text FROM nav_snapshots WHERE account_id = ? ORDER BY snapshot_date",
            (account,))}

    def flows(self, connection, account=ACCOUNT_A):
        return _external_flows(connection, account, subject_type="physical_account")

    def close(self):
        self.v2.close()
        self.pilot.close()


class SwitchFigureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def databases(self, statements):
        databases = _Databases(self.root, statements)
        self.addCleanup(databases.close)
        return databases

    def test_without_external_rows_no_figure_changes(self):
        db = self.databases(_statements())
        sections = dict(db.v2.execute("SELECT section, COUNT(*) FROM flex_rows WHERE account_id = ? "
                                      "GROUP BY section", (ACCOUNT_A,)).fetchall())
        # Dividends and withholding tax are loaded, Transfers is present but empty.
        self.assertGreater(sections["CashTransactions"], 0)
        self.assertNotIn("Transfers", sections)
        self.assertEqual(db.flows(db.v2), {})
        self.assertEqual(db.flows(db.pilot), {})
        nav = db.nav(db.v2)
        self.assertEqual(nav, db.nav(db.pilot))
        as_of = max(nav)
        self.assertEqual(performance_periods(nav, db.flows(db.v2), as_of=as_of),
                         performance_periods(nav, db.flows(db.pilot), as_of=as_of))
        # A composite's account sleeve keeps exactly its raw NAV.
        self.assertEqual(_flow_adjusted_nav(db.v2, ACCOUNT_A, nav, subject_type="physical_account"), nav)

    def test_deposit_in_the_window_changes_periods_that_contain_it(self):
        deposit_day = FIRST_RUN + timedelta(days=RUNS - 5)
        db = self.databases(_statements(deposits={(ACCOUNT_A, deposit_day): "2500"}))
        nav = validated_nav(db.nav(db.v2))
        after, before = validated_flows(db.flows(db.v2)), validated_flows(db.flows(db.pilot))
        self.assertEqual(after, {deposit_day.isoformat(): 2500.0})
        self.assertEqual(before, {})
        self.assertEqual(db.flows(db.v2, ACCOUNT_B), {})
        days = list(nav)
        earlier = period_return(nav, after, days[0], (deposit_day - timedelta(days=1)).isoformat())
        self.assertEqual(earlier.as_dict(), period_return(
            nav, before, days[0], (deposit_day - timedelta(days=1)).isoformat()).as_dict())
        with_flow = period_return(nav, after, days[0], days[-1])
        without_flow = period_return(nav, before, days[0], days[-1])
        # Quantified: only the deposit day's Modified Dietz return differs.
        prior = days[days.index(deposit_day.isoformat()) - 1]
        current = deposit_day.isoformat()
        r_without = (nav[current] - nav[prior]) / nav[prior]
        r_with = (nav[current] - nav[prior] - 2500) / (nav[prior] + 1250)
        expected = (1 + without_flow.twr) / (1 + r_without) * (1 + r_with) - 1
        self.assertAlmostEqual(with_flow.twr, expected, places=12)
        self.assertLess(with_flow.twr, without_flow.twr - 0.02)
        self.assertEqual(with_flow.net_flows, 2500.0)
        adjusted = _flow_adjusted_nav(db.v2, ACCOUNT_A, db.nav(db.v2), subject_type="physical_account")
        self.assertNotEqual(adjusted[days[-1]], nav[days[-1]])
        self.assertEqual(_flow_adjusted_nav(db.pilot, ACCOUNT_A, db.nav(db.pilot), subject_type="physical_account"),
                         db.nav(db.pilot))

    def test_deposit_before_the_window_changes_nothing_without_a_backfill(self):
        # After the switch only the last 14 days are pulled; older flex_rows stay absent.
        deposit_day = FIRST_RUN
        statements = _statements(deposits={(ACCOUNT_A, deposit_day): "5000"})
        db = self.databases(statements[-2:])
        self.assertEqual(db.flows(db.v2), {})
        backfill = load_candidates([statement_from_bytes(f"s{i}.xml", s) for i, s in enumerate(statements)],
                                   lambda statement: statement.content, accounts=[ACCOUNT_A],
                                   work_dir=self.root / "backfill")
        planned = plan(backfill, accounts=[ACCOUNT_A])
        self.assertEqual(planned["accounts"][ACCOUNT_A]["flows_added"], {deposit_day.isoformat(): 5000.0})
        self.assertTrue(planned["flows_added"])


class FlowBackfillTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def candidates(self, statements, accounts=(ACCOUNT_A,)):
        parsed = [statement_from_bytes(f"s{i}.xml", content) for i, content in enumerate(statements)]
        return load_candidates(parsed, lambda statement: statement.content, accounts=accounts,
                               work_dir=self.root / "work")

    def test_history_without_flows(self):
        statements = _statements()
        path = self.candidates(statements + statements[:3])  # duplicates are loaded once
        planned = plan(path, accounts=[ACCOUNT_A])
        item = planned["accounts"][ACCOUNT_A]
        self.assertFalse(planned["flows_added"])
        self.assertEqual(item["flows_added"], {})
        self.assertEqual(set(item["candidate_sections"]), {"CashTransactions"})
        self.assertEqual(item["new_rows"], item["candidate_rows"])
        self.assertEqual(item["new_external_cash_transactions"], 0)
        self.assertLess(item["first_date"], FIRST_RUN.isoformat())  # from the first statement on
        connection = sqlite3.connect(path)
        sections = {row[0] for row in connection.execute("SELECT DISTINCT section FROM flex_rows")}
        accounts = {row[0] for row in connection.execute("SELECT DISTINCT account_id FROM flex_rows")}
        trades = connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        connection.close()
        self.assertEqual(accounts, {ACCOUNT_A})
        self.assertTrue(sections <= set(SECTIONS) | {"Statement", "AccountInformation", "ConversionRates"})
        self.assertEqual(trades, 0)
        self.assertNotIn("rows", without_rows(planned)["accounts"][ACCOUNT_A])
        # Applying with nothing that adds a flow needs no acceptance.
        self.assertEqual(apply(_NoWrites(), {**planned, "accounts": {}}, tenant_id="t")["inserted"], 0)

    def test_deposits_and_transfers_are_flows_and_need_acceptance(self):
        deposit_day = FIRST_RUN + timedelta(days=3)
        transfer_day = FIRST_RUN + timedelta(days=10)
        statements = _statements(deposits={(ACCOUNT_A, deposit_day): "5000"},
                                 transfers=[(transfer_day, "-1200")])
        planned = plan(self.candidates(statements, accounts=(ACCOUNT_A, ACCOUNT_B)), accounts=[ACCOUNT_A, ACCOUNT_B])
        item = planned["accounts"][ACCOUNT_A]
        self.assertEqual(item["flows_added"], {deposit_day.isoformat(): 5000.0, transfer_day.isoformat(): -1200.0})
        self.assertEqual(item["new_external_cash_transactions"], 1)
        self.assertEqual(item["new_transfers"], 1)
        self.assertEqual(planned["accounts"][ACCOUNT_B]["flows_added"], {transfer_day.isoformat(): -1200.0})
        with self.assertRaises(BackfillError):
            apply(_NoWrites(), planned, tenant_id="t")

    def test_statement_must_not_change_between_reads(self):
        statement = statement_from_bytes("s.xml", _statements()[0])
        with self.assertRaises(BackfillError):
            load_candidates([statement], lambda _: _statements()[1], accounts=[ACCOUNT_A], work_dir=self.root)

    def test_command_is_a_masked_dry_run(self):
        files = []
        for index, content in enumerate(_statements()[:5]):
            path = self.root / f"s{index}.xml"
            path.write_bytes(content)
            files += ["--statement-file", str(path)]
        report = self.root / "report.json"
        output = io.StringIO()
        with redirect_stdout(output):
            code = command.main(files + ["--account", ACCOUNT_A, "--report", str(report)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertNotIn(ACCOUNT_A, output.getvalue() + report.read_text())
        self.assertEqual(json.loads(report.read_text())["mode"], "dry_run")

        deposit = _statements(deposits={(ACCOUNT_A, FIRST_RUN - timedelta(days=2)): "5000"})[0]
        (self.root / "s0.xml").write_bytes(deposit)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(command.main(files + ["--account", ACCOUNT_A, "--report", str(report)]), 1)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            command.main(files + ["--accounts-from-registry"])


class _NoWrites:
    def execute(self, *_):
        raise AssertionError("the live database must not be touched")


class NavTotalsTest(unittest.TestCase):
    def test_a_daily_record_without_a_total_field_is_traced_by_its_parts(self):
        # v1.1's Flex query sends EquitySummaryByReportDateInBase without "total"; the loader stores the
        # sum of the parts, so the trace must read the record the same way.
        rows = [{"data_json": json.dumps({"reportDate": "20240102", "total": "1000.50"})},
                {"data_json": json.dumps({"reportDate": "20240103", "cash": "-200", "stock": "1150.25",
                                          "dividendAccruals": "0.75", "interestAccruals": ""})},
                {"data_json": json.dumps({"reportDate": "20240104"})}]
        self.assertEqual(_equity_totals(rows), {"2024-01-02": {Decimal("1000.50")},
                                                "2024-01-03": {Decimal("951.00")}})


if __name__ == "__main__":
    unittest.main()
