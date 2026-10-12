import unittest
from datetime import date
from ibkr_etl.platform_health import aggregate_health


class PlatformHealthTests(unittest.TestCase):
    def status(self, rows, run=None):
        return aggregate_health(run or {'status': 'success'}, rows, today=date(2026, 10, 1))

    def test_one_success_cannot_hide_other_account_failure(self):
        self.assertEqual(self.status([
            {'status': 'fail', 'assurance_date': '2026-10-01'},
            {'status': 'pass', 'assurance_date': '2026-10-01'},
        ]), 'fail')

    def test_missing_or_stale_assurance_is_not_green(self):
        for rows in ([], [{'status': None}], [{'status': 'pass', 'assurance_date': '2026-09-29'}]):
            self.assertEqual(self.status(rows), 'warn')

    def test_current_and_previous_day_pass(self):
        self.assertEqual(self.status([
            {'status': 'pass', 'assurance_date': '2026-10-01'},
            {'status': 'pass', 'assurance_date': '2026-09-30'},
        ]), 'pass')

    def test_ingestion_failure_is_not_green(self):
        self.assertEqual(self.status([], {'status': 'failed'}), 'fail')
