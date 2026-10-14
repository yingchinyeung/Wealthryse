from __future__ import annotations

import unittest
from pathlib import Path

from ibkr_etl.internal_release import InternalReleaseError, approve_internal_release


def _snapshot(**overrides):
    value = {
        "snapshot_id": "751c2b5a-c9cd-54fb-808e-ff98edbd8d67",
        "subject_id": "U16088571",
        "period_end": "2026-08-31",
        "status": "frozen",
        "quality_status": "warn",
        "publication_allowed": False,
        "publication_blockers_json": (
            '["primary_benchmark:not_licensed_for_publication",'
            '"secondary_benchmark:not_licensed_for_publication",'
            '"sector_allocation:not_licensed_for_publication"]'
        ),
    }
    value.update(overrides)
    return value


def _runs():
    return [
        {
            "generation_run_id": 39,
            "snapshot_id": "751c2b5a-c9cd-54fb-808e-ff98edbd8d67",
            "report_type": "factsheet",
            "status": "awaiting_approval",
            "output_sha256": "a" * 64,
        },
        {
            "generation_run_id": 40,
            "snapshot_id": "751c2b5a-c9cd-54fb-808e-ff98edbd8d67",
            "report_type": "presentation",
            "status": "awaiting_approval",
            "output_sha256": "b" * 64,
        },
    ]


class InternalReleasePolicyTests(unittest.TestCase):
    def test_user_accepted_yfinance_package_can_be_released_internally(self) -> None:
        approval = approve_internal_release(
            _snapshot(), _runs(), user_acceptance_recorded=True
        )
        self.assertEqual(approval.subject_id, "U16088571")
        self.assertEqual(approval.generation_run_ids, (39, 40))
        self.assertIn(
            "primary_benchmark:not_licensed_for_publication", approval.blockers
        )

    def test_multiple_secondary_benchmark_license_warnings_are_internal_only(self) -> None:
        snapshot = _snapshot(
            publication_blockers_json=(
                '["primary_benchmark:not_licensed_for_publication",'
                '"secondary_1_benchmark:not_licensed_for_publication",'
                '"secondary_2_benchmark:not_licensed_for_publication"]'
            )
        )
        approval = approve_internal_release(snapshot, _runs(), user_acceptance_recorded=True)
        self.assertEqual(len(approval.blockers), 3)

    def test_internal_release_rejects_quality_and_nonlicensing_blockers(self) -> None:
        for snapshot in (
            _snapshot(quality_status="fail"),
            _snapshot(publication_blockers_json='["risk:crisis_replay_unavailable"]'),
        ):
            with self.subTest(snapshot=snapshot), self.assertRaises(
                InternalReleaseError
            ):
                approve_internal_release(snapshot, _runs(), user_acceptance_recorded=True)

    def test_internal_release_requires_recorded_acceptance_and_complete_pair(self) -> None:
        with self.assertRaises(InternalReleaseError):
            approve_internal_release(_snapshot(), _runs(), user_acceptance_recorded=False)
        with self.assertRaises(InternalReleaseError):
            approve_internal_release(_snapshot(), _runs()[1:], user_acceptance_recorded=True)
        with self.assertRaises(InternalReleaseError):
            approve_internal_release(_snapshot(), [*_runs(), _runs()[0]], user_acceptance_recorded=True)
        # Decks are no longer built: the factsheet alone is a complete release.
        approve_internal_release(_snapshot(), _runs()[:1], user_acceptance_recorded=True)

    def test_existing_blob_restores_metadata_after_http_properties(self) -> None:
        script = (
            Path(__file__).parents[1] / "scripts" / "release_internal_reports.py"
        ).read_text(encoding="utf-8")
        existing_branch = script.split("if destination_client.exists():", 1)[1].split(
            "else:", 1
        )[0]
        self.assertLess(
            existing_branch.index("set_http_headers"),
            existing_branch.index("set_blob_metadata"),
        )


if __name__ == "__main__":
    unittest.main()
