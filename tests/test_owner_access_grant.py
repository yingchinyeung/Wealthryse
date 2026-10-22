import copy
import unittest
from datetime import datetime, timezone

from run_owner_access_grant import TARGET_EMAIL, inspect


class OwnerAccessGrantScope(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 16, tzinfo=timezone.utc)
        def role(identity, name):
            return {'entra_object_id': identity, 'role_name': name, 'role_status': 'active',
                    'effective_from': '2026-01-01T00:00:00+00:00', 'effective_to': None}
        self.state = {
            'users': [{'entra_object_id': 'owner-1', 'email': 'owner@example.com', 'status': 'active'},
                      {'entra_object_id': 'target-1', 'email': TARGET_EMAIL.upper(), 'status': 'active'}],
            'roles': [role('owner-1', 'owner'), role('target-1', 'viewer')],
            'subjects': [], 'access': [],
        }

    def test_registered_viewer_is_eligible_alongside_one_owner(self):
        target, owners = inspect(self.state, self.now)
        self.assertEqual(target['entra_object_id'], 'target-1')
        self.assertEqual(owners, ['owner-1'])

    def test_unexpected_states_are_rejected(self):
        for kind in ('unregistered', 'disabled', 'already_owner', 'no_owner', 'two_owners', 'no_role', 'expired_role'):
            with self.subTest(kind=kind):
                state = copy.deepcopy(self.state)
                if kind == 'unregistered':
                    state['users'].pop()
                elif kind == 'disabled':
                    state['users'][1]['status'] = 'disabled'
                elif kind == 'already_owner':
                    state['roles'][1]['role_name'] = 'owner'
                elif kind == 'no_owner':
                    state['roles'][0]['role_status'] = 'revoked'
                elif kind == 'two_owners':
                    state['users'].append({'entra_object_id': 'owner-2', 'email': 'o2@example.com', 'status': 'active'})
                    state['roles'].append(dict(state['roles'][0], entra_object_id='owner-2'))
                elif kind == 'no_role':
                    state['roles'].pop()
                else:
                    state['roles'][1]['effective_to'] = '2026-02-01T00:00:00+00:00'
                with self.assertRaises(ValueError):
                    inspect(state, self.now)


if __name__ == '__main__':
    unittest.main()
