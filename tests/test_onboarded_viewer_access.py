import copy
import unittest
from datetime import datetime, timezone

from run_onboarded_viewer_access import SUBJECTS, choose_viewers, missing_access


class ViewerScope(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        self.state = {
            'users': [{'entra_object_id': 'registered-viewer', 'status': 'active'},
                      {'entra_object_id': 'disabled-viewer', 'status': 'disabled'},
                      {'entra_object_id': 'expired-viewer', 'status': 'active'}],
            'roles': [{'entra_object_id': identity, 'role_name': 'viewer', 'role_status': 'active',
                       'effective_from': '2026-01-01T00:00:00+00:00',
                       'effective_to': '2026-10-01T00:00:00+00:00' if identity == 'expired-viewer' else None}
                      for identity in ('registered-viewer', 'disabled-viewer', 'expired-viewer')],
            'subjects': [{'subject_id': subject, 'lifecycle_status': 'active'} for subject in SUBJECTS],
            'features': [{'subject_id': subject, 'feature_key': key, 'feature_state': 'active'}
                         for subject in SUBJECTS for key in ('viewer_access', 'website_analytics')],
            'access': [],
        }

    def test_only_active_effective_existing_viewers(self):
        viewers = choose_viewers(self.state, self.now)
        self.assertEqual([v['entra_object_id'] for v in viewers], ['registered-viewer'])
        self.assertEqual(missing_access(self.state, viewers), [('registered-viewer', s) for s in SUBJECTS])

    def test_existing_read_and_higher_grants_are_preserved(self):
        for level in ('read', 'report', 'analyze', 'approve', 'admin'):
            with self.subTest(level=level):
                state = copy.deepcopy(self.state)
                state['access'] = [{'entra_object_id': 'registered-viewer', 'subject_id': s, 'access_level': level} for s in SUBJECTS]
                before = copy.deepcopy(state)
                self.assertEqual(missing_access(state, choose_viewers(state, self.now)), [])
                self.assertEqual(state, before)

    def test_unapproved_subject_or_feature_state_is_rejected(self):
        for kind in ('extra', 'inactive', 'feature', 'nonreader', 'no_viewer'):
            with self.subTest(kind=kind):
                state = copy.deepcopy(self.state)
                if kind == 'extra':
                    state['subjects'].append({'subject_id': 'unapproved-account', 'lifecycle_status': 'active'})
                elif kind == 'inactive':
                    state['subjects'][0]['lifecycle_status'] = 'onboarding'
                elif kind == 'feature':
                    state['features'][0]['feature_state'] = 'disabled'
                elif kind == 'nonreader':
                    state['access'].append({'entra_object_id': 'registered-viewer', 'subject_id': SUBJECTS[0], 'access_level': 'onboard'})
                else:
                    state['roles'] = []
                with self.assertRaises(ValueError):
                    missing_access(state, choose_viewers(state, self.now))


if __name__ == '__main__':
    unittest.main()
