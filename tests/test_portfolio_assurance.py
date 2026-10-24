from __future__ import annotations

import json
import unittest
from unittest import mock

from ibkr_etl.portfolio_assurance import (
    APPROVED_FACTSHEET_LAYOUT,
    APPROVED_PRESENTATION_LAYOUT,
    AssuranceModelConfig,
    approved_report_versions,
    evaluate_portfolio_assurance,
    generate_assurance_review,
)
from ibkr_etl.monthly_artifacts import factsheet_layout_version
from ibkr_etl.presentation_design import presentation_content_contract_version


def _result(name: str, data: dict, quality: str = "pass") -> dict:
    return {
        "tool_name": name,
        "subject_id": "U16088571",
        "as_of": "2026-09-22",
        "quality_status": quality,
        "data": data,
    }


def _results() -> list[dict]:
    metrics = {
        "total_return": 0.1,
        "annualized_return": 0.08,
        "annualized_volatility": 0.17,
        "risk_free_annual_rate": 0.04,
        "sharpe_ratio": 0.3,
        "expected_maximum_drawdown_1y": -0.2,
        "actual_maximum_drawdown_1y": -0.12,
        "tail_maximum_drawdown_1y": -0.3,
        "crisis_maximum_drawdown": -0.4,
        "var_95_daily": -0.02,
        "daily_expected_tail_loss": -0.03,
        "win_rate": 0.52,
        "blue_mean_daily_return": 0.0003,
    }
    return [
        _result(
            "get_portfolio_summary",
            {
                "latest_nav": {
                    "snapshot_date": "2026-09-22",
                    "currency": "USD",
                    "total_text": "110",
                },
                "positions": 2,
            },
        ),
        _result(
            "get_performance_series",
            {
                "series": [
                    {"date": "2025-01-20", "currency": "USD", "nav": 100.0},
                    {"date": "2026-09-22", "currency": "USD", "nav": 110.0},
                ],
                "observations": 2,
            },
        ),
        _result("get_holdings", {"returned": 2, "holdings": [{}, {}]}),
        _result(
            "compare_with_benchmarks",
            {
                "series": [
                    {"date": "2025-01-20", "portfolio": 100.0, "benchmark": 100.0},
                    {"date": "2026-09-22", "portfolio": 110.0, "benchmark": 108.0},
                ],
                "metrics": {"portfolio_total_return": 0.1},
            },
            quality="warn",
        ),
        _result("get_risk_metrics", {"metrics": metrics}),
        _result(
            "get_data_freshness",
            {
                "portfolio": {
                    "nav_date": "2026-09-22",
                    "position_date": "2026-09-22",
                    "cash_date": "2026-09-22",
                },
                "risk_free_date": "2026-09-22",
            },
        ),
        _result(
            "get_sector_allocation",
            {
                "available": True,
                "classification_coverage_pct": 100.0,
                "allocation": [
                    {"sector": "Technology", "weight": 0.6},
                    {"sector": "Financials", "weight": 0.4},
                ],
            },
            quality="warn",
        ),
    ]


def _report_rows() -> list[dict]:
    return [
        {
            "period_end": "2026-09-30",
            "report_type": "factsheet",
            "template_version": f"sha256:abc+layout:{APPROVED_FACTSHEET_LAYOUT}",
            "output_reference": "internal-reports/review/factsheet.pdf",
            "output_sha256": "a" * 64,
        },
        {
            "period_end": "2026-09-30",
            "report_type": "presentation",
            "template_version": f"sha256:def+layout:{APPROVED_PRESENTATION_LAYOUT}",
            "output_reference": "internal-reports/review/presentation.pptx",
            "output_sha256": "b" * 64,
        },
    ]


class _Response:
    status = "completed"
    usage = None

    def __init__(self, payload: dict) -> None:
        self.output_text = json.dumps(payload)


class _Client:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.responses = self
        self.last_arguments = None

    def create(self, **kwargs):
        self.last_arguments = kwargs
        return _Response(self.payload)


class PortfolioAssuranceTests(unittest.TestCase):
    def test_ms_rejects_legacy_content_contract_and_accepts_subject_contract(self) -> None:
        results = _results()
        for row in results:
            row['subject_id'] = 'alpha-world-ms'
        reports = _report_rows()
        reports[0]['template_version'] = f"sha256:abc+layout:{factsheet_layout_version('alpha-world-ms')}"
        legacy = evaluate_portfolio_assurance(results, reports, subject_id='alpha-world-ms')
        check = 'reports.2026-09-30.presentation.layout'
        self.assertIn(check, {row['check_id'] for row in legacy['findings']})
        reports[1]['template_version'] = f"sha256:def+layout:{presentation_content_contract_version('alpha-world-ms')}"
        qualified = evaluate_portfolio_assurance(results, reports, subject_id='alpha-world-ms')
        self.assertNotIn(check, {row['check_id'] for row in qualified['findings']})
        self.assertEqual(qualified['approved_report_layouts']['presentation'],
                         presentation_content_contract_version('alpha-world-ms'))

    def test_a_month_with_its_factsheet_and_no_deck_passes(self) -> None:
        result = evaluate_portfolio_assurance(_results(), _report_rows()[:1])
        self.assertEqual(result["status"], "pass")
        missing = evaluate_portfolio_assurance(_results(), _report_rows()[1:])
        self.assertIn("reports.2026-09-30.pair", {row["check_id"] for row in missing["findings"]})

    def test_validated_data_metrics_and_august_layouts_pass(self) -> None:
        result = evaluate_portfolio_assurance(_results(), _report_rows())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["checks_failed"], 0)
        self.assertEqual(
            result["approved_report_layouts"]["factsheet"],
            APPROVED_FACTSHEET_LAYOUT,
        )

    def test_nav_metric_tampering_fails_closed(self) -> None:
        results = _results()
        results[4]["data"]["metrics"]["total_return"] = 0.5
        result = evaluate_portfolio_assurance(results, _report_rows())
        self.assertEqual(result["status"], "fail")
        self.assertIn("metrics.total_return", {row["check_id"] for row in result["findings"]})

    def test_future_report_layout_drift_fails_closed(self) -> None:
        rows = _report_rows()
        rows[0]["template_version"] = "sha256:abc+layout:unapproved"
        result = evaluate_portfolio_assurance(_results(), rows)
        self.assertEqual(result["status"], "fail")
        self.assertIn("reports.2026-09-30.factsheet.layout", {row["check_id"] for row in result["findings"]})

    def test_registry_subject_uses_its_subject_specific_layout(self) -> None:
        subject_id = "U16088606"
        results = _results()
        for result in results:
            result["subject_id"] = subject_id
        rows = _report_rows()
        rows[0]["template_version"] = (
            f"sha256:abc+layout:{factsheet_layout_version(subject_id)}"
        )
        result = evaluate_portfolio_assurance(
            results, rows, subject_id=subject_id
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["subject_id"], subject_id)
        self.assertEqual(
            result["approved_report_layouts"]["factsheet"],
            factsheet_layout_version(subject_id),
        )

    def test_designed_subject_factsheet_matches_its_approved_design(self) -> None:
        subject_id = "KIWOOM-TEST"
        results = _results()
        for row in results:
            row["subject_id"] = subject_id
        rows = _report_rows()[:1]
        rows[0]["template_version"] = "spec:" + "c" * 64
        approved = {"factsheet": "spec:" + "c" * 64}
        result = evaluate_portfolio_assurance(results, rows, subject_id=subject_id, approved_versions=approved)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["approved_report_layouts"]["factsheet"], approved["factsheet"])
        # Without its design the built-in layout check still applies and fails.
        unknown = evaluate_portfolio_assurance(results, rows, subject_id=subject_id)
        self.assertIn("reports.2026-09-30.factsheet.layout", {row["check_id"] for row in unknown["findings"]})
        rows[0]["template_version"] = "spec:" + "d" * 64
        drifted = evaluate_portfolio_assurance(results, rows, subject_id=subject_id, approved_versions=approved)
        self.assertEqual(drifted["status"], "fail")
        self.assertIn("reports.2026-09-30.factsheet.layout", {row["check_id"] for row in drifted["findings"]})

    def test_approved_report_versions_reads_the_live_case_design(self) -> None:
        from ibkr_etl.designed_reports import DesignedReportError

        cases = [
            {"case_id": "c1", "target_subject_id": "KIWOOM-A", "report_style": None},
            {"case_id": "c2", "target_subject_id": "STYLED", "report_style": "composite"},
        ]
        with mock.patch("ibkr_etl.onboarding_month_end.live_cases", return_value=cases), mock.patch(
                "ibkr_etl.designed_reports.approved_design", return_value={"spec_sha256": "e" * 64}) as design:
            self.assertEqual(approved_report_versions(object(), "t", "KIWOOM-A"), {"factsheet": "spec:" + "e" * 64})
            design.assert_called_once()
            self.assertIsNone(approved_report_versions(object(), "t", "STYLED"))
            self.assertIsNone(approved_report_versions(object(), "t", "U16088571"))
            design.side_effect = DesignedReportError("missing")
            self.assertIsNone(approved_report_versions(object(), "t", "KIWOOM-A"))

    def test_registry_subject_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            evaluate_portfolio_assurance(
                _results(), _report_rows(), subject_id="U16088606"
            )

    def test_model_is_qualitative_and_cannot_change_deterministic_status(self) -> None:
        deterministic = evaluate_portfolio_assurance(_results(), _report_rows())
        client = _Client(
            {
                "summary": "All deterministic reconciliation gates passed.",
                "observations": ["The approved report layouts remain pinned."],
            }
        )
        config = AssuranceModelConfig(
            "https://example.openai.azure.com",
            "gpt-5-6-sol-apac",
            "southeastasia",
            "apac-data-zone",
            "identity",
        )
        reviewed = generate_assurance_review(
            deterministic, config, client_factory=lambda _: client
        )
        self.assertEqual(reviewed["review"]["summary"], "All deterministic reconciliation gates passed.")
        self.assertNotIn("tools", client.last_arguments)
        self.assertFalse(client.last_arguments["store"])

    def test_model_numeric_claim_is_rejected(self) -> None:
        deterministic = evaluate_portfolio_assurance(_results(), _report_rows())
        client = _Client(
            {"summary": "All 12 checks passed.", "observations": []}
        )
        config = AssuranceModelConfig(
            "https://example.openai.azure.com",
            "gpt-5-6-sol-apac",
            "southeastasia",
            "apac-data-zone",
            "identity",
        )
        with self.assertRaisesRegex(RuntimeError, "numerical claim"):
            generate_assurance_review(
                deterministic, config, client_factory=lambda _: client
            )


if __name__ == "__main__":
    unittest.main()
