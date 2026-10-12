import copy
from datetime import datetime, timezone
import unittest
from deploy_upgrade_metadata_security_20261010 import accept, schedule_window
from control_upgrade_metadata_runtime_20261010 import validate_completion


class MetadataGuardTests(unittest.TestCase):
    def setUp(self):
        self.proof = {'kind': 'complete', 'mode': 'shadow', 'access_check': 'passed',
                      'inventory_complete': True, 'denied_column_checks': 7,
                      'financial_reconciliation': 'not_certified', 'records': 1, 'captured_at': 'now'}
        self.rows = [{'kind': 'risk', 'index': 0, 'captured_at': 'now'}]
        self.record = {'passed': True, 'image': 'candidate', 'saved_settings_preserved': True,
                       'read_only_runtime': True, 'financial_writes': 0,
                       'proof': self.proof, 'rows': self.rows}
    def test_complete_read_only_acceptance(self):
        accept(self.record, 'candidate')
    def test_wrong_image_or_writes_fail(self):
        with self.assertRaises(ValueError): accept(self.record, 'other')
        self.record['financial_writes'] = 1
        with self.assertRaises(ValueError): accept(self.record, 'candidate')
    def test_missing_or_duplicate_records_fail(self):
        for rows in ([], self.rows * 2, [{**self.rows[0], 'index': 1}]):
            with self.assertRaises(ValueError): validate_completion(self.proof, rows)
    def test_scope_and_access_fail_closed(self):
        for key, value in (('mode', 'active'), ('access_check', 'failed'), ('inventory_complete', False),
                           ('denied_column_checks', 6), ('financial_reconciliation', 'certified')):
            proof = {**self.proof, key: value}
            with self.assertRaises(ValueError): validate_completion(proof, self.rows)
    def test_wrong_timestamp_or_kind_fails(self):
        for key, value in (('captured_at', 'other'), ('kind', 'error')):
            with self.assertRaises(ValueError): validate_completion(self.proof, [{**self.rows[0], key: value}])
    def test_scheduler_guard(self):
        for minute in (3, 7, 11, 33, 37, 41):
            with self.assertRaises(ValueError): schedule_window(datetime(2026, 10, 10, 1, minute, tzinfo=timezone.utc))
        for minute in (0, 15, 25, 45, 55):
            schedule_window(datetime(2026, 10, 10, 1, minute, tzinfo=timezone.utc))


if __name__ == '__main__': unittest.main()
