"""Pure additive Azure descriptor tests; no IO effects/live acceptance."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import owner_task_isolated_sql_readback as correction
import test_owner_task_isolated_sql as fixture


class ReadbackTests(unittest.TestCase):
    def value(self):
        plan = fixture.plan(); identity = dict(principalId='11111111-1111-4111-8111-111111111111', clientId='22222222-2222-4222-8222-222222222222')
        value = copy.deepcopy(plan['definition']); value.update(id=correction.prior.JOB, name=correction.worker.JOB, type='Microsoft.App/jobs')
        value['identity']['userAssignedIdentities'][correction.prior.MI] = identity
        value['properties']['provisioningState'] = 'Succeeded'
        return value, plan, identity

    def test_accepts_absence_or_exact_empty_descriptor_without_mutating_input(self):
        value, plan, identity = self.value(); correction.validate_job(value, plan, identity)
        value['properties']['template']['containers'][0]['resources']['ephemeralStorage'] = ''
        before = copy.deepcopy(value); result = correction.validate_job(value, plan, identity)
        self.assertEqual(value, before)
        self.assertTrue(result['only_empty_ephemeral_storage_descriptor_removed'])
        self.assertEqual(result['native_job_sha256'], correction.prior.digest(value))

    def test_rejects_nonempty_null_boolean_extra_resource_and_cpu_change(self):
        for altered in ('1Gi', None, False, 0, {}):
            value, plan, identity = self.value()
            value['properties']['template']['containers'][0]['resources']['ephemeralStorage'] = altered
            with self.assertRaises(Exception): correction.validate_job(value, plan, identity)
        value, plan, identity = self.value(); value['properties']['template']['containers'][0]['resources']['cpu'] = 1
        with self.assertRaises(Exception): correction.validate_job(value, plan, identity)

    def test_rejects_extra_env_credentials_runtime_identity_and_retries(self):
        for mutation in (lambda v: v['properties']['configuration']['identitySettings'][0].update(lifecycle='All'),
            lambda v: v['properties']['configuration'].update(replicaRetryLimit=1),
            lambda v: v['properties']['template']['containers'][0]['env'].append(dict(name='PGSERVICE', value='Synthetic'))):
            value, plan, identity = self.value(); mutation(value)
            with self.assertRaises(Exception): correction.validate_job(value, plan, identity)

    def test_exact_760_plus_two_preserves_previous_sources(self):
        before = {f'old/{i}.py': 'a' * 64 for i in range(760)}
        after = dict(before, **{k: 'b' * 64 for k in correction.ADDITIONS})
        correction.validate_sources(before, after)
        for mode in ('changed', 'extra', 'missing'):
            wrong = dict(after)
            if mode == 'changed': wrong['old/0.py'] = 'c' * 64
            if mode == 'extra': wrong['other.py'] = 'd' * 64
            if mode == 'missing': wrong.pop(next(iter(correction.ADDITIONS)))
            with self.assertRaises(Exception): correction.validate_sources(before, wrong)

    def test_same_operation_folder_reservations_and_no_predecessor_global_patch(self):
        self.assertEqual(correction.FOLDER, correction.prior.FOLDER)
        self.assertEqual(correction.worker.OPERATION, correction.prior.worker.OPERATION)
        source = (ROOT / 'scripts/owner_task_isolated_sql_readback.py').read_text(encoding='utf-8')
        self.assertNotIn('setattr(', source); self.assertNotIn('prior.FOLDER =', source)
        self.assertNotIn('unlink(', source); self.assertNotIn('DROP ', source)

    def test_consumed_dispatch_stops_before_any_connection_or_load(self):
        with patch.object(Path, 'exists', return_value=True), patch.object(correction, 'guards') as guards:
            with self.assertRaises(Exception): correction.dispatch(Mock(), 'start')
            guards.assert_not_called()

    def test_explicit_full_discovery_deadlines_and_unchanged_skip_validator(self):
        responses = [SimpleNamespace(returncode=0, stdout='', stderr='complete synthetic fixture'),
            SimpleNamespace(returncode=0, stdout='PASS: synthetic fixture', stderr=''),
            SimpleNamespace(returncode=0, stdout='', stderr='')]
        with patch.object(correction.subprocess, 'run', side_effect=responses) as run, \
                patch.object(correction.prior.qa, 'validate_regression_result', return_value=(10, [])) as validate:
            result = correction.run_local_checks()
        validate.assert_called_once_with(responses[0])
        self.assertEqual(run.call_args_list[0].args[0][1:], ['-m', 'unittest', 'discover', '-s', 'tests', '-v'])
        self.assertEqual([call.kwargs['timeout'] for call in run.call_args_list], [600, 90, 90])
        self.assertTrue(all(call.kwargs['cwd'] == ROOT for call in run.call_args_list))
        self.assertEqual(result['regression_deadline_seconds'], 600)
        self.assertTrue(result['complete_discovery_required'])
        self.assertFalse(result['live_sql_tests_passed'])


if __name__ == '__main__': unittest.main()
