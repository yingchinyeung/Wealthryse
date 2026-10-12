import unittest
from datetime import date, datetime, timedelta, timezone
from ibkr_etl.operational_monitor import (REQUIRED_SECTIONS, assemble_evidence, assess,
                                         instant, queue_state, risk_coverage)


class OperationalMonitorTests(unittest.TestCase):
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)

    def row(self, **updates):
        return {"status":"queued", "available_at":self.now-timedelta(hours=2), **updates}

    def test_ready_backlog(self):
        self.assertEqual(queue_state(self.row(), now=self.now), "overdue_ready_work")

    def test_recent_work(self):
        self.assertEqual(queue_state(self.row(available_at=self.now), now=self.now), "ready_within_grace")

    def test_scheduled_backoff_not_late(self):
        self.assertEqual(queue_state(self.row(available_at=self.now+timedelta(hours=1)), now=self.now),
                         "scheduled_or_backoff")

    def test_waiting_dependency_not_ready_backlog(self):
        self.assertEqual(queue_state(self.row(has_dependency=True, dependency_status="running"), now=self.now),
                         "waiting_for_dependency")

    def test_dependency_completion_resets_eligibility_clock(self):
        self.assertEqual(queue_state(self.row(has_dependency=True, dependency_status="succeeded",
            dependency_completed_at=self.now), now=self.now), "ready_within_grace")

    def test_failed_and_missing_dependency(self):
        for status, expected in [("failed","blocked_by_failed_dependency"), (None,"missing_dependency")]:
            self.assertEqual(queue_state(self.row(has_dependency=True, dependency_status=status), now=self.now), expected)

    def test_invalid_successful_dependency(self):
        self.assertEqual(queue_state(self.row(has_dependency=True, dependency_status="succeeded"), now=self.now),
                         "missing_dependency_completion")

    def test_expired_lease_grace(self):
        row = self.row(status="running", lease_expires_at=self.now-timedelta(minutes=4))
        self.assertEqual(queue_state(row, now=self.now), "running")
        self.assertEqual(queue_state(row, now=self.now+timedelta(minutes=1)), "expired_lease")

    def test_missing_lease_and_paused(self):
        self.assertEqual(queue_state(self.row(status="running"), now=self.now), "missing_lease")
        self.assertEqual(queue_state(self.row(), now=self.now, paused=True), "paused_or_not_active")

    def test_terminal_and_deadletter(self):
        for status in ("succeeded", "cancelled", "published"):
            self.assertEqual(queue_state(self.row(status=status), now=self.now), "terminal")
        self.assertEqual(queue_state(self.row(status="dead_letter"), now=self.now), "dead_letter")

    def test_timezone_required_and_equivalent(self):
        with self.assertRaises(ValueError):
            instant("2026-10-01T12:00:00")
        self.assertEqual(instant("2026-10-01T20:00:00+08:00"), self.now)

    def subject(self, **updates):
        return {"subject_id":"example", "lifecycle_status":"active", "nav_date":"2026-09-30",
                "risk_pass_date":"2026-09-30", **updates}

    def test_risk_dates(self):
        for updates, expected in [({}, "dates_aligned"), ({"risk_pass_date":None},"no_usable_risk_snapshot"),
            ({"risk_pass_date":"2026-09-29"},"risk_behind_nav"),
            ({"risk_pass_date":"2026-10-01"},"risk_ahead_of_nav"),
            ({"nav_date":"2026-10-02"},"future_observation"),
            ({"lifecycle_status":"paused"},"not_active"), ({"nav_date":None},"missing_nav")]:
            self.assertEqual(risk_coverage(self.subject(**updates), today=date(2026,10,1)), expected)

    def test_warning_data_available_not_silently_passed(self):
        self.assertEqual(risk_coverage(self.subject(risk_pass_date=None, risk_usable_date="2026-09-30"),
                         today=date(2026,10,1)), "dates_aligned_with_quality_warning")

    def test_failed_latest_snapshot_does_not_count_as_usable(self):
        self.assertEqual(risk_coverage(self.subject(risk_pass_date=None, risk_usable_date=None,
                         risk_any_date="2026-09-30"), today=date(2026,10,1)), "no_usable_risk_snapshot")

    def test_does_not_invent_calendar_health(self):
        result = assess({"schema":"operational-evidence-v1", "capture":{"read_only":"on",
            "captured_at":self.now, "hkt_date":"2026-10-01"}, "subjects":[self.subject()],
            "work":[], "outbox":[], "inventory_complete":True})
        self.assertEqual(result["status"], "no_findings_in_checked_scope")
        self.assertEqual(result["absolute_market_freshness"], "unverified_calendar_policy_required")

    def test_incomplete_inventory_not_healthy(self):
        result = assess({"schema":"operational-evidence-v1", "capture":{"read_only":"on",
            "captured_at":self.now, "hkt_date":"2026-10-01"}, "subjects":[],
            "work":[], "outbox":[], "inventory_complete":False})
        self.assertEqual(result["status"], "needs_review")

    def messages(self):
        return [{"section":section, "value":True if section == "complete" else []}
                for section in REQUIRED_SECTIONS if section != "work"]

    def test_reordered_and_duplicate_evidence(self):
        messages = self.messages() + [{"section":"work", "length":1},
            {"section":"work", "index":0, "row":{"status":"running"}}]
        self.assertEqual(assemble_evidence(list(reversed(messages))+messages)["work"], [{"status":"running"}])

    def test_missing_rows_not_healthy_empty_queue(self):
        with self.assertRaises(ValueError):
            assemble_evidence(self.messages()+[{"section":"work", "length":1}])

    def test_conflicting_evidence_rejected(self):
        with self.assertRaises(ValueError):
            assemble_evidence(self.messages()+[{"section":"work", "length":0},
                                               {"section":"complete", "value":False}])

    def test_missing_completion_rejected(self):
        with self.assertRaises(ValueError):
            assemble_evidence([r for r in self.messages() if r["section"] != "complete"]+
                              [{"section":"work", "length":0}])


if __name__ == "__main__":
    unittest.main()
