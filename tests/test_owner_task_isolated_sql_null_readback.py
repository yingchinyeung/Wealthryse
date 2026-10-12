"""Only absent/null descriptors accepted; no effects or predecessor mutation."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import owner_task_isolated_sql_null_readback as correction
import test_owner_task_isolated_sql_named as fixture


class NullReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.plan=fixture.plan()

    def value(self):
        value=copy.deepcopy(self.plan['definition'])
        identity=dict(principalId='11111111-1111-4111-8111-111111111111',clientId='22222222-2222-4222-8222-222222222222')
        value.update(id=correction.prior.JOB,name=correction.worker.JOB,type='Microsoft.App/jobs')
        value['identity']['userAssignedIdentities'][correction.prior.MI]=identity
        value['properties']['provisioningState']='Succeeded'
        return value,identity

    def test_absent_and_only_exact_null_defaults_preserve_native(self):
        value,identity=self.value();correction.validate_job(value,self.plan,identity)
        value['properties']['template'].update(initContainers=None,volumes=None)
        before=copy.deepcopy(value);r=correction.validate_job(value,self.plan,identity)
        self.assertEqual(before,value);self.assertTrue(r['only_null_execution_descriptors_removed'])
        self.assertEqual(r['native_job_sha256'],correction.prior.digest(value))

    def test_empty_or_nonempty_lists_objects_boolean_and_strings_rejected(self):
        for field in ('initContainers','volumes'):
            for payload in ([],[{}],{},False,''):
                value,identity=self.value();value['properties']['template'][field]=payload
                with self.assertRaises(Exception):correction.validate_job(value,self.plan,identity)

    def test_extra_mount_env_credential_cpu_or_template_field_rejected(self):
        for mutate in (lambda t:t.update(extra=None),lambda t:t['containers'][0].update(volumeMounts=[]),
            lambda t:t['containers'][0]['env'].append(dict(name='PGPASSWORD',value='Synthetic')),
            lambda t:t['containers'][0]['resources'].update(cpu=1)):
            value,identity=self.value();mutate(value['properties']['template'])
            with self.assertRaises(Exception):correction.validate_job(value,self.plan,identity)

    def test_exact_additive_sources_preserve_all_766_frozen_hashes(self):
        before={f'old/{i}.py':'a'*64 for i in range(766)}
        after=dict(before,**{k:'b'*64 for k in correction.ADDITIONS})
        correction.validate_sources(before,after);after['old/0.py']='c'*64
        with self.assertRaises(Exception):correction.validate_sources(before,after)

    def test_start_consumed_before_any_load_or_network(self):
        with patch.object(Path,'exists',return_value=True),patch.object(correction,'guards') as guards:
            with self.assertRaises(Exception):correction.start(Mock())
            guards.assert_not_called()

    def test_same_physical_job_operation_and_reservation_no_install_or_reset(self):
        self.assertEqual(correction.FOLDER,correction.prior.FOLDER)
        self.assertEqual(correction.worker.OPERATION,correction.prior.worker.OPERATION)
        code=(ROOT/'scripts/owner_task_isolated_sql_null_readback.py').read_text()
        self.assertNotIn('setattr(',code);self.assertNotIn('prior.FOLDER =',code)
        self.assertNotIn('unlink(',code);self.assertNotIn("effect('job'",code)
        self.assertIn("prior.reserve('start',value)",code)


if __name__=='__main__':unittest.main()
