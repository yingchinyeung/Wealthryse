from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from ibkr_etl import web
from ibkr_etl.performance_periods import (
    PerformanceInputError,
    SHORT_PERIOD_IRR_NOTE,
    flow_statement,
    performance_periods,
    period_return,
    validated_flows,
    validated_nav,
    xirr,
)


def _period(result: dict, key: str) -> dict:
    return next(item for item in result["periods"] if item["key"] == key)


class XirrTests(unittest.TestCase):
    def test_single_year_growth(self) -> None:
        # 365 days, ten percent growth.
        self.assertAlmostEqual(xirr([("2021-01-01", -1000), ("2022-01-01", 1100)]), 0.10, places=10)

    def test_excel_reference_example(self) -> None:
        flows = [
            ("2008-01-01", -10000),
            ("2008-03-01", 2750),
            ("2008-10-30", 4250),
            ("2009-02-15", 3250),
            ("2009-04-01", 2750),
        ]
        self.assertAlmostEqual(xirr(flows), 0.373362535, places=8)

    def test_negative_return(self) -> None:
        self.assertAlmostEqual(xirr([("2021-01-01", -1000), ("2022-01-01", 800)]), -0.20, places=10)

    def test_no_sign_change_has_no_solution(self) -> None:
        self.assertIsNone(xirr([("2021-01-01", -1000), ("2021-06-01", -10)]))
        self.assertIsNone(xirr([]))
        self.assertIsNone(xirr([("2021-01-01", -1000), ("2021-01-01", 1100)]))

    def test_total_loss_has_no_finite_rate(self) -> None:
        self.assertIsNone(xirr([("2021-01-01", -1000), ("2022-01-01", 0)]))


class PeriodBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nav = validated_nav(
            {
                "2024-06-28": 100.0,
                "2024-12-31": 110.0,
                "2025-03-31": 121.0,
                "2025-06-30": 133.1,
                "2025-07-15": 133.1,
            }
        )

    def test_standard_periods_use_prior_period_end_as_base(self) -> None:
        result = performance_periods(self.nav, {}, as_of="2025-07-20")
        keys = [item["key"] for item in result["periods"]]
        self.assertEqual(
            keys, ["mtd", "qtd", "ytd", "year_2025", "year_2024", "since_inception"]
        )
        mtd = _period(result, "mtd")
        self.assertEqual((mtd["start_date"], mtd["end_date"]), ("2025-06-30", "2025-07-15"))
        self.assertAlmostEqual(mtd["twr"], 0.0)
        ytd = _period(result, "ytd")
        self.assertEqual(ytd["start_date"], "2024-12-31")
        self.assertAlmostEqual(ytd["twr"], 0.21)
        self.assertEqual(ytd["status"], "pass")
        self.assertEqual(_period(result, "year_2025")["label"], "2025 (to date)")
        since = _period(result, "since_inception")
        self.assertFalse(since["partial"])
        self.assertAlmostEqual(since["twr"], 0.331)
        self.assertEqual(result["inception_date"], "2024-06-28")
        self.assertEqual(result["effective_as_of"], "2025-07-15")

    def test_partial_first_year_is_flagged(self) -> None:
        result = performance_periods(self.nav, {}, as_of="2025-07-20")
        first_year = _period(result, "year_2024")
        self.assertTrue(first_year["partial"])
        self.assertEqual(first_year["status"], "partial")
        self.assertEqual(first_year["start_date"], "2024-06-28")
        self.assertAlmostEqual(first_year["twr"], 0.10)
        self.assertIn("History begins on 2024-06-28", first_year["issues"][0])

    def test_missing_boundary_date_uses_last_nav_on_or_before(self) -> None:
        # 2025-06-30 is present; 2025-05-31 is not, so May's base is 2025-03-31.
        result = period_return(self.nav, {}, "2025-06-01", "2025-06-15")
        self.assertEqual(result.status, "empty")
        result = period_return(self.nav, {}, "2025-05-01", "2025-06-30")
        self.assertEqual((result.start_date, result.end_date), ("2025-03-31", "2025-06-30"))
        self.assertAlmostEqual(result.twr, 0.10)

    def test_period_entirely_before_history_is_empty(self) -> None:
        result = period_return(self.nav, {}, "2023-01-01", "2023-12-31")
        self.assertEqual(result.status, "empty")
        self.assertIsNone(result.twr)

    def test_month_without_observations_is_empty_not_zero(self) -> None:
        result = performance_periods(self.nav, {}, as_of="2025-08-05")
        mtd = _period(result, "mtd")
        self.assertEqual(mtd["status"], "empty")
        self.assertIsNone(mtd["twr"])
        self.assertIsNone(mtd["irr_annualized"])

    def test_custom_period_and_reversed_dates(self) -> None:
        result = performance_periods(
            self.nav, {}, as_of="2025-07-15", custom_start="2025-01-01", custom_end="2025-03-31"
        )
        self.assertAlmostEqual(result["custom"]["twr"], 0.10)
        self.assertEqual(result["custom"]["start_date"], "2024-12-31")
        with self.assertRaises(PerformanceInputError):
            period_return(self.nav, {}, "2025-04-01", "2025-03-01")

    def test_custom_start_before_history_starts_at_inception(self) -> None:
        result = performance_periods(
            self.nav, {}, as_of="2025-07-15", custom_start="2020-01-01", custom_end="2025-07-15"
        )
        self.assertTrue(result["custom"]["partial"])
        self.assertEqual(result["custom"]["start_date"], "2024-06-28")
        self.assertAlmostEqual(result["custom"]["twr"], 0.331)

    def test_observations_after_as_of_are_ignored(self) -> None:
        result = performance_periods(self.nav, {}, as_of="2025-06-30")
        self.assertEqual(result["effective_as_of"], "2025-06-30")
        self.assertAlmostEqual(_period(result, "since_inception")["twr"], 0.331)

    def test_empty_history(self) -> None:
        result = performance_periods({}, {}, as_of=None)
        self.assertEqual(result["periods"], [])
        result = performance_periods({}, {}, as_of="2025-01-31")
        self.assertEqual([item["status"] for item in result["periods"]], ["empty"] * 3)

    def test_invalid_inputs_are_rejected(self) -> None:
        with self.assertRaises(PerformanceInputError):
            validated_nav([("2025-01-01", 1.0), ("2025-01-01", 2.0)])
        with self.assertRaises(PerformanceInputError):
            validated_nav({"not-a-date": 1.0})
        with self.assertRaises(PerformanceInputError):
            validated_flows({"2025-01-01": float("nan")})


class FlowAdjustmentTests(unittest.TestCase):
    def test_contribution_is_not_performance(self) -> None:
        nav = {"2025-01-31": 100.0, "2025-02-28": 150.0}
        result = period_return(nav, {"2025-02-28": 50.0}, "2025-02-01", "2025-02-28")
        self.assertAlmostEqual(result.twr, 0.0)
        self.assertEqual(result.net_flows, 50.0)
        self.assertEqual((result.flows_in, result.flows_out), (50.0, 0.0))

    def test_matches_website_modified_dietz_chain(self) -> None:
        nav = {"2025-01-02": 100.0, "2025-01-03": 112.0, "2025-01-06": 100.0}
        flows = {"2025-01-03": 10.0, "2025-01-04": -5.0}
        result = period_return(nav, flows, "2025-01-03", "2025-01-06")
        first = (112 - 100 - 10) / (100 + 5)
        second = (100 - 112 + 5) / (112 - 2.5)
        self.assertAlmostEqual(result.twr, (1 + first) * (1 + second) - 1)

    def test_flow_on_start_boundary_belongs_to_prior_period(self) -> None:
        nav = {"2025-01-31": 150.0, "2025-02-28": 165.0}
        flows = {"2025-01-31": 50.0}
        result = period_return(nav, flows, "2025-02-01", "2025-02-28")
        self.assertAlmostEqual(result.twr, 0.10)
        self.assertEqual(result.net_flows, 0.0)
        # The money-weighted return agrees when no flows fall inside.
        self.assertAlmostEqual(result.irr_period, 0.10, places=8)

    def test_flow_on_end_boundary_is_inside_the_period(self) -> None:
        nav = {"2025-01-31": 100.0, "2025-02-28": 160.0}
        result = period_return(nav, {"2025-02-28": 50.0}, "2025-02-01", "2025-02-28")
        self.assertAlmostEqual(result.twr, 10.0 / 125.0)
        self.assertEqual(result.net_flows, 50.0)

    def test_weekend_flow_counts_in_next_interval(self) -> None:
        nav = {"2025-01-03": 100.0, "2025-01-06": 90.0}
        result = period_return(nav, {"2025-01-04": -10.0}, "2025-01-04", "2025-01-06")
        self.assertAlmostEqual(result.twr, 0.0)
        self.assertEqual(result.flows_out, -10.0)

    def test_non_positive_denominator_fails_closed(self) -> None:
        nav = {"2025-01-02": 100.0, "2025-01-03": 10.0, "2025-01-06": 11.0}
        result = period_return(nav, {"2025-01-03": -250.0}, "2025-01-03", "2025-01-06")
        self.assertEqual(result.status, "fail")
        self.assertIsNone(result.twr)
        self.assertIsNone(result.irr_annualized)
        self.assertIn("denominator is not positive on 2025-01-03", result.issues[0])

    def test_return_at_or_below_minus_one_fails_closed(self) -> None:
        nav = {"2025-01-02": 100.0, "2025-01-03": 0.0, "2025-01-06": 10.0}
        result = period_return(nav, {}, "2025-01-03", "2025-01-06")
        self.assertEqual(result.status, "fail")
        self.assertIn("return is invalid on 2025-01-03", result.issues[0])

    def test_irr_annualised_with_short_period_note(self) -> None:
        nav = {"2025-01-31": 100.0, "2025-02-28": 102.0}
        result = period_return(nav, {}, "2025-02-01", "2025-02-28")
        self.assertAlmostEqual(result.irr_annualized, 1.02 ** (365 / 28) - 1, places=8)
        self.assertAlmostEqual(result.irr_period, 0.02, places=8)
        self.assertEqual(result.irr_note, SHORT_PERIOD_IRR_NOTE)
        self.assertIsNone(result.twr_annualized)

    def test_irr_weights_flow_timing(self) -> None:
        nav = {"2025-01-01": 1000.0, "2026-01-01": 2200.0}
        result = period_return(nav, {"2025-07-02": 1000.0}, "2025-01-02", "2026-01-01")
        expected = xirr([("2025-01-01", -1000), ("2025-07-02", -1000), ("2026-01-01", 2200)])
        self.assertAlmostEqual(result.irr_annualized, expected)
        self.assertIsNone(result.irr_note)
        self.assertIsNotNone(result.twr_annualized)


class FlowStatementTests(unittest.TestCase):
    def test_statement_totals_and_groupings(self) -> None:
        flows = {"2025-01-05": 100.0, "2025-01-20": -30.0, "2025-02-01": 50.0, "2026-03-01": -10.0, "2026-04-01": 0.0}
        statement = flow_statement(flows, "2025-01-05", "2026-03-01")
        self.assertEqual([row["date"] for row in statement["flows"]],
                         ["2025-01-05", "2025-01-20", "2025-02-01", "2026-03-01"])
        self.assertEqual(statement["flows"][1]["direction"], "withdrawal")
        self.assertEqual(statement["totals"], {"flows_in": 150.0, "flows_out": -40.0, "net": 110.0, "count": 4})
        self.assertEqual(statement["by_month"][0], {"period": "2025-01", "flows_in": 100.0, "flows_out": -30.0, "net": 70.0, "count": 2})
        self.assertEqual([row["period"] for row in statement["by_year"]], ["2025", "2026"])

    def test_statement_bounds_are_inclusive_and_validated(self) -> None:
        flows = {"2025-01-05": 100.0, "2025-01-06": 5.0}
        self.assertEqual(flow_statement(flows, "2025-01-06", "2025-01-06")["totals"]["net"], 5.0)
        self.assertEqual(flow_statement({}, None, None)["flows"], [])
        with self.assertRaises(PerformanceInputError):
            flow_statement(flows, "2025-02-01", "2025-01-01")


class PerformancePeriodEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = {"email": "viewer@example.invalid", "role": "viewer"}
        self.access = {
            "subject_type": "physical_account",
            "base_currency": "USD",
            "reporting_currency": "USD",
            "history_start_date": "2025-01-20",
        }
        self.guard = self.enterContext(
            patch.object(web, "_portfolio_access_context", return_value=self.access)
        )
        self.db = self.enterContext(patch.object(web, "_database"))
        self.conn = self.db.return_value.__enter__.return_value
        self.conn.execute.return_value.fetchall.return_value = [
            {"snapshot_date": "2025-01-31", "total_text": "100"},
            {"snapshot_date": "2025-02-28", "total_text": "160"},
            {"snapshot_date": "2025-03-03", "total_text": ""},
        ]
        self.flows = self.enterContext(
            patch("ibkr_etl.provisional_benchmark._external_flows",
                  return_value={"2025-02-28": 50.0, "2025-01-20": 100.0})
        )

    def test_periods_use_nav_and_external_flows(self) -> None:
        result = web.performance_periods_view(
            "U16088571", as_of="2025-03-05", start="2025-02-01", end="2025-02-28", user=self.user
        )
        self.guard.assert_called_once_with("U16088571", self.user)
        statement, parameters = self.conn.execute.call_args.args
        self.assertIn("FROM subject_nav_series", statement)
        self.assertIn("FROM platform_subjects s", statement)
        self.assertEqual(parameters, (web.TENANT_ID, "U16088571", "2025-01-20", "2025-03-05", "2025-03-05"))
        self.assertEqual(self.flows.call_args.kwargs, {"subject_type": "physical_account"})
        self.assertEqual(self.flows.call_args.args[1], "U16088571")
        self.assertAlmostEqual(result["custom"]["twr"], 10.0 / 125.0)
        self.assertEqual(result["currency"], "USD")
        self.assertTrue(result["flows_applicable"])
        self.assertEqual(result["effective_as_of"], "2025-02-28")
        self.assertEqual(_period(result, "mtd")["status"], "empty")

    def test_flows_statement_defaults_to_history_start(self) -> None:
        result = web.external_flows_statement("U16088571", user=self.user)
        self.assertEqual(result["from_date"], "2025-01-20")
        self.assertIsNone(result["to_date"])
        self.assertEqual(result["totals"]["net"], 150.0)
        self.assertTrue(result["applicable"])
        result = web.external_flows_statement(
            "U16088571", from_date="2025-02-01", to_date="2025-02-28", user=self.user
        )
        self.assertEqual([row["date"] for row in result["flows"]], ["2025-02-28"])

    def test_filtered_portfolio_reads_derived_ledger_flows(self) -> None:
        self.access["subject_type"] = "filtered_portfolio"
        web.external_flows_statement("filtered-1", user=self.user)
        self.assertEqual(self.flows.call_args.kwargs, {"subject_type": "filtered_portfolio"})

    def test_composite_has_no_flows(self) -> None:
        self.access["subject_type"] = "composite_portfolio"
        self.access["base_currency"] = "HKD"
        self.access["reporting_currency"] = "HKD"
        statement = web.external_flows_statement("alpha-world-ms", user=self.user)
        self.assertFalse(statement["applicable"])
        self.assertEqual(statement["flows"], [])
        self.assertIn("Composite portfolios", statement["message"])
        periods = web.performance_periods_view("alpha-world-ms", as_of="2025-03-05", user=self.user)
        self.assertFalse(periods["flows_applicable"])
        self.assertAlmostEqual(_period(periods, "since_inception")["twr"], 0.60)
        self.flows.assert_not_called()

    def test_authorization_precedes_database(self) -> None:
        self.guard.side_effect = HTTPException(status_code=404, detail="Portfolio not available")
        for call in (
            lambda: web.performance_periods_view("U1", user=self.user),
            lambda: web.external_flows_statement("U1", user=self.user),
        ):
            with self.assertRaises(HTTPException):
                call()
        self.db.assert_not_called()
        self.flows.assert_not_called()

    def test_invalid_dates_are_rejected(self) -> None:
        for kwargs in ({"as_of": "nope"}, {"start": "2025-03-01", "end": "2025-02-01"}):
            with self.assertRaises(HTTPException) as raised:
                web.performance_periods_view("U16088571", user=self.user, **kwargs)
            self.assertEqual(raised.exception.status_code, 400)
        with self.assertRaises(HTTPException) as raised:
            web.external_flows_statement(
                "U16088571", from_date="2025-03-01", to_date="2025-02-01", user=self.user
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.db.assert_not_called()

    def test_currency_mismatch_fails_closed(self) -> None:
        self.access["reporting_currency"] = "HKD"
        with self.assertRaises(HTTPException) as raised:
            web.external_flows_statement("U16088606", user=self.user)
        self.assertEqual(raised.exception.status_code, 409)
        self.flows.assert_not_called()

    def test_flow_reader_errors_are_conflicts(self) -> None:
        self.flows.side_effect = RuntimeError("Unknown account: U1")
        with self.assertRaises(HTTPException) as raised:
            web.performance_periods_view("U1", user=self.user)
        self.assertEqual(raised.exception.status_code, 409)


class PerformancePageTests(unittest.TestCase):
    def test_page_exposes_periods_custom_picker_and_flows(self) -> None:
        page = web.account_page("U16088571", "performance", None, {"email": "analyst@example.com"})
        for marker in (
            'id="period-returns"',
            'id="period-from"',
            'id="period-apply"',
            'id="flows-statement"',
            "/performance/periods?",
            "/flows?",
            "if(activeSection==='performance')await loadPerformancePeriods()",
            "Benchmark price return (excludes dividends)",
        ):
            self.assertIn(marker, page)


class BenchmarkReturnBasisTests(unittest.TestCase):
    def test_price_indices_are_labelled_price_return(self) -> None:
        self.assertEqual(
            web._benchmark_return_basis("price"),
            {"return_basis": "price_return", "return_basis_label": "Price return (excludes dividends)"},
        )
        self.assertEqual(web._benchmark_return_basis("gross_total_return")["return_basis"], "total_return")
        self.assertEqual(web._benchmark_return_basis(None)["return_basis"], "unknown")

    def test_comparison_response_reports_convention(self) -> None:
        benchmark = {
            "benchmark_id": "GSPC", "symbol": "^GSPC", "name": "S&P 500 Price Index",
            "provider": "Yahoo Finance via yfinance", "license_status": "restricted",
            "return_convention": "price", "mapping_type": "primary", "reporting_currency": "USD",
        }
        records = [
            {"observation_date": f"2025-01-{day:02d}", "portfolio_value": 100 + day,
             "benchmark_level": 50 + day, "normalized_portfolio": 100 + day,
             "normalized_benchmark": 100 + day, "portfolio_return": 0.01,
             "benchmark_return": 0.01, "excess_return": 0.0, "quality_status": "pass"}
            for day in range(2, 6)
        ]
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = benchmark
        connection.execute.return_value.fetchall.return_value = records
        database = MagicMock()
        database.__enter__.return_value = connection
        with patch.object(web, "_portfolio_access_context"), patch.object(web, "_database", return_value=database):
            result = web.benchmark_comparison("U16088571", user={"email": "viewer@example.invalid"})
        self.assertEqual(result["benchmark_return_convention"], "price")
        self.assertEqual(result["benchmark_return_basis"], "price_return")


if __name__ == "__main__":
    unittest.main()
