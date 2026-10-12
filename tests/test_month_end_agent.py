from __future__ import annotations

import json
import unittest

from ibkr_etl.month_end_agent import (
    MonthEndAgentConfig,
    MonthEndAgentError,
    generate_month_end_commentary,
    prepare_month_end_context,
)


def _snapshot() -> dict:
    return {
        "subject_id": "U16088571",
        "period_end": "2026-08-31",
        "data_as_of": "2026-08-31",
        "reporting_currency": "USD",
        "benchmark": {"name": "S&P 500 Price Index"},
        "secondary_benchmark": {"name": "NASDAQ Composite"},
        "performance_comparison": {
            "metrics": {
                "portfolio_total_return": 0.12,
                "benchmark_total_return": 0.09,
                "excess_total_return": 0.03,
                "portfolio_annualized_volatility": 0.2,
                "benchmark_annualized_volatility": 0.15,
            }
        },
        "secondary_performance_comparison": {"metrics": {}},
        "risk": {
            "metrics": {
                "annualized_return": 0.1,
                "annualized_volatility": 0.2,
                "sharpe_ratio": 0.4,
                "daily_expected_tail_loss": -0.03,
            },
            "crisis_replay": {"crisis_maximum_drawdown": -0.35},
        },
        "holdings": {
            "holdings": [
                {
                    "symbol": "TEST",
                    "description": "PERSONAL NAME MUST NOT LEAVE",
                    "market_value_text": "100",
                    "currency": "USD",
                }
            ]
        },
        "sector_allocation": {
            "sectors": [{"sector": "Technology", "weight": 1.0}]
        },
        "month_end_performance": [{"date": "2026-08-31", "portfolio": 112.0}],
    }


class _Usage:
    def model_dump(self):
        return {"input_tokens": 10, "output_tokens": 20}


class _Response:
    status = "completed"
    usage = _Usage()

    def __init__(self, payload: dict):
        self.output_text = json.dumps(payload)


class _Client:
    def __init__(self, payload: dict):
        self.last_arguments = None
        self.responses = self
        self.payload = payload

    def create(self, **kwargs):
        self.last_arguments = kwargs
        return _Response(self.payload)


class MonthEndAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = MonthEndAgentConfig(
            endpoint="https://example.openai.azure.com",
            deployment="gpt-5-6-sol-apac",
            region="southeastasia",
            processing_scope="apac-data-zone",
            managed_identity_client_id="managed-identity",
        )

    def test_context_excludes_personal_and_raw_fields(self) -> None:
        context = prepare_month_end_context(_snapshot())
        encoded = json.dumps(context)
        self.assertNotIn("PERSONAL NAME", encoded)
        self.assertNotIn("description", encoded)
        self.assertEqual(context["subject_id"], "U16088571")
        self.assertIn("personal_information", context["data_policy"]["excluded"])

    def test_nonpilot_context_uses_its_own_identity_and_excludes_private_fields(self) -> None:
        snapshot = _snapshot()
        snapshot["subject_id"] = "U16088606"
        snapshot["raw_ibkr_file"] = "RAW DATA MUST NOT LEAVE"
        snapshot["flex_token"] = "CREDENTIAL MUST NOT LEAVE"
        context = prepare_month_end_context(snapshot)
        self.assertEqual(context["subject_id"], "U16088606")
        encoded = json.dumps(context)
        self.assertNotIn("PERSONAL NAME", encoded)
        self.assertNotIn("RAW DATA MUST NOT LEAVE", encoded)
        self.assertNotIn("CREDENTIAL MUST NOT LEAVE", encoded)

    def test_nonpilot_uses_foundry_with_approved_region_and_store_disabled(self) -> None:
        snapshot = _snapshot()
        snapshot["subject_id"] = "hong-kong-china"
        client = _Client(
            {
                "executive_summary": "The portfolio record is complete for the reporting period.",
                "performance_observations": [
                    "The primary benchmark comparison is available.",
                    "Recent monthly results varied.",
                ],
                "risk_observations": [
                    "The risk metrics are based on validated data.",
                    "The crisis replay shows downside sensitivity.",
                ],
            }
        )
        result = generate_month_end_commentary(
            snapshot, self.config, client_factory=lambda _config: client
        )
        self.assertEqual(result["model_deployment"], "gpt-5-6-sol-apac")
        self.assertEqual(result["processing_scope"], "apac-data-zone")
        self.assertFalse(client.last_arguments["store"])
        self.assertEqual(
            json.loads(client.last_arguments["input"])["subject_id"], "hong-kong-china"
        )

    def test_grounded_commentary_uses_strict_structured_output(self) -> None:
        client = _Client(
            {
                "executive_summary": "The portfolio outperformed its primary benchmark while carrying higher volatility.",
                "performance_observations": [
                    "Relative performance was positive over the common history.",
                    "Recent monthly results remained mixed.",
                ],
                "risk_observations": [
                    "Concentration remains an important source of portfolio risk.",
                    "The crisis replay indicates meaningful downside sensitivity.",
                ],
            }
        )
        result = generate_month_end_commentary(
            _snapshot(), self.config, client_factory=lambda _config: client
        )
        self.assertEqual(result["model_deployment"], "gpt-5-6-sol-apac")
        self.assertEqual(
            client.last_arguments["text"]["format"]["type"], "json_schema"
        )
        self.assertFalse(client.last_arguments["store"])
        self.assertEqual(client.last_arguments["reasoning"], {"effort": "low"})

    def test_numeric_model_claim_fails_closed(self) -> None:
        client = _Client(
            {
                "executive_summary": "The portfolio returned 12 percent.",
                "performance_observations": ["Relative returns were positive.", "Results were mixed."],
                "risk_observations": ["Risk remains elevated.", "Drawdown risk is material."],
            }
        )
        with self.assertRaisesRegex(MonthEndAgentError, "numerical claim"):
            generate_month_end_commentary(
                _snapshot(), self.config, client_factory=lambda _config: client
            )

    def test_invalid_subject_is_rejected(self) -> None:
        snapshot = _snapshot()
        snapshot["subject_id"] = "../OTHER"
        with self.assertRaisesRegex(MonthEndAgentError, "identity is invalid"):
            prepare_month_end_context(snapshot)


if __name__ == "__main__":
    unittest.main()
