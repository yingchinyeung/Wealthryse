"""External flows from silver behind FLOWS_SOURCE (ibkr_etl.flows_source), on SQLite.

Daily made-up Flex statements are loaded with today's loader, each asking for the last 14
days as the daily pull does, so every record arrives many times. Synthetic accounts and
amounts only. tests/test_flows_source_db.py runs the PostgreSQL side.
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import flows_source, provisional_benchmark
from ibkr_etl.config import Settings
from ibkr_etl.provisional_benchmark import _bronze_external_flows, _external_flows
from ibkr_etl.service import ingest_content

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "U9300001"
OTHER = "U9300002"
FIRST = date(2026, 9, 1)
DAYS = 40
LOOKBACK = 14


def _d(day: date) -> str:
    return day.strftime("%Y%m%d")


def _element(tag: str, **attributes: str) -> str:
    return f"<{tag} " + " ".join(f'{key}="{value}"' for key, value in attributes.items()) + " />"


def deposit(day: date, amount: str, *, currency="USD", ident: str | None = None, typed=True, **extra) -> dict:
    """A bank deposit (or, negative, a withdrawal) as IBKR reports it in CashTransactions."""
    row = dict(currency=currency, reportDate=_d(day), settleDate=_d(day), dateTime=f"{_d(day)};101500",
               description="CASH RECEIPTS / ELECTRONIC FUND TRANSFERS", amount=amount, **extra)
    if typed:
        row["type"] = "Deposits/Withdrawals"
    if ident:
        row["transactionID"] = ident
    return row


def dividend(day: date) -> dict:
    return dict(currency="USD", conid="9001", transactionID=f"DIV{day.toordinal()}", reportDate=_d(day),
                dateTime=f"{_d(day)};160000", type="Dividends", description="SYNA CASH DIVIDEND", amount="12.50")


def transfer(day: date, amount: str) -> dict:
    return dict(currency="USD", assetCategory="CASH", date=_d(day), type="INTERNAL", direction="IN",
                cashTransfer=amount)


def statement(account: str, start: date, end: date, *, cash=(), transfers=()) -> bytes:
    """One account's statement for start..end: the records dated inside it, as IBKR re-sends them."""
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    inside = lambda row: _d(start) <= (row.get("reportDate") or row.get("date")) <= _d(end)  # noqa: E731
    parts = [f'<FlexStatement accountId="{account}" fromDate="{_d(start)}" toDate="{_d(end)}" period="Custom">',
             _element("AccountInformation", accountId=account, currency="USD", baseCurrency="USD",
                      accountType="Individual"),
             "<ConversionRates>"]
    parts += [_element("ConversionRate", reportDate=_d(day), fromCurrency="HKD", toCurrency="USD", rate="0.1285")
              for day in days]
    parts += ["</ConversionRates>", "<CashTransactions>"]
    parts += [_element("CashTransaction", **{"accountId": account, **row}) for row in cash if inside(row)]
    parts += ["</CashTransactions>", "<Transfers>"]
    parts += [_element("Transfer", **{"accountId": account, **row}) for row in transfers if inside(row)]
    parts += ["</Transfers>", "</FlexStatement>"]
    return ('<?xml version="1.0" encoding="UTF-8"?><FlexQueryResponse queryName="Synthetic" type="AF">'
            f'<FlexStatements count="1">{"".join(parts)}</FlexStatements></FlexQueryResponse>').encode()


def daily_statements(account: str, *, cash=(), transfers=(), days=DAYS) -> list[bytes]:
    """What the daily pull fetches each morning: the last 14 days, so records are re-sent."""
    out = []
    for run in range(days):
        end = FIRST + timedelta(days=run)
        out.append(statement(account, max(FIRST, end - timedelta(days=LOOKBACK - 1)), end,
                             cash=cash, transfers=transfers))
    return out


def standard_cash() -> list[dict]:
    return [
        deposit(FIRST + timedelta(days=3), "25000", ident="D1"),
        deposit(FIRST + timedelta(days=10), "-4000.50", ident="D2"),
        deposit(FIRST + timedelta(days=10), "1500", ident="D3"),
        deposit(FIRST + timedelta(days=17), "80000", currency="HKD", ident="D4"),
        # Older Flex files: no type and no id, so the description decides and the key hashes the content.
        deposit(FIRST + timedelta(days=21), "700", typed=False),
        dividend(FIRST + timedelta(days=5)),
        dividend(FIRST + timedelta(days=21)),
    ]


class _Loaded:
    """A SQLite database loaded with today's loader."""

    def __init__(self, root: Path):
        self.settings = Settings(
            project_root=root, flex_token="", flex_query_id="", database_path=root / "flows.sqlite",
            raw_report_dir=root / "raw", log_dir=root / "logs", timezone_name="Asia/Hong_Kong",
            daily_lookback_days=LOOKBACK, user_agent="test", request_timeout_seconds=10, max_wait_seconds=20)
        self._connection = None

    def load(self, contents: list[bytes]) -> None:
        for content in contents:
            ingest_content(self.settings, content, run_type="flows-source-test")

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.settings.database_path)
            self._connection.row_factory = sqlite3.Row
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()


class FlowsSourceTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = _Loaded(Path(directory.name))
        self.addCleanup(self.db.close)
        environment = patch.dict(os.environ, {"FLOWS_SOURCE": "", "IBKR_FLEX_LEGACY_KEY_COMPAT": "",
                                              "IBKR_FX_EVENT_KEY": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.lines: list[str] = []
        emitter = patch.object(flows_source, "emit", self.lines.append)
        emitter.start()
        self.addCleanup(emitter.stop)

    def flows(self, mode: str, account=ACCOUNT, subject_type="physical_account") -> dict[str, float]:
        with patch.dict(os.environ, {"FLOWS_SOURCE": mode}):
            return _external_flows(self.db.connection, account, subject_type=subject_type)

    def bronze(self, account=ACCOUNT) -> dict[str, float]:
        return _bronze_external_flows(self.db.connection, account, subject_type="physical_account")

    def logged(self) -> list[dict]:
        out = []
        for line in self.lines:
            self.assertTrue(line.startswith("FLOWS_SOURCE "), line)
            out.append(json.loads(line[len("FLOWS_SOURCE "):]))
        return out

    def assert_no_values_or_ids(self) -> None:
        for line in self.lines:
            self.assertNotRegex(line, r"U9\d{5}")
            self.assertNotRegex(line, r"\d+\.\d")  # no amounts


class ModeTests(unittest.TestCase):
    def test_off_unless_fallback_or_on(self):
        for value, expected in (("", "off"), ("off", "off"), ("OFF", "off"), (" false ", "off"), ("bronze", "off"),
                                ("fallback", "fallback"), (" Fallback ", "fallback"), ("on", "on"), ("ON", "on")):
            with self.subTest(value=value):
                self.assertEqual(flows_source.mode({"FLOWS_SOURCE": value}), (expected, None))
        self.assertEqual(flows_source.mode({}), ("off", None))
        self.assertEqual(flows_source.mode({"FLOWS_SOURCE": "silver"}), ("off", "unknown_setting"))

    def test_a_value_not_understood_reads_bronze_and_says_so_once(self):
        lines: list[str] = []
        with patch.object(flows_source, "_noted", set()):
            for _ in range(3):
                self.assertEqual(flows_source.selected_mode({"FLOWS_SOURCE": "shadow"}, out=lines.append), "off")
        self.assertEqual(lines, ['FLOWS_SOURCE {"decision":"bronze","reason":"unknown_setting"}'])


class MaskTests(unittest.TestCase):
    def test_subjects_are_masked_as_the_certified_build_masks_them(self):
        from ibkr_etl.certified_build import mask

        for subject in ("U9300001", "F9300012", "alpha-test", "U93"):
            self.assertEqual(flows_source._mask(subject), mask(subject))


class SilverEqualsBronzeTests(FlowsSourceTestCase):
    def setUp(self):
        super().setUp()
        self.db.load(daily_statements(ACCOUNT, cash=standard_cash()))

    def test_re_sent_records_are_held_once_in_bronze_and_silver(self):
        # Each record arrived up to 14 times; one row each, in both layers.
        connection = self.db.connection
        bronze_rows = connection.execute("SELECT COUNT(*) FROM flex_rows WHERE account_id = ? "
                                         "AND section = 'CashTransactions'", (ACCOUNT,)).fetchone()[0]
        silver_rows = connection.execute("SELECT COUNT(*) FROM cash_transactions WHERE account_id = ?",
                                         (ACCOUNT,)).fetchone()[0]
        self.assertEqual((bronze_rows, silver_rows), (7, 7))
        runs = connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0]
        self.assertEqual(runs, DAYS)

    def test_off_is_todays_reader(self):
        expected = {
            (FIRST + timedelta(days=3)).isoformat(): 25000.0,
            (FIRST + timedelta(days=10)).isoformat(): -4000.5 + 1500.0,
            (FIRST + timedelta(days=17)).isoformat(): 80000 * 0.1285,
            (FIRST + timedelta(days=21)).isoformat(): 700.0,
        }
        self.assertEqual(self.flows("off"), self.bronze())
        self.assertEqual(set(self.flows("off")), set(expected))
        for day, amount in expected.items():
            self.assertAlmostEqual(self.flows("off")[day], amount, places=9)
        self.assertEqual(self.lines, [])

    def test_off_never_reads_silver(self):
        self.db.connection.execute("DROP TABLE cash_transactions")
        self.assertEqual(self.flows("off"), self.bronze())
        self.assertEqual(self.flows(""), self.bronze())
        self.assertEqual(self.lines, [])

    def test_fallback_uses_silver_when_it_equals_bronze(self):
        silver = flows_source.silver_external_flows(self.db.connection, ACCOUNT)
        with patch.object(flows_source, "silver_external_flows", wraps=flows_source.silver_external_flows) as read:
            result = self.flows("fallback")
        read.assert_called_once()
        self.assertEqual(result, silver)
        self.assertEqual(result, self.bronze())
        self.assertEqual(self.lines, [])

    def test_on_reads_silver_only(self):
        silver = flows_source.silver_external_flows(self.db.connection, ACCOUNT)
        with patch.object(provisional_benchmark, "_bronze_external_flows",
                          side_effect=AssertionError("bronze read")):
            self.assertEqual(self.flows("on"), silver)
        self.assertEqual(self.lines, [])

    def test_recording_rows_does_not_change_todays_figure(self):
        records: list = []
        self.assertEqual(_bronze_external_flows(self.db.connection, ACCOUNT, subject_type="account",
                                                records=records), self.bronze())
        self.assertEqual(sorted((source, day) for source, day, *_ in records),
                         sorted([("cash", (FIRST + timedelta(days=offset)).isoformat())
                                 for offset in (3, 10, 10, 17, 21)]))

    def test_a_subject_type_is_looked_up_as_today(self):
        self.db.connection.execute("CREATE TABLE platform_subjects (subject_id TEXT, subject_type TEXT)")
        self.db.connection.execute("INSERT INTO platform_subjects VALUES (?, 'physical_account')", (ACCOUNT,))
        with patch.dict(os.environ, {"FLOWS_SOURCE": "fallback"}):
            self.assertEqual(_external_flows(self.db.connection, ACCOUNT), self.bronze())
            with self.assertRaisesRegex(RuntimeError, "Unknown subject"):
                _external_flows(self.db.connection, "U9399999")
        self.assertEqual(self.lines, [])

    def test_unknown_account_fails_as_today_in_every_mode(self):
        for mode in ("off", "fallback", "on"):
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, "Unknown account"):
                self.flows(mode, account="U9399999")


class CorrectionTests(FlowsSourceTestCase):
    def test_a_correction_under_the_same_key_replaces_the_amount_in_both(self):
        day = FIRST + timedelta(days=3)
        self.db.load(daily_statements(ACCOUNT, cash=[deposit(day, "25000", ident="D1")], days=8))
        # IBKR restates the deposit; the later statements carry the new amount under the same id.
        self.db.load([statement(ACCOUNT, FIRST, FIRST + timedelta(days=8),
                                cash=[deposit(day, "25500", ident="D1")])])
        self.assertEqual(self.bronze(), {day.isoformat(): 25500.0})
        self.assertEqual(self.flows("fallback"), {day.isoformat(): 25500.0})
        self.assertEqual(self.lines, [])

    def test_a_correction_under_a_new_key_is_counted_twice_by_both(self):
        # Without a transaction id the key hashes the amount: the restated record is a second row in
        # both layers, so today's reader counts both, and so does silver. Equal, and as today.
        day = FIRST + timedelta(days=3)
        self.db.load(daily_statements(ACCOUNT, cash=[deposit(day, "700", typed=False)], days=8))
        self.db.load([statement(ACCOUNT, FIRST, FIRST + timedelta(days=8),
                                cash=[deposit(day, "750", typed=False)])])
        self.assertEqual(self.bronze(), {day.isoformat(): 1450.0})
        self.assertEqual(self.flows("fallback"), {day.isoformat(): 1450.0})
        self.assertEqual(self.lines, [])

    def test_a_legacy_key_in_silver_still_compares_equal(self):
        # IBKR_FLEX_LEGACY_KEY_COMPAT: silver keeps v1.1's key where bronze has the parser's.
        self.db.load(daily_statements(ACCOUNT, cash=standard_cash(), days=25))
        self.db.connection.execute("UPDATE cash_transactions SET source_key = 'legacy:' || source_key")
        self.assertEqual(self.flows("fallback"), self.bronze())
        self.assertEqual(self.lines, [])


class DifferenceTests(FlowsSourceTestCase):
    def setUp(self):
        super().setUp()
        self.db.load(daily_statements(ACCOUNT, cash=standard_cash(), days=25))
        self.expected = self.bronze()

    def decision(self) -> dict:
        self.assertEqual(self.flows("fallback"), self.expected)  # today's figure, every time
        lines = self.logged()
        self.assertEqual(len(lines), 1, self.lines)
        self.assert_no_values_or_ids()
        return lines[0]

    def test_transfers_have_no_silver_home_so_bronze_is_kept(self):
        self.db.load([statement(ACCOUNT, FIRST, FIRST + timedelta(days=24), cash=standard_cash(),
                                transfers=[transfer(FIRST + timedelta(days=12), "3000")])])
        self.expected = self.bronze()
        self.assertEqual(self.expected[(FIRST + timedelta(days=12)).isoformat()], 3000.0)
        self.assertEqual(self.decision(), {"dates_differing": 1, "decision": "bronze", "mode": "fallback",
                                           "kinds": {"transfers_not_in_silver": 1}, "reason": "silver_differs",
                                           "subject": "u…01"})
        self.assertTrue(self.lines[0].startswith('FLOWS_SOURCE {"dates_differing":1,"decision":"bronze"'))

    def test_a_flow_only_silver_has(self):
        # An account-sync account whose bronze rows were never backfilled: silver has the deposit.
        self.db.connection.execute("DELETE FROM flex_rows WHERE account_id = ? AND data_json LIKE '%\"D1\"%'",
                                   (ACCOUNT,))
        self.expected = self.bronze()
        self.assertEqual(self.decision()["kinds"], {"only_in_silver": 1})

    def test_a_flow_only_bronze_has(self):
        self.db.connection.execute("DELETE FROM cash_transactions WHERE transaction_id = 'D4'")
        self.assertEqual(self.decision()["kinds"], {"only_in_bronze": 1})

    def test_amount_and_currency_differences(self):
        self.db.connection.execute("UPDATE cash_transactions SET amount_text = '25000.01' WHERE transaction_id = 'D1'")
        self.db.connection.execute("UPDATE cash_transactions SET currency = 'HKD' WHERE transaction_id = 'D3'")
        line = self.decision()
        self.assertEqual(line["kinds"], {"currency_differs": 1, "amount_differs": 1})
        self.assertEqual(line["dates_differing"], 2)

    def test_a_cent_counts(self):
        self.db.connection.execute("UPDATE cash_transactions SET amount_text = '-4000.49' WHERE transaction_id = 'D2'")
        self.assertEqual(self.decision()["kinds"], {"amount_differs": 1})

    def test_a_consolidated_copy_is_skipped_by_bronze_but_not_by_silver(self):
        # accountId "-": the parser files it under the statement's account in both layers;
        # only bronze keeps the raw accountId to skip it.
        day = FIRST + timedelta(days=22)
        self.db.load([statement(ACCOUNT, day, day, cash=[deposit(day, "900", accountId="-")])])
        self.expected = self.bronze()
        self.assertNotIn(day.isoformat(), self.expected)
        self.assertEqual(self.decision()["kinds"], {"only_in_silver": 1})

    def test_silver_unreadable_keeps_bronze(self):
        self.db.connection.execute("ALTER TABLE cash_transactions RENAME TO cash_transactions_elsewhere")
        line = self.decision()
        self.assertEqual(line, {"decision": "bronze", "error": "OperationalError", "mode": "fallback",
                                "reason": "silver_unreadable", "subject": "u…01"})

    def test_on_shows_silver_even_when_it_differs(self):
        self.db.connection.execute("DELETE FROM cash_transactions WHERE transaction_id = 'D4'")
        on = self.flows("on")
        self.assertNotIn((FIRST + timedelta(days=17)).isoformat(), on)
        self.assertEqual(self.lines, [])


class KiwoomFlowsTests(FlowsSourceTestCase):
    """A Kiwoom U.S. account has no Flex file: both readers take its DEPOSIT and WITHDRAWAL cash rows."""

    US = "KIWOOM-T9300003"
    KR = "KIWOOM-T9300004"

    def setUp(self):
        super().setUp()
        self.db.load(daily_statements(ACCOUNT, cash=standard_cash(), days=1))
        connection = self.db.connection
        for account, currency in ((self.US, "USD"), (self.KR, "KRW")):
            connection.execute(
                "INSERT INTO accounts (account_id, alias, base_currency, account_type, ib_entity, first_seen_at, "
                "last_seen_at, source_json) VALUES (?, 'Synthetic', ?, 'kiwoom', NULL, '2026-09-01', '2026-09-01', "
                "'{}')", (account, currency))
            for key, (day, kind, amount) in enumerate((("2026-09-02", "DEPOSIT", "5000"),
                                                       ("2026-09-09", "WITHDRAWAL", "-1200.25"),
                                                       ("2026-09-09", "DEPOSIT", "300"),
                                                       ("2026-09-10", "DIVIDEND", "8.40"))):
                connection.execute(
                    "INSERT INTO cash_transactions (account_id, source_key, transaction_id, event_datetime, "
                    "model_code, effective_date, settle_date, transaction_type, description, amount_text, currency, "
                    "conid, row_sha256, last_seen_run_id) "
                    "VALUES (?, ?, ?, ?, '', ?, NULL, ?, ?, ?, ?, NULL, '0', 1)",
                    (account, f"{account}-{key}", f"{account}-{key}", day, day, kind, kind, amount, currency))
        connection.commit()

    def test_both_readers_take_a_us_accounts_deposits_and_withdrawals(self):
        expected = {"2026-09-02": 5000.0, "2026-09-09": -900.25}
        self.assertEqual(self.bronze(self.US), expected)
        self.assertEqual(flows_source.silver_external_flows(self.db.connection, self.US), expected)
        for mode in ("off", "fallback", "on"):
            with self.subTest(mode=mode):
                self.assertEqual(self.flows(mode, account=self.US), expected)
        self.assertEqual(self.lines, [])

    def test_records_compare_equal(self):
        bronze_records: list = []
        silver_records: list = []
        bronze = _bronze_external_flows(self.db.connection, self.US, subject_type="physical_account",
                                        records=bronze_records)
        silver = flows_source.silver_external_flows(self.db.connection, self.US, records=silver_records)
        self.assertEqual({source for source, *_ in bronze_records}, {"kiwoom"})
        self.assertTrue(flows_source.compare(bronze, bronze_records, silver, silver_records).equal)

    def test_a_won_account_is_left_out_by_both(self):
        self.assertEqual(self.bronze(self.KR), {})
        self.assertEqual(flows_source.silver_external_flows(self.db.connection, self.KR), {})

    def test_an_ibkr_account_is_unchanged(self):
        self.assertEqual(self.flows("fallback"), self.bronze())
        self.assertEqual(self.lines, [])


class PortfolioTests(FlowsSourceTestCase):
    def test_portfolios_read_their_ledger_in_every_mode(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE derived_portfolio_cash_ledger (portfolio_id TEXT, event_date TEXT, "
                           "amount_base REAL, included INTEGER, external_flow INTEGER)")
        connection.execute("INSERT INTO derived_portfolio_cash_ledger VALUES ('alpha-test', '2026-09-04', 100.0, 1, 1)")
        for subject_type in ("filtered_portfolio", "composite_portfolio"):
            for mode in ("off", "fallback", "on"):
                with self.subTest(subject_type=subject_type, mode=mode), \
                        patch.object(flows_source, "silver_external_flows", side_effect=AssertionError("silver")), \
                        patch.dict(os.environ, {"FLOWS_SOURCE": mode}):
                    self.assertEqual(_external_flows(connection, "alpha-test", subject_type=subject_type),
                                     {"2026-09-04": 100.0})
        self.assertEqual(self.lines, [])


class CompareTests(unittest.TestCase):
    def test_summing_order_alone_is_not_a_difference(self):
        day = "2026-09-04"
        amounts = [1e8, 0.01, -1e8]
        bronze_records = [("cash", day, "USD", amount, amount) for amount in amounts]
        silver_records = [bronze_records[0], bronze_records[2], bronze_records[1]]
        bronze = {day: (1e8 + 0.01) - 1e8}
        silver = {day: (1e8 - 1e8) + 0.01}
        self.assertNotEqual(bronze, silver)
        self.assertTrue(flows_source.compare(bronze, bronze_records, silver, silver_records).equal)

    def test_same_total_from_different_flows_is_a_difference_only_when_dates_differ(self):
        one = [("cash", "2026-09-04", "USD", 100.0, 100.0)]
        two = [("cash", "2026-09-04", "USD", 60.0, 60.0), ("cash", "2026-09-04", "USD", 40.0, 40.0)]
        self.assertTrue(flows_source.compare({"2026-09-04": 100.0}, one, {"2026-09-04": 100.0}, two).equal)
        moved = [("cash", "2026-09-05", "USD", 100.0, 100.0)]
        result = flows_source.compare({"2026-09-04": 100.0}, one, {"2026-09-05": 100.0}, moved)
        self.assertEqual((result.dates_differing, dict(result.kinds)),
                         (2, {"only_in_bronze": 1, "only_in_silver": 1}))

    def test_a_deposit_and_withdrawal_that_cancel_still_count_as_a_date(self):
        both = [("cash", "2026-09-04", "USD", 100.0, 100.0), ("cash", "2026-09-04", "USD", -100.0, -100.0)]
        result = flows_source.compare({"2026-09-04": 0.0}, both, {}, [])
        self.assertEqual(dict(result.kinds), {"only_in_bronze": 1})


class CompareSubjectsTests(FlowsSourceTestCase):
    def test_counts_only(self):
        self.db.load(daily_statements(ACCOUNT, cash=standard_cash(), days=25))
        self.db.load(daily_statements(OTHER, cash=standard_cash(), transfers=[transfer(FIRST + timedelta(days=4), "10")],
                                      days=25))
        tally = flows_source.compare_subjects(self.db.connection, [
            (ACCOUNT, "physical_account"), (OTHER, "physical_account"), ("U9399999", "physical_account"),
            ("alpha-test", "composite_portfolio")])
        report = tally.report(tenants=1)
        self.assertEqual({key: report[key] for key in ("subjects", "accounts", "portfolios_not_affected", "matching",
                                                       "differing", "errors")},
                         {"subjects": 4, "accounts": 3, "portfolios_not_affected": 1, "matching": 1, "differing": 1,
                          "errors": 1})
        self.assertEqual(report["error_kinds"], {"bronze RuntimeError": 1})
        self.assertEqual(report["differences"], {"transfers_not_in_silver": {"subjects": 1, "dates": 1}})
        self.assertEqual(report["notes"]["bronze_transfer_rows"], {"subjects": 1, "rows": 1})
        self.assertEqual(report["notes"]["bronze_external_cash_rows"], {"subjects": 2, "rows": 10})
        self.assertEqual(report["notes"]["silver_external_cash_rows"], {"subjects": 2, "rows": 10})
        self.assertEqual(report["differing_subjects"], [
            {"subject": "u…02", "dates_differing": 1, "kinds": {"transfers_not_in_silver": 1},
             "notes": {"bronze_external_cash_rows": 5, "bronze_transfer_rows": 1, "silver_external_cash_rows": 5}}])
        text = json.dumps(report, ensure_ascii=False)
        self.assertNotRegex(text, r"U9\d{5}")
        self.assertEqual(self.lines, [])  # the comparison reports; it does not log decisions


class OneSwitchTests(unittest.TestCase):
    """Every flows reader goes through provisional_benchmark._external_flows, so FLOWS_SOURCE covers them."""

    def test_only_the_switch_and_the_bronze_backfill_call_the_bronze_reader(self):
        callers = set()
        for path in sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "scripts").glob("*.py")):
            if re.search(r"\b_bronze_external_flows\b", path.read_text(encoding="utf-8")):
                callers.add(path.relative_to(ROOT).as_posix())
        self.assertEqual(callers, {"src/ibkr_etl/provisional_benchmark.py", "src/ibkr_etl/flows_source.py",
                                   "src/ibkr_etl/flow_backfill.py"})

    def test_the_readers_of_flows_import_the_switch(self):
        users = set()
        for path in sorted((ROOT / "src").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"\b_external_flows\(", text) and "def _external_flows" not in text:
                users.add(path.relative_to(ROOT).as_posix())
                self.assertRegex(text, r"from (\.|ibkr_etl\.)provisional_benchmark import [^\n]*\b_external_flows\b",
                                 path.name)
        self.assertEqual(users, {
            "src/ibkr_etl/agent_tools.py", "src/ibkr_etl/derived_portfolio.py",
            "src/ibkr_etl/monthly_reporting_analytics.py", "src/ibkr_etl/switch_dry_run.py",
            "src/ibkr_etl/web.py", "src/wealthryse_agent/tools.py"})


class CommandTests(unittest.TestCase):
    def test_the_command_needs_a_database(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}), redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit):
            flows_source.main(["compare"])


if __name__ == "__main__":
    unittest.main()
