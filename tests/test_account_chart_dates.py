"""The account design's NAV chart always dates its bottom axis, however short the history."""
from __future__ import annotations

import unittest
from datetime import date

from ibkr_etl.monthly_artifacts import (
    _account_chart_date_labels,
    _account_chart_month_ticks,
)

START = date(2025, 3, 21)  # a history that starts late in a month, as Korea iStrategy's does


def labels(end: date) -> list[str]:
    return [label for _, label in _account_chart_date_labels(START, end)]


class AccountChartDateTests(unittest.TestCase):
    def test_a_few_weeks_of_history_is_dated_by_week(self):
        self.assertEqual(_account_chart_month_ticks(START, date(2025, 3, 31)), ())
        self.assertEqual(labels(date(2025, 3, 31)), ["24 Mar", "31 Mar"])
        self.assertEqual(labels(date(2025, 4, 30)), ["24 Mar", "31 Mar", "7 Apr", "14 Apr", "21 Apr", "28 Apr"])

    def test_a_few_months_of_history_is_dated_by_month(self):
        self.assertEqual(labels(date(2025, 5, 30)), ["Apr", "May"])
        self.assertEqual(labels(date(2025, 6, 30)), ["Apr", "May", "Jun"])

    def test_a_longer_history_keeps_the_approved_odd_month_cadence(self):
        self.assertEqual(labels(date(2025, 7, 31)), ["May", "Jul"])
        self.assertEqual(labels(date(2026, 9, 30)), ["May", "Jul", "Sep", "Nov", "2026", "Mar", "May", "Jul", "Sep"])
        ticks = [tick for tick, _ in _account_chart_date_labels(START, date(2026, 9, 30))]
        self.assertEqual(tuple(ticks), _account_chart_month_ticks(START, date(2026, 9, 30)))


if __name__ == "__main__":
    unittest.main()
