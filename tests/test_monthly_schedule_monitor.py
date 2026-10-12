import unittest
from datetime import datetime, timedelta, timezone
from ibkr_etl.monthly_schedule_monitor import monthly_issue, monthly_query


class MonthlyScheduleMonitorTests(unittest.TestCase):
    def clock(self, day=1, hour=1, minute=15):
        return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)

    def test_grace_boundary(self):
        self.assertIsNone(monthly_issue(self.clock(minute=14), 30, 0, 0))
        self.assertEqual(monthly_issue(self.clock(), 30, 0, 0), "MissingMonthlyStartTelemetry")

    def test_hkt_same_result(self):
        self.assertEqual(monthly_issue(self.clock().astimezone(timezone(timedelta(hours=8))), 30, 0, 0),
                         monthly_issue(self.clock(), 30, 0, 0))

    def test_runtime_grace(self):
        self.assertIsNone(monthly_issue(self.clock(minute=44), 30, 1, 0))
        self.assertEqual(monthly_issue(self.clock(minute=45), 30, 1, 0), "MissingMonthlyTerminalTelemetry")

    def test_terminal_handled_by_existing_failure_alerts(self):
        self.assertIsNone(monthly_issue(self.clock(), 30, 1, 1))
        self.assertIsNone(monthly_issue(self.clock(), 30, 0, 1))

    def test_observation_window_expiry_not_recovery_claim(self):
        self.assertEqual(monthly_issue(self.clock(day=3, hour=0, minute=59), 30, 0, 0), "MissingMonthlyStartTelemetry")
        self.assertIsNone(monthly_issue(self.clock(day=3, minute=0), 30, 0, 0))
        self.assertIsNone(monthly_issue(self.clock(day=31), 30, 0, 0))

    def test_months_and_year_boundaries(self):
        for year, month in ((2026, 1), (2026, 2), (2028, 2), (2026, 12)):
            self.assertEqual(monthly_issue(datetime(year, month, 1, 1, 15, tzinfo=timezone.utc), 30, 0, 0),
                             "MissingMonthlyStartTelemetry")

    def test_query_matches_specific_execution_not_manual_retry(self):
        query = monthly_query(30)
        self.assertIn("on JobName, ExecutionName", query)
        self.assertIn("startofmonth(Clock)", query)
        self.assertIn("Clock < Due + 2d", query)
        self.assertNotIn("Log_s", query)

    def test_invalid_inputs(self):
        for runtime in (0, 361, "30", True):
            with self.assertRaises(ValueError):
                monthly_query(runtime)
        with self.assertRaises(ValueError):
            monthly_issue(datetime(2026, 10, 1), 30, 0, 0)


if __name__ == "__main__":
    unittest.main()
