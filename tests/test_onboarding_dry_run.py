from __future__ import annotations

import unittest
from datetime import date

from ibkr_etl.onboarding_dry_run import _DiscardingConnection, evaluate

START = date(2025, 1, 20)
TODAY = date(2025, 3, 3)


def _measured(**changes):
    return {"first_date": "2025-01-20", "latest_date": "2025-02-28", "observations": 29, "lowest_nav": 96.5,
            "latest_nav": 104.25, **changes}


class _Recording:
    def __init__(self):
        self.calls = []

    def execute(self, sql, parameters=None):
        self.calls.append(sql)

    def commit(self):
        self.calls.append("COMMIT")

    def rollback(self):
        self.calls.append("ROLLBACK")


class PortfolioDryRunRuleTests(unittest.TestCase):
    def test_a_clean_run_passes_both_checks(self):
        checks = evaluate(_measured(), start=START, currency="HKD", today=TODAY)
        self.assertEqual({key: status for key, (status, _) in checks.items()},
                         {"sleeves_cover_history": "pass", "calculation_dry_run": "pass"})
        self.assertEqual(checks["calculation_dry_run"][1],
                         "29 daily values from 2025-01-20 to 2025-02-28, positive throughout; "
                         "104.25 HKD on 2025-02-28.")

    def test_a_weekend_start_is_fine_but_a_late_sleeve_is_not(self):
        self.assertEqual(evaluate(_measured(first_date="2025-01-22"), start=START, currency="HKD",
                                  today=TODAY)["sleeves_cover_history"][0], "pass")
        status, message = evaluate(_measured(first_date="2025-02-14"), start=START, currency="HKD",
                                   today=TODAY)["sleeves_cover_history"]
        self.assertEqual(status, "fail")
        self.assertIn("Choose a later start date", message)

    def test_a_value_at_or_below_zero_fails_and_a_stale_one_warns(self):
        self.assertEqual(evaluate(_measured(lowest_nav=-3.0), start=START, currency="HKD",
                                  today=TODAY)["calculation_dry_run"][0], "fail")
        self.assertEqual(evaluate(_measured(), start=START, currency="HKD",
                                  today=date(2025, 3, 20))["calculation_dry_run"][0], "warn")
        nothing = evaluate(_measured(first_date=None, latest_date=None, observations=0), start=START,
                           currency="HKD", today=TODAY)
        self.assertEqual({status for status, _ in nothing.values()}, {"fail"})

    def test_the_calculation_cannot_commit(self):
        inner = _Recording()
        dry = _DiscardingConnection(inner)
        dry.execute("INSERT")
        dry.commit()
        dry.rollback()
        self.assertEqual(inner.calls, ["INSERT", "ROLLBACK"])
        self.assertEqual(dry.backend, "postgresql")


if __name__ == "__main__":
    unittest.main()
