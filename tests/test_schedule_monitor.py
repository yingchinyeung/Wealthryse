import unittest
from datetime import datetime, timedelta, timezone
from ibkr_etl.schedule_monitor import (DAILY_V1, DAILY_V2, daily_issue, daily_query,
                                      due_daily, execution_name, heartbeat_query)


class ScheduleMonitorTests(unittest.TestCase):
    def now(self, hour=8, minute=15):
        return datetime(2026, 10, 1, hour, minute, tzinfo=timezone.utc)

    def test_grace_and_previous_cycle(self):
        self.assertEqual(due_daily(self.now(8, 14), 480).day, 30)
        self.assertEqual(due_daily(self.now(), 480), self.now(8, 0))

    def test_overnight_hkt_risk_free_cycle(self):
        due = due_daily(self.now(0, 5), 1380)
        self.assertEqual(due, datetime(2026, 9, 30, 23, tzinfo=timezone.utc))

    def test_hkt_and_utc_same_result(self):
        local = self.now().astimezone(timezone(timedelta(hours=8)))
        self.assertEqual(due_daily(local, 480), due_daily(self.now(), 480))

    def test_scheduled_identity_matches_observed_azure_execution(self):
        self.assertEqual(execution_name('ibkr-v11-daily', self.now(8, 0)),
                         'ibkr-v11-daily-29847360')

    def test_missing_start_is_detected(self):
        self.assertEqual(daily_issue(self.now(), 480, 60, 0, 0), 'MissingScheduledStartTelemetry')

    def test_runtime_and_logging_grace(self):
        self.assertIsNone(daily_issue(self.now(9, 14), 480, 60, 1, 0))
        self.assertEqual(daily_issue(self.now(9, 15), 480, 60, 1, 0),
                         'MissingScheduledTerminalTelemetry')

    def test_terminal_failure_does_not_duplicate_failure_alert(self):
        self.assertIsNone(daily_issue(self.now(9, 30), 480, 60, 1, 1))
        self.assertIsNone(daily_issue(self.now(9, 30), 480, 60, 0, 1))

    def test_query_preserves_empty_log_and_specific_execution_detection(self):
        query = daily_query(DAILY_V1)
        self.assertIn('join kind=leftouter', query)
        self.assertIn('on JobName, ExecutionName', query)
        self.assertNotIn('Log_s', query)
        self.assertNotIn('union isfuzzy', query)

    def test_query_scope_and_heartbeat(self):
        self.assertEqual(len(DAILY_V1) + len(DAILY_V2), 11)
        self.assertIn('ago(20m)', heartbeat_query())
        self.assertIn('ago(35m)', heartbeat_query())
        self.assertNotIn('Log_s', heartbeat_query())

    def test_invalid_schedule_and_naive_time_fail(self):
        with self.assertRaises(ValueError):
            daily_query((("bad'; injected", 0, 1),))
        with self.assertRaises(ValueError):
            due_daily(datetime(2026, 10, 1), 480)


if __name__ == '__main__':
    unittest.main()
