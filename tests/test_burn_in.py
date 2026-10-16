from __future__ import annotations

import unittest
from datetime import date

from ibkr_etl.burn_in import (
    burn_in_completion_date,
    consecutive_pass_days,
    evaluate_database_state,
    flex_import_failures,
    freshness_check,
    portal_boundary_check,
)

FLEX_FAILURES = (
    "Latest institutional Flex import is not successful",
    "Institutional Flex import has not completed for the current HKT date",
    "Latest NAV row was not loaded by an institutional Flex import within the current HKT date",
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


    def _state(self, **changes):
        state = {
            "latest_nav_date": "2026-10-20",
            "latest_gspc_date": "2026-10-20",
            "latest_ixic_date": "2026-10-20",
            "latest_risk_free_date": "2026-10-20",
            "latest_import": {"run_id": 7, "status": "success", "completed_at": "2026-10-20T08:12:00+00:00"},
            "latest_snapshot": {"status": "frozen", "quality_status": "pass"},
            "ready_report_types": ["factsheet"],
            "client_approval_count": 0,
            "portfolio_analyst_assurance": {"status": "pass", "assurance_date": "2026-10-21"},
        }
        state.update(changes)
        return state

    def _flex(self, completed="2026-10-21T08:31:00+00:00", **changes):
        run = {"run_id": 9, "run_type": "institutional-flex-daily", "status": "success",
               "completed_at": completed}
        flex = {
            "daily_feed": "ibkr_flex",
            "latest_import": {"run_id": 9, "status": "success", "completed_at": completed},
            "latest_nav_run": run,
        }
        return self._state(**{**flex, **changes})

    def test_v11_feed_keeps_todays_import_check_exactly(self) -> None:
        today = date(2026, 10, 21)
        checks, failures = evaluate_database_state(self._state(), today_hkt=today)
        self.assertEqual(failures, [])
        self.assertNotIn("daily_feed", checks)
        self.assertEqual(checks["latest_import"]["run_id"], 7)
        # An old success still passes, as today: the v1.1 check never looked at the date.
        _, failures = evaluate_database_state(
            self._state(latest_import={"status": "success", "completed_at": "2026-09-01T08:00:00+00:00"}),
            today_hkt=today,
        )
        self.assertEqual(failures, [])
        _, failures = evaluate_database_state(
            self._state(latest_import={"status": "failed"}), today_hkt=today
        )
        self.assertEqual(failures, ["Latest account-only portal import is not successful"])

    def test_flex_feed_needs_todays_successful_pull_that_wrote_the_nav(self) -> None:
        today = date(2026, 10, 21)
        checks, failures = evaluate_database_state(self._flex(), today_hkt=today)
        self.assertEqual(failures, [])
        self.assertEqual(checks["daily_feed"], "ibkr_flex")
        self.assertEqual(checks["latest_nav_run"]["run_id"], 9)
        # 16:30 UTC on the 20th is 00:30 HKT on the 21st.
        _, failures = evaluate_database_state(self._flex("2026-10-20T16:30:00+00:00"), today_hkt=today)
        self.assertEqual(failures, [])

    def test_flex_feed_fails_on_yesterdays_pull_failed_pull_or_v11_nav(self) -> None:
        today = date(2026, 10, 21)
        _, failures = evaluate_database_state(self._flex("2026-10-20T08:31:00+00:00"), today_hkt=today)
        self.assertEqual(failures, list(FLEX_FAILURES[1:]))
        _, failures = evaluate_database_state(
            self._flex(latest_import={"status": "failed", "completed_at": "2026-10-21T08:31:00+00:00"}),
            today_hkt=today,
        )
        self.assertEqual(failures, [FLEX_FAILURES[0]])
        _, failures = evaluate_database_state(
            self._flex(latest_import=None, latest_nav_run=None), today_hkt=today
        )
        self.assertEqual(failures, [FLEX_FAILURES[0], FLEX_FAILURES[2]])
        # The NAV row was last written by account-sync, not by the Flex pull.
        stale = {"run_id": 7, "run_type": "staging-snapshot-import", "status": "success",
                 "completed_at": "2026-10-21T08:12:00+00:00"}
        _, failures = evaluate_database_state(self._flex(latest_nav_run=stale), today_hkt=today)
        self.assertEqual(failures, [FLEX_FAILURES[2]])
        # A completion time in the future or unreadable never passes.
        _, failures = evaluate_database_state(self._flex("not-a-date"), today_hkt=today)
        self.assertEqual(failures, list(FLEX_FAILURES[1:]))

    def test_flex_failures_accept_a_wider_window_when_asked(self) -> None:
        state = self._flex("2026-10-20T08:31:00+00:00")
        self.assertEqual(flex_import_failures(state, today_hkt=date(2026, 10, 21), maximum_age_days=1), [])
        self.assertEqual(
            flex_import_failures(state, today_hkt=date(2026, 10, 22), maximum_age_days=1),
            [
                "Institutional Flex import has not completed for the last 2 HKT days",
                "Latest NAV row was not loaded by an institutional Flex import within the last 2 HKT days",
            ],
        )


if __name__ == "__main__":
    unittest.main()
