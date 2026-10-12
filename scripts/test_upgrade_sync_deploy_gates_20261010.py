import copy
import unittest
from deploy_upgrade_sync_security_20261010 import accept, CHECKS


class Gates(unittest.TestCase):
    def fixture(self):
        return {'passed': True, 'image': 'exact', 'job_settings_preserved': True, 'proof': {'passed': True, 'checks': sorted(CHECKS), 'public_tables_checked': 92, 'public_table_differences': [], 'temporary_objects_dropped_on_disconnect': True, 'production_connections': 0, 'production_writes': 0, 'persistent_clone_writes': 0, 'broker_calls': 0}}
    def test_complete(self): accept(self.fixture(), 'exact')
    def test_wrong_image(self):
        with self.assertRaises(ValueError): accept(self.fixture(), 'other')
    def test_every_write_or_production_access_blocks(self):
        for key in ('production_connections', 'production_writes', 'persistent_clone_writes', 'broker_calls'):
            p = self.fixture(); p['proof'][key] = 1
            with self.assertRaises(ValueError): accept(p, 'exact')
    def test_missing_duplicate_or_failed_replay_blocks(self):
        for changes in ({'checks': sorted(CHECKS)[:-1]}, {'checks': sorted(CHECKS) + [sorted(CHECKS)[0]]}, {'passed': False}):
            p = self.fixture(); p['proof'].update(changes)
            with self.assertRaises(ValueError): accept(p, 'exact')
    def test_preservation_and_cleanup_required(self):
        for changes in ({'public_tables_checked': 91}, {'public_table_differences': ['cash_snapshots']}, {'temporary_objects_dropped_on_disconnect': False}):
            p = self.fixture(); p['proof'].update(changes)
            with self.assertRaises(ValueError): accept(p, 'exact')


if __name__ == '__main__': unittest.main()
