"""Offline trusted-controller gates; never exercise Azure or certify live SQL."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import qualify_owner_task_aca_runtime as controller
import owner_task_aca_runtime_contract as contract
import owner_task_aca_runtime_transport as transport
from test_owner_task_aca_runtime_contract import descriptor, source

PRINCIPAL = '824dd8fe-7c16-45ac-8d6b-571779872bc9'
CLIENT = '1a2715b3-f843-42a2-b4e6-f49ee5fd3c11'
EXECUTION = contract.JOB + '-abcdefgh'
REPLICA = EXECUTION + '-abcde'
RECEIPT = {'sha256': 'a' * 64, 'blob': 'test-only', 'version_id': '2026-10-04T18:00:00.0000000Z', 'protected_readback_verified': True}


def observed():
    return dict(operation_id=contract.PROBE_FAMILY_ID, image=contract.IMAGE,
        runtime_source_sha256=contract.RUNTIME_SOURCE_SHA256, runtime=descriptor())


def pf():
    return dict(prerequisite={'expected_runtime': descriptor(), 'observed': observed()},
        plan=contract.build(source(), descriptor()), sources={'test-only': 'a' * 64})


def execution(*, terminal=True):
    return dict(id=contract.JOB_ID + '/executions/' + EXECUTION, name=EXECUTION, type='Microsoft.App/jobs/executions',
        properties=dict(status='Succeeded' if terminal else 'Running', startTime='2026-10-04T18:00:00.0000000Z',
            endTime='2026-10-04T18:02:01.0000000Z' if terminal else None,
            template=controller.start_body(pf())))


def assignment():
    return dict(id=transport.ASSIGNMENT_ID, name=transport.ASSIGNMENT, type='Microsoft.Authorization/roleAssignments',
        properties=dict(principalId=PRINCIPAL, principalType='ServicePrincipal', roleDefinitionId=transport.ROLE_ID,
            scope=contract.REGISTRY_ID))


def permission(*, granted=True):
    return dict(subscription=[assignment()] if granted else [], resource_group_inherited=[],
        registry_inherited=[assignment()] if granted else [], observed_arm_inventory_complete=True,
        tenant_permission_closure_verified=False)


class SourceTests(unittest.TestCase):
    def test_exact_719_plus_six_only_old_hashes_unchanged(self):
        old = {'file-' + str(i): 'a' * 64 for i in range(719)}
        now = dict(old, **{name: 'b' * 64 for name in controller.ADDITIONS})
        controller.validate_source_extension(old, now)
        for change in ('extra', 'missing', 'changed', 'wronghash'):
            altered = dict(now)
            if change == 'extra': altered['other.py'] = 'c' * 64
            elif change == 'missing': del altered[next(iter(controller.ADDITIONS))]
            elif change == 'changed': altered['file-1'] = 'c' * 64
            else: altered[next(iter(controller.ADDITIONS))] = True
            with self.assertRaises(ValueError): controller.validate_source_extension(old, altered)

    def test_runtime_bytes_and_plan_are_pinned(self):
        self.assertEqual(source(), controller.source_bytes())
        value = controller.plan({'expected_runtime': descriptor()})
        self.assertEqual(contract.IMAGE, value['image'])
        self.assertEqual(120, value['hold_seconds'])
        self.assertEqual([{'identity': contract.IDENTITY_ID, 'lifecycle': 'None'}],
            value['definition']['properties']['configuration']['identitySettings'])
        self.assertEqual([], value['definition']['properties']['configuration']['secrets'])

    def test_start_schema_drops_only_empty_job_volumes(self):
        before = pf(); result = controller.start_body(before)
        self.assertEqual({'containers', 'initContainers'}, set(result))
        self.assertEqual([], before['plan']['definition']['properties']['template']['volumes'])
        before['plan']['definition']['properties']['template']['volumes'] = [{'name': 'forbidden'}]
        with self.assertRaises(ValueError): controller.start_body(before)

    def test_future_authority_and_security_proofs_remain_false(self):
        for key in ('tls_connection_verified', 'provider_semantics_verified', 'owner_runtime_active',
                'model_credential_containment_verified', 'tenant_wide_permission_closure_verified',
                'independently_observed_aca_engine_digest', 'managed_identity_unavailability_behaviorally_verified',
                'six_specialist_runtimes_active', 'ibkr_source_writes'):
            self.assertIn(key, controller.FALSE_FLAGS)


class RoleTests(unittest.TestCase):
    def test_exact_registry_grant_and_rg_ancestor_empty(self):
        with patch.object(transport, 'role_inventory', return_value=permission()), patch.object(controller, 'get', return_value={}), \
                patch.object(transport, 'validate_role_definition'), patch.object(transport, 'validate_assignment') as validate:
            result = controller.roles(Mock(), PRINCIPAL, granted=True)
        self.assertEqual(2, validate.call_count)
        self.assertTrue(result['registered_subscription_assignment_inventory_verified'])
        self.assertFalse(result['tenant_wide_permission_closure_verified'])

    def test_empty_new_identity_inventory_before_grant(self):
        with patch.object(transport, 'role_inventory', return_value=permission(granted=False)), patch.object(controller, 'get', return_value={}), \
                patch.object(transport, 'validate_role_definition'):
            controller.roles(Mock(), PRINCIPAL, granted=False)

    def test_group_grants_extra_scopes_boolean_flags_incomplete_inventory_refused(self):
        for change in ('rg', 'duplicate', 'incomplete', 'closure', 'missing', 'unknown'):
            p = permission()
            if change == 'rg': p['resource_group_inherited'] = [assignment()]
            elif change == 'duplicate': p['registry_inherited'].append(assignment())
            elif change == 'incomplete': p['observed_arm_inventory_complete'] = 1
            elif change == 'closure': p['tenant_permission_closure_verified'] = 0
            elif change == 'missing': del p['subscription']
            else: p['other_subscriptions'] = []
            with patch.object(transport, 'role_inventory', return_value=p), patch.object(controller, 'get', return_value={}), \
                    patch.object(transport, 'validate_role_definition'), self.assertRaises(ValueError):
                controller.roles(Mock(), PRINCIPAL, granted=True)


class ExecutionTests(unittest.TestCase):
    def test_exact_native_running_and_terminal_template(self):
        self.assertEqual('Succeeded', controller.validate_execution(execution(), pf(), EXECUTION, terminal=True)['status'])
        self.assertEqual('Running', controller.validate_execution(execution(terminal=False), pf(), EXECUTION, terminal=False)['status'])

    def test_altered_job_status_times_secret_sidecar_unknown_shape_refused(self):
        for change in ('identity', 'status', 'window', 'template', 'extra', 'timezone', 'ended-running'):
            v = execution(terminal=change != 'ended-running')
            if change == 'identity': v['id'] += '-other'
            elif change == 'status': v['properties']['status'] = 'Completed'
            elif change == 'window': v['properties']['endTime'] = '2026-10-04T18:20:00Z'
            elif change == 'template': v['properties']['template']['containers'][0]['env'] = [{'name': 'MODEL_KEY', 'value': 'forbidden'}]
            elif change == 'extra': v['properties']['unexpected'] = True
            elif change == 'timezone': v['properties']['startTime'] = '2026-10-04T18:00:00+08:00'
            else: v['properties']['endTime'] = '2026-10-04T18:02:01Z'
            with self.assertRaises(ValueError): controller.validate_execution(v, pf(), EXECUTION, terminal=change != 'ended-running')

    def test_native_inventory_empty_one_only_no_hidden_page_or_duplicate(self):
        for value in ({'value': []}, {'value': [execution()], 'nextLink': None}):
            with patch.object(controller, 'get', return_value=value): self.assertEqual(value, controller.inventory(Mock()))
        for value in ({'value': [execution(), execution()]}, {'value': [], 'nextLink': 'more'}, {'value': [] , 'extra': True}, {'value': None}):
            with patch.object(controller, 'get', return_value=value), self.assertRaises(ValueError): controller.inventory(Mock())

    def test_start_200_exact_ack_and_non_200_unknown_consumed_inspect_only(self):
        base = dict(raw_ack={'id': contract.JOB_ID + '/executions/' + EXECUTION, 'name': EXECUTION}, http_status=200)
        with patch.object(controller, 'load', return_value=({'protected_receipt': RECEIPT}, {})), \
                patch.object(controller, 'chain', return_value=({'protected_receipt': RECEIPT}, base)):
            self.assertEqual(EXECUTION, controller.start_chain({'protected_receipt': RECEIPT}, pf())[1])
        for bad in (dict(base, http_status=202), dict(base, raw_ack={}), dict(base, raw_ack=dict(base['raw_ack'], unexpected='x'))):
            with patch.object(controller, 'load', return_value=({'protected_receipt': RECEIPT}, {})), \
                    patch.object(controller, 'chain', return_value=({'protected_receipt': RECEIPT}, bad)), self.assertRaises(ValueError):
                controller.start_chain({'protected_receipt': RECEIPT}, pf())


class ConsoleTests(unittest.TestCase):
    def row(self, native=None):
        return dict(Group=REPLICA, Container=contract.CONTAINER, LogTime='2026-10-04T18:00:01Z',
            Log=contract.PREFIX + json.dumps(native or observed(), sort_keys=True, separators=(',', ':')))

    def test_exact_full_record_only_uses_captured_native_replica(self):
        self.assertEqual(observed(), controller.parse_console([self.row()], {'name': REPLICA}, observed()))

    def test_full_record_native_numeric_aliases_duplicates_and_extra_events_refused(self):
        for key, altered in (('sql_calls', False), ('libpq_version', 180006.0), ('owner_runtime_active', 0), ('effective_uid', True)):
            v = observed(); v['runtime'][key] = altered
            with self.assertRaises(ValueError): controller.parse_console([self.row(v)], {'name': REPLICA}, observed())
        for rows in ([], [self.row(), self.row()], [dict(self.row(), Group=EXECUTION + '-other')],
                [dict(self.row(), Container='other')], [dict(self.row(), Log=self.row()['Log'] + '\n')],
                [dict(self.row(), Log=contract.PREFIX + '{"runtime":{},"runtime":{}}')]):
            with self.assertRaises(ValueError): controller.parse_console(rows, {'name': REPLICA}, observed())


class EffectTests(unittest.TestCase):
    def test_start_immediate_capture_is_read_only_and_pending_does_not_redispatch(self):
        p = pf(); identity = {'properties': {'principalId': PRINCIPAL}}
        ack = dict(http_status=200, raw_ack={'id': contract.JOB_ID + '/executions/' + EXECUTION, 'name': EXECUTION}, protected_receipt=RECEIPT)
        with patch.object(controller, 'prepared', return_value=({'protected_receipt': RECEIPT}, p)), patch.object(controller, 'arm'), \
                patch.object(controller, 'install_chain', return_value=({'protected_receipt': RECEIPT}, {}, identity, {})), \
                patch.object(controller, 'inventory', return_value={'value': []}), patch.object(controller, 'roles'), \
                patch.object(controller, 'mutate', return_value=ack) as effect, \
                patch.object(controller, 'capture_bound', side_effect=ValueError('native_not_ready')) as capture, patch('builtins.print'):
            self.assertEqual(ack, controller.start())
        effect.assert_called_once(); capture.assert_called_once_with(p, ack)

    def test_fast_capture_uses_native_running_replica_and_preserves_ack_binding(self):
        p = pf(); native = execution(terminal=False)
        ack = dict(http_status=200, raw_ack={'id': contract.JOB_ID + '/executions/' + EXECUTION, 'name': EXECUTION}, protected_receipt=RECEIPT)
        with patch.object(controller.previous, 'sources', return_value=p['sources']), patch.object(controller, 'arm'), \
                patch.object(controller, 'get', side_effect=[native, {}, native]), \
                patch.object(transport, 'validate_replica', return_value={'name': REPLICA}), patch.object(controller, 'save', side_effect=lambda v, n: v):
            captured = controller.capture_bound(p, ack)
        self.assertEqual('native_running_replica_binding_observed', captured['status'])
        self.assertEqual(RECEIPT, captured['start_ack_receipt'])
        self.assertEqual({'name': REPLICA}, captured['binding'])
        for key in controller.FALSE_FLAGS: self.assertIs(captured[key], False)

    def test_only_six_sources_selfplan_and_no_cloud_reads_in_fast_capture_prepared(self):
        value = pf(); value.update(status='prepared_secretless_aca_runtime', operation_id=controller.OPERATION_ID,
            local_checks={}, **{key: False for key in controller.FALSE_FLAGS})
        value['prerequisite']['frozen_sources'] = {}
        with patch.object(controller, 'load', return_value=({}, value)), patch.object(controller.previous, 'sources', return_value=value['sources']), \
                patch.object(controller, 'validate_source_extension'), patch.object(controller.previous.q.canary, 'validate_correction_checks'), \
                patch.object(controller, 'resource_guards', side_effect=AssertionError('must not block running capture')):
            controller.prepared(cloud=False)

    def test_each_effect_reservation_intent_readback_fresh_check_precedes_exact_request(self):
        events = []; value = pf(); caller = Mock()
        caller.request.side_effect = lambda *a, **k: (events.append('request') or ({'observed': True}, 201))
        def save(v, name): events.append(name); return dict(v, protected_receipt=RECEIPT)
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller, 'reserve', side_effect=lambda *a: (events.append('reserve') or RECEIPT)), \
                patch.object(controller, 'save', side_effect=save), patch.object(controller, 'reservation_readback', side_effect=lambda *a: events.append('readback')), \
                patch.object(controller.previous, 'sources', return_value=value['sources']), patch.object(controller, 'arm', return_value=caller), \
                patch.object(controller, 'pre_effect_checks', side_effect=lambda *a: events.append('freshchecks')):
            result = controller.mutate('identity', {'protected_receipt': RECEIPT}, value, contract.IDENTITY_ID, transport.MI_API,
                'PUT', transport.identity_body(), {})
        self.assertEqual(['reserve', 'identity-intent.json', 'readback', 'freshchecks', 'request', 'identity-ack.json'], events)
        self.assertTrue(result['attempt_consumed']); self.assertFalse(result['automatic_retry'])

    def test_unknown_effect_retains_fixed_sanitized_consumed_outcome_once(self):
        caller = Mock(); caller.request.side_effect = RuntimeError('secret-value-must-never-be-retained')
        saved = []
        def save(value, name): saved.append((value, name)); return dict(value, protected_receipt=RECEIPT)
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), patch.object(controller, 'reserve', return_value=RECEIPT), \
                patch.object(controller, 'save', side_effect=save), patch.object(controller, 'reservation_readback'), \
                patch.object(controller.previous, 'sources', return_value=pf()['sources']), patch.object(controller, 'arm', return_value=caller), \
                patch.object(controller, 'pre_effect_checks'), self.assertRaisesRegex(ValueError, 'effect_unknown_attempt_consumed_inspect_only'):
            controller.mutate('identity', {'protected_receipt': RECEIPT}, pf(), contract.IDENTITY_ID, transport.MI_API, 'PUT', transport.identity_body(), {})
        self.assertEqual(1, caller.request.call_count)
        self.assertEqual('identity-outcome.json', saved[-1][1]); self.assertNotIn('secret-value', json.dumps(saved))
        self.assertFalse(saved[-1][0]['no_effects_verified']); self.assertFalse(saved[-1][0]['automatic_retry'])

    def test_existing_local_intent_ack_or_outcome_blocks_before_any_reservation_or_cloud(self):
        for suffix in ('intent', 'ack', 'outcome'):
            with tempfile.TemporaryDirectory() as folder:
                Path(folder, 'identity-' + suffix + '.json').touch()
                with patch.object(controller, 'FOLDER', Path(folder)), patch.object(controller, 'reserve') as reserve, \
                        patch.object(controller, 'arm') as client, self.assertRaisesRegex(ValueError, 'effect_consumed_inspect_only'):
                    controller.mutate('identity', {'protected_receipt': RECEIPT}, pf(), contract.IDENTITY_ID, transport.MI_API, 'PUT', transport.identity_body(), {})
                reserve.assert_not_called(); client.assert_not_called()

    def test_reservation_invalid_blob_version_sha_native_boolean_refused_before_cloud(self):
        v = controller.intent_value('identity', {'protected_receipt': RECEIPT}, contract.IDENTITY_ID, transport.MI_API, 'PUT', transport.identity_body(), {})
        for altered in ({}, dict(RECEIPT, protected_readback_verified=1), dict(RECEIPT, blob='other'), dict(RECEIPT, version_id='invalid')):
            with patch.object(controller, 'reservation_blob') as blob, self.assertRaises(ValueError):
                controller.reservation_readback('identity', altered, v)
            blob.assert_not_called()

    def test_new_grant_cannot_replace_existing_role_assignment(self):
        identity = {'properties': {'principalId': PRINCIPAL}}
        with patch.object(controller, 'identity_observation', return_value=identity), patch.object(controller, 'roles'), \
                patch.object(controller, 'get', return_value=assignment()), self.assertRaisesRegex(ValueError, 'grant_appeared_after_reservation'):
            controller.pre_effect_checks('grant', Mock(), pf(), transport.assignment_body(PRINCIPAL))

    def test_start_checks_registered_job_and_empty_inventory_after_reservation(self):
        identity = {'properties': {'principalId': PRINCIPAL}}; events = []
        with patch.object(controller, 'identity_observation', return_value=identity), \
                patch.object(controller, 'roles', side_effect=lambda *a, **kw: events.append('roles')), \
                patch.object(controller, 'job_observation', side_effect=lambda *a: events.append('job')), \
                patch.object(controller, 'inventory', return_value={'value': []}):
            controller.pre_effect_checks('start', Mock(), pf(), controller.start_body(pf()))
        self.assertEqual(['roles', 'job'], events)
        with patch.object(controller, 'identity_observation', return_value=identity), patch.object(controller, 'roles'), \
                patch.object(controller, 'job_observation'), patch.object(controller, 'inventory', return_value={'value': [execution()]}), \
                self.assertRaisesRegex(ValueError, 'execution_appeared_after_reservation'):
            controller.pre_effect_checks('start', Mock(), pf(), controller.start_body(pf()))


class CollectionTests(unittest.TestCase):
    def test_complete_native_console_descriptor_acceptance_then_fresh_read_only_verify(self):
        p = pf(); p['resource_guards'] = {}; native = execution(); job = {'test': 'job'}; identity = {'properties': {'principalId': PRINCIPAL}}
        captured = dict(status='native_running_replica_binding_observed', operation_id=controller.OPERATION_ID,
            start_ack_receipt=RECEIPT, raw_execution=execution(terminal=False), raw_replicas={}, binding={'name': REPLICA})
        rows = [ConsoleTests().row()]
        console = dict(rows=rows, row_count=1, observed_console_inventory_stable=True, late_delivery_excluded=False)
        retained = []
        def save(value, filename): retained.append(value); return dict(value, protected_receipt=RECEIPT)
        def load(filename):
            return ({'protected_receipt': RECEIPT}, captured if filename == 'replica-binding.json' else retained[-1])
        with patch.object(controller, 'prepared', return_value=({'protected_receipt': RECEIPT}, p)), \
                patch.object(controller, 'arm', return_value=Mock()), patch.object(controller, 'install_chain', return_value=({'protected_receipt': RECEIPT}, job, identity, {})), \
                patch.object(controller, 'start_chain', return_value=({'protected_receipt': RECEIPT}, EXECUTION)), patch.object(controller, 'load', side_effect=load), \
                patch.object(transport, 'validate_replica', return_value={'name': REPLICA}), patch.object(controller, 'get', return_value=native), \
                patch.object(controller, 'inventory', return_value={'value': [native]}), patch.object(transport, 'LogsTransport'), \
                patch.object(transport, 'collect_console', return_value=console) as collect, patch.object(controller, 'job_observation', return_value=job), \
                patch.object(controller, 'roles', return_value={}), patch.object(controller, 'resource_guards', return_value={}), \
                patch.object(controller.previous, 'sources', return_value=p['sources']), patch.object(controller, 'save', side_effect=save) as write:
            accepted = controller.collect(False)
            self.assertEqual('secretless_aca_runtime_observed', accepted['status'])
            self.assertTrue(accepted['acceptance_recorded']); self.assertEqual(rows, accepted['complete_console_events'])
            for key in controller.FALSE_FLAGS: self.assertIs(accepted[key], False)
            self.assertEqual(0, accepted['sql_calls']); self.assertEqual(0, accepted['model_calls'])
            result = controller.collect(True)
            self.assertEqual('independent_secretless_aca_runtime_verified', result['status'])
            self.assertEqual(1, write.call_count)
            scope = collect.call_args.args[1]
            self.assertEqual('2026-10-04T17:59:00+00:00', scope.start_utc)
            self.assertEqual('2026-10-04T18:12:01+00:00', scope.end_utc)


if __name__ == '__main__':
    unittest.main()
