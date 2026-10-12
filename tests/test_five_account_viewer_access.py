import copy
import unittest
from datetime import datetime, timezone

from run_five_account_viewer_access import NEW_SUBJECT, SUBJECTS, approved_scope, choose_viewers, missing_access


class FiveAccountViewerScope(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        viewers = ['viewer-%d' % n for n in range(4)]
        self.state = {
            'users': [{'entra_object_id': identity, 'status': 'active'} for identity in viewers]
                     + [{'entra_object_id': 'disabled-viewer', 'status': 'disabled'}],
            'roles': [{'entra_object_id': identity, 'role_name': 'viewer', 'role_status': 'active',
                       'effective_from': '2026-01-01T00:00:00+00:00', 'effective_to': None}
                      for identity in viewers + ['disabled-viewer']],
            'subjects': [{'subject_id': subject, 'lifecycle_status': 'active'} for subject in SUBJECTS],
            'features': [{'subject_id': subject, 'feature_key': key, 'feature_state': 'active'}
                         for subject in SUBJECTS for key in ('viewer_access', 'website_analytics')],
            'access': [{'entra_object_id': identity, 'subject_id': subject, 'access_level': 'read'}
                       for identity in viewers for subject in SUBJECTS if subject != NEW_SUBJECT],
        }

    def test_exactly_one_new_subject_grant_per_existing_viewer(self):
        viewers = choose_viewers(self.state, self.now)
        missing = missing_access(self.state, viewers)
        self.assertEqual(missing, [('viewer-%d' % n, NEW_SUBJECT) for n in range(4)])
        approved_scope(viewers, missing)

    def test_other_viewer_counts_or_subjects_are_rejected(self):
        for kind in ('fifth_viewer', 'three_viewers', 'other_subject_missing'):
            with self.subTest(kind=kind):
                state = copy.deepcopy(self.state)
                if kind == 'fifth_viewer':
                    state['users'][-1]['status'] = 'active'
                elif kind == 'three_viewers':
                    state['users'][0]['status'] = 'disabled'
                else:
                    state['access'] = [row for row in state['access']
                                       if (row['entra_object_id'], row['subject_id']) != ('viewer-0', 'alpha-world-ms')]
                viewers = choose_viewers(state, self.now)
                with self.assertRaises(ValueError):
                    approved_scope(viewers, missing_access(state, viewers))

    def test_inactive_subject_or_feature_is_rejected(self):
        for kind in ('extra', 'inactive', 'feature', 'nonreader'):
            with self.subTest(kind=kind):
                state = copy.deepcopy(self.state)
                if kind == 'extra':
                    state['subjects'].append({'subject_id': 'unapproved-account', 'lifecycle_status': 'active'})
                elif kind == 'inactive':
                    next(s for s in state['subjects'] if s['subject_id'] == NEW_SUBJECT)['lifecycle_status'] = 'onboarding'
                elif kind == 'feature':
                    next(f for f in state['features'] if f['subject_id'] == NEW_SUBJECT)['feature_state'] = 'disabled'
                else:
                    state['access'].append({'entra_object_id': 'viewer-0', 'subject_id': NEW_SUBJECT, 'access_level': 'onboard'})
                with self.assertRaises(ValueError):
                    missing_access(state, choose_viewers(state, self.now))


if __name__ == '__main__':
    unittest.main()
