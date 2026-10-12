from __future__ import annotations

import copy
import unittest

from ibkr_etl.reporting_snapshot import (
    ReportingSnapshotError,
    active_month_end_snapshot_id,
    assemble_month_end_payload,
)
from ibkr_etl.util import canonical_json, sha256_text


def _result(data, *, quality="pass", as_of="2026-08-31"):
    return {
        "tool_name": "test",
        "subject_id": "U16088571",
        "as_of": as_of,
        "quality_status": quality,
        "data": data,
        "citations": [{"table": "test"}],
        "generated_at": "2026-09-23T00:00:00+00:00",
    }


def _inputs():
    primary = {
        "benchmark": {
            "benchmark_id": "GSPC",
            "provider": "Yahoo Finance via yfinance",
            "return_convention": "price",
            "license_status": "restricted",
        },
        "publication_allowed": False,
        "metrics": {"portfolio_total_return": 0.2},
        "series": [
            {"date": "2025-01-21", "portfolio": 100.0, "benchmark": 100.0},
            {"date": "2025-01-31", "portfolio": 101.0, "benchmark": 102.0},
            {"date": "2025-02-28", "portfolio": 103.0, "benchmark": 104.0},
            {"date": "2026-08-31", "portfolio": 120.0, "benchmark": 115.0},
        ],
    }
    secondary = copy.deepcopy(primary)
    secondary["benchmark"]["benchmark_id"] = "IXIC"
    return {
        "summary": _result({"latest_nav": {"total_text": "120000"}}),
        "performance": _result({"series": [], "observations": 0}),
        "holdings": _result({"holdings": [], "returned": 0}),
        "primary_benchmark": _result(primary, quality="warn"),
        "secondary_benchmark": _result(secondary, quality="warn"),
        "risk": _result(
            {
                "metrics": {
                    "sharpe_ratio": 0.5,
                    "crisis_maximum_drawdown": -0.35,
                },
                "crisis_replay": {
                    "holdings_as_of": "2026-08-31",
                    "maximum_drawdown": -0.35,
                    "calculation_version": "as-of-holdings-crisis-replay-v2",
                },
            }
        ),
        "freshness": _result({"portfolio": {"nav_date": "2026-08-31"}}),
        "sector_allocation": _result(
            {"available": False, "allocation": []}, quality="warn"
        ),
    }


class _SnapshotRows:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = ()

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return self

    def fetchall(self):
        return self.rows


class ReportingSnapshotTests(unittest.TestCase):
    def test_renderer_reuses_the_single_active_month_end_snapshot(self) -> None:
        connection = _SnapshotRows([{"snapshot_id": "approved-snapshot"}])
        snapshot_id = active_month_end_snapshot_id(
            connection,
            tenant_id="tenant",
            subject_id="U16088571",
            period_end="2026-07-31",
        )
        self.assertEqual(snapshot_id, "approved-snapshot")
        self.assertIn("status IN ('frozen', 'approved')", connection.sql)
        self.assertEqual(connection.params, ("tenant", "U16088571", "2026-07-31"))

    def test_renderer_fails_closed_if_multiple_snapshots_are_active(self) -> None:
        connection = _SnapshotRows(
            [{"snapshot_id": "first"}, {"snapshot_id": "second"}]
        )
        with self.assertRaisesRegex(ReportingSnapshotError, "More than one active"):
            active_month_end_snapshot_id(
                connection,
                tenant_id="tenant",
                subject_id="U16088571",
                period_end="2026-07-31",
            )

    def test_provisional_inputs_freeze_but_block_publication(self) -> None:
        payload, quality, allowed, blockers = assemble_month_end_payload(
            subject_id="U16088571",
            period_start="2025-01-20",
            period_end="2026-08-31",
            reporting_currency="USD",
            tool_results=_inputs(),
        )
        self.assertEqual(quality, "warn")
        self.assertFalse(allowed)
        self.assertEqual(payload["benchmark"]["benchmark_id"], "GSPC")
        self.assertEqual(payload["secondary_benchmark"]["benchmark_id"], "IXIC")
        self.assertIn("primary_benchmark:not_licensed_for_publication", blockers)
        self.assertIn("sector_allocation:validated_classification_unavailable", blockers)
        self.assertEqual(payload["month_end_performance"][-1]["date"], "2026-08-31")

    def test_complete_provisional_sector_data_remains_nonpublishing(self) -> None:
        inputs = _inputs()
        inputs["sector_allocation"] = _result(
            {
                "available": True,
                "allocation": [{"sector": "Technology", "weight": 1.0}],
                "publication_allowed": False,
                "license_status": "restricted",
            },
            quality="warn",
        )
        payload, quality, allowed, blockers = assemble_month_end_payload(
            subject_id="U16088571",
            period_start="2025-01-20",
            period_end="2026-08-31",
            reporting_currency="USD",
            tool_results=inputs,
        )
        self.assertEqual(quality, "warn")
        self.assertFalse(allowed)
        self.assertIn("sector_allocation:not_licensed_for_publication", blockers)
        self.assertNotIn("sector_allocation:validated_classification_unavailable", blockers)
        self.assertTrue(payload["sector_allocation"]["available"])

    def test_additional_configured_benchmark_is_preserved_in_snapshot(self) -> None:
        inputs = _inputs()
        additional = copy.deepcopy(inputs["secondary_benchmark"])
        additional["data"]["benchmark"]["benchmark_id"] = "SHCOMP"
        inputs["additional_benchmark_1"] = additional
        payload, _quality, allowed, blockers = assemble_month_end_payload(
            subject_id="U16088606",
            period_start="2025-01-20",
            period_end="2026-08-31",
            reporting_currency="USD",
            tool_results=inputs,
        )
        self.assertFalse(allowed)
        self.assertEqual(
            [row["benchmark"]["benchmark_id"] for row in payload["benchmark_comparisons"]],
            ["GSPC", "IXIC", "SHCOMP"],
        )
        self.assertIn("secondary_2_benchmark:not_licensed_for_publication", blockers)

    def test_snapshot_payload_is_stable_across_generation_times(self) -> None:
        first = _inputs()
        second = copy.deepcopy(first)
        for result in second.values():
            result["generated_at"] = "2030-01-01T00:00:00+00:00"
        second["risk"]["data"]["crisis_replay"]["generated_at"] = (
            "2030-01-01T00:00:01+00:00"
        )
        payload_a = assemble_month_end_payload(
            subject_id="U16088571",
            period_start="2025-01-20",
            period_end="2026-08-31",
            reporting_currency="USD",
            tool_results=first,
        )[0]
        payload_b = assemble_month_end_payload(
            subject_id="U16088571",
            period_start="2025-01-20",
            period_end="2026-08-31",
            reporting_currency="USD",
            tool_results=second,
        )[0]
        self.assertEqual(sha256_text(canonical_json(payload_a)), sha256_text(canonical_json(payload_b)))

    def test_non_month_end_is_rejected(self) -> None:
        with self.assertRaises(ReportingSnapshotError):
            assemble_month_end_payload(
                subject_id="U16088571",
                period_start="2025-01-20",
                period_end="2026-08-30",
                reporting_currency="USD",
                tool_results=_inputs(),
            )

    def test_stale_month_end_data_is_rejected(self) -> None:
        inputs = _inputs()
        inputs["summary"]["as_of"] = "2026-08-20"
        with self.assertRaises(ReportingSnapshotError):
            assemble_month_end_payload(
                subject_id="U16088571",
                period_start="2025-01-20",
                period_end="2026-08-31",
                reporting_currency="USD",
                tool_results=inputs,
            )

    def test_missing_crisis_replay_blocks_publication(self) -> None:
        inputs = _inputs()
        inputs["risk"]["data"].pop("crisis_replay")
        _, quality, allowed, blockers = assemble_month_end_payload(
            subject_id="U16088571",
            period_start="2025-01-20",
            period_end="2026-08-31",
            reporting_currency="USD",
            tool_results=inputs,
        )
        self.assertEqual(quality, "warn")
        self.assertFalse(allowed)
        self.assertIn("risk:crisis_replay_unavailable", blockers)

    def test_future_crisis_holdings_are_rejected(self) -> None:
        inputs = _inputs()
        inputs["risk"]["data"]["crisis_replay"]["holdings_as_of"] = "2026-09-18"
        with self.assertRaisesRegex(ReportingSnapshotError, "after the portfolio"):
            assemble_month_end_payload(
                subject_id="U16088571",
                period_start="2025-01-20",
                period_end="2026-08-31",
                reporting_currency="USD",
                tool_results=inputs,
            )


if __name__ == "__main__":
    unittest.main()
