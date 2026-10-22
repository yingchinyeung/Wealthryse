"""The first-month watch after go-live: which cards it posts, and that each appears once. Synthetic values."""
from __future__ import annotations

import unittest
from datetime import date

from wealthryse_agent.onboarding_watch import KIND, findings, watch_live_cases

CASE = {"case_id": "c1", "subject_id": "U0000901", "display_name": "Synthetic Growth", "source_kind": "broker_account",
        "history_start_date": "2025-01-02", "went_live_at": "2026-10-01T09:00:00+00:00",
        "latest_data_date": "2026-10-02", "latest_assurance_date": "2026-10-03", "latest_assurance_status": "pass",
        "month_team_has_access": True, "first_month_end_status": "pending"}


def keys(case, today=date(2026, 10, 4)):
    return [key for key, _, _ in findings(case, today)]


class FakeStore:
    def __init__(self):
        self.items = {}

    def add_inbox(self, *, kind, account_id, period_key, title, body, citations, run_id):
        if (kind, account_id, period_key) in self.items:
            return False
        self.items[(kind, account_id, period_key)] = (title, body)
        return True


class WatchTests(unittest.TestCase):
    def test_a_healthy_first_day_is_handed_over(self):
        self.assertEqual(keys(CASE), ["first_day"])

    def test_missing_data_after_a_long_weekend_is_not_a_gap_but_five_days_is(self):
        quiet = dict(CASE, latest_data_date="2026-10-02", latest_assurance_status=None, latest_assurance_date=None)
        self.assertEqual(keys(quiet, date(2026, 10, 6)), [])
        self.assertEqual(keys(quiet, date(2026, 10, 7)), ["stale:2026-10-02"])
        never = dict(quiet, latest_data_date=None)
        self.assertEqual(keys(never, date(2026, 10, 7)), ["stale:none"])

    def test_failed_checks_and_missing_month_end_access_are_reported(self):
        case = dict(CASE, latest_assurance_status="fail", month_team_has_access=False)
        found = {key: body for key, _, body in findings(case, date(2026, 10, 4))}
        self.assertIn("assurance_fail:2026-10-03", found)
        self.assertIn("--grant month_team=U0000901", found["month_team_access"])

    def test_once_released_the_case_is_done(self):
        self.assertEqual(keys(dict(CASE, first_month_end_status="pass", month_team_has_access=False)),
                         ["handed_over"])

    def test_each_card_is_posted_once(self):
        store = FakeStore()

        def call(name, arguments):
            self.assertEqual(name, "list_live_onboarding_cases")
            return {"data": {"cases": [dict(CASE, month_team_has_access=False)]}}

        self.assertEqual(watch_live_cases(store, call, 5, date(2026, 10, 4)), 2)
        self.assertEqual(watch_live_cases(store, call, 6, date(2026, 10, 4)), 0)
        self.assertEqual({k[0] for k in store.items}, {KIND})
        self.assertEqual({k[2] for k in store.items}, {"c1:first_day", "c1:month_team_access"})


if __name__ == "__main__":
    unittest.main()
