from __future__ import annotations

import importlib.util
import unittest
from datetime import date
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "health_check.py"
SPEC = importlib.util.spec_from_file_location("health_check_script", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HealthCheckFreshnessTests(unittest.TestCase):
    def test_daily_default_accepts_only_previous_hkt_date(self) -> None:
        today = date(2026, 9, 22)
        self.assertTrue(
            MODULE._requested_to_is_fresh(
                "2026-09-21", today_hkt=today, max_request_lag_days=1
            )
        )
        self.assertFalse(
            MODULE._requested_to_is_fresh(
                "2026-09-20", today_hkt=today, max_request_lag_days=1
            )
        )

    def test_pre_schedule_maintenance_allows_two_day_lag(self) -> None:
        today = date(2026, 9, 22)
        self.assertTrue(
            MODULE._requested_to_is_fresh(
                "2026-09-20", today_hkt=today, max_request_lag_days=2
            )
        )
        self.assertFalse(
            MODULE._requested_to_is_fresh(
                "2026-09-19", today_hkt=today, max_request_lag_days=2
            )
        )

    def test_rejects_today_future_and_invalid_dates(self) -> None:
        today = date(2026, 9, 22)
        for requested_to in (None, "", "not-a-date", "2026-09-22", "2026-09-23"):
            with self.subTest(requested_to=requested_to):
                self.assertFalse(
                    MODULE._requested_to_is_fresh(
                        requested_to,
                        today_hkt=today,
                        max_request_lag_days=2,
                    )
                )


if __name__ == "__main__":
    unittest.main()
