from __future__ import annotations

import unittest
from datetime import date

from ibkr_etl.autonomy_review import (
    build_review_summary,
    next_review_due,
    previous_month,
    review_confirmation_phrase,
)


class AutonomyReviewTests(unittest.TestCase):
    def test_previous_month_handles_year_boundary(self) -> None:
        start, end, label = previous_month(date(2027, 1, 1))
        self.assertEqual((start.isoformat(), end.isoformat(), label), ("2026-12-01", "2026-12-31", "2026-12"))

    def test_review_due_is_fifteenth_of_next_month(self) -> None:
        self.assertEqual(next_review_due(date(2026, 9, 24)).isoformat(), "2026-10-15T23:59:59+00:00")

    def test_summary_is_fixed_and_contains_no_raw_data(self) -> None:
        summary = build_review_summary(
            {"supervisor_runs": 20, "failed_runs": 1, "unexpected": "secret"},
            policy_version="controlled-autonomy-v1",
        )
        self.assertNotIn("unexpected", summary)
        self.assertEqual(summary["supervisor_runs"], 20)
        self.assertEqual(summary["failed_runs"], 1)
        self.assertEqual(review_confirmation_phrase("2026-09"), "APPROVE MONTHLY AUTHORITY REVIEW 2026-09")


if __name__ == "__main__":
    unittest.main()
