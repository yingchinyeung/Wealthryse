from __future__ import annotations

import unittest
from datetime import date

from ibkr_etl.burn_in import (
    burn_in_completion_date,
    consecutive_pass_days,
    evaluate_database_state,
    freshness_check,
    portal_boundary_check,
)


class BurnInTests(unittest.TestCase):
    def test_freshness_accepts_weekend_tolerance(self) -> None:
        result, failure = freshness_check(
            "NAV", "2026-09-18", today_hkt=date(2026, 9, 21), maximum_age_days=4
        )
        self.assertIsNone(failure)
        self.assertEqual(result["age_days"], 3)

    def test_portal_requires_approved_single_tenant_redirect(self) -> None:
        result, failure = portal_boundary_check(
            302,
            "https://login.microsoftonline.com/0578a50b-a7a1-40a4-905b-8b20dba84124/oauth2/v2.0/authorize?client_id=x",
            tenant_id="0578a50b-a7a1-40a4-905b-8b20dba84124",
        )
        self.assertIsNone(failure)
        self.assertEqual(result["redirect_host"], "login.microsoftonline.com")

    def test_portal_accepts_server_to_server_auth_denial(self) -> None:
        result, failure = portal_boundary_check(
            401,
            None,
            tenant_id="0578a50b-a7a1-40a4-905b-8b20dba84124",
        )
        self.assertIsNone(failure)
        self.assertEqual(result["boundary_mode"], "server-to-server-denial")

    def test_database_state_fails_closed_on_missing_report_pair(self) -> None:
        checks, failures = evaluate_database_state(
            {
                "latest_nav_date": "2026-09-22",
                "latest_gspc_date": "2026-09-22",
                "latest_ixic_date": "2026-09-22",
                "latest_risk_free_date": "2026-09-22",
                "latest_import": {"status": "success"},
                "latest_snapshot": {"status": "frozen", "quality_status": "warn"},
                "ready_report_types": ["presentation"],
                "client_approval_count": 0,
            },
            today_hkt=date(2026, 9, 23),
        )
        self.assertIn("latest_snapshot", checks)
        self.assertIn(
            "Latest month-end snapshot does not have a ready factsheet", failures
        )

    def test_fourteen_day_window_is_inclusive_and_non_retroactive(self) -> None:
        self.assertEqual(
            burn_in_completion_date(date(2026, 9, 24)).isoformat(), "2026-10-07"
        )

    def test_consecutive_passes_stop_at_failure_or_missing_date(self) -> None:
        complete = [
            {"result_date": date(2026, 9, day).isoformat(), "status": "pass"}
            for day in range(24, 27)
        ]
        self.assertEqual(
            consecutive_pass_days(complete, through_date=date(2026, 9, 26)), 3
        )
        failed = [
            {"result_date": "2026-09-24", "status": "pass"},
            {"result_date": "2026-09-25", "status": "fail"},
            {"result_date": "2026-09-26", "status": "pass"},
        ]
        self.assertEqual(
            consecutive_pass_days(failed, through_date=date(2026, 9, 26)), 1
        )
        missing = [complete[0], complete[2]]
        self.assertEqual(
            consecutive_pass_days(missing, through_date=date(2026, 9, 26)), 1
        )


if __name__ == "__main__":
    unittest.main()
