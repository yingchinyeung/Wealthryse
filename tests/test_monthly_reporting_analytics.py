from __future__ import annotations

import json
import sqlite3
import unittest

from ibkr_etl.monthly_reporting_analytics import build_monthly_reporting_analytics


class MonthlyReportingAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE nav_snapshots (
                account_id TEXT,
                snapshot_date TEXT,
                currency TEXT,
                asset_class TEXT,
                total_text TEXT
            );
            CREATE TABLE position_snapshots (
                account_id TEXT,
                snapshot_date TEXT,
                conid TEXT,
                currency TEXT,
                quantity_text TEXT,
                mark_price_text TEXT
            );
            CREATE TABLE instruments (
                conid TEXT PRIMARY KEY,
                symbol TEXT,
                description TEXT
            );
            CREATE TABLE instrument_classifications (
                conid TEXT,
                provider TEXT,
                sector TEXT
            );
            CREATE TABLE cash_transactions (
                account_id TEXT,
                effective_date TEXT,
                transaction_type TEXT,
                description TEXT,
                amount_text TEXT,
                currency TEXT,
                conid TEXT,
                source_key TEXT
            );
            CREATE TABLE fx_rates (
                account_id TEXT,
                rate_date TEXT,
                from_currency TEXT,
                to_currency TEXT,
                rate_text TEXT
            );
            """
        )
        self.connection.execute(
            "INSERT INTO instruments VALUES (?, ?, ?)",
            ("1", "TEST", "Test Security"),
        )
        self.connection.execute(
            "INSERT INTO instrument_classifications VALUES (?, ?, ?)",
            ("1", "Yahoo Finance search profile", "Technology"),
        )
        for day, nav, price in (
            ("2026-07-31", "100", "5.00"),
            ("2026-08-03", "102", "5.10"),
            ("2026-08-31", "103", "5.20"),
        ):
            self.connection.execute(
                "INSERT INTO nav_snapshots VALUES (?, ?, ?, ?, ?)",
                ("U16088571", day, "USD", "TOTAL", nav),
            )
            self.connection.execute(
                "INSERT INTO position_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                ("U16088571", day, "1", "USD", "10", price),
            )
        for row in (
            ("2026-08-10", "Dividend", "TEST dividend", "10", "1", "cash-1"),
            ("2026-08-20", "Interest", "Debit interest", "-2", None, "cash-2"),
            ("2026-08-21", "Deposit", "External deposit", "100", None, "cash-3"),
        ):
            self.connection.execute(
                """
                INSERT INTO cash_transactions
                (account_id, effective_date, transaction_type, description,
                 amount_text, currency, conid, source_key)
                VALUES ('U16088571', ?, ?, ?, ?, 'USD', ?, ?)
                """,
                row,
            )
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_monthly_attribution_reconciles_and_excludes_external_cash(self) -> None:
        result = build_monthly_reporting_analytics(
            self.connection,
            subject_id="U16088571",
            period_end="2026-08-31",
            reporting_currency="USD",
        )

        self.assertEqual(result["quality_status"], "pass")
        data = result["data"]
        self.assertTrue(data["available"])
        self.assertEqual(data["daily_observations"], 2)
        self.assertAlmostEqual(data["portfolio_monthly_return"], 0.03, places=12)
        self.assertAlmostEqual(
            data["equity_contribution"] + data["residual_contribution"],
            data["portfolio_monthly_return"],
            places=12,
        )
        self.assertEqual(data["security_contributions"][0]["symbol"], "TEST")
        self.assertEqual(data["sector_contributions"][0]["sector"], "Technology")
        cash = data["cash_activity"]
        self.assertEqual(cash["total_dividends"], 10.0)
        self.assertEqual(cash["interest_paid"], -2.0)
        self.assertEqual(cash["net_listed_cash_activity"], 8.0)
        self.assertEqual(cash["dividends_by_symbol"], [{"symbol": "TEST", "amount": 10.0}])

    def test_non_month_end_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "calendar month end"):
            build_monthly_reporting_analytics(
                self.connection,
                subject_id="U16088571",
                period_end="2026-08-30",
                reporting_currency="USD",
            )

    def test_missing_initial_positions_do_not_drop_nav_return(self) -> None:
        self.connection.execute("DELETE FROM position_snapshots WHERE snapshot_date='2026-07-31'")
        result=build_monthly_reporting_analytics(self.connection,subject_id='U16088571',period_end='2026-08-31',reporting_currency='USD')
        data=result['data']
        self.assertTrue(data['available']);self.assertEqual(result['quality_status'],'pass')
        self.assertEqual(data['daily_observations'],2)
        self.assertAlmostEqual(data['portfolio_monthly_return'],.03,places=12)
        self.assertAlmostEqual(data['equity_contribution'],10*(5.2-5.1)/102,places=12)
        self.assertAlmostEqual(data['equity_contribution']+data['residual_contribution'],.03,places=12)
        self.assertEqual(data['position_coverage']['residual_only_intervals'],['2026-08-03'])
        self.assertEqual(data['position_coverage']['missing_position_dates'],['2026-07-31'])

    def test_missing_intermediate_positions_do_not_skip_nav_interval(self) -> None:
        self.connection.execute("INSERT INTO nav_snapshots VALUES ('U16088571','2026-08-04','USD','TOTAL','104')")
        data=build_monthly_reporting_analytics(self.connection,subject_id='U16088571',period_end='2026-08-31',reporting_currency='USD')['data']
        self.assertEqual(data['daily_observations'],3)
        self.assertAlmostEqual(data['portfolio_monthly_return'],.03,places=12)
        self.assertEqual(data['position_coverage']['residual_only_intervals'],['2026-08-04','2026-08-31'])
        self.assertAlmostEqual(data['equity_contribution']+data['residual_contribution'],.03,places=12)

    def test_missing_final_positions_do_not_truncate_month(self) -> None:
        self.connection.execute("DELETE FROM position_snapshots WHERE snapshot_date='2026-08-31'")
        data=build_monthly_reporting_analytics(self.connection,subject_id='U16088571',period_end='2026-08-31',reporting_currency='USD')['data']
        self.assertEqual(data['period_end'],'2026-08-31');self.assertEqual(data['daily_observations'],2)
        self.assertAlmostEqual(data['portfolio_monthly_return'],.03,places=12)
        self.assertEqual(data['position_coverage']['residual_only_intervals'],['2026-08-31'])

    def test_boundary_uses_latest_nav_not_older_position_date(self) -> None:
        self.connection.execute("DELETE FROM position_snapshots WHERE snapshot_date='2026-07-31'")
        self.connection.execute("INSERT INTO nav_snapshots VALUES ('U16088571','2026-07-30','USD','TOTAL','90')")
        self.connection.execute("INSERT INTO position_snapshots VALUES ('U16088571','2026-07-30','1','USD','10','4.50')")
        data=build_monthly_reporting_analytics(self.connection,subject_id='U16088571',period_end='2026-08-31',reporting_currency='USD')['data']
        self.assertAlmostEqual(data['portfolio_monthly_return'],.03,places=12)
        self.assertEqual(data['position_coverage']['missing_position_dates'],['2026-07-31'])

    def test_without_any_matched_positions_attribution_remains_unavailable(self) -> None:
        self.connection.execute("DELETE FROM position_snapshots")
        result=build_monthly_reporting_analytics(self.connection,subject_id='U16088571',period_end='2026-08-31',reporting_currency='USD')
        self.assertEqual(result['quality_status'],'fail');self.assertFalse(result['data']['available'])

    def test_non_base_positions_and_cash_are_converted_with_flex_fx(self) -> None:
        self.connection.execute("DELETE FROM nav_snapshots")
        self.connection.execute("DELETE FROM position_snapshots")
        self.connection.execute("DELETE FROM cash_transactions")
        for day, nav, price, rate in (
            ("2026-07-31", "100", "50.00", "0.0200"),
            ("2026-08-03", "104", "51.00", "0.0202"),
            ("2026-08-31", "108", "52.00", "0.0204"),
        ):
            self.connection.execute(
                "INSERT INTO nav_snapshots VALUES (?, ?, ?, ?, ?)",
                ("U16088571", day, "USD", "TOTAL", nav),
            )
            self.connection.execute(
                "INSERT INTO position_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                ("U16088571", day, "1", "HKD", "100", price),
            )
            self.connection.execute(
                "INSERT INTO fx_rates VALUES (?, ?, ?, ?, ?)",
                ("U16088571", day, "HKD", "USD", rate),
            )
        self.connection.execute(
            """
            INSERT INTO cash_transactions
            (account_id, effective_date, transaction_type, description,
             amount_text, currency, conid, source_key)
            VALUES ('U16088571', '2026-08-10', 'Dividend', 'TEST dividend',
                    '100', 'HKD', '1', 'cash-hkd')
            """
        )
        self.connection.commit()

        result = build_monthly_reporting_analytics(
            self.connection,
            subject_id="U16088571",
            period_end="2026-08-31",
            reporting_currency="USD",
        )

        self.assertEqual(result["quality_status"], "pass")
        data = result["data"]
        self.assertEqual(data["fx_conversion"]["converted_currencies"], ["HKD"])
        self.assertEqual(data["fx_conversion"]["missing_currencies"], [])
        self.assertAlmostEqual(data["cash_activity"]["total_dividends"], 2.02)
        self.assertAlmostEqual(
            data["equity_contribution"] + data["residual_contribution"],
            data["portfolio_monthly_return"],
            places=12,
        )

    def test_filtered_portfolio_resolves_fx_source_from_governed_definition(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE platform_subjects (
                subject_id TEXT PRIMARY KEY,
                subject_type TEXT NOT NULL,
                source_account_id TEXT
            );
            CREATE TABLE subject_calculation_definitions (
                subject_id TEXT,
                subject_type TEXT,
                definition_version INTEGER,
                configuration_json TEXT,
                approval_status TEXT,
                created_at TEXT
            );
            CREATE TABLE subject_nav_series (
                subject_id TEXT, snapshot_date TEXT, currency TEXT, total_text TEXT
            );
            CREATE TABLE subject_position_series (
                subject_id TEXT, snapshot_date TEXT, conid TEXT, currency TEXT,
                quantity_text TEXT, mark_price_text TEXT
            );
            CREATE TABLE subject_cash_activity_series (
                subject_id TEXT, effective_date TEXT, transaction_type TEXT,
                description TEXT, amount_text TEXT, currency TEXT, conid TEXT,
                source_key TEXT, included INTEGER
            );
            CREATE TABLE instruments (
                conid TEXT PRIMARY KEY, symbol TEXT, description TEXT
            );
            CREATE TABLE instrument_classifications (
                conid TEXT, provider TEXT, sector TEXT
            );
            CREATE TABLE fx_rates (
                account_id TEXT, rate_date TEXT, from_currency TEXT,
                to_currency TEXT, rate_text TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO platform_subjects VALUES ('filtered', 'filtered_portfolio', NULL)"
        )
        connection.execute(
            "INSERT INTO subject_calculation_definitions VALUES (?, ?, ?, ?, ?, ?)",
            (
                "filtered",
                "filtered_portfolio",
                1,
                json.dumps({"source_account_id": "U-SOURCE"}),
                "approved",
                "2026-08-01T00:00:00Z",
            ),
        )
        connection.execute("INSERT INTO instruments VALUES ('1', 'TEST', 'Test Security')")
        connection.execute(
            "INSERT INTO instrument_classifications VALUES ('1', 'Yahoo Finance search profile', 'Technology')"
        )
        for day, nav, price, rate in (
            ("2026-07-31", "100", "50.00", "0.0200"),
            ("2026-08-03", "104", "51.00", "0.0202"),
            ("2026-08-31", "108", "52.00", "0.0204"),
        ):
            connection.execute(
                "INSERT INTO subject_nav_series VALUES ('filtered', ?, 'USD', ?)",
                (day, nav),
            )
            connection.execute(
                "INSERT INTO subject_position_series VALUES ('filtered', ?, '1', 'HKD', '100', ?)",
                (day, price),
            )
            connection.execute(
                "INSERT INTO fx_rates VALUES ('U-SOURCE', ?, 'HKD', 'USD', ?)",
                (day, rate),
            )
        connection.commit()
        try:
            result = build_monthly_reporting_analytics(
                connection,
                subject_id="filtered",
                period_end="2026-08-31",
                reporting_currency="USD",
            )
        finally:
            connection.close()

        self.assertEqual(result["quality_status"], "pass")
        self.assertTrue(result["data"]["available"])
        self.assertEqual(result["data"]["fx_conversion"]["converted_currencies"], ["HKD"])
        self.assertEqual(result["data"]["fx_conversion"]["missing_currencies"], [])


if __name__ == "__main__":
    unittest.main()
