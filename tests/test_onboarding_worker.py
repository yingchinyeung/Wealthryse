"""The onboarding worker's timing and what it does for each case (ibkr_etl.onboarding_worker)."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from ibkr_etl.onboarding_worker import Case, next_scheduled, plan, window_note, windows


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 26, hour, minute, tzinfo=timezone.utc)


def case(**changes) -> Case:
    values = dict(case_id="c1", source_kind="broker_account", broker="kiwoom_rest", stage="request",
                  subject_id=None, history_start_date="2025-01-02", checks={}, yahoo_approved=False)
    values.update(changes)
    return Case(**values)


BUILT = {"history_built": "pass"}


class WindowTests(unittest.TestCase):
    def test_the_daily_chain_and_kiwoom_rebuild_hours(self):
        self.assertEqual(windows(at(7, 19)), {"daily_chain": False, "kiwoom_rebuild": False})
        self.assertTrue(windows(at(7, 20))["daily_chain"])
        self.assertTrue(windows(at(9, 44))["daily_chain"])
        self.assertFalse(windows(at(9, 45))["daily_chain"])
        for hour in (16, 17, 19, 21):
            self.assertTrue(windows(at(hour, 30))["kiwoom_rebuild"], hour)
        for hour in (15, 18, 22):
            self.assertFalse(windows(at(hour, 30))["kiwoom_rebuild"], hour)
        self.assertIn("09:45 UTC", window_note(at(8)))
        self.assertIn("rebuilding", window_note(at(17)))
        self.assertIsNone(window_note(at(12)))

    def test_the_next_scheduled_run_skips_the_daily_chain(self):
        self.assertEqual(next_scheduled(at(12, 5)), at(12, 20))
        self.assertEqual(next_scheduled(at(12, 20)), at(12, 40))
        self.assertEqual(next_scheduled(at(7, 10)), at(9, 0) + timedelta(hours=1))
        self.assertEqual(next_scheduled(at(8, 0)), at(10, 0))


class PlanTests(unittest.TestCase):
    def test_nothing_runs_in_the_daily_chain_or_after_go_live(self):
        self.assertEqual(plan(case(), now=at(8)), [])
        self.assertEqual(plan(case(stage="first_month_end"), now=at(12)), [])

    def test_kiwoom_is_called_while_the_stage_needs_it(self):
        self.assertEqual(plan(case(), now=at(12)), ["kiwoom"])
        self.assertEqual(plan(case(stage="load_and_reconcile"), now=at(12)), ["kiwoom"])
        filling = case(stage="build_history", subject_id="KIWOOM-1", checks={"history_built": "pending"})
        self.assertEqual(plan(filling, now=at(12)), ["kiwoom"])
        # Past days only outside Kiwoom's rebuild hours; the first load still runs then.
        self.assertEqual(plan(filling, now=at(17)), [])
        self.assertEqual(plan(case(), now=at(17)), ["kiwoom"])
        built = case(stage="build_history", subject_id="KIWOOM-1", checks=BUILT)
        self.assertNotIn("kiwoom", plan(built, now=at(12)))
        self.assertIn("kiwoom", plan(case(stage="build_history", subject_id="KIWOOM-1", checks=BUILT,
                                          requested=True), now=at(12)))

    def test_ibkr_cases_record_the_reconciliation_and_never_call_kiwoom(self):
        self.assertEqual(plan(case(broker="ibkr_flex", stage="load_and_reconcile"), now=at(12)), ["reconcile"])
        self.assertEqual(plan(case(broker="ibkr_flex"), now=at(12)), [])

    def test_names_need_the_yahoo_consent_and_a_built_history(self):
        built = case(stage="build_history", subject_id="KIWOOM-1", checks=BUILT)
        self.assertEqual(plan(built, now=at(12)), ["render"])
        consented = case(stage="build_history", subject_id="KIWOOM-1", checks=BUILT, yahoo_approved=True)
        self.assertEqual(plan(consented, now=at(12)), ["classify", "render"])
        recent = case(stage="build_history", subject_id="KIWOOM-1", yahoo_approved=True,
                      checks={**BUILT, "reports_rendered": "pass"},
                      last_steps={"classify": at(6), "render": at(10)})
        self.assertEqual(plan(recent, now=at(12)), ["review"])
        self.assertEqual(plan(recent, now=at(12) + timedelta(days=1)), ["classify", "render", "review"])

    def test_the_review_is_recorded_until_it_passes(self):
        reviewed = case(stage="build_history", subject_id="KIWOOM-1", last_steps={"render": at(11)},
                        checks={**BUILT, "reports_rendered": "pass", "agent_review_complete": "pass"})
        self.assertEqual(plan(reviewed, now=at(12)), [])

    def test_a_derived_portfolio_renders_but_is_not_classified(self):
        derived = case(source_kind="composite_portfolio", broker=None, stage="build_history",
                       subject_id="synthetic", checks=BUILT, yahoo_approved=True)
        self.assertEqual(plan(derived, now=at(12)), ["render"])


if __name__ == "__main__":
    unittest.main()
