from __future__ import annotations

import copy
import json
import unittest
from datetime import date

from ibkr_etl.onboarding_preview import (
    build_physical_preview_validation,
    preview_evidence_sha256,
)


def ready_payload() -> dict:
    return {
        "subject": {
            "subject_id": "U16088606",
            "subject_type": "physical_account",
            "display_name": "U16088606-Greater China",
            "reporting_currency": "USD",
            "history_start_date": "2025-01-20",
        },
        "nav": {
            "first_date": "2025-01-20",
            "latest_date": "2026-09-24",
            "observations": 439,
        },
        "positions": {"latest_date": "2026-09-24", "rows": 13785},
        "benchmarks": [
            {
                "benchmark_id": benchmark_id,
                "mapping_type": "primary" if index == 0 else "secondary",
                "latest_comparison_date": "2026-09-24",
                "comparison_rows": 410,
                "reporting_currency": "USD",
            }
            for index, benchmark_id in enumerate(("taiex", "hsi", "shcomp"))
        ],
        "templates": [
            {
                "report_type": report_type,
                "asset_role": asset_role,
                "version_label": "owner-supplied-v1",
                "approval_status": "validated",
                "sha256": character * 64,
                "byte_count": 100,
            }
            for report_type, asset_role, character in (
                ("factsheet", "template", "a"),
                ("presentation", "template", "b"),
                ("factsheet", "completed_example", "c"),
            )
        ],
        "checks": [
            {"check_id": check_id, "status": "pass"}
            for check_id in (
                "source_account_discovered",
                "source_history_available",
                "data_import_reconciled",
                "financial_contract_approved",
                "benchmark_fx_reconciled",
                "templates_registered",
                "external_market_data_approved",
                "risk_analytics_validated",
            )
        ]
        + [
            {"check_id": "website_preview_validated", "status": "pending"},
            {"check_id": "owner_activation", "status": "pending"},
        ],
    }


class OnboardingPreviewTests(unittest.TestCase):
    def test_ready_physical_preview_is_integrity_bound(self) -> None:
        evidence = build_physical_preview_validation(**ready_payload())
        self.assertTrue(evidence["ready_for_owner_preview_approval"])
        self.assertEqual(len(evidence["evidence_sha256"]), 64)
        without_hash = dict(evidence)
        digest = without_hash.pop("evidence_sha256")
        self.assertEqual(digest, preview_evidence_sha256(without_hash))
        self.assertTrue(all(row["status"] == "pass" for row in evidence["results"]))
        self.assertEqual(json.loads(json.dumps(evidence)), evidence)

    def test_database_date_values_are_normalized_before_approval_storage(self) -> None:
        payload = ready_payload()
        payload["subject"]["history_start_date"] = date(2025, 1, 20)
        payload["nav"]["first_date"] = date(2025, 1, 20)
        payload["nav"]["latest_date"] = date(2026, 9, 25)
        payload["positions"]["latest_date"] = date(2026, 9, 25)
        payload["benchmarks"][0]["latest_comparison_date"] = date(2026, 9, 24)
        payload["benchmarks"][1]["latest_comparison_date"] = date(2026, 9, 25)
        payload["benchmarks"][2]["latest_comparison_date"] = date(2026, 9, 24)

        evidence = build_physical_preview_validation(**payload)

        self.assertTrue(evidence["ready_for_owner_preview_approval"])
        self.assertEqual(evidence["latest_data_date"], "2026-09-25")
        self.assertEqual(json.loads(json.dumps(evidence)), evidence)

    def test_preview_fails_closed_on_stale_positions(self) -> None:
        payload = ready_payload()
        payload["positions"]["latest_date"] = "2026-09-23"
        evidence = build_physical_preview_validation(**payload)
        self.assertFalse(evidence["ready_for_owner_preview_approval"])
        failed = {row["check_id"] for row in evidence["results"] if row["status"] == "fail"}
        self.assertIn("latest_positions_aligned", failed)

    def test_preview_accepts_bounded_cross_market_holiday_alignment(self) -> None:
        payload = ready_payload()
        payload["nav"]["latest_date"] = "2026-09-25"
        payload["positions"]["latest_date"] = "2026-09-25"
        payload["benchmarks"][0]["latest_comparison_date"] = "2026-09-24"
        payload["benchmarks"][1]["latest_comparison_date"] = "2026-09-25"
        payload["benchmarks"][2]["latest_comparison_date"] = "2026-09-24"
        evidence = build_physical_preview_validation(**payload)
        benchmark_check = next(
            row
            for row in evidence["results"]
            if row["check_id"] == "benchmarks_reconciled"
        )
        self.assertEqual(benchmark_check["status"], "pass")
        self.assertEqual(
            [row["staleness_days"] for row in benchmark_check["evidence"]["benchmarks"]],
            [1, 0, 1],
        )

    def test_preview_fails_closed_on_excessively_stale_or_future_benchmark(self) -> None:
        stale = ready_payload()
        stale["benchmarks"][0]["latest_comparison_date"] = "2026-09-19"
        stale_evidence = build_physical_preview_validation(**stale)
        stale_check = next(
            row
            for row in stale_evidence["results"]
            if row["check_id"] == "benchmarks_reconciled"
        )
        self.assertEqual(stale_check["status"], "fail")

        future = ready_payload()
        future["benchmarks"][0]["latest_comparison_date"] = "2026-09-25"
        future_evidence = build_physical_preview_validation(**future)
        future_check = next(
            row
            for row in future_evidence["results"]
            if row["check_id"] == "benchmarks_reconciled"
        )
        self.assertEqual(future_check["status"], "fail")

    def test_preview_fails_closed_on_incomplete_templates_or_prior_gate(self) -> None:
        payload = ready_payload()
        payload["templates"] = payload["templates"][:2]
        payload["checks"][0]["status"] = "warn"
        evidence = build_physical_preview_validation(**payload)
        failed = {row["check_id"] for row in evidence["results"] if row["status"] == "fail"}
        self.assertEqual(
            failed,
            {"report_documents_validated", "prior_onboarding_gates_passed"},
        )

    def test_long_exchange_closure_requires_exact_last_session(self) -> None:
        payload = ready_payload()
        payload["nav"]["latest_date"] = "2026-10-06"
        payload["positions"]["latest_date"] = "2026-10-06"
        for row in payload["benchmarks"]:
            row["latest_comparison_date"] = "2026-10-06"
        shanghai = payload["benchmarks"][2]
        shanghai["latest_comparison_date"] = "2026-09-30"
        evidence = build_physical_preview_validation(**payload)
        self.assertTrue(evidence["ready_for_owner_preview_approval"])
        check = next(r for r in evidence["results"] if r["check_id"] == "benchmarks_reconciled")
        row = check["evidence"]["benchmarks"][2]
        self.assertEqual(row["expected_exchange_session"], "2026-09-30")
        self.assertEqual(row["freshness_policy"], "exact-last-session-during-closure")
        self.assertEqual(row["staleness_days"], 6)
        for bad_date in ("2026-09-29", "2026-10-07"):
            with self.subTest(date=bad_date):
                shanghai["latest_comparison_date"] = bad_date
                self.assertFalse(build_physical_preview_validation(**payload)["ready_for_owner_preview_approval"])

    def test_closure_exception_does_not_waive_currency_or_rows(self) -> None:
        for change in ({"reporting_currency": "HKD"}, {"comparison_rows": 0}):
            with self.subTest(change=change):
                payload = ready_payload()
                payload["nav"]["latest_date"] = "2026-10-06"
                payload["positions"]["latest_date"] = "2026-10-06"
                for row in payload["benchmarks"]:
                    row["latest_comparison_date"] = "2026-10-06"
                payload["benchmarks"][2].update(latest_comparison_date="2026-09-30", **change)
                self.assertFalse(build_physical_preview_validation(**payload)["ready_for_owner_preview_approval"])

    def test_closure_exception_expires_when_market_reopens(self) -> None:
        payload = ready_payload()
        payload["nav"]["latest_date"] = "2026-10-08"
        payload["positions"]["latest_date"] = "2026-10-08"
        for row in payload["benchmarks"]:
            row["latest_comparison_date"] = "2026-10-08"
        payload["benchmarks"][2]["latest_comparison_date"] = "2026-09-30"
        self.assertFalse(build_physical_preview_validation(**payload)["ready_for_owner_preview_approval"])

    def test_known_market_unknown_calendar_year_fails_closed(self) -> None:
        payload = ready_payload()
        payload["nav"]["latest_date"] = "2030-10-01"
        payload["positions"]["latest_date"] = "2030-10-01"
        for row in payload["benchmarks"]:
            row["latest_comparison_date"] = "2030-10-01"
        self.assertFalse(build_physical_preview_validation(**payload)["ready_for_owner_preview_approval"])

    def test_unknown_market_cannot_claim_long_closure_exception(self) -> None:
        payload = ready_payload()
        payload["benchmarks"][0].update(benchmark_id="unregistered-market",
                                        latest_comparison_date="2026-09-18")
        self.assertFalse(build_physical_preview_validation(**payload)["ready_for_owner_preview_approval"])

    def test_observed_ms_usd_mappings_remain_blocked_despite_closure_fix(self) -> None:
        payload = ready_payload()
        payload["subject"].update(subject_id="alpha-world-ms", subject_type="composite_portfolio",
                                  display_name="Alpha World MS", reporting_currency="HKD")
        payload["nav"].update(latest_date="2026-10-06", observations=447)
        payload["positions"] = {"latest_date": None, "rows": 0}
        payload["benchmarks"] = [
            {"benchmark_id": benchmark, "mapping_type": "primary" if index == 0 else "secondary",
             "latest_comparison_date": day, "comparison_rows": count, "reporting_currency": "USD"}
            for index, (benchmark, day, count) in enumerate((
                ("GSPC", "2026-10-06", 430), ("HSI", "2026-10-06", 421),
                ("IXIC", "2026-10-06", 430), ("SHCOMP", "2026-09-30", 412)))
        ]
        evidence = build_physical_preview_validation(**payload)
        self.assertFalse(evidence["ready_for_owner_preview_approval"])
        self.assertEqual([r["check_id"] for r in evidence["results"] if r["status"] == "fail"],
                         ["benchmarks_reconciled"])

    def test_preview_digest_changes_with_financial_evidence(self) -> None:
        payload = ready_payload()
        first = build_physical_preview_validation(**payload)["evidence_sha256"]
        changed = copy.deepcopy(payload)
        changed["nav"]["observations"] += 1
        second = build_physical_preview_validation(**changed)["evidence_sha256"]
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
