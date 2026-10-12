"""Named continuation tests: predecessor bytes/attempts remain unchanged."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import deploy_owner_task_isolated_sql_named as control
import run_owner_task_isolated_sql_named as worker
import owner_task_isolated_sql_named_launch as launch
import test_owner_task_isolated_sql as original_fixture


def plan():
    source = {k: (ROOT / k).read_bytes() for k in launch.SOURCE_PATHS}
    return launch.build(source, {k: hashlib.sha256(v).hexdigest() for k,v in source.items()}, original_fixture.runtime())


class NamedSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.plan = plan()

    def test_name_validated_before_source_runtime_or_cloud_use(self):
        self.assertEqual(len(worker.JOB), 25)
        for name in ('x'*33, 'Upper', 'a--b', 'a-', '', '-a'):
            with patch.object(worker, 'JOB', name), self.assertRaises(Exception): launch.build({}, {}, {})

    def test_original_worker_behavior_unchanged_except_new_identity_declarations(self):
        old = (ROOT / 'scripts/run_owner_task_isolated_sql.py').read_text(encoding='utf-8')
        expected = old.replace(control.predecessor.worker.OPERATION, worker.OPERATION)
        expected = expected.replace(control.predecessor.worker.JOB, worker.JOB)
        expected = expected.replace(control.predecessor.worker.PREFIX, worker.PREFIX)
        expected = expected.replace('Trusted synthetic installer/restricted-LOGIN acceptance on ONE private host.',
            'Separately named synthetic SQL worker after a rejected 33-character job name.')
        self.assertEqual((ROOT / 'scripts/run_owner_task_isolated_sql_named.py').read_text(encoding='utf-8'), expected)
        self.assertNotEqual(worker.OPERATION, control.predecessor.worker.OPERATION)
        self.assertEqual(worker.IDENTITY_ID, control.predecessor.worker.IDENTITY_ID)
        self.assertEqual(worker.target.DATABASE, control.predecessor.target.DATABASE)

    def test_distinct_physical_job_source_bundle_and_consumed_namespace(self):
        p = self.plan
        self.assertEqual(len(p['source_hashes']), 14)
        self.assertIn('scripts/run_owner_task_isolated_sql_named.py', p['source_hashes'])
        self.assertNotIn('scripts/run_owner_task_isolated_sql.py', p['source_hashes'])
        self.assertNotEqual(control.FOLDER, control.predecessor.FOLDER)
        self.assertNotEqual(control.PREFIX, control.predecessor.PREFIX)
        self.assertNotEqual(control.JOB, control.predecessor.JOB)
        self.assertLessEqual(len(launch.canonical(p['definition']['properties']['template']['containers'][0]['args'])), 65536)

    def test_manual_pinned_one_secret_no_runtime_identity_or_retry(self):
        p = self.plan; d = p['definition']; config = d['properties']['configuration']
        self.assertEqual(config['triggerType'], 'Manual')
        self.assertEqual(config['replicaRetryLimit'], 0)
        self.assertEqual(config['identitySettings'], [{'identity': worker.IDENTITY_ID, 'lifecycle': 'None'}])
        self.assertEqual(config['replicaTimeout'], 600)
        self.assertEqual(config['secrets'], control.predecessor.launch.build(
            {k:(ROOT/k).read_bytes() for k in control.predecessor.launch.SOURCE_PATHS},
            {k:hashlib.sha256((ROOT/k).read_bytes()).hexdigest() for k in control.predecessor.launch.SOURCE_PATHS},
            original_fixture.runtime())['definition']['properties']['configuration']['secrets'])
        self.assertFalse(p['step_2_complete'])

    def test_only_job_start_effects_reuse_existing_identity_no_grants(self):
        self.assertEqual(control.STAGES, ('job', 'start'))
        for stage in ('identity', 'acr', 'secret', 'delete', None):
            with self.assertRaises(Exception): control.effect(stage, self.plan)
        self.assertEqual(control.effect('job', self.plan)[:3], ('PUT', control.JOB, '2025-07-01'))
        self.assertEqual(control.effect('start', self.plan)[:3], ('POST', control.JOB+'/start', '2025-07-01'))

    def test_exact_additions_preserve_all_762_reviewed_sources(self):
        before = {f'old/{i}.py':'a'*64 for i in range(762)}
        after = dict(before, **{k:'b'*64 for k in control.ADDITIONS})
        control.validate_sources(before, after)
        after['old/0.py'] = 'c'*64
        with self.assertRaises(Exception): control.validate_sources(before, after)

    def test_native_empty_resource_alias_does_not_allow_substantive_drift(self):
        identity = dict(principalId='11111111-1111-4111-8111-111111111111', clientId='22222222-2222-4222-8222-222222222222')
        value = copy.deepcopy(self.plan['definition'])
        value.update(id=control.JOB, name=worker.JOB, type='Microsoft.App/jobs')
        value['identity']['userAssignedIdentities'][control.MI] = identity
        value['properties']['provisioningState'] = 'Succeeded'
        value['properties']['template']['containers'][0]['resources']['ephemeralStorage'] = ''
        before = copy.deepcopy(value)
        control.validate_job(value, self.plan, identity); self.assertEqual(value, before)
        value['properties']['template']['containers'][0]['resources']['ephemeralStorage'] = '1Gi'
        with self.assertRaises(Exception): control.validate_job(value, self.plan, identity)

    def test_consumed_attempt_not_replayed(self):
        with patch.object(Path, 'exists', return_value=True), patch.object(control, 'load') as load:
            with self.assertRaises(Exception): control.dispatch(Mock(), 'job')
            load.assert_not_called()

    def test_unknown_cloud_route_and_frozen_job_mutation_rejected_before_credentials(self):
        client = control.Transport()
        client.credential = Mock()
        for method,path in (('DELETE',control.JOB), ('PUT',control.predecessor.JOB), ('GET',control.target.VAULT+'/secrets/unknown')):
            with self.assertRaises(Exception): client.call(method,path,'2025-07-01',stage='job',plan=self.plan,body={})
        client.credential.get_token.assert_not_called()


if __name__ == '__main__': unittest.main()
