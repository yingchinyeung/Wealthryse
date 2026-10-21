import json
import sqlite3
import unittest
from unittest.mock import patch

from ibkr_etl.derived_portfolio import _flow_adjusted_nav
from ibkr_etl.monthly_reporting_analytics import _external_cash_flow
from ibkr_etl.provisional_benchmark import _external_flows
from ibkr_etl.monthly_artifacts import (
    MonthlyArtifactError,
    _normalize_nav,
    _trailing_twelve_month_return,
)


class FactsheetFlowAdjustedReturnTests(unittest.TestCase):
    def test_deposit_is_not_counted_as_return(self):
        rows = _normalize_nav([
            {"date": "2026-01-02", "nav": 100.0},
            {"date": "2026-01-05", "nav": 150.0, "flow": 50.0},
        ])
        self.assertAlmostEqual(rows[-1]["value"], 100.0)
        self.assertEqual(rows[-1]["nav"], 150.0)

    def test_modified_dietz_matches_website_formula(self):
        rows = _normalize_nav([
            {"date": "2026-01-02", "nav": 100.0},
            {"date": "2026-01-05", "nav": 160.0, "flow": 50.0},
        ])
        # r = (160 - 100 - 50) / (100 + 25)
        self.assertAlmostEqual(rows[-1]["value"], 100.0 * (1 + 10.0 / 125.0))

    def test_withdrawal_is_not_counted_as_loss(self):
        rows = _normalize_nav([
            {"date": "2026-01-02", "nav": 200.0},
            {"date": "2026-01-05", "nav": 100.0, "flow": -100.0},
        ])
        self.assertAlmostEqual(rows[-1]["value"], 100.0)

    def test_rows_without_flows_keep_the_old_nav_ratio(self):
        rows = _normalize_nav([
            {"date": "2026-01-02", "nav": 100.0},
            {"date": "2026-01-05", "nav": 110.0},
            {"date": "2026-01-06", "nav": 99.0},
        ])
        self.assertAlmostEqual(rows[-1]["value"], 99.0)

    def test_first_row_flow_is_ignored(self):
        rows = _normalize_nav([
            {"date": "2026-01-02", "nav": 100.0, "flow": 100.0},
            {"date": "2026-01-05", "nav": 100.0},
        ])
        self.assertEqual(rows[0]["value"], 100.0)
        self.assertAlmostEqual(rows[-1]["value"], 100.0)

    def test_non_positive_denominator_is_rejected(self):
        with self.assertRaises(MonthlyArtifactError):
            _normalize_nav([
                {"date": "2026-01-02", "nav": 100.0},
                {"date": "2026-01-05", "nav": 1.0, "flow": -250.0},
            ])

    def test_trailing_twelve_months_ignores_deposits(self):
        rows = _normalize_nav([
            {"date": "2025-01-31", "nav": 100.0},
            {"date": "2025-06-30", "nav": 1100.0, "flow": 1000.0},
            {"date": "2026-01-31", "nav": 1210.0},
        ])
        self.assertAlmostEqual(_trailing_twelve_month_return(rows, "2026-01-31"), 0.10)

    def test_trailing_twelve_months_starts_at_prior_close_when_month_end_is_closed(self):
        # 31 May 2025 was a Saturday: the window starts at the 30 May close,
        # not the first close after it.
        rows = _normalize_nav([
            {"date": "2025-05-30", "nav": 100.0},
            {"date": "2025-06-02", "nav": 104.0},
            {"date": "2026-05-29", "nav": 120.0},
        ])
        self.assertAlmostEqual(_trailing_twelve_month_return(rows, "2026-05-31"), 0.20)

    def test_trailing_twelve_months_needs_a_full_year_of_history(self):
        rows = _normalize_nav([
            {"date": "2025-06-02", "nav": 100.0},
            {"date": "2026-05-29", "nav": 120.0},
        ])
        self.assertIsNone(_trailing_twelve_month_return(rows, "2026-05-31"))


class CompositeSleeveFlowTests(unittest.TestCase):
    def adjusted(self, values, flows):
        with patch("ibkr_etl.provisional_benchmark._external_flows", return_value=flows) as loader:
            result = _flow_adjusted_nav(object(), "U1", values, subject_type="account")
        loader.assert_called_once()
        self.assertEqual(loader.call_args.kwargs, {"subject_type": "account"})
        return result

    def test_sleeve_without_flows_keeps_raw_nav_exactly(self):
        values = {"2026-01-02": 100.0, "2026-01-05": 103.3, "2026-01-06": 101.7}
        self.assertEqual(self.adjusted(values, {}), values)

    def test_deposit_does_not_inflate_sleeve(self):
        values = {"2026-01-02": 100.0, "2026-01-05": 150.0, "2026-01-06": 165.0}
        result = self.adjusted(values, {"2026-01-03": 50.0})
        self.assertAlmostEqual(result["2026-01-05"], 100.0)
        self.assertAlmostEqual(result["2026-01-06"], 110.0)


class ExternalFlowSourceTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE accounts (account_id TEXT, base_currency TEXT);
            CREATE TABLE fx_rates (account_id TEXT, rate_date TEXT, from_currency TEXT,
                                   to_currency TEXT, rate_text TEXT);
            CREATE TABLE flex_rows (account_id TEXT, section TEXT, data_json TEXT);
            INSERT INTO accounts VALUES ('U1', 'USD');
            """
        )

    def add(self, section, **values):
        self.connection.execute(
            "INSERT INTO flex_rows VALUES ('U1', ?, ?)", (section, json.dumps(values))
        )

    def flows(self):
        return _external_flows(self.connection, "U1", subject_type="account")

    def test_bank_deposit_in_cash_transactions_without_type_is_a_flow(self):
        self.add("CashTransactions", accountId="U1", currency="USD", reportDate="20260529",
                 dateTime="20260528;230647", amount="25000",
                 description="CASH RECEIPTS / ELECTRONIC FUND TRANSFERS")
        self.assertEqual(self.flows(), {"2026-05-29": 25000.0})

    def test_typed_withdrawal_is_a_flow(self):
        self.add("CashTransactions", accountId="U1", currency="USD", reportDate="20260601",
                 amount="-5000", type="Deposits/Withdrawals", description="DISBURSEMENT")
        self.assertEqual(self.flows(), {"2026-06-01": -5000.0})

    def test_consolidated_copy_and_income_are_not_flows(self):
        self.add("CashTransactions", accountId="-", currency="USD", reportDate="20260529",
                 amount="25000", description="CASH RECEIPTS / ELECTRONIC FUND TRANSFERS")
        self.add("CashTransactions", accountId="U1", currency="USD", reportDate="20260529",
                 amount="120", description="SWIRE PACIFIC CASH DIVIDEND")
        self.add("CashTransactions", accountId="U1", currency="USD", reportDate="20260529",
                 amount="3", description="BHP ADR (AMERICAN DEPOSITARY RECEIPT) DIVIDEND")
        self.assertEqual(self.flows(), {})

    def test_internal_cash_transfer_still_counts(self):
        self.add("Transfers", accountId="U1", currency="USD", date="20260526",
                 assetCategory="CASH", cashTransfer="25000", type="INTERNAL")
        self.assertEqual(self.flows(), {"2026-05-26": 25000.0})


class KiwoomExternalFlowTests(unittest.TestCase):
    """A Kiwoom account has no Flex file: its DEPOSIT and WITHDRAWAL cash rows are its flows (USD only)."""

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE accounts (account_id TEXT, base_currency TEXT);
            CREATE TABLE fx_rates (account_id TEXT, rate_date TEXT, from_currency TEXT,
                                   to_currency TEXT, rate_text TEXT);
            CREATE TABLE flex_rows (account_id TEXT, section TEXT, data_json TEXT);
            CREATE TABLE cash_transactions (account_id TEXT, effective_date TEXT, transaction_type TEXT,
                                            amount_text TEXT, currency TEXT);
            INSERT INTO accounts VALUES ('KIWOOM-US', 'USD'), ('KIWOOM-KR', 'KRW'), ('U1', 'USD');
            """
        )

    def cash(self, account, day, kind, amount, currency):
        self.connection.execute("INSERT INTO cash_transactions VALUES (?, ?, ?, ?, ?)",
                                (account, day, kind, amount, currency))

    def flows(self, account):
        return _external_flows(self.connection, account, subject_type="physical_account")

    def test_usd_kiwoom_deposits_and_withdrawals_are_flows_and_income_is_not(self):
        self.cash("KIWOOM-US", "2025-11-26", "DEPOSIT", "377.2", "USD")
        self.cash("KIWOOM-US", "2026-01-08", "DEPOSIT", "167.19", "USD")
        self.cash("KIWOOM-US", "2026-01-08", "WITHDRAWAL", "-20", "USD")
        self.cash("KIWOOM-US", "2025-11-26", "DIVIDEND", "1.96", "USD")
        self.cash("KIWOOM-US", "2026-01-22", "CORPORATE_ACTION_CASH", "770", "USD")
        flows = self.flows("KIWOOM-US")
        self.assertEqual(set(flows), {"2025-11-26", "2026-01-08"})
        self.assertAlmostEqual(flows["2025-11-26"], 377.2)
        self.assertAlmostEqual(flows["2026-01-08"], 147.19)

    def test_korea_kiwoom_account_is_unchanged(self):
        self.cash("KIWOOM-KR", "2025-11-26", "DEPOSIT", "500000", "KRW")
        self.assertEqual(self.flows("KIWOOM-KR"), {})

    def test_an_account_with_a_flex_file_reads_only_the_flex_file(self):
        self.connection.execute("INSERT INTO flex_rows VALUES ('U1', 'Trades', '{}')")
        self.cash("U1", "2026-01-08", "DEPOSIT", "100", "USD")
        self.assertEqual(self.flows("U1"), {})


class AttributionCashClassifierTests(unittest.TestCase):
    def test_swire_dividend_is_not_an_external_wire(self):
        self.assertFalse(_external_cash_flow("Dividends", "SWIRE PACIFIC LTD-CL A CASH DIVIDEND"))
        self.assertTrue(_external_cash_flow("Deposits/Withdrawals", "WIRE IN"))
        self.assertTrue(_external_cash_flow("", "CASH RECEIPTS / ELECTRONIC FUND TRANSFERS"))


if __name__ == "__main__":
    unittest.main()
