"""Synthetic fixed preparation controller tests; no Azure/SQL acceptance claims."""
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_owner_task_isolated_catalog as prepare
import test_owner_task_provider_launch_contract as launches
import test_owner_task_catalog_logs as logs

PRINCIPAL = '11111111-1111-1111-1111-111111111111'
CLIENT = '22222222-2222-2222-2222-222222222222'


def identity():
    return dict(id=prepare.MI, name=prepare.MI.rsplit('/', 1)[1],
        type='Microsoft.ManagedIdentity/userAssignedIdentities', location='eastasia',
        tags=prepare.identity_body()['tags'], properties=dict(principalId=PRINCIPAL, clientId=CLIENT,
            tenantId='0578a50b-a7a1-40a4-905b-8b20dba84124'))


def assignment(stage):
    return dict(id=prepare.assignment_id(stage), properties=dict(
        **prepare.role_body(stage, PRINCIPAL)['properties'], scope=prepare.ROLES[stage][0], condition=None))


class MemoryPath:
    def __init__(self, records, name=None): self.records, self.name = records, name
    def __truediv__(self, name): return MemoryPath(self.records, name)
    def exists(self): return self.name in self.records


class PreparationTests(unittest.TestCase):
    def test_source_extension_preserves_every_one_of_the_743_frozen_sources(self):
        before = {'frozen/' + str(i): 'a' * 64 for i in range(743)}
        after = dict(before, **{name: 'b' * 64 for name in prepare.ADDITIONS})
        prepare.validate_sources(before, after)
        for mutate in (lambda v: v.update({'frozen/0': 'c' * 64}), lambda v: v.pop('frozen/0'),
            lambda v: v.update({'extra.py': 'd' * 64}), lambda v: v.update({next(iter(prepare.ADDITIONS)): True})):
            value = deepcopy(after); mutate(value)
            with self.assertRaises(ValueError): prepare.validate_sources(before, value)

    def test_fixed_effects_are_one_new_identity_two_leaf_grants_and_one_idle_job(self):
        plan = prepare.launch.build(*launches.inputs())
        self.assertEqual(prepare.effect('identity', plan), (prepare.MI, '2023-01-31', prepare.identity_body()))
        for stage in ('acr', 'secret'):
            path, api, body = prepare.effect(stage, plan, PRINCIPAL)
            self.assertEqual(path, prepare.assignment_id(stage)); self.assertEqual(api, '2022-04-01')
            self.assertEqual(body, prepare.role_body(stage, PRINCIPAL))
        self.assertEqual(prepare.ROLES['secret'][0], prepare.SECRET)
        self.assertTrue(prepare.SECRET.endswith('/secrets/ownertrial-20261004-admin-url'))
        self.assertEqual(prepare.effect('job', plan)[2], plan['definition'])
        for stage in ('start', 'sql', 'delete', 'production', 'grant-owner', None, True):
            with self.assertRaises(ValueError): prepare.effect(stage, plan, PRINCIPAL)

    def test_transport_rejects_other_resource_secret_value_post_delete_and_changed_body_before_token(self):
        credential, session = Mock(), Mock(); client = prepare.Transport(credential, session)
        plan = prepare.launch.build(*launches.inputs())
        cases = [('POST', prepare.JOB + '/start', '2025-07-01', {}),
            ('DELETE', prepare.MI, '2023-01-31', {}),
            ('GET', prepare.SECRET, '7.4', {}),
            ('GET', '/foreign', '2023-01-31', {}),
            ('GET', prepare.MI, '2023-01-31', {'body': {}}),
            ('PUT', prepare.MI, '2023-01-31', {'stage': 'identity', 'body': dict(location='eastasia'), 'plan': plan}),
            ('PUT', prepare.JOB, '2025-07-01', {'stage': 'job', 'body': {}, 'plan': plan})]
        for method, path, api, kwargs in cases:
            with self.assertRaises(ValueError): client.call(method, path, api, **kwargs)
        credential.get_token.assert_not_called(); session.request.assert_not_called()

    def test_transport_no_retries_or_environment_proxy_and_closes_successful_read(self):
        client = prepare.Transport(Mock())
        self.assertFalse(client.session.trust_env)
        self.assertEqual(client.session.get_adapter('https://').max_retries.total, 0)
        client.session.close()
        response = logs.Response(identity(), 200)
        session = logs.Session([response]); credential = logs.Credential()
        value, status = prepare.Transport(credential, session).call('GET', prepare.MI, '2023-01-31')
        self.assertEqual(value, identity()); self.assertEqual(status, 200)
        self.assertTrue(response.closed)
        self.assertFalse(session.calls[0][1]['allow_redirects'])

    def test_identity_requires_exact_new_principal_tenant_scope_and_tags(self):
        prepare.identity(identity())
        for mutate in (lambda v: v.update(id=prepare.JOB), lambda v: v.update(tags={}),
            lambda v: v['properties'].update(tenantId=PRINCIPAL),
            lambda v: v['properties'].update(principalId='bad'),
            lambda v: v['properties'].update(unreviewed=True)):
            value = identity(); mutate(value)
            with self.assertRaises(ValueError): prepare.identity(value)

    def test_complete_role_inventory_rejects_inherited_broad_extra_or_conditional_grants(self):
        client = Mock()
        wanted = [assignment('acr'), assignment('secret')]
        client.call.return_value = (dict(value=deepcopy(wanted)), 200)
        self.assertEqual(prepare.role_inventory(client, PRINCIPAL, ['acr', 'secret']), wanted)
        for mutate in (lambda v: v.update(nextLink='https://foreign'),
            lambda v: v['value'].append(deepcopy(v['value'][0])),
            lambda v: v['value'][1]['properties'].update(scope=prepare.SUB_SCOPE),
            lambda v: v['value'][1]['properties'].update(principalId=CLIENT),
            lambda v: v['value'][1]['properties'].update(condition='unreviewed'),
            lambda v: v['value'][1]['properties'].update(roleDefinitionId=prepare.ROLE_BASE + 'owner')):
            value = dict(value=deepcopy(wanted)); mutate(value); client.call.return_value = value, 200
            with self.assertRaises(ValueError): prepare.role_inventory(client, PRINCIPAL, ['acr', 'secret'])


class EffectTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.records = {}
        self.inputs = launches.inputs(); self.plan = prepare.launch.build(*self.inputs)
        self.pf_raw = dict(protected_receipt=dict(sha256='a' * 64))
        self.pf = dict(foundation=dict(plan=self.plan), baseline={})
        self.stack.enter_context(patch.object(prepare, 'FOLDER', MemoryPath(self.records)))
        self.stack.enter_context(patch.object(prepare, 'frozen', return_value=(self.pf_raw, self.pf, *self.inputs[:2])))
        self.stack.enter_context(patch.object(prepare.host, 'checked_owner'))
        self.baseline = self.stack.enter_context(patch.object(prepare, 'baseline', return_value={}))
        self.reserve = self.stack.enter_context(patch.object(prepare, 'reserve', return_value=dict(synthetic=True)))
        self.save = self.stack.enter_context(patch.object(prepare, 'save', side_effect=self.save_record))
        self.stack.enter_context(patch.object(prepare, 'load', side_effect=self.load_record))
        self.roles = self.stack.enter_context(patch.object(prepare, 'role_inventory', return_value=[]))
        self.client = Mock(); self.client.call.side_effect = self.call
        self.resource = None; self.puts = []

    def save_record(self, value, name):
        if name in self.records: raise AssertionError('overwrite')
        self.records[name] = dict(deepcopy(value), protected_receipt=dict(sha256='b' * 64, blob=name))
        return deepcopy(self.records[name])

    def load_record(self, name):
        raw = deepcopy(self.records[name]); return raw, {k: v for k, v in raw.items() if k != 'protected_receipt'}

    def call(self, method, path, api, **kwargs):
        if method == 'PUT':
            self.puts.append((path, deepcopy(kwargs['body']))); self.resource = identity()
            return deepcopy(self.resource), 201
        return deepcopy(self.resource), 404 if self.resource is None else 200

    def test_single_identity_put_and_fresh_verification_then_consumed_attempt_rejection(self):
        prepare.run_stage(self.client, 'identity')
        self.assertEqual(len(self.puts), 1)
        self.assertEqual(set(self.records), {'identity-intent.json', 'identity-ack.json'})
        prepare.run_stage(self.client, 'identity', verify=True)
        accepted = self.records['identity-accepted.json']
        self.assertFalse(accepted['job_started']); self.assertEqual(accepted['sql_calls'], 0)
        self.assertFalse(accepted['fresh_restricted_login_verified'])
        result = prepare.run_stage(self.client, 'identity', verify=True)
        self.assertEqual(result['status'], 'fresh_idle_catalog_preparation_verified')
        with self.assertRaises(ValueError): prepare.run_stage(self.client, 'identity')
        self.assertEqual(len(self.puts), 1)

    def test_partial_marker_or_missing_prerequisite_denies_without_effect(self):
        self.records['identity-intent.json'] = dict(partial=True)
        with self.assertRaises(ValueError): prepare.run_stage(self.client, 'identity')
        self.reserve.assert_not_called(); self.assertEqual(self.puts, [])
        with self.assertRaises(KeyError): prepare.run_stage(self.client, 'acr')
        self.assertEqual(self.puts, [])

    def test_reservation_failure_leaves_no_put_and_no_automatic_retry(self):
        self.reserve.side_effect = ValueError('synthetic_reservation_failure')
        with self.assertRaises(ValueError): prepare.run_stage(self.client, 'identity')
        self.save.assert_not_called(); self.assertEqual(self.puts, [])

    def test_unknown_put_response_consumes_intent_and_cannot_redispatch(self):
        original = self.call
        def call(method, path, api, **kwargs):
            if method == 'PUT': self.puts.append(path); raise ValueError('synthetic_lost_ack')
            return original(method, path, api, **kwargs)
        self.client.call.side_effect = call
        with self.assertRaises(ValueError): prepare.run_stage(self.client, 'identity')
        self.assertIn('identity-intent.json', self.records)
        self.assertNotIn('identity-ack.json', self.records)
        with self.assertRaises(ValueError): prepare.run_stage(self.client, 'identity')
        self.assertEqual(len(self.puts), 1)

    def test_final_baseline_or_absence_drift_after_intent_prevents_put(self):
        self.baseline.return_value = dict(changed=True)
        with self.assertRaises(ValueError): prepare.run_stage(self.client, 'identity')
        self.assertIn('identity-intent.json', self.records); self.assertEqual(self.puts, [])

    def test_accepted_native_identity_drift_is_rejected_readonly(self):
        prepare.run_stage(self.client, 'identity'); prepare.run_stage(self.client, 'identity', verify=True)
        self.resource['properties']['clientId'] = '33333333-3333-3333-3333-333333333333'
        with self.assertRaises(ValueError): prepare.run_stage(self.client, 'identity', verify=True)
        self.assertEqual(len(self.puts), 1)


if __name__ == '__main__': unittest.main()
