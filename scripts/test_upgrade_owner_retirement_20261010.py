import copy, json, unittest
from retire_upgrade_owner_jobs_20261010 import APPROVED, OUT, target, validate_plan, preserve_others, restore_body, assert_manual_idle


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((OUT / 'plan.json').read_bytes())
        self.qualified = {'passed': True, 'plan_receipt': self.plan['protected_receipt']}

    def test_exact_22_only(self):
        self.assertEqual(len(APPROVED), 22); validate_plan(self.plan, self.qualified)
        self.assertNotIn('ibkrv2stg-bootstrap', APPROVED)

    def test_wrong_name_or_type_rejected(self):
        for name in ('ibkrv2stg-web', 'ibkrv2stg-bootstrap', 'ibkrv2stg-onboard-render', 'ibkrv2stg-own-new'):
            entry = dict(self.plan['jobs'][0], name=name)
            with self.assertRaises(ValueError): target(entry)
        entry = dict(self.plan['jobs'][0], id=self.plan['jobs'][0]['id'].replace('/jobs/', '/containerApps/'))
        with self.assertRaises(ValueError): target(entry)

    def test_missing_or_duplicate_target_rejected(self):
        p = copy.deepcopy(self.plan); p['jobs'][-1] = p['jobs'][0]
        with self.assertRaises(ValueError): validate_plan(p, self.qualified)

    def test_dependency_and_approval_binding_required(self):
        for q in ({'passed': False, 'plan_receipt': self.plan['protected_receipt']}, {'passed': True, 'plan_receipt': {}}):
            with self.assertRaises(ValueError): validate_plan(self.plan, q)
        self.plan['owner_authorization'] = 'unapproved'
        with self.assertRaises(ValueError): validate_plan(self.plan, self.qualified)

    def test_other_workload_drift_rejected(self):
        preserve_others(self.plan['non_target_bindings'], self.plan)
        actual = copy.deepcopy(self.plan['non_target_bindings']); actual['ibkrv2stg-web']['images'] = ['different']
        with self.assertRaises(ValueError): preserve_others(actual, self.plan)

    def test_restore_preserves_configuration_and_identity_ids(self):
        for j in self.plan['jobs']:
            body = restore_body(j)
            self.assertEqual(body['properties']['configuration'], j['definition']['properties']['configuration'])
            self.assertEqual(body['properties']['template'], j['definition']['properties']['template'])
            self.assertEqual(set(body['identity']['userAssignedIdentities']), set(j['definition']['identity']['userAssignedIdentities']))

    def test_active_or_scheduled_cannot_be_retired(self):
        job = copy.deepcopy(self.plan['jobs'][0]['definition'])
        with self.assertRaises(ValueError): assert_manual_idle(job, [{'properties': {'status': 'Running'}}])
        job['properties']['configuration']['triggerType'] = 'Schedule'
        with self.assertRaises(ValueError): assert_manual_idle(job, [])


if __name__ == '__main__': unittest.main()
