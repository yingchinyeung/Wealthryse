from __future__ import annotations

import unittest

from ibkr_etl.internal_release import InternalReleaseError
from ibkr_etl.monthly_release import review_package
from ibkr_etl.monthly_reports import MonthlyReportError


SNAPSHOT_ID = "751c2b5a-c9cd-54fb-808e-ff98edbd8d67"


def _snapshot(**overrides):
    value = {
        "snapshot_id": SNAPSHOT_ID,
        "subject_id": "U16088606",
        "period_end": "2026-08-31",
        "status": "frozen",
        "quality_status": "pass",
        "publication_blockers_json": "[]",
        "data_sha256": "c" * 64,
    }
    value.update(overrides)
    return value


def _runs():
    return [
        {
            "generation_run_id": 81,
            "snapshot_id": SNAPSHOT_ID,
            "report_type": "factsheet",
            "status": "awaiting_approval",
            "output_reference": "internal-reports/review/U16088606/2026-08-31/factsheet.pdf",
            "output_sha256": "a" * 64,
        },
        {
            "generation_run_id": 82,
            "snapshot_id": SNAPSHOT_ID,
            "report_type": "presentation",
            "status": "awaiting_approval",
            "output_reference": "internal-reports/review/U16088606/2026-08-31/presentation.pptx",
            "output_sha256": "b" * 64,
        },
    ]


def _review(snapshot=None, runs=None):
    return review_package(
        snapshot or _snapshot(), runs or _runs(),
        subject_id="U16088606", period_end="2026-08-31",
        container="internal-reports",
    )


class MonthlyReleaseTests(unittest.TestCase):
    def test_exact_pair_is_bound_to_account_month_snapshot_and_digests(self):
        review = _review()
        self.assertEqual(review["approved_types"], [])
        self.assertEqual(len(review["selection_version"]), 64)
        self.assertEqual(review["release_scope"], "internal_shareholder")
        self.assertEqual(review["client_distribution"], "prohibited")
        changed = _runs()
        changed[0]["output_sha256"] = "d" * 64
        self.assertNotEqual(review["package_sha256"], _review(runs=changed)["package_sha256"])

    def test_wrong_account_artifact_fails_closed(self):
        runs = _runs()
        runs[0]["output_reference"] = "internal-reports/review/U16088571/2026-08-31/factsheet.pdf"
        with self.assertRaises(MonthlyReportError):
            _review(runs=runs)

    def test_incomplete_or_failed_pair_cannot_be_released(self):
        with self.assertRaises(MonthlyReportError):
            _review(runs=_runs()[:1])
        runs = _runs()
        runs[1]["status"] = "failed"
        with self.assertRaises(MonthlyReportError):
            _review(runs=runs)
        with self.assertRaises(InternalReleaseError):
            _review(snapshot=_snapshot(quality_status="fail"))

    def test_unrelated_publication_blocker_prevents_internal_release(self):
        with self.assertRaises(InternalReleaseError):
            _review(snapshot=_snapshot(publication_blockers_json='["risk:crisis_replay_unavailable"]'))

    def test_individual_file_state_and_revocation_are_versioned(self):
        release = {
            "artifact_release_id": 91,
            "generation_run_id": 81,
            "report_type": "factsheet",
            "subject_id": "U16088606",
            "container_name": "internal-reports",
            "blob_name": "released/U16088606/2026-08-31/factsheet.pdf",
            "artifact_sha256": "a" * 64,
            "revoked_at": None,
            "state_revision": 1,
        }
        runs = _runs()
        runs[0]["status"] = "released_internal"
        active = review_package(
            _snapshot(), runs, subject_id="U16088606", period_end="2026-08-31",
            container="internal-reports", releases=[release],
        )
        self.assertEqual(active["approved_types"], ["factsheet"])
        self.assertEqual(active["artifacts"][0]["source_kind"], "released")
        self.assertEqual(active["artifacts"][1]["source_kind"], "review")
        revoked = review_package(
            _snapshot(), runs, subject_id="U16088606", period_end="2026-08-31",
            container="internal-reports",
            releases=[{**release, "revoked_at": "2026-09-29T12:00:00Z", "state_revision": 2}],
        )
        self.assertEqual(revoked["approved_types"], [])
        self.assertEqual(active["package_sha256"], revoked["package_sha256"])
        self.assertNotEqual(active["selection_version"], revoked["selection_version"])

    def test_revocation_preview_remains_possible_when_new_approval_is_blocked(self):
        blocked = _snapshot(quality_status="fail")
        preview = review_package(
            blocked, _runs(), subject_id="U16088606", period_end="2026-08-31",
            container="internal-reports", enforce_eligibility=False,
        )
        self.assertEqual(preview["approved_types"], [])
        with self.assertRaises(InternalReleaseError):
            _review(snapshot=blocked)


if __name__ == "__main__":
    unittest.main()
