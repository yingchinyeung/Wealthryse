from __future__ import annotations

import math
import unittest
from datetime import date, timedelta

from ibkr_etl.benchmark_analytics import (
    BenchmarkContract,
    ComparisonRow,
    ComparisonValidationError,
    build_comparison,
    expected_tail_loss_contributors,
    portfolio_risk_metrics,
)


APPROVED_XNDX = BenchmarkContract(
    benchmark_id="XNDX",
    currency="USD",
    return_convention="gross_total_return",
    observation_status="official_close",
    license_status="approved",
)


class BenchmarkAnalyticsTests(unittest.TestCase):
    def calendar_comparison(self, benchmark_id, expected, latest="2026-09-30"):
        first = "2026-09-28" if latest == "2026-09-29" else "2026-09-29"
        levels = {first:100, latest:101}
        return build_comparison(
            subject_id="calendar-test", reporting_currency="USD", portfolio_currency="USD",
            portfolio_values=levels, cash_flows=None,
            benchmark=BenchmarkContract(benchmark_id,"USD","gross_total_return","official_close","approved"),
            benchmark_levels=levels, start_date=first, expected_as_of=expected,
            minimum_return_observations=1,
        )

    def test_shanghai_holiday_does_not_invent_prices_or_change_returns(self):
        for cutoff in ("2026-10-01", "2026-10-05", "2026-10-07"):
            result = self.calendar_comparison("SHCOMP", cutoff)
            self.assertNotIn("freshness", {i.check_id for i in result.issues})
            self.assertEqual([r.observation_date for r in result.rows], ["2026-09-29","2026-09-30"])
            self.assertAlmostEqual(result.rows[-1].portfolio_return, 0.01)

    def test_reopening_and_missing_last_session_fail_closed(self):
        for benchmark, cutoff, latest in (("SHCOMP","2026-10-08","2026-09-30"),
                ("SHCOMP","2026-10-05","2026-09-29"),
                ("HSI","2026-10-05","2026-09-30"),
                ("GSPC","2026-10-05","2026-09-30"),
                ("SHCOMP","2026-10-05","2026-10-01"),
                ("SHCOMP","2027-01-05","2026-09-30")):
            with self.subTest(benchmark=benchmark, cutoff=cutoff, latest=latest):
                result = self.calendar_comparison(benchmark, cutoff, latest)
                self.assertEqual(result.quality_status, "fail")
                self.assertIn("freshness", {i.check_id for i in result.issues})

    def test_portfolio_risk_metrics_fail_closed_without_risk_free_rate(self) -> None:
        rows = [
            ComparisonRow("2025-01-20", 100, 100, 100, 100, None, None, None),
            ComparisonRow("2025-01-21", 101, 101, 101, 101, 0.01, 0.01, 0.0),
            ComparisonRow("2025-01-22", 99.99, 100, 99.99, 100, -0.01, -0.0099, -0.0001),
            ComparisonRow("2025-01-23", 101.9898, 101, 101.9898, 101, 0.02, 0.01, 0.01),
        ]
        metrics = portfolio_risk_metrics(rows)
        self.assertEqual(metrics["return_observations"], 3)
        self.assertAlmostEqual(metrics["total_return"], 0.019898, places=6)
        self.assertIsNone(metrics["risk_free_annual_rate"])
        self.assertIsNone(metrics["sharpe_ratio"])
        self.assertGreater(metrics["annualized_volatility"], 0)
        self.assertGreater(metrics["maximum_drawdown"], -0.011)
        self.assertLess(metrics["maximum_drawdown"], -0.009)

    def test_portfolio_risk_metrics_calculates_sharpe_with_validated_rate(self) -> None:
        rows = [
            ComparisonRow("2025-01-20", 100, 100, 100, 100, None, None, None),
            ComparisonRow("2025-01-21", 101, 101, 101, 101, 0.01, 0.01, 0.0),
            ComparisonRow("2025-01-22", 101.505, 101, 101.505, 101, 0.005, 0.0, 0.005),
            ComparisonRow("2025-01-23", 102.52005, 102, 102.52005, 102, 0.01, 0.01, 0.0),
        ]
        metrics = portfolio_risk_metrics(rows, risk_free_annual_rate=0.0429)
        self.assertEqual(metrics["risk_free_annual_rate"], 0.0429)
        self.assertIsNotNone(metrics["sharpe_ratio"])
        self.assertTrue(math.isfinite(float(metrics["sharpe_ratio"])))
        self.assertEqual(metrics["daily_expected_tail_loss"], metrics["cvar_95_daily"])
        self.assertIsNone(metrics["expected_maximum_drawdown_1y"])
        self.assertIsNone(metrics["tail_maximum_drawdown_1y"])
        self.assertIsNone(metrics["crisis_maximum_drawdown"])

    def test_expected_tail_loss_contributors_reconcile_unchanged_positions(self) -> None:
        rows = [
            ComparisonRow("2025-01-20", 100, 100, 100, 100, None, None, None),
            ComparisonRow("2025-01-21", 105, 101, 105, 101, 0.05, 0.01, 0.04),
            ComparisonRow("2025-01-22", 99.75, 100, 99.75, 100, -0.05, -0.0099, -0.0401),
            ComparisonRow("2025-01-23", 101.745, 101, 101.745, 101, 0.02, 0.01, 0.01),
        ]
        positions = [
            {"snapshot_date": "2025-01-21", "conid": "A", "currency": "USD", "quantity_text": "1", "market_value_text": "60", "symbol": "A", "description": "Alpha"},
            {"snapshot_date": "2025-01-21", "conid": "B", "currency": "USD", "quantity_text": "1", "market_value_text": "45", "symbol": "B", "description": "Beta"},
            {"snapshot_date": "2025-01-22", "conid": "A", "currency": "USD", "quantity_text": "1", "market_value_text": "56", "symbol": "A", "description": "Alpha"},
            {"snapshot_date": "2025-01-22", "conid": "B", "currency": "USD", "quantity_text": "1", "market_value_text": "43.75", "symbol": "B", "description": "Beta"},
        ]
        result = expected_tail_loss_contributors(rows, positions)
        self.assertEqual(result["tail_observations"], 1)
        self.assertAlmostEqual(result["daily_expected_tail_loss"], 0.05)
        self.assertEqual(result["top"][0]["symbol"], "A")
        self.assertAlmostEqual(
            result["top"][0]["expected_tail_loss_contribution"], 4 / 105
        )
        self.assertAlmostEqual(result["residual_contribution"], 0.0)

    def test_actual_one_year_drawdown_uses_only_last_252_returns(self) -> None:
        rows = [
            ComparisonRow("2024-01-01", 100, 100, 100, 100, None, None, None)
        ]
        start = date(2024, 1, 2)
        for index in range(260):
            daily_return = -0.5 if index == 0 else 0.001
            rows.append(
                ComparisonRow(
                    (start + timedelta(days=index)).isoformat(),
                    100,
                    100,
                    100,
                    100,
                    daily_return,
                    0.0,
                    daily_return,
                )
            )
        metrics = portfolio_risk_metrics(rows, 0.04)
        self.assertGreater(float(metrics["actual_maximum_drawdown_1y"]), -0.01)
        self.assertIsNotNone(metrics["expected_maximum_drawdown_1y"])
        self.assertIsNotNone(metrics["tail_maximum_drawdown_1y"])
        self.assertLessEqual(
            float(metrics["tail_maximum_drawdown_1y"]),
            float(metrics["expected_maximum_drawdown_1y"]),
        )

    def test_common_date_alignment_normalization_and_cash_flow_adjustment(self) -> None:
        result = build_comparison(
            subject_id="U16088571",
            reporting_currency="USD",
            portfolio_currency="USD",
            portfolio_values={
                "2025-01-20": 100.0,
                "2025-01-21": 111.0,
                "2025-01-22": 112.0,
                "2025-01-23": 114.24,
            },
            cash_flows={"2025-01-21": 10.0},
            benchmark=APPROVED_XNDX,
            benchmark_levels={
                "2025-01-20": 200.0,
                "2025-01-21": 202.0,
                "2025-01-23": 206.04,
            },
            start_date="2025-01-20",
            expected_as_of="2025-01-23",
            minimum_return_observations=2,
        )
        self.assertEqual(result.quality_status, "pass")
        self.assertEqual([row.observation_date for row in result.rows], [
            "2025-01-20", "2025-01-21", "2025-01-23"
        ])
        self.assertAlmostEqual(result.rows[1].portfolio_return or 0, 1 / 105)
        self.assertAlmostEqual(result.rows[1].benchmark_return or 0, 0.01)
        self.assertAlmostEqual(result.rows[-1].normalized_benchmark, 103.02)
        self.assertEqual(result.metrics["return_observations"], 2)
        self.assertTrue(math.isfinite(float(result.metrics["beta"])))

    def test_unapproved_provider_fails_closed(self) -> None:
        pending = BenchmarkContract(
            benchmark_id="XNDX",
            currency="USD",
            return_convention="gross_total_return",
            observation_status="official_close",
            license_status="pending",
        )
        result = build_comparison(
            subject_id="U16088571",
            reporting_currency="USD",
            portfolio_currency="USD",
            portfolio_values={"2025-01-20": 100, "2025-01-21": 101},
            cash_flows=None,
            benchmark=pending,
            benchmark_levels={"2025-01-20": 100, "2025-01-21": 101},
            start_date="2025-01-20",
            minimum_return_observations=1,
        )
        self.assertEqual(result.quality_status, "fail")
        self.assertIn("benchmark_license", {issue.check_id for issue in result.issues})

    def test_restricted_provisional_feed_is_warn_only_in_development(self) -> None:
        provisional = BenchmarkContract(
            benchmark_id="XNDX",
            currency="USD",
            return_convention="gross_total_return",
            observation_status="provisional_close",
            license_status="restricted",
        )
        result = build_comparison(
            subject_id="U16088571",
            reporting_currency="USD",
            portfolio_currency="USD",
            portfolio_values={"2025-01-20": 100, "2025-01-21": 101},
            cash_flows=None,
            benchmark=provisional,
            benchmark_levels={"2025-01-20": 100, "2025-01-21": 100.5},
            start_date="2025-01-20",
            minimum_return_observations=1,
            development_mode=True,
        )
        self.assertEqual(result.quality_status, "warn")
        statuses = {issue.check_id: issue.status for issue in result.issues}
        self.assertEqual(statuses["benchmark_license"], "warn")
        self.assertEqual(statuses["benchmark_observation_status"], "warn")

    def test_price_index_is_accepted_only_when_explicitly_required(self) -> None:
        price_index = BenchmarkContract(
            benchmark_id="GSPC",
            currency="USD",
            return_convention="price",
            observation_status="provisional_close",
            license_status="restricted",
        )
        result = build_comparison(
            subject_id="U16088571",
            reporting_currency="USD",
            portfolio_currency="USD",
            portfolio_values={"2025-01-20": 100, "2025-01-21": 101},
            cash_flows={},
            benchmark=price_index,
            benchmark_levels={"2025-01-20": 100, "2025-01-21": 100.5},
            start_date="2025-01-20",
            minimum_return_observations=1,
            development_mode=True,
            required_return_convention="price",
        )
        self.assertEqual(result.quality_status, "warn")
        self.assertNotIn(
            "benchmark_convention", {issue.check_id for issue in result.issues}
        )

    def test_wrong_currency_convention_intraday_and_stale_data_fail(self) -> None:
        invalid = BenchmarkContract(
            benchmark_id="NDX",
            currency="HKD",
            return_convention="price",
            observation_status="intraday",
            license_status="approved",
        )
        result = build_comparison(
            subject_id="U16088571",
            reporting_currency="USD",
            portfolio_currency="HKD",
            portfolio_values={"2025-01-21": 100, "2025-01-22": 101},
            cash_flows=None,
            benchmark=invalid,
            benchmark_levels={"2025-01-21": 100, "2025-01-22": 101},
            start_date="2025-01-20",
            expected_as_of="2025-02-01",
            maximum_staleness_days=4,
            minimum_return_observations=1,
        )
        checks = {issue.check_id for issue in result.issues}
        self.assertEqual(result.quality_status, "fail")
        self.assertTrue({
            "portfolio_currency", "benchmark_currency", "benchmark_convention",
            "benchmark_observation_status", "freshness", "normalization_date"
        }.issubset(checks))

    def test_reasonableness_and_minimum_sample_gates_fail(self) -> None:
        result = build_comparison(
            subject_id="U16088571",
            reporting_currency="USD",
            portfolio_currency="USD",
            portfolio_values={"2025-01-20": 100, "2025-01-21": 250},
            cash_flows=None,
            benchmark=APPROVED_XNDX,
            benchmark_levels={"2025-01-20": 100, "2025-01-21": 101},
            start_date="2025-01-20",
            minimum_return_observations=30,
        )
        checks = {issue.check_id for issue in result.issues}
        self.assertEqual(result.quality_status, "fail")
        self.assertIn("portfolio_return_reasonableness", checks)
        self.assertIn("sample_size", checks)

    def test_duplicate_and_nonpositive_inputs_are_rejected(self) -> None:
        with self.assertRaises(ComparisonValidationError):
            build_comparison(
                subject_id="U16088571",
                reporting_currency="USD",
                portfolio_currency="USD",
                portfolio_values=[("2025-01-20", 100), ("2025-01-20", 101)],
                cash_flows=None,
                benchmark=APPROVED_XNDX,
                benchmark_levels={"2025-01-20": 100, "2025-01-21": 101},
                start_date="2025-01-20",
            )
        with self.assertRaises(ComparisonValidationError):
            build_comparison(
                subject_id="U16088571",
                reporting_currency="USD",
                portfolio_currency="USD",
                portfolio_values={"2025-01-20": 0},
                cash_flows=None,
                benchmark=APPROVED_XNDX,
                benchmark_levels={"2025-01-20": 100},
                start_date="2025-01-20",
            )


if __name__ == "__main__":
    unittest.main()
