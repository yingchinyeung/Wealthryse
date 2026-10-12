"""Offline named-job continuation tests; never authorize or dispatch cloud work."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import qualify_owner_task_aca_runtime_named as controller
import qualify_owner_task_aca_runtime_continuation as prior
import qualify_owner_task_aca_runtime as original
import owner_task_aca_runtime_contract as contract
import owner_task_aca_runtime_transport as transport
from test_owner_task_aca_runtime_contract import descriptor, observed_job, source

PRINCIPAL = '824dd8fe-7c16-45ac-8d6b-571779872bc9'
CLIENT = '1a2715b3-f843-42a2-b4e6-f49ee5fd3c11'
TENANT = '0578a50b-a7a1-40a4-905b-8b20dba84124'
EXECUTION = 'ibkrv2stg-ownerruntime-20261004-abcdefgh'
REPLICA = EXECUTION + '-abcde'
ALIAS = contract.IDENTITY_ID.replace('/resourceGroups/', '/resourcegroups/')
RECEIPT = dict(blob='test-only', sha256='a' * 64,
    version_id='2026-10-04T20:00:00.0000000Z', protected_readback_verified=True)


def identity():
    return dict(id=ALIAS, name=contract.IDENTITY, type='Microsoft.ManagedIdentity/userAssignedIdentities',
        location='eastasia', tags=transport.identity_body()['tags'], properties=dict(principalId=PRINCIPAL,
            clientId=CLIENT, tenantId=TENANT))


def old_sources():
    value = {'file-' + str(i): 'a' * 64 for i in range(726)}
    value['scripts/verify_owner_task_source_checkpoint.py'] = '3be945e836b897b162c37613ae038e2169a2a2b85b9aeacc8575514909b7eff3'
    return value


def observed():
    return dict(operation_id=contract.PROBE_FAMILY_ID, image=contract.IMAGE,
        runtime_source_sha256=contract.RUNTIME_SOURCE_SHA256, runtime=descriptor())


def checks():
    return dict(test_count=2270, passed_test_count=2270, skipped_tests=[], unittest_exit_code=0,
        compilation_exit_code=0, repository_policy='PASS: synthetic offline fixture',
        live_sql_tests_passed=False, financial_or_visual_acceptance=False,
        os_or_dependency_security_certification=False)


def protected(value, kind, *, version='2026-10-04T20:00:00.0000000Z'):
    value = copy.deepcopy(value); sha = hashlib.sha256(original.canonical(value)).hexdigest()
    value['protected_receipt'] = dict(blob='acceptance/gateway-read-owner-task-pg-' + kind + '-' + sha + '.json',
        sha256=sha, version_id=version, protected_readback_verified=True)
    return value


class Credential:
    def __init__(self): self.calls = []
    def get_token(self, audience):
        self.calls.append(audience); return SimpleNamespace(token='synthetic-no-authority', expires_on=9999999999)


class Response:
    def __init__(self, value=None, *, status=200, raw=None, close_error=False):
        self.status_code = status; self.raw = raw if raw is not None else json.dumps(value).encode('utf-8')
        self.close_error = close_error; self.closed = False
    def iter_content(self, chunk_size): yield self.raw
    def close(self):
        self.closed = True
        if self.close_error: raise RuntimeError('synthetic close failure')


class Session:
    def __init__(self, responses): self.responses = list(responses); self.calls = []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, copy.deepcopy(kwargs)))
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        return result


def new_job(*, mi_alias=True):
    value = observed_job(); value['id'] = controller.JOB_ID; value['name'] = controller.JOB
    if mi_alias:
        assigned = value['identity']['userAssignedIdentities']; assigned[ALIAS] = assigned.pop(contract.IDENTITY_ID)
        config = value['properties']['configuration']
        config['registries'][0]['identity'] = ALIAS; config['identitySettings'][0]['identity'] = ALIAS
    return value


def plan_pf():
    dependency = dict(expected_runtime=descriptor(), observed=observed())
    return dict(prerequisite=dependency, plan=controller.plan(dependency), history=dict(identity=identity()),
        sources={'test-only': 'a' * 64}, resource_guards={}, accepted_grant_receipt=prior_receipt('install'))


def native_execution(*, terminal=True):
    return dict(id=controller.JOB_ID + '/executions/' + EXECUTION, name=EXECUTION,
        type='Microsoft.App/jobs/executions', properties=dict(status='Succeeded' if terminal else 'Running',
            startTime='2026-10-04T20:30:00.0000000Z', endTime='2026-10-04T20:32:01.0000000Z' if terminal else None,
            template=original.start_body(plan_pf())))


def replicas():
    return {'value': [dict(id=controller.JOB_ID + '/executions/' + EXECUTION + '/replicas/' + REPLICA,
        name=REPLICA, type='Microsoft.App/containerapps/revisions/replicas',
        createdTime='2026-10-04T20:30:00Z', runningState='Running', containers=[dict(
            name=contract.CONTAINER, ready=True, started=True, restartCount=0, runningState='Running')])], 'nextLink': None}


def console_row():
    return dict(Group=REPLICA, Container=contract.CONTAINER, LogTime='2026-10-04T20:30:01Z',
        Log=contract.PREFIX + json.dumps(observed(), sort_keys=True, separators=(',', ':')))


def console_response(rows):
    return {'tables': [dict(name='PrimaryResult', columns=[dict(name=n, type=t)
        for n, t in transport.CONSOLE_COLUMNS], rows=[[row[n] for n, _ in transport.CONSOLE_COLUMNS] for row in rows])]}


def start_ack():
    return dict(http_status=200, raw_ack={'id': controller.JOB_ID + '/executions/' + EXECUTION,
        'name': EXECUTION}, protected_receipt=RECEIPT)


def body(raw):
    return {key: value for key, value in raw.items() if key != 'protected_receipt'}


def prior_receipt(stage):
    kind = 'aca-runtime-continuation-grant-acceptance' if stage == 'install' else controller.KIND_PREFIX + 'install-acceptance'
    return dict(RECEIPT, blob='acceptance/gateway-read-owner-task-pg-' + kind + '-' + RECEIPT['sha256'] + '.json')


def assignment():
    return dict(id=transport.ASSIGNMENT_ID, name=transport.ASSIGNMENT, type='Microsoft.Authorization/roleAssignments',
        properties=dict(principalId=PRINCIPAL, principalType='ServicePrincipal', roleDefinitionId=transport.ROLE_ID,
            scope=contract.REGISTRY_ID))


def role_definition():
    return dict(id=transport.ROLE_ID, name=transport.ACR_ROLE, type='Microsoft.Authorization/roleDefinitions',
        properties=dict(roleName='AcrPull', type='BuiltInRole', assignableScopes=['/'], permissions=[dict(
            actions=['Microsoft.ContainerRegistry/registries/pull/read'], notActions=[], dataActions=[], notDataActions=[])]))


def permission():
    inventory = dict(subscription=[assignment()], resource_group_inherited=[], registry_inherited=[assignment()],
        observed_arm_inventory_complete=True, tenant_permission_closure_verified=False)
    return dict(inventories=inventory, role_definition=role_definition(),
        registered_subscription_assignment_inventory_verified=True, tenant_wide_permission_closure_verified=False)


def history_records():
    """Embedded synthetic predecessor records; no retained live-record reads."""
    old725 = {'file-' + str(i): 'a' * 64 for i in range(725)}
    old727 = dict(old725, **{name: 'b' * 64 for name in prior.ADDITIONS})
    identity_receipt = protected({'synthetic': True}, 'aca-runtime-identity-ack')['protected_receipt']
    pf = dict(status='prepared_secretless_aca_runtime_continuation', operation_id=prior.OPERATION_ID,
        trial_operation_id=prior.TRIAL_OPERATION_ID, history=dict(sources=old725, identity=identity(),
            receipts={'identity-ack.json': identity_receipt}), identity=identity(), sources=old727,
        prerequisite=dict(expected_runtime=descriptor(), observed=observed()),
        plan=original.plan({'expected_runtime': descriptor(), 'observed': observed()}), resource_guards={},
        local_checks=checks(), sql_calls=0, model_calls=0, **{key: False for key in prior.FALSE_FLAGS})
    records = {'preflight.json': protected(pf, 'aca-runtime-continuation-preflight')}
    grant_value = prior.intent_value('grant', records['preflight.json'], transport.ASSIGNMENT_ID, transport.ROLE_API,
        'PUT', transport.assignment_body(PRINCIPAL), {'identity': identity_receipt})
    grant_reservation = dict(blob=prior.PREFIX + 'grant-reservation.json',
        sha256=hashlib.sha256(original.canonical(grant_value)).hexdigest(), version_id='2026-10-04T20:00:01.0000000Z',
        etag='"0xABC"', protected_readback_verified=True)
    records['grant-intent.json'] = protected(dict(grant_value, status='reserved_one_exact_cloud_effect',
        reservation_receipt=grant_reservation), 'aca-runtime-continuation-grant-intent')
    records['grant-ack.json'] = protected(dict(status='one_exact_cloud_effect_acknowledged', operation_id=prior.OPERATION_ID,
        trial_operation_id=prior.TRIAL_OPERATION_ID, stage='grant', intent_receipt=records['grant-intent.json']['protected_receipt'],
        http_status=201, raw_ack=assignment(), attempt_consumed=True, automatic_retry=False), 'aca-runtime-continuation-grant-ack')
    records['grant-acceptance.json'] = protected(dict(status='exact_registry_pull_role_observed', operation_id=prior.OPERATION_ID,
        trial_operation_id=prior.TRIAL_OPERATION_ID, identity_receipt=identity_receipt,
        ack_receipt=records['grant-ack.json']['protected_receipt'], assignment=assignment(), roles=permission(),
        sql_calls=0, model_calls=0, **{key: False for key in prior.FALSE_FLAGS}), 'aca-runtime-continuation-grant-acceptance')
    install_value = prior.intent_value('install', records['preflight.json'], contract.JOB_ID, transport.JOB_API,
        'PUT', pf['plan']['definition'], {'grant': records['grant-acceptance.json']['protected_receipt']})
    install_reservation = dict(blob=prior.PREFIX + 'install-reservation.json',
        sha256=hashlib.sha256(original.canonical(install_value)).hexdigest(), version_id='2026-10-04T20:00:02.0000000Z',
        etag='"0xDEF"', protected_readback_verified=True)
    records['install-intent.json'] = protected(dict(install_value, status='reserved_one_exact_cloud_effect',
        reservation_receipt=install_reservation), 'aca-runtime-continuation-install-intent')
    records['install-outcome.json'] = protected(dict(status='effect_unknown_ack_unverified_inspect_only', operation_id=prior.OPERATION_ID,
        trial_operation_id=prior.TRIAL_OPERATION_ID, stage='install', intent_receipt=records['install-intent.json']['protected_receipt'],
        reservation_receipt=install_reservation, attempt_consumed=True, automatic_retry=False, no_effects_verified=False,
        sql_calls=0, model_calls=0), 'aca-runtime-continuation-install-outcome')
    activity = []
    for status, timestamp in (('Failed', controller.FAILURE_ENDED), ('Started', controller.FAILURE_STARTED)):
        row = dict(resourceId=contract.JOB_ID, correlationId=controller.CORRELATION_ID, operationId=controller.CORRELATION_ID,
            operationName={'value': 'Microsoft.App/jobs/write'}, httpRequest=dict(method='PUT',
                uri=transport.ARM + contract.JOB_ID + '?api-version=' + transport.JOB_API), status={'value': status},
            eventTimestamp=timestamp)
        if status == 'Failed':
            row.update(subStatus={'value': 'BadRequest'}, properties=dict(statusCode='BadRequest',
                statusMessage=json.dumps({'error': {'code': 'ContainerAppInvalidName',
                    'message': 'Synthetic invalid job name. The length must be between 2 and 32 characters inclusive.'}})))
        activity.append(row)
    records['install-diagnosis.json'] = protected(dict(status='explicit_invalid_job_name_rejection_observed', operation_id=prior.OPERATION_ID,
        trial_operation_id=prior.TRIAL_OPERATION_ID, checked_at_utc='2026-10-04T20:06:35+00:00', original_job=contract.JOB, original_name_length=35,
        error_code='ContainerAppInvalidName', correlation_id=controller.CORRELATION_ID, original_attempt_consumed=True,
        original_automatic_retry=False, original_job_absent_before_and_after=True, no_runtime_execution_observed=True,
        intent_receipt=records['install-intent.json']['protected_receipt'], outcome_receipt=records['install-outcome.json']['protected_receipt'],
        sources=old727, raw_activity_inventory={'value': activity}, sql_calls=0, model_calls=0),
        'aca-runtime-continuation-install-diagnosis')
    return records


def supersession_record():
    """Synthetic absence provenance, independently pinned inside a test only."""
    source_map = dict(old_sources(), **controller.SUPERSEDED_SOURCE_HASHES)
    sha, version = controller.HISTORY_PINS['preflight.json']
    value = dict(status='unaccepted_named_candidate_absence_verified_before_supersession',
        operation_id='2a2cf3cc-fe8a-498b-89a5-7445075cdec4', trial_operation_id=controller.TRIAL_OPERATION_ID,
        checked_at_utc='2026-10-04T20:57:42.166723+00:00', local_records_absent=True,
        protected_candidate_family_absent=True, effect_reservations_absent=True, capture_stop_absent=True,
        new_job_absent=True, rejected_original_job_absent=True, source_supersession_authorized_only_before_effect=True,
        initial_diagnosis_not_retained_due_to_duplicate_keyword=True, failed_preflight_deadline_seconds=300,
        prior_preflight_receipt=dict(blob='acceptance/gateway-read-owner-task-pg-aca-runtime-continuation-preflight-' + sha + '.json',
            sha256=sha, version_id=version, protected_readback_verified=True), sources=source_map,
        observed_preflight_failures=[dict(status='regression_deadline_exceeded_not_passed', reported_completion_utc='2026-10-04T20:33:00Z'),
            dict(status='regression_deadline_exceeded_not_passed', reported_completion_utc='2026-10-04T20:48:52Z')],
        standalone_discovery=dict(tests=2313, passed=2311, skipped=2, elapsed_seconds=273.85, protected_preflight_acceptance=False),
        local_captured_qa=dict(tests=2313, passed=2311, skipped=2, elapsed_seconds=291.869, compilation_exit_code=0,
            repository_policy='PASS: 293 tracked files, 710 source files scanned, 59 contiguous migrations, no prohibited paths or secret patterns',
            protected_preflight_acceptance=False), sql_calls=0, model_calls=0,
        **{key: False for key in controller.FALSE_FLAGS})
    return protected(value, controller.SUPERSESSION_KIND, version=controller.SUPERSESSION_PIN[1])


@contextlib.contextmanager
def synthetic_supersession_pin(raw):
    receipt = raw['protected_receipt']
    with patch.object(controller, 'SUPERSESSION_PIN', (receipt['sha256'], receipt['version_id'])): yield


@contextlib.contextmanager
def synthetic_history_pins(records):
    pins = {name: (raw['protected_receipt']['sha256'], raw['protected_receipt']['version_id']) for name, raw in records.items()}
    with patch.object(controller, 'HISTORY_PINS', pins): yield


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.offline = contextlib.ExitStack(); self.addCleanup(self.offline.close)
        for target in ('azure.identity.AzureCliCredential.get_token', 'requests.sessions.Session.request',
                'subprocess.check_output', 'subprocess.run'):
            self.offline.enter_context(patch(target, side_effect=AssertionError('offline fixture attempted external access')))


class SourceAndNameTests(OfflineTestCase):
    def test_preserves_exact_727_and_only_two_new_sources(self):
        old = old_sources(); current = dict(old, **{name: 'b' * 64 for name in controller.ADDITIONS})
        controller.validate_sources(old, current)
        self.assertEqual(2, len(controller.ADDITIONS)); self.assertEqual(729, len(current))
        for change in ('extra', 'missing', 'changed', 'hash', 'oldcount'):
            changed = dict(current); baseline = dict(old)
            if change == 'extra': changed['another.py'] = 'c' * 64
            elif change == 'missing': del changed[next(iter(controller.ADDITIONS))]
            elif change == 'changed': changed['file-1'] = 'c' * 64
            elif change == 'hash': changed[next(iter(controller.ADDITIONS))] = True
            else: del baseline['file-1']
            with self.subTest(change=change), self.assertRaises(ValueError): controller.validate_sources(baseline, changed)

    def test_new_job_name_is_fixed_valid_31_chars_and_excludes_consumed_old_target(self):
        self.assertEqual('ibkrv2stg-ownerruntime-20261004', controller.JOB)
        self.assertEqual(31, len(controller.JOB))
        self.assertGreater(len(contract.JOB), 32)
        self.assertNotEqual(controller.JOB, contract.JOB)
        self.assertEqual(('install', 'start'), controller.STAGES)
        for prohibited in ('identity', 'identity-verify', 'grant', 'grant-verify'):
            self.assertNotIn(prohibited, controller.COMMANDS)
        self.assertFalse(hasattr(controller, 'identity_create')); self.assertFalse(hasattr(controller, 'grant_create'))

    def test_unaccepted_candidate_supersession_has_distinct_fixed_outer_operation_and_reservations(self):
        self.assertEqual('6ef9173d-1aa3-4ff7-810a-d2ce23e75d6a', controller.OPERATION_ID)
        self.assertEqual('owner-task-aca-runtime-named-v2-20261004', controller.FOLDER.name)
        self.assertEqual('probes/owner-codex-aca-runtime-named-v2-20261004/', controller.PREFIX)
        self.assertNotEqual('2a2cf3cc-fe8a-498b-89a5-7445075cdec4', controller.OPERATION_ID)
        self.assertEqual(prior.TRIAL_OPERATION_ID, controller.TRIAL_OPERATION_ID)
        self.assertEqual(contract.OPERATION_ID, plan_pf()['plan']['definition']['tags']['operation'])

    def test_execution_name_accepts_only_registered_new_family(self):
        self.assertEqual(EXECUTION, controller.execution_name(EXECUTION))
        for value in (None, True, contract.JOB + '-abcdefgh', controller.JOB, EXECUTION + '/',
                EXECUTION.upper(), controller.JOB + '-../../old', controller.JOB + '-123456',
                controller.JOB + '-1234567890123'):
            with self.subTest(value=value), self.assertRaises(ValueError): controller.execution_name(value)

    def test_conservative_deployment_name_guard_rejects_invalid_names_before_external_access(self):
        controller.validate_name(controller.JOB)
        for value in (None, True, 'a', 'a' * 32, contract.JOB, 'valid--name', 'valid-name-',
                '1valid-name', '-valid-name', 'Valid-name', 'valid_name', 'valid name',
                'valid-name/other', 'valid-namé', 'valid%2fname'):
            with patch.object(controller, 'reserve') as reserve, patch.object(controller, 'arm') as arm, \
                    self.subTest(value=value), self.assertRaises(ValueError): controller.validate_name(value)
            reserve.assert_not_called(); arm.assert_not_called()

    def test_requested_body_source_image_tags_and_runtime_are_exact_frozen_definition(self):
        value = controller.plan({'expected_runtime': descriptor(), 'observed': observed()})
        self.assertEqual(original.canonical(contract.definition(source(), descriptor())), original.canonical(value['definition']))
        self.assertEqual(contract.IMAGE, value['image'])
        self.assertEqual(contract.RUNTIME_SOURCE_SHA256, value['runtime_source_sha256'])
        self.assertEqual(contract.PAYLOAD_SHA256, value['probe_payload_sha256'])
        self.assertEqual(original.plan({'expected_runtime': descriptor(), 'observed': observed()}), value)
        self.assertEqual(contract.OPERATION_ID, value['definition']['tags']['operation'])


class AdapterTests(OfflineTestCase):
    def test_native_new_job_adaptation_retains_exact_raw_value_with_only_known_identity_aliases(self):
        for alias in (False, True):
            native = new_job(mi_alias=alias)
            native['properties']['eventStreamEndpoint'] = contract.EVENT_STREAM_ENDPOINT.replace(contract.JOB, controller.JOB)
            before = copy.deepcopy(native)
            checked = controller.validate_job_metadata(native, descriptor(), PRINCIPAL, CLIENT)
            self.assertEqual(before, checked); self.assertEqual(before, native)
            self.assertIsNot(native, checked); self.assertIsNot(native['properties'], checked['properties'])

    def test_job_metadata_alias_cannot_change_foreign_name_identity_endpoint_or_authority(self):
        for change in ('oldjob', 'otherjob', 'provider', 'foreignmi', 'bothmi', 'registry', 'lifecycle',
                'oldendpoint', 'foreignendpoint', 'queryendpoint', 'principal', 'client', 'secret', 'image'):
            native = new_job(); endpoint = contract.EVENT_STREAM_ENDPOINT.replace(contract.JOB, controller.JOB)
            native['properties']['eventStreamEndpoint'] = endpoint
            if change == 'oldjob': native['id'] = contract.JOB_ID; native['name'] = contract.JOB
            elif change == 'otherjob': native['id'] += '-other'; native['name'] += '-other'
            elif change == 'provider': native['id'] = native['id'].replace('Microsoft.App', 'microsoft.app')
            elif change == 'foreignmi': assigned = native['identity']['userAssignedIdentities']; assigned[ALIAS + '-other'] = assigned.pop(ALIAS)
            elif change == 'bothmi': native['identity']['userAssignedIdentities'][contract.IDENTITY_ID] = dict(principalId=PRINCIPAL, clientId=CLIENT)
            elif change == 'registry': native['properties']['configuration']['registries'][0]['identity'] = ALIAS + '-other'
            elif change == 'lifecycle': native['properties']['configuration']['identitySettings'][0]['lifecycle'] = 'Main'
            elif change == 'oldendpoint': native['properties']['eventStreamEndpoint'] = contract.EVENT_STREAM_ENDPOINT
            elif change == 'foreignendpoint': native['properties']['eventStreamEndpoint'] = endpoint.replace('eastasia.', 'foreign.')
            elif change == 'queryendpoint': native['properties']['eventStreamEndpoint'] += '?secret=forbidden'
            elif change == 'principal': native['identity']['userAssignedIdentities'][ALIAS]['principalId'] = CLIENT
            elif change == 'client': native['identity']['userAssignedIdentities'][ALIAS]['clientId'] = PRINCIPAL
            elif change == 'secret': native['properties']['configuration']['secrets'] = [{'name': 'forbidden', 'value': 'test-only'}]
            else: native['properties']['template']['containers'][0]['image'] = 'foreign:latest'
            with self.subTest(change=change), self.assertRaises(ValueError):
                controller.validate_job_metadata(native, descriptor(), PRINCIPAL, CLIENT)

    def test_new_execution_and_replica_adapters_preserve_native_new_target(self):
        p = plan_pf()
        for terminal in (False, True):
            native = native_execution(terminal=terminal); before = copy.deepcopy(native)
            result = controller.validate_execution(native, p, EXECUTION, terminal=terminal)
            self.assertEqual(native['properties'], result); self.assertEqual(before, native)
        native = replicas(); before = copy.deepcopy(native)
        result = controller.validate_replica(native, EXECUTION)
        self.assertEqual(native['value'][0], result); self.assertEqual(before, native)

    def test_foreign_execution_templates_time_prefix_extra_replica_and_boolean_restart_denied(self):
        p = plan_pf()
        for change in ('oldname', 'foreignid', 'template', 'time', 'status'):
            native = native_execution()
            if change == 'oldname': native['name'] = contract.JOB + '-abcdefgh'
            elif change == 'foreignid': native['id'] = contract.JOB_ID + '/executions/' + EXECUTION
            elif change == 'template': native['properties']['template']['containers'][0]['env'] = [{'name': 'SECRET', 'value': 'test-only'}]
            elif change == 'time': native['properties']['endTime'] = '2026-10-04T20:50:00Z'
            else: native['properties']['status'] = 'Completed'
            with self.subTest(change=change), self.assertRaises(ValueError): controller.validate_execution(native, p, EXECUTION, terminal=True)
        for change in ('oldname', 'foreignid', 'duplicate', 'restart', 'partial'):
            native = replicas()
            if change == 'oldname': native['value'][0]['name'] = contract.JOB + '-abcdefgh-abcde'
            elif change == 'foreignid': native['value'][0]['id'] = contract.JOB_ID + '/executions/' + EXECUTION + '/replicas/' + REPLICA
            elif change == 'duplicate': native['value'].append(copy.deepcopy(native['value'][0]))
            elif change == 'restart': native['value'][0]['containers'][0]['restartCount'] = False
            else: native['nextLink'] = 'unread page'
            with self.subTest(change=change), self.assertRaises(ValueError): controller.validate_replica(native, EXECUTION)


class HistoryTests(OfflineTestCase):
    def test_seven_literal_history_pins_include_exact_failure_diagnosis_and_times(self):
        self.assertEqual({'preflight.json', 'grant-intent.json', 'grant-ack.json', 'grant-acceptance.json',
            'install-intent.json', 'install-outcome.json', 'install-diagnosis.json'}, set(controller.HISTORY_PINS))
        self.assertEqual(('8b0f9d190f6fbcb93087475495a6422d7524d8f24e2f750ddb1a889d251aa0fa',
            '2026-10-04T20:06:48.3111684Z'), controller.HISTORY_PINS['install-diagnosis.json'])
        self.assertEqual('867625d0-98f7-437e-be41-028bccbaf941', controller.CORRELATION_ID)
        self.assertEqual('2026-10-04T20:01:03.3917952Z', controller.FAILURE_STARTED)
        self.assertEqual('2026-10-04T20:01:04.923118Z', controller.FAILURE_ENDED)

    def test_embedded_complete_predecessor_history_remains_consumed_and_native(self):
        records = history_records(); before = copy.deepcopy(records)
        with synthetic_history_pins(records): result = controller.validate_history(records)
        self.assertEqual(before, records); self.assertEqual(identity(), result['identity'])
        self.assertEqual(records['install-diagnosis.json']['raw_activity_inventory'], result['failure_diagnosis']['raw_activity_inventory'])
        self.assertEqual('effect_unknown_ack_unverified_inspect_only', records['install-outcome.json']['status'])
        self.assertIs(records['install-outcome.json']['attempt_consumed'], True)

    def test_raw_failed_started_activity_pair_must_be_exact_complete_correlated_target(self):
        records = history_records(); base = body(records['install-diagnosis.json']); pf = body(records['preflight.json'])
        controller.validate_diagnosis(base, pf, records['install-intent.json'], records['install-outcome.json'])
        # Inventory order is not chronology; only exact native timestamps matter.
        reversed_value = copy.deepcopy(base); reversed_value['raw_activity_inventory']['value'].reverse()
        controller.validate_diagnosis(reversed_value, pf, records['install-intent.json'], records['install-outcome.json'])
        for change in ('count', 'partial', 'duplicate', 'correlation', 'operation', 'target', 'method', 'uri', 'startedtime',
                'failedtime', 'status', 'errorcode', 'duplicatedjson', 'unknownkey', 'absentbool', 'consumedbool', 'retrybool', 'sources'):
            value = copy.deepcopy(base); rows = value['raw_activity_inventory']['value']; failed, started = rows
            if change == 'count': rows.pop()
            elif change == 'partial': value['raw_activity_inventory']['nextLink'] = 'unread-page'
            elif change == 'duplicate': rows[1] = copy.deepcopy(failed)
            elif change == 'correlation': failed['correlationId'] = '11111111-1111-4111-8111-111111111111'
            elif change == 'operation': failed['operationName']['value'] = 'Microsoft.App/jobs/delete'
            elif change == 'target': failed['resourceId'] = controller.JOB_ID
            elif change == 'method': failed['httpRequest']['method'] = 'POST'
            elif change == 'uri': failed['httpRequest']['uri'] += '&other=1'
            elif change == 'startedtime': started['eventTimestamp'] = controller.FAILURE_ENDED
            elif change == 'failedtime': failed['eventTimestamp'] = controller.FAILURE_STARTED
            elif change == 'status': failed['status']['value'] = 'Succeeded'
            elif change == 'errorcode': failed['properties']['statusMessage'] = json.dumps({'error': {'code': 'Other', 'message': 'wrong'}})
            elif change == 'duplicatedjson': failed['properties']['statusMessage'] = '{"error":{"code":"Other","code":"ContainerAppInvalidName","message":"wrong"}}'
            elif change == 'unknownkey': failed['properties']['statusMessage'] = json.dumps({'error': {'code': 'ContainerAppInvalidName', 'message': 'wrong'}, 'other': 0})
            elif change == 'absentbool': value['original_job_absent_before_and_after'] = 1
            elif change == 'consumedbool': value['original_attempt_consumed'] = 1
            elif change == 'retrybool': value['original_automatic_retry'] = 0
            else: value['sources']['file-1'] = 'f' * 64
            with self.subTest(change=change), self.assertRaises(ValueError):
                controller.validate_diagnosis(value, pf, records['install-intent.json'], records['install-outcome.json'])

    def test_missing_corrupt_pin_or_semantically_reprotected_history_is_denied(self):
        records = history_records()
        for change in ('missing', 'sha', 'version', 'protectedbool', 'installroute', 'outcomeconsumed', 'outcomeretry', 'plan'):
            altered = copy.deepcopy(records)
            if change == 'missing': del altered['install-outcome.json']
            elif change == 'sha': altered['grant-ack.json']['protected_receipt']['sha256'] = 'f' * 64
            elif change == 'version': altered['install-diagnosis.json']['protected_receipt']['version_id'] = '2026-10-04T20:02:00.0000000Z'
            elif change == 'protectedbool': altered['preflight.json']['protected_receipt']['protected_readback_verified'] = 1
            elif change == 'installroute':
                value = body(altered['install-intent.json']); value['path'] = controller.JOB_ID
                altered['install-intent.json'] = protected(value, 'aca-runtime-continuation-install-intent')
            elif change == 'outcomeconsumed':
                value = body(altered['install-outcome.json']); value['attempt_consumed'] = 1
                altered['install-outcome.json'] = protected(value, 'aca-runtime-continuation-install-outcome')
            elif change == 'outcomeretry':
                value = body(altered['install-outcome.json']); value['automatic_retry'] = 0
                altered['install-outcome.json'] = protected(value, 'aca-runtime-continuation-install-outcome')
            else:
                value = body(altered['preflight.json']); value['plan']['definition']['properties']['configuration']['replicaRetryLimit'] = 1
                altered['preflight.json'] = protected(value, 'aca-runtime-continuation-preflight')
            selected = synthetic_history_pins(altered) if change in ('installroute', 'outcomeconsumed', 'outcomeretry', 'plan') else synthetic_history_pins(records)
            with selected, self.subTest(change=change), self.assertRaises(ValueError): controller.validate_history(altered)


class TransportTests(OfflineTestCase):
    def make(self, credential, session):
        return controller.ArmTransport(credential, session, runtime_source_bytes=source(), expected_runtime=descriptor())

    def test_exact_new_job_install_and_start_with_body_snapshot_and_one_shot_request(self):
        c = Credential(); session = Session([Response({'id': controller.JOB_ID, 'name': controller.JOB}, status=201),
            Response({'id': controller.JOB_ID + '/executions/' + EXECUTION, 'name': EXECUTION})])
        arm = self.make(c, session); definition = contract.definition(source(), descriptor())
        arm.request(controller.JOB_ID, transport.JOB_API, method='PUT', body=definition)
        arm.request(controller.JOB_ID + '/start', transport.JOB_API, method='POST', body=transport.start_template(definition))
        self.assertEqual(['PUT', 'POST'], [row[0] for row in session.calls])
        self.assertEqual(transport.ARM + controller.JOB_ID + '?api-version=' + transport.JOB_API, session.calls[0][1])
        for row in session.calls:
            self.assertIs(row[2]['allow_redirects'], False); self.assertIs(row[2]['stream'], True); self.assertEqual((5, 20), row[2]['timeout'])
        with self.assertRaisesRegex(ValueError, 'effect_consumed'):
            arm.request(controller.JOB_ID, transport.JOB_API, method='PUT', body=definition)
        self.assertEqual(2, len(session.calls)); self.assertEqual(1, len(c.calls))

    def test_old_targets_identity_grant_delete_extra_uri_or_mutated_body_denied_before_token(self):
        definition = contract.definition(source(), descriptor()); start = transport.start_template(definition)
        cases = [('PUT', contract.JOB_ID, transport.JOB_API, definition), ('POST', contract.JOB_ID + '/start', transport.JOB_API, start),
            ('PUT', contract.IDENTITY_ID, transport.MI_API, transport.identity_body()),
            ('PUT', transport.ASSIGNMENT_ID, transport.ROLE_API, transport.assignment_body(PRINCIPAL)),
            ('DELETE', controller.JOB_ID, transport.JOB_API, None), ('PATCH', controller.JOB_ID, transport.JOB_API, definition),
            ('PUT', controller.JOB_ID + '-other', transport.JOB_API, definition),
            ('PUT', controller.JOB_ID, '2024-03-01', definition), ('POST', controller.JOB_ID + '/start', transport.JOB_API, definition)]
        for method, path, api, value in cases:
            c = Credential(); session = Session([]); arm = self.make(c, session)
            with self.subTest(method=method, path=path), self.assertRaises(ValueError): arm.request(path, api, method=method, body=value)
            self.assertEqual([], c.calls); self.assertEqual([], session.calls)
        c = Credential(); session = Session([]); arm = self.make(c, session)
        with self.assertRaises(ValueError):
            arm.call('PUT', controller.JOB_ID + '?api-version=' + transport.JOB_API + '&api-version=' + transport.JOB_API, body=definition)
        self.assertEqual([], c.calls)

    def test_unknown_token_or_http_ack_closes_response_consumes_effect_and_sanitizes_message(self):
        definition = contract.definition(source(), descriptor())
        for phase in ('credential', 'session', 'close', 'empty202'):
            c = Credential(); session = Session([Response({}, close_error=True) if phase == 'close' else
                Response(status=202, raw=b'') if phase == 'empty202' else ValueError('sensitive-test-value')])
            if phase == 'credential': c.get_token = Mock(side_effect=ValueError('sensitive-test-value'))
            arm = self.make(c, session)
            with self.subTest(phase=phase), self.assertRaises(ValueError) as error:
                arm.request(controller.JOB_ID, transport.JOB_API, method='PUT', body=definition)
            self.assertNotIn('sensitive-test-value', str(error.exception))
            with self.assertRaisesRegex(ValueError, 'effect_consumed'):
                arm.request(controller.JOB_ID, transport.JOB_API, method='PUT', body=definition)
            self.assertLessEqual(len(session.calls), 1)

    def test_complete_bounded_native_http_error_is_private_and_not_printed(self):
        payload = b'{"error":{"code":"SyntheticRejected","message":"synthetic-body"}}'
        c = Credential(); response = Response(status=400, raw=payload); session = Session([response]); arm = self.make(c, session)
        with self.assertRaises(controller.NativeHttpError) as caught:
            arm.request(controller.JOB_ID, transport.JOB_API, method='PUT', body=contract.definition(source(), descriptor()))
        self.assertEqual('aca_named_native_http_rejection_inspect_only', str(caught.exception)); self.assertTrue(response.closed)
        diagnostic = caught.exception.diagnostic()
        self.assertEqual(400, diagnostic['native_http_status']); self.assertEqual(len(payload), diagnostic['response_body_bytes'])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), diagnostic['response_body_sha256'])
        self.assertIs(diagnostic['response_headers_recorded'], False)
        self.assertNotIn('synthetic-body', str(caught.exception))

    def test_private_native_error_maximum_budget_is_explicitly_retained_or_unretained(self):
        for size, retained in ((controller.MAX_PRIVATE_ERROR_BODY, True), (controller.MAX_PRIVATE_ERROR_BODY + 1, False),
                (controller.MAX_BYTES, False)):
            payload = b'x' * size; error = controller.NativeHttpError(400, payload); diagnostic = error.diagnostic()
            self.assertIs(diagnostic['complete_response_body_retained'], retained)
            self.assertIs(diagnostic['complete_response_body_unretained'], not retained)
            self.assertEqual(size, diagnostic['response_body_bytes']); self.assertEqual(hashlib.sha256(payload).hexdigest(), diagnostic['response_body_sha256'])
            if retained: self.assertIsInstance(diagnostic['response_body_base64'], str)
            else: self.assertIsNone(diagnostic['response_body_base64'])
            self.assertLessEqual(len(controller.canonical(diagnostic)), controller.MAX_BYTES)
        with self.assertRaises(ValueError): controller.NativeHttpError(400, b'x' * (controller.MAX_BYTES + 1))

    def test_invalid_patched_deployment_name_fails_before_constructor_credentials_or_reserve(self):
        c = Credential(); session = Session([])
        for invalid in (contract.JOB, 'a' * 32, 'valid--name', 'invalid_name'):
            with patch.object(controller, 'JOB', invalid), patch.object(controller, 'reserve') as reserve, \
                    self.subTest(invalid=invalid), self.assertRaises(ValueError): self.make(c, session)
            reserve.assert_not_called()
        self.assertEqual([], c.calls); self.assertEqual([], session.calls)


class EffectAndGuardTests(OfflineTestCase):
    def args(self):
        p = plan_pf()
        return ('install', {'protected_receipt': RECEIPT}, p, controller.JOB_ID,
            transport.JOB_API, 'PUT', p['plan']['definition'], {'grant': prior_receipt('install')})

    def test_exact_install_or_start_only_body_and_prior_scope_before_reservation(self):
        p = plan_pf()
        valid = [('install', controller.JOB_ID, 'PUT', p['plan']['definition'], {'grant': prior_receipt('install')}),
            ('start', controller.JOB_ID + '/start', 'POST', controller.start_body(p), {'install': prior_receipt('start')})]
        for stage, path, method, value, prior_value in valid:
            controller.validate_effect_request(stage, path, transport.JOB_API, method, value, p, prior_value)
            for change in ('oldtarget', 'identity', 'grant', 'wrongmethod', 'wrongstage', 'body', 'prior', 'priorbool'):
                target, verb, effect_stage, wanted, ancestry = path, method, stage, copy.deepcopy(value), copy.deepcopy(prior_value)
                if change == 'oldtarget': target = contract.JOB_ID
                elif change == 'identity': target, verb, wanted = contract.IDENTITY_ID, 'PUT', transport.identity_body()
                elif change == 'grant': target, verb, wanted = transport.ASSIGNMENT_ID, 'PUT', transport.assignment_body(PRINCIPAL)
                elif change == 'wrongmethod': verb = 'POST' if method == 'PUT' else 'PUT'
                elif change == 'wrongstage': effect_stage = 'grant'
                elif change == 'body': wanted['extra'] = True
                elif change == 'prior': ancestry = {'other': RECEIPT}
                else: ancestry[next(iter(ancestry))]['protected_readback_verified'] = 1
                with patch.object(controller, 'reserve') as reserve, patch.object(controller, 'arm') as arm, \
                        self.subTest(stage=stage, change=change), self.assertRaises(ValueError):
                    controller.mutate(effect_stage, {'protected_receipt': RECEIPT}, p, target, transport.JOB_API, verb, wanted, ancestry)
                reserve.assert_not_called(); arm.assert_not_called()

    def test_effect_snapshot_exact_order_once_and_external_mutations_do_not_change_request(self):
        args = self.args(); wanted = copy.deepcopy(args[6]); events = []; saved = []; caller = Mock()
        caller.request.side_effect = lambda *a, **k: events.append('request') or ({'id': controller.JOB_ID, 'name': controller.JOB}, 201)
        def reserve(stage, value):
            events.append('reserve'); args[6]['properties']['configuration']['replicaRetryLimit'] = 99
            args[7]['grant']['sha256'] = 'f' * 64; return RECEIPT
        def save(value, name): events.append(name); saved.append(copy.deepcopy(value)); return dict(value, protected_receipt=RECEIPT)
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller, 'reserve', side_effect=reserve), patch.object(controller, 'save', side_effect=save), \
                patch.object(controller, 'superseded_absence'), \
                patch.object(controller, 'reservation_readback', side_effect=lambda *a: events.append('readback')), \
                patch.object(controller, 'pre_effect_checks', side_effect=lambda *a: events.append('freshchecks')), \
                patch.object(controller.previous, 'sources', return_value=args[2]['sources']), patch.object(controller, 'arm', return_value=caller):
            result = controller.mutate(*args)
        self.assertEqual(['reserve', 'install-intent.json', 'readback', 'freshchecks', 'request', 'install-ack.json'], events)
        self.assertEqual(wanted, saved[0]['body']); self.assertEqual(wanted, caller.request.call_args.kwargs['body'])
        self.assertEqual('a' * 64, saved[0]['prior_receipts']['grant']['sha256'])
        self.assertIs(result['attempt_consumed'], True); self.assertIs(result['automatic_retry'], False)

    def test_lost_ack_retains_sanitized_outcome_and_same_scope_is_consumed(self):
        args = self.args(); caller = Mock(); caller.request.side_effect = ValueError('sensitive-test-value')
        saved = []
        with tempfile.TemporaryDirectory() as folder:
            def save(value, name): Path(folder, name).touch(); saved.append((copy.deepcopy(value), name)); return dict(value, protected_receipt=RECEIPT)
            with patch.object(controller, 'FOLDER', Path(folder)), patch.object(controller, 'reserve', return_value=RECEIPT) as reserve, \
                    patch.object(controller, 'save', side_effect=save), patch.object(controller, 'reservation_readback'), \
                    patch.object(controller, 'superseded_absence'), \
                    patch.object(controller, 'pre_effect_checks'), patch.object(controller.previous, 'sources', return_value=args[2]['sources']), \
                    patch.object(controller, 'arm', return_value=caller):
                with self.assertRaisesRegex(ValueError, 'effect_unknown_attempt_consumed_inspect_only'): controller.mutate(*args)
                with self.assertRaisesRegex(ValueError, 'effect_consumed_inspect_only'): controller.mutate(*args)
            reserve.assert_called_once(); caller.request.assert_called_once()
        self.assertEqual('install-outcome.json', saved[-1][1]); self.assertNotIn('sensitive-test-value', json.dumps(saved))
        self.assertIs(saved[-1][0]['no_effects_verified'], False); self.assertIs(saved[-1][0]['automatic_retry'], False)

    def test_private_error_diagnostic_extraction_failure_still_retains_consumed_outcome(self):
        args = self.args(); error = controller.NativeHttpError(400, b'synthetic-body'); caller = Mock(); caller.request.side_effect = error
        saved = []
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), patch.object(controller, 'reserve', return_value=RECEIPT), \
                patch.object(controller, 'save', side_effect=lambda value, name: saved.append((copy.deepcopy(value), name)) or dict(value, protected_receipt=RECEIPT)), \
                patch.object(controller, 'superseded_absence'), \
                patch.object(controller, 'reservation_readback'), patch.object(controller, 'pre_effect_checks'), \
                patch.object(controller.previous, 'sources', return_value=args[2]['sources']), patch.object(controller, 'arm', return_value=caller), \
                patch.object(error, 'diagnostic', side_effect=ValueError('sensitive-extractor-value')), \
                self.assertRaisesRegex(ValueError, 'effect_unknown_attempt_consumed_inspect_only'):
            controller.mutate(*args)
        self.assertEqual('install-outcome.json', saved[-1][1]); self.assertIsNone(saved[-1][0]['private_native_http_diagnostic'])
        self.assertIs(saved[-1][0]['native_http_diagnostic_unretained'], True)
        self.assertNotIn('sensitive-extractor-value', json.dumps(saved))

    def test_new_job_appearance_or_existing_execution_after_reservation_denies_effect(self):
        p = plan_pf()
        for stage in controller.STAGES:
            with patch.object(controller, 'grant_binding', return_value=(prior_receipt('install'), identity(), permission())), \
                    patch.object(controller, 'superseded_absence'), \
                    patch.object(controller, 'get', return_value={'exists': True}), patch.object(controller, 'job_observation'), \
                    patch.object(controller, 'inventory', return_value={'value': [native_execution()]}), \
                    self.subTest(stage=stage), self.assertRaises(ValueError): controller.pre_effect_checks(stage, Mock(), p, {})

    def test_registered_resource_guard_excludes_only_three_known_diagnostics(self):
        allowed = {controller.previous.q.canary.JOB: {}, contract.JOB: {}, controller.JOB: {}}
        baseline = dict(other_jobs={**{'known-' + str(i): {} for i in range(34)}, **allowed}, job_inventory_complete=True)
        for extra in (False, True):
            value = copy.deepcopy(baseline)
            if extra: value['other_jobs']['unregistered-extra-job'] = {}
            with patch.object(controller.previous.q.canary.host, 'checked_owner'), \
                    patch.object(controller.previous.q.deploy, 'baseline', return_value=value), \
                    patch.object(controller.previous.q.deploy, 'request', return_value={}), \
                    patch.object(controller.previous.q.canary, 'validate_job'), patch.object(controller.previous.q.deploy, 'settings', return_value='a' * 64), \
                    patch.object(controller.previous.q, 'registry_metadata', return_value={}), \
                    patch.object(controller.previous.q.canary, 'historical_host', return_value={}):
                if extra:
                    with self.assertRaises(ValueError): controller.resource_guards()
                else: self.assertEqual(34, len(controller.resource_guards()['baseline']['other_jobs']))


class ConsoleTests(OfflineTestCase):
    def scope(self):
        return controller.console_scope(EXECUTION, '2026-10-04T20:29:00Z', '2026-10-04T20:42:01Z')

    def test_complete_named_execution_family_query_has_no_runtime_container_or_prefix_filter(self):
        scope = self.scope(); text = controller.console_query(scope)
        self.assertIn(EXECUTION + '-', text); self.assertNotIn(contract.JOB + '-abcdefgh', text)
        self.assertNotIn(contract.CONTAINER, text); self.assertNotIn(contract.PREFIX, text)
        for forbidden in ('take ', 'limit ', 'sample ', 'summarize ', 'distinct '):
            self.assertNotIn(forbidden, text.lower())
        for change in ('execution', 'extra', 'start', 'end'):
            changed = dict(scope)
            if change == 'execution': changed['execution'] = contract.JOB + '-abcdefgh'
            elif change == 'extra': changed['container'] = contract.CONTAINER
            elif change == 'start': changed['start_utc'] = '2026-10-04T20:43:00Z'
            else: changed['end_utc'] = '2026-10-04T20:28:00Z'
            with self.subTest(change=change), self.assertRaises(ValueError): controller.checked_scope(changed)

    def test_complete_inventory_preserves_duplicate_and_other_container_events(self):
        rows = [console_row(), console_row(), dict(console_row(), Container='platform-sidecar', Log='other event')]
        query = Mock(side_effect=[console_response(rows), console_response(list(reversed(rows)))])
        result = controller.collect_console(query, self.scope())
        self.assertEqual(3, result['row_count']); self.assertEqual(sorted(rows, key=controller.canonical), result['rows'])
        self.assertEqual(2, query.call_count); self.assertEqual(controller.digest(result['rows']), result['inventory_sha256'])
        self.assertIs(result['observed_console_inventory_stable'], True)
        for key in ('late_delivery_excluded', 'zero_log_replica_absence_verified', 'platform_replacement_excluded'):
            self.assertIs(result[key], False)

    def test_console_drift_partial_errors_foreign_groups_and_unknown_columns_fail_closed(self):
        scope = self.scope(); row = console_row()
        with self.assertRaisesRegex(ValueError, 'logs_drift'):
            controller.collect_console(Mock(side_effect=[console_response([row]), console_response([row, row])]), scope)
        for change in ('error', 'partial', 'extra_table', 'foreign', 'unknown_column', 'wrong_type', 'outside_time'):
            response = console_response([row])
            if change == 'error': response['error'] = {'code': 'PartialError'}
            elif change == 'partial': response['tables'][0]['partial'] = True
            elif change == 'extra_table': response['tables'].append(copy.deepcopy(response['tables'][0]))
            elif change == 'foreign': response['tables'][0]['rows'][0][0] = REPLICA.replace(controller.JOB, contract.JOB)
            elif change == 'unknown_column': response['tables'][0]['columns'][1]['name'] = 'Unknown'
            elif change == 'wrong_type': response['tables'][0]['rows'][0][1] = None
            else: response['tables'][0]['rows'][0][2] = '2026-10-04T21:00:00Z'
            with self.subTest(change=change), self.assertRaises(ValueError): controller.validate_console_response(response, scope)

    def test_logs_transport_is_fixed_bounded_no_redirect_no_retry_and_sanitizes_failures(self):
        scope = self.scope(); credential = Credential(); response = Response(console_response([console_row()])); session = Session([response])
        value = controller.LogsTransport(credential, session).call(scope)
        self.assertEqual(console_response([console_row()]), value); self.assertTrue(response.closed)
        method, url, kwargs = session.calls[0]
        self.assertEqual('POST', method); self.assertEqual(transport.LOGS_ENDPOINT, url)
        self.assertIs(kwargs['allow_redirects'], False); self.assertIs(kwargs['stream'], True); self.assertEqual((5, 20), kwargs['timeout'])
        self.assertEqual([transport.LOGS_AUDIENCE], credential.calls)
        for phase in ('credential', 'session', 'close', 'oversize', 'partial'):
            credential = Credential()
            selected = Response(console_response([console_row()]), close_error=phase == 'close')
            if phase == 'oversize': selected = Response(raw=b'x' * (controller.MAX_BYTES + 1))
            if phase == 'partial': selected = Response(dict(console_response([]), error={'code': 'PartialError'}))
            session = Session([ValueError('sensitive-logs-value') if phase == 'session' else selected])
            if phase == 'credential': credential.get_token = Mock(side_effect=ValueError('sensitive-logs-value'))
            with self.subTest(phase=phase), self.assertRaises(ValueError) as caught:
                controller.LogsTransport(credential, session).call(scope)
            self.assertNotIn('sensitive-logs-value', str(caught.exception)); self.assertLessEqual(len(session.calls), 1)


class LocalQaTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.offline.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def regression(self, *, skipped=False, failure=False, unknown_skip=False):
        lines = []
        import os
        if os.name == 'nt':
            lines.append('test_actual_windows_junction_entry_is_rejected (test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_actual_windows_junction_entry_is_rejected) ... ok')
        if skipped:
            # A permitted OS exclusion is not converted to a passing test.
            if os.name == 'nt':
                lines.append("test_symlink_entry_never_followed (test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_symlink_entry_never_followed) ... skipped 'OS does not permit unprivileged test symlink creation'")
            else:
                lines.append("test_actual_windows_junction_entry_is_rejected (test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_actual_windows_junction_entry_is_rejected) ... skipped 'Windows junction fixture'")
        if unknown_skip:
            lines.append("test_unknown (test_unregistered.UnknownTests.test_unknown) ... skipped 'not reviewed'")
        lines.append('Ran 2313 tests in 291.869s')
        skip_count = int(skipped) + int(unknown_skip)
        lines.append('FAILED (failures=1)' if failure else 'OK (skipped=' + str(skip_count) + ')' if skip_count else 'OK')
        return SimpleNamespace(returncode=1 if failure else 0, stdout='', stderr='\n'.join(lines) + '\n')

    def policy(self, *, success=True):
        return SimpleNamespace(returncode=0 if success else 1,
            stdout='PASS: synthetic offline policy fixture' if success else 'FAIL: synthetic policy fixture', stderr='')

    def compile(self, *, success=True):
        return SimpleNamespace(returncode=0 if success else 1, stdout='', stderr='')

    def test_fixed_600_90_90_complete_commands_and_actual_frozen_result_validator(self):
        output = io.StringIO()
        with patch.object(controller.subprocess, 'run', side_effect=[self.regression(), self.policy(), self.compile()]) as run, \
                patch.object(controller.previous.q.qa, 'validate_regression_result', wraps=controller.previous.q.qa.validate_regression_result) as validator, \
                contextlib.redirect_stdout(output): result = controller.run_local_checks()
        self.assertEqual(3, run.call_count); validator.assert_called_once()
        first, second, third = run.call_args_list
        self.assertEqual([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], first.args[0])
        self.assertEqual([sys.executable, 'scripts/ci_policy_check.py'], second.args[0])
        self.assertEqual([sys.executable, '-m', 'compileall', '-q', '-x', 'node_modules', 'src', 'scripts', 'tests'], third.args[0])
        self.assertEqual([600, 90, 90], [call.kwargs['timeout'] for call in run.call_args_list])
        for call in run.call_args_list:
            self.assertEqual(controller.ROOT, call.kwargs['cwd']); self.assertIs(call.kwargs['capture_output'], True); self.assertIs(call.kwargs['text'], True)
        self.assertEqual({'test_count', 'passed_test_count', 'skipped_tests', 'unittest_exit_code', 'compilation_exit_code',
            'repository_policy', 'live_sql_tests_passed', 'financial_or_visual_acceptance', 'os_or_dependency_security_certification'}, set(result))
        self.assertEqual(2313, result['test_count']); self.assertEqual(2313, result['passed_test_count']); self.assertEqual([], result['skipped_tests'])
        for key in ('live_sql_tests_passed', 'financial_or_visual_acceptance', 'os_or_dependency_security_certification'):
            self.assertIs(result[key], False)
        self.assertNotIn('synthetic-no-authority', output.getvalue())

    def test_known_skip_is_recorded_not_passed_and_unknown_skip_or_failure_blocks_remaining_phases(self):
        with patch.object(controller.subprocess, 'run', side_effect=[self.regression(skipped=True), self.policy(), self.compile()]):
            result = controller.run_local_checks()
        self.assertEqual(2312, result['passed_test_count']); self.assertEqual(1, len(result['skipped_tests']))
        self.assertEqual('not_exercised', result['skipped_tests'][0]['result'])
        for regression in (self.regression(failure=True), self.regression(unknown_skip=True),
                SimpleNamespace(returncode=0, stdout='', stderr='OK\n')):
            with patch.object(controller.subprocess, 'run', return_value=regression) as run, \
                    patch.object(controller, 'save') as save, patch.object(controller, 'reserve') as reserve, self.assertRaises(ValueError):
                controller.run_local_checks()
            run.assert_called_once(); save.assert_not_called(); reserve.assert_not_called()

    def test_each_phase_timeout_partial_output_or_provider_error_never_becomes_pass_or_retry(self):
        good = [self.regression(), self.policy(), self.compile()]
        for phase in range(3):
            for error in (subprocess.TimeoutExpired('synthetic-offline-command', 600 if phase == 0 else 90,
                    output='sensitive-partial-output', stderr='Ran 2313 tests in 1.0s\nOK\n'), ValueError('sensitive-provider-value')):
                output = io.StringIO()
                with patch.object(controller.subprocess, 'run', side_effect=good[:phase] + [error]) as run, \
                        patch.object(controller, 'save') as save, patch.object(controller, 'reserve') as reserve, \
                        contextlib.redirect_stdout(output), self.subTest(phase=phase, error=type(error).__name__), self.assertRaises(ValueError) as caught:
                    controller.run_local_checks()
                self.assertEqual(phase + 1, run.call_count); save.assert_not_called(); reserve.assert_not_called()
                self.assertNotIn('sensitive-partial-output', str(caught.exception)); self.assertNotIn('sensitive-provider-value', str(caught.exception))
                self.assertNotIn('sensitive-partial-output', output.getvalue()); self.assertNotIn('sensitive-provider-value', output.getvalue())

    def test_nonzero_policy_compilation_or_nonpass_policy_rejects_without_remaining_phases(self):
        cases = ([self.regression(), self.policy(success=False)],
            [self.regression(), SimpleNamespace(returncode=0, stdout='unknown policy output', stderr='')],
            [self.regression(), self.policy(), self.compile(success=False)])
        for outputs in cases:
            with patch.object(controller.subprocess, 'run', side_effect=outputs) as run, \
                    patch.object(controller, 'save') as save, patch.object(controller, 'reserve') as reserve, self.assertRaises(ValueError):
                controller.run_local_checks()
            self.assertEqual(len(outputs), run.call_count); save.assert_not_called(); reserve.assert_not_called()

    def test_phase_progress_is_truthful_and_timeout_has_no_false_pass_event(self):
        phases = ('full_regression', 'repository_policy', 'compilation')
        for failed_phase in (None, 0, 1, 2):
            values = [self.regression(), self.policy(), self.compile()]
            if failed_phase is not None:
                values = values[:failed_phase] + [subprocess.TimeoutExpired('offline-fixture', 600)]
            output = io.StringIO()
            with patch.object(controller.subprocess, 'run', side_effect=values), contextlib.redirect_stdout(output):
                if failed_phase is None: controller.run_local_checks()
                else:
                    with self.assertRaises(ValueError): controller.run_local_checks()
            records = [json.loads(line) for line in output.getvalue().splitlines()]
            completed = 3 if failed_phase is None else failed_phase
            expected = [(phase, status) for phase in phases[:completed]
                for status in ('local_qa_phase_started', 'local_qa_phase_passed')]
            if failed_phase is not None: expected.append((phases[failed_phase], 'local_qa_phase_started'))
            self.assertEqual(expected, [(row['phase'], row['status']) for row in records])
            for row in records:
                if row['status'] == 'local_qa_phase_passed':
                    self.assertEqual({'status', 'phase', 'elapsed_seconds'}, set(row)); self.assertGreaterEqual(row['elapsed_seconds'], 0)
                else: self.assertEqual({'status', 'phase'}, set(row))

    def test_exact_qa_policy_is_separate_immutable_and_rejects_old_deadline_or_sharding(self):
        policy = controller.local_qa_policy(); validated = controller.validate_local_qa_policy(policy)
        self.assertEqual(policy, validated); self.assertIsNot(policy, validated)
        self.assertEqual(600, policy['regression_timeout_seconds']); self.assertEqual(90, policy['repository_policy_timeout_seconds'])
        self.assertEqual(90, policy['compilation_timeout_seconds'])
        self.assertEqual('3be945e836b897b162c37613ae038e2169a2a2b85b9aeacc8575514909b7eff3', policy['result_validator_source_sha256'])
        for key, value in (('regression_timeout_seconds', 300), ('regression_timeout_seconds', True), ('full_discovery', 1),
                ('sharding', True), ('automatic_retry', True), ('repository_policy_timeout_seconds', 600),
                ('regression_argv', ['-m', 'unittest', 'tests.test_owner_task_aca_runtime_named']),
                ('result_validator_source_sha256', 'f' * 64), ('extra', 0)):
            changed = copy.deepcopy(policy); changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): controller.validate_local_qa_policy(changed)

    def test_prepare_timeout_before_new_preflight_or_effect_reservation_is_never_accepted(self):
        source_map = dict(old_sources(), **{key: 'b' * 64 for key in controller.ADDITIONS})
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller.previous, 'sources', return_value=source_map), \
                patch.object(controller, 'history', return_value={'sources': old_sources()}), \
                patch.object(controller, 'supersession', return_value=supersession_record()), \
                patch.object(controller, 'prerequisite', return_value=plan_pf()['prerequisite']), \
                patch.object(controller.subprocess, 'run', side_effect=subprocess.TimeoutExpired('offline', 600, stderr='Ran 1 tests in 1.0s\nOK\n')) as run, \
                patch.object(controller, 'save') as save, patch.object(controller, 'reserve') as reserve, \
                patch.object(controller, 'superseded_absence') as absence, patch.object(controller, 'arm') as arm, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'local_qa_phase_unverified'):
            controller.prepare()
        run.assert_called_once(); save.assert_not_called(); reserve.assert_not_called(); absence.assert_not_called(); arm.assert_not_called()


class SupersessionTests(OfflineTestCase):
    def test_exact_original_unaccepted_diagnosis_pin_and_pair_are_not_reclassified_as_a_pass(self):
        self.assertEqual(('2eab0fb509b6a2cb38203e050bddc094320ede5ea73b9c3bec234ed341cfd43c',
            '2026-10-04T20:57:57.0738821Z'), controller.SUPERSESSION_PIN)
        self.assertEqual({'scripts/qualify_owner_task_aca_runtime_named.py':
            '684f5e2aabeef6fad4f431167c462b0ae30292c4f3fe18d2c1506819ea193aec',
            'tests/test_owner_task_aca_runtime_named.py': 'db0e044f904be4d7861fa6cf0ca2c849ba95a6123eadd7ba8959e431763d9093'},
            controller.SUPERSEDED_SOURCE_HASHES)
        raw = supersession_record(); before = copy.deepcopy(raw)
        with synthetic_supersession_pin(raw): value = controller.validate_supersession(raw)
        self.assertEqual(body(raw), value); self.assertEqual(before, raw)
        self.assertIs(value['standalone_discovery']['protected_preflight_acceptance'], False)
        self.assertIs(value['local_captured_qa']['protected_preflight_acceptance'], False)
        self.assertEqual(2, len(value['observed_preflight_failures']))

    def test_reprotected_absence_privilege_timing_or_source_mutations_are_denied(self):
        for change in ('absence', 'absence_alias', 'stop', 'newjob', 'runtime', 'calls', 'deadline', 'qa_accepted',
                'failure', 'source', 'validator', 'prior', 'extra', 'checked_time'):
            raw = supersession_record(); value = body(raw)
            if change == 'absence': value['effect_reservations_absent'] = False
            elif change == 'absence_alias': value['effect_reservations_absent'] = 1
            elif change == 'stop': value['capture_stop_absent'] = False
            elif change == 'newjob': value['new_job_absent'] = False
            elif change == 'runtime': value['owner_runtime_active'] = True
            elif change == 'calls': value['sql_calls'] = False
            elif change == 'deadline': value['failed_preflight_deadline_seconds'] = 600
            elif change == 'qa_accepted': value['local_captured_qa']['protected_preflight_acceptance'] = True
            elif change == 'failure': value['observed_preflight_failures'][0]['status'] = 'passed'
            elif change == 'source': value['sources'][next(iter(controller.ADDITIONS))] = 'b' * 64
            elif change == 'validator': value['sources']['scripts/verify_owner_task_source_checkpoint.py'] = 'f' * 64
            elif change == 'prior': value['prior_preflight_receipt']['protected_readback_verified'] = 1
            elif change == 'extra': value['extra_authority'] = True
            else: value['checked_at_utc'] = '2026-10-04T20:57:42+01:00'
            changed = protected(value, controller.SUPERSESSION_KIND, version=controller.SUPERSESSION_PIN[1])
            with synthetic_supersession_pin(changed), self.subTest(change=change), self.assertRaises(ValueError):
                controller.validate_supersession(changed)
        raw = supersession_record()
        with self.assertRaises(ValueError): controller.validate_supersession(raw)

    def test_only_exact_two_unaccepted_source_paths_can_change_with_no_new_path_or_stale_revision(self):
        old = body(supersession_record())['sources']; current = dict(old, **{key: 'b' * 64 for key in controller.ADDITIONS})
        controller.validate_supersession_sources(old, current)
        for change in ('extra', 'missing', 'old727', 'unchanged', 'oldhash', 'bool'):
            baseline = dict(old); changed = dict(current)
            if change == 'extra': changed['newfile.py'] = 'f' * 64
            elif change == 'missing': del changed['file-1']
            elif change == 'old727': changed['file-1'] = 'f' * 64
            elif change == 'unchanged': changed[next(iter(controller.ADDITIONS))] = old[next(iter(controller.ADDITIONS))]
            elif change == 'oldhash': baseline[next(iter(controller.ADDITIONS))] = 'f' * 64
            else: changed[next(iter(controller.ADDITIONS))] = True
            with self.subTest(change=change), self.assertRaises(ValueError): controller.validate_supersession_sources(baseline, changed)

    def storage(self, *, rows=None, error=None, token=None, page_error=None):
        service = Mock()
        rows = rows if rows is not None else [SimpleNamespace(
            name='acceptance/gateway-read-owner-task-pg-' + controller.SUPERSESSION_KIND + '-' + controller.SUPERSESSION_PIN[0] + '.json',
            version_id=controller.SUPERSESSION_PIN[1])]
        class PagedFixture:
            def __init__(self):
                self.rows = rows; self.continuation_token = token; self.calls = 0
            def by_page(self): return self
            def __next__(self):
                self.calls += 1
                if page_error is not None: raise page_error
                if self.calls != 1: raise AssertionError('fixture must never request another page')
                return iter(self.rows)
        service.get_container_client.return_value.list_blobs.return_value = PagedFixture()
        if error is None:
            error = controller.HttpResponseError('synthetic missing'); error.status_code = 404; error.error_code = 'BlobNotFound'
        service.get_blob_client.return_value.get_blob_properties.side_effect = error
        return service

    def test_fresh_old_family_absence_requires_only_exact_diagnosis_version_and_three_exact_missing_probes(self):
        service = self.storage()
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'qa-timing-diagnosis.json').touch()
            with patch.object(controller, 'SUPERSEDED_FOLDER', Path(folder)), patch.object(controller, 'superseded_storage', return_value=service), \
                    patch.object(controller.previous.q.deploy, 'archive_protection'):
                controller.superseded_absence()
        call = service.get_container_client.return_value.list_blobs.call_args
        self.assertEqual(['versions'], call.kwargs['include']); self.assertEqual(2, call.kwargs['results_per_page'])
        self.assertEqual(10, call.kwargs['timeout']); self.assertEqual(0, call.kwargs['retry_total'])
        self.assertEqual('acceptance/gateway-read-owner-task-pg-aca-runtime-named-', call.kwargs['name_starts_with'])
        self.assertEqual([controller.SUPERSEDED_PREFIX + name for name in ('install-reservation.json', 'start-reservation.json', 'capture-stop.json')],
            [call.args[1] for call in service.get_blob_client.call_args_list])
        self.assertEqual(3, service.get_blob_client.return_value.get_blob_properties.call_count)

    def test_old_effect_local_extra_remote_version_partial_or_unknown404_blocks_supersession(self):
        from azure.storage.blob import StorageErrorCode
        for change in ('localextra', 'localmissing', 'remoteextra', 'remoteempty', 'wrongversion', 'container404', 'resource404',
                'forbidden403', 'boolstatus', 'existing', 'sdk_unknown'):
            service = self.storage()
            page = service.get_container_client.return_value.list_blobs.return_value
            if change == 'remoteextra':
                page.rows.append(SimpleNamespace(name='old-preflight.json', version_id='unknown'))
            elif change == 'remoteempty': page.rows = []
            elif change == 'wrongversion': page.rows[0].version_id = '2026-10-04T20:00:00.0000000Z'
            elif change == 'existing': service.get_blob_client.return_value.get_blob_properties.side_effect = None
            elif change == 'sdk_unknown': service.get_blob_client.return_value.get_blob_properties.side_effect = ValueError('sensitive-storage-value')
            elif change in ('container404', 'resource404', 'forbidden403', 'boolstatus'):
                error = controller.HttpResponseError('sensitive-storage-value'); error.status_code = True if change == 'boolstatus' else 403 if change == 'forbidden403' else 404
                error.error_code = StorageErrorCode.container_not_found if change == 'container404' else 'ResourceNotFound' if change == 'resource404' else 'BlobNotFound'
                service.get_blob_client.return_value.get_blob_properties.side_effect = error
            with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'SUPERSEDED_FOLDER', Path(folder)), \
                    patch.object(controller, 'superseded_storage', return_value=service), patch.object(controller.previous.q.deploy, 'archive_protection'), \
                    self.subTest(change=change):
                if change != 'localmissing': Path(folder, 'qa-timing-diagnosis.json').touch()
                if change == 'localextra': Path(folder, 'install-intent.json').touch()
                with self.assertRaises(ValueError) as caught: controller.superseded_absence()
                self.assertNotIn('sensitive-storage-value', str(caught.exception))

    def test_old_family_inventory_uses_one_complete_page_and_never_follows_unknown_continuation(self):
        for token in (None, '', 'opaque-next', False, 0, [], {}):
            service = self.storage(token=token); page = service.get_container_client.return_value.list_blobs.return_value
            with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'SUPERSEDED_FOLDER', Path(folder)), \
                    patch.object(controller, 'superseded_storage', return_value=service), patch.object(controller.previous.q.deploy, 'archive_protection'), \
                    self.subTest(token=token):
                Path(folder, 'qa-timing-diagnosis.json').touch()
                if token is None or type(token) is str and token == '': controller.superseded_absence()
                else:
                    with self.assertRaisesRegex(ValueError, 'protected_absence_unproven'): controller.superseded_absence()
                    service.get_blob_client.assert_not_called()
            self.assertEqual(1, page.calls)
        service = self.storage(page_error=ValueError('sensitive-page-value'))
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'SUPERSEDED_FOLDER', Path(folder)), \
                patch.object(controller, 'superseded_storage', return_value=service), self.assertRaises(ValueError) as caught:
            Path(folder, 'qa-timing-diagnosis.json').touch(); controller.superseded_absence()
        self.assertNotIn('sensitive-page-value', str(caught.exception)); service.get_blob_client.assert_not_called()

    def test_old_candidate_evidence_appearance_blocks_effect_before_reservation_or_after_reservation_before_request(self):
        args = EffectAndGuardTests.args(self); client = Mock()
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller, 'superseded_absence', side_effect=ValueError('old candidate effect exists')) as absence, \
                patch.object(controller, 'reserve') as reserve, patch.object(controller, 'arm', return_value=client), self.assertRaises(ValueError):
            controller.mutate(*args)
        absence.assert_called_once(); reserve.assert_not_called(); client.request.assert_not_called()
        for stage in controller.STAGES:
            with patch.object(controller, 'superseded_absence', side_effect=ValueError('old candidate effect exists')) as absence, \
                    patch.object(controller, 'grant_binding') as grant, self.subTest(stage=stage), self.assertRaises(ValueError):
                controller.pre_effect_checks(stage, client, plan_pf(), {})
            absence.assert_called_once(); grant.assert_not_called(); client.request.assert_not_called()


class PreparedTests(OfflineTestCase):
    def value(self):
        p = plan_pf(); old = old_sources(); current = dict(old, **{key: 'b' * 64 for key in controller.ADDITIONS})
        p.update(status='prepared_secretless_valid_name_aca_runtime', operation_id=controller.OPERATION_ID,
            trial_operation_id=controller.TRIAL_OPERATION_ID, job=controller.JOB, job_id=controller.JOB_ID,
            sources=current, local_checks=checks(), local_qa_policy=controller.local_qa_policy(),
            supersession=supersession_record(), identity=identity(), **{key: False for key in controller.FALSE_FLAGS})
        p['history'].update(sources=old, receipts={'grant-acceptance.json': copy.deepcopy(p['accepted_grant_receipt'])})
        return p

    def test_source_only_prepared_validation_is_strict_and_performs_no_live_dependency_calls(self):
        p = self.value(); raw = protected(p, 'aca-runtime-named-preflight')
        with synthetic_supersession_pin(p['supersession']), patch.object(controller, 'load', return_value=(raw, p)), patch.object(controller.previous, 'sources', return_value=p['sources']), \
                patch.object(controller, 'history') as history, patch.object(controller, 'prerequisite') as prerequisite, \
                patch.object(controller, 'grant_binding') as grant, patch.object(controller, 'arm') as arm:
            self.assertEqual((raw, p), controller.prepared(cloud=False))
        for mock in (history, prerequisite, grant, arm): mock.assert_not_called()
        for invalid in (None, 0, 1, 'false'):
            with patch.object(controller, 'load') as load, self.subTest(invalid=invalid), self.assertRaises(ValueError):
                controller.prepared(cloud=invalid)
            load.assert_not_called()

    def test_reprotected_preflight_mutations_unknown_authority_or_source_drift_are_rejected(self):
        original_value = self.value()
        for change in ('status', 'job', 'job_id', 'op', 'source', 'plan', 'identity', 'receipt', 'qa', 'flag'):
            p = copy.deepcopy(original_value)
            if change == 'status': p['status'] = 'accepted'
            elif change == 'job': p['job'] = contract.JOB
            elif change == 'job_id': p['job_id'] = contract.JOB_ID
            elif change == 'op': p['operation_id'] = prior.OPERATION_ID
            elif change == 'source': p['sources']['file-1'] = 'f' * 64
            elif change == 'plan': p['plan']['definition']['properties']['configuration']['replicaRetryLimit'] = 1
            elif change == 'identity': p['identity']['properties']['principalId'] = CLIENT
            elif change == 'receipt': p['accepted_grant_receipt']['sha256'] = 'f' * 64
            elif change == 'qa': p['local_checks']['live_sql_tests_passed'] = True
            else: p[controller.FALSE_FLAGS[0]] = 0
            raw = protected(p, 'aca-runtime-named-preflight')
            with synthetic_supersession_pin(p['supersession']), patch.object(controller, 'load', return_value=(raw, p)), \
                    patch.object(controller.previous, 'sources', return_value=original_value['sources']), \
                    self.subTest(change=change), self.assertRaises(ValueError): controller.prepared(cloud=False)

    def test_preflight_binds_fresh_policy_and_exact_superseded_source_revision_not_old_qa_pass(self):
        for change in ('policy_timeout', 'policy_bool', 'old_operation', 'superseded_source', 'absence', 'pin'):
            p = self.value(); actual = copy.deepcopy(p['sources'])
            if change == 'policy_timeout': p['local_qa_policy']['regression_timeout_seconds'] = 300
            elif change == 'policy_bool': p['local_qa_policy']['full_discovery'] = 1
            elif change == 'old_operation': p['operation_id'] = controller.SUPERSEDED_OPERATION_ID
            elif change == 'superseded_source':
                value = body(p['supersession']); value['sources']['file-1'] = 'f' * 64
                p['supersession'] = protected(value, controller.SUPERSESSION_KIND, version=controller.SUPERSESSION_PIN[1])
            elif change == 'absence':
                value = body(p['supersession']); value['effect_reservations_absent'] = False
                p['supersession'] = protected(value, controller.SUPERSESSION_KIND, version=controller.SUPERSESSION_PIN[1])
            else: p['supersession']['protected_receipt']['version_id'] = '2026-10-04T20:00:00.0000000Z'
            raw = protected(p, controller.KIND_PREFIX + 'preflight')
            pin = synthetic_supersession_pin(p['supersession']) if change != 'pin' else contextlib.nullcontext()
            with pin, patch.object(controller, 'load', return_value=(raw, p)), \
                    patch.object(controller.previous, 'sources', return_value=actual), self.subTest(change=change), self.assertRaises(ValueError):
                controller.prepared(cloud=False)


class CaptureTests(OfflineTestCase):
    @contextlib.contextmanager
    def fixture(self, responses, *, times=None):
        p = plan_pf(); saved = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(controller, 'capture_permitted'))
            stop = stack.enter_context(patch.object(controller, 'capture_stop'))
            getter = stack.enter_context(patch.object(controller, 'get', side_effect=responses))
            stack.enter_context(patch.object(controller.previous, 'sources', return_value=p['sources']))
            stack.enter_context(patch.object(controller, 'arm', return_value=Mock()))
            stack.enter_context(patch.object(controller, 'save', side_effect=lambda value, name:
                saved.append((copy.deepcopy(value), name)) or dict(value, protected_receipt=RECEIPT)))
            clock = stack.enter_context(patch.object(controller.time, 'monotonic', side_effect=times) if times is not None
                else patch.object(controller.time, 'monotonic', return_value=0))
            sleep = stack.enter_context(patch.object(controller.time, 'sleep'))
            mutate = stack.enter_context(patch.object(controller, 'mutate'))
            reserve = stack.enter_context(patch.object(controller, 'reserve'))
            yield p, saved, getter, stop, sleep, mutate, reserve, clock

    def test_exact_running_replica_capture_retains_native_mapping_without_dispatch(self):
        running = native_execution(terminal=False)
        with self.fixture([running, replicas(), copy.deepcopy(running)]) as (p, saved, get, stop, sleep, mutate, reserve, _):
            value = controller.capture_bound(p, start_ack())
        self.assertEqual(3, get.call_count); stop.assert_not_called(); sleep.assert_not_called()
        mutate.assert_not_called(); reserve.assert_not_called()
        self.assertEqual('replica-binding.json', saved[0][1]); self.assertEqual(running, value['raw_execution'])
        self.assertEqual(replicas(), value['raw_replicas']); self.assertEqual(replicas()['value'][0], value['binding'])
        self.assertEqual(RECEIPT, value['start_ack_receipt']); self.assertEqual(0, value['sql_calls']); self.assertEqual(0, value['model_calls'])
        for key in controller.FALSE_FLAGS: self.assertIs(value[key], False)

    def test_only_explicit_processing_empty_complete_or_registered_not_found_may_poll(self):
        running = native_execution(terminal=False); processing = copy.deepcopy(running); processing['properties']['status'] = 'Processing'
        absent = controller.NativeHttpError(404, b'{"error":{"code":"ResourceNotFound","message":"not ready"}}')
        cases = ([processing, running, replicas(), running], [running, {'value': [], 'nextLink': None}, running, replicas(), running],
            [absent, running, replicas(), running])
        for responses in cases:
            with self.fixture(responses) as (p, saved, get, stop, sleep, mutate, reserve, _), self.subTest(first=type(responses[0]).__name__):
                controller.capture_bound(p, start_ack())
            self.assertEqual(1, len(saved)); self.assertEqual(1, sleep.call_count); stop.assert_not_called()
            mutate.assert_not_called(); reserve.assert_not_called()

    def test_bad_first_then_clean_metadata_cannot_be_washed_into_acceptance(self):
        running = native_execution(terminal=False)
        for change in ('foreign', 'template', 'unknownstatus', 'terminal', 'duplicate', 'foreignreplica', 'restart', 'partial', 'drift', 'bad404'):
            before = copy.deepcopy(running); native_replicas = replicas(); after = copy.deepcopy(running)
            if change == 'foreign': before['id'] = before['id'].replace(controller.JOB, contract.JOB)
            elif change == 'template': before['properties']['template']['containers'][0]['image'] += '-other'
            elif change == 'unknownstatus': before['properties']['status'] = 'Unexpected'
            elif change == 'terminal': before = native_execution()
            elif change == 'duplicate': native_replicas['value'].append(copy.deepcopy(native_replicas['value'][0]))
            elif change == 'foreignreplica': native_replicas['value'][0]['name'] += '-other'
            elif change == 'restart': native_replicas['value'][0]['containers'][0]['restartCount'] = 1
            elif change == 'partial': native_replicas['nextLink'] = 'https://example.invalid/continued'
            elif change == 'drift': after['properties']['startTime'] = '2026-10-04T20:30:02Z'
            else: before = controller.NativeHttpError(404, b'{"error":{"code":"ContainerNotFound","message":"wrong"}}')
            responses = [before, native_replicas, after, running, replicas(), running]
            with self.fixture(responses) as (p, saved, get, stop, sleep, mutate, reserve, _), self.subTest(change=change), \
                    self.assertRaisesRegex(ValueError, 'capture_validation_unqualified'):
                controller.capture_bound(p, start_ack())
            self.assertEqual([], saved); stop.assert_called_once_with(start_ack()); sleep.assert_not_called()
            self.assertLessEqual(get.call_count, 3); mutate.assert_not_called(); reserve.assert_not_called()

    def test_readiness_attempts_and_deadline_are_bounded_without_structural_stop_or_start_retry(self):
        processing = native_execution(terminal=False); processing['properties']['status'] = 'Processing'
        with self.fixture([processing] * 45) as (p, saved, get, stop, sleep, mutate, reserve, _), self.assertRaises(controller.CapturePending):
            controller.capture_bound(p, start_ack())
        self.assertEqual(45, get.call_count); self.assertEqual(44, sleep.call_count); self.assertEqual([], saved)
        stop.assert_not_called(); mutate.assert_not_called(); reserve.assert_not_called()
        with self.fixture([], times=[0, 91, 91]) as (p, saved, get, stop, sleep, mutate, reserve, _), self.assertRaises(controller.CapturePending):
            controller.capture_bound(p, start_ack())
        get.assert_not_called(); sleep.assert_not_called(); stop.assert_not_called(); self.assertEqual([], saved)

    def test_explicit_not_found_readiness_has_exact_native_status_and_error_shape(self):
        for status, raw, allowed in ((404, b'{"error":{"code":"ResourceNotFound","message":"x"}}', True),
                (403, b'{"error":{"code":"ResourceNotFound","message":"x"}}', False),
                (404, b'{"error":{"code":"BlobNotFound","message":"x"}}', False),
                (404, b'{"error":{"code":"ResourceNotFound","message":"x","extra":0}}', False),
                (404, b'{"error":{"code":"ResourceNotFound","message":null}}', False),
                (404, b'{"error":{"code":"Other","code":"ResourceNotFound","message":"x"}}', False)):
            with self.subTest(status=status, raw=raw):
                self.assertIs(controller.capture_not_found(controller.NativeHttpError(status, raw)), allowed)
        self.assertIs(controller.capture_not_found(ValueError('not found')), False)

    def test_start_downgrades_only_explicit_readiness_and_never_structural_failure(self):
        p = plan_pf(); ack = start_ack()
        for error in (controller.CapturePending(), ValueError('structural failure')):
            output = io.StringIO()
            with patch.object(controller, 'prepared', return_value=({'protected_receipt': RECEIPT}, p)), \
                    patch.object(controller, 'arm', return_value=Mock()), patch.object(controller, 'capture_permitted'), \
                    patch.object(controller, 'install_chain', return_value=({'protected_receipt': prior_receipt('start')}, {}, {}, {})), \
                    patch.object(controller, 'inventory', return_value={'value': []}), patch.object(controller, 'mutate', return_value=ack) as mutate, \
                    patch.object(controller, 'capture_bound', side_effect=error), contextlib.redirect_stdout(output):
                if type(error) is controller.CapturePending: self.assertEqual(ack, controller.start())
                else:
                    with self.assertRaisesRegex(ValueError, 'structural failure'): controller.start()
            mutate.assert_called_once(); self.assertEqual('start', mutate.call_args.args[0])
            if type(error) is controller.CapturePending:
                emitted = json.loads(output.getvalue()); self.assertIs(emitted['automatic_start_retry'], False)
                self.assertIs(emitted['start_attempt_consumed'], True)
            else: self.assertEqual('', output.getvalue())

    def test_local_stop_or_remote_stop_blocks_later_capture_collection_and_verification(self):
        for local in ('capture-stop-intent.json', 'capture-diagnostic.json', None):
            with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                    patch.object(controller, 'capture_control_blob', return_value=Mock()) as blob, \
                    patch.object(controller, 'prepared') as prepared, patch.object(controller, 'arm') as arm, \
                    patch.object(controller, 'mutate') as mutate, self.subTest(local=local):
                if local: Path(folder, local).touch()
                for action in (controller.capture, controller.collect, lambda: controller.collect(True)):
                    with self.assertRaisesRegex(ValueError, 'previously_unqualified'): action()
                prepared.assert_not_called(); arm.assert_not_called(); mutate.assert_not_called()
                if local: blob.assert_not_called()
                else: self.assertEqual(3, blob.call_count)

    def test_lost_capture_stop_upload_ack_keeps_local_validation_consumed(self):
        blob = Mock(); blob.upload_blob.side_effect = ValueError('sensitive-stop-provider-value')
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller, 'capture_control_blob', return_value=blob) as control, patch.object(controller, 'save') as save:
            with self.assertRaisesRegex(ValueError, 'stop_outcome_unverified') as caught: controller.capture_stop(start_ack())
            self.assertNotIn('sensitive-stop-provider-value', str(caught.exception))
            self.assertTrue(Path(folder, 'capture-stop-intent.json').is_file())
            with self.assertRaisesRegex(ValueError, 'previously_unqualified'): controller.capture_permitted()
            control.assert_called_once(); blob.upload_blob.assert_called_once(); save.assert_not_called()
            payload = json.loads(Path(folder, 'capture-stop-intent.json').read_text(encoding='utf-8'))
            self.assertEqual('native_capture_validation_failed', payload['reason']); self.assertIs(payload['capture_or_collection_authorized'], False)
            self.assertNotIn('sensitive-stop-provider-value', json.dumps(payload))
            kwargs = blob.upload_blob.call_args.kwargs
            self.assertIs(kwargs['overwrite'], False); self.assertEqual(0, kwargs['retry_total']); self.assertEqual(1, kwargs['max_concurrency'])


class TerminalCollectionTests(OfflineTestCase):
    @contextlib.contextmanager
    def fixture(self, rows, *, terminal=None, inventory_value=None, get_after=None):
        p = plan_pf(); ack = start_ack(); running = native_execution(terminal=False)
        captured = dict(status='native_running_replica_binding_observed', operation_id=controller.OPERATION_ID,
            trial_operation_id=controller.TRIAL_OPERATION_ID, start_ack_receipt=ack['protected_receipt'],
            raw_execution=running, raw_replicas=replicas(), binding=replicas()['value'][0])
        terminal = native_execution() if terminal is None else terminal
        saved = []; retained = {}; log_calls = Mock(side_effect=lambda scope: console_response(rows))
        def load(name):
            if name == 'replica-binding.json': return {'protected_receipt': RECEIPT}, copy.deepcopy(captured)
            return {'protected_receipt': RECEIPT}, copy.deepcopy(retained[name])
        def save(value, name):
            saved.append((copy.deepcopy(value), name)); retained[name] = copy.deepcopy(value)
            return dict(value, protected_receipt=RECEIPT)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(controller, 'capture_permitted'))
            stack.enter_context(patch.object(controller, 'prepared', return_value=({'protected_receipt': RECEIPT}, p)))
            stack.enter_context(patch.object(controller, 'arm', return_value=Mock()))
            stack.enter_context(patch.object(controller, 'install_chain', return_value=({'protected_receipt': RECEIPT}, new_job(), identity(), permission())))
            stack.enter_context(patch.object(controller, 'start_chain', return_value=(ack, EXECUTION)))
            stack.enter_context(patch.object(controller, 'load', side_effect=load))
            stack.enter_context(patch.object(controller, 'save', side_effect=save))
            getter = stack.enter_context(patch.object(controller, 'get', side_effect=lambda *a, **k:
                copy.deepcopy(terminal if get_after is None or getter.call_count % 2 else get_after)))
            stack.enter_context(patch.object(controller, 'inventory', return_value={'value': [terminal]} if inventory_value is None else inventory_value))
            stack.enter_context(patch.object(controller, 'job_observation', return_value=new_job()))
            stack.enter_context(patch.object(controller, 'roles', return_value=permission()))
            stack.enter_context(patch.object(controller, 'resource_guards', return_value=p['resource_guards']))
            stack.enter_context(patch.object(controller.previous, 'sources', return_value=p['sources']))
            stack.enter_context(patch.object(controller, 'LogsTransport', return_value=SimpleNamespace(call=log_calls)))
            mutate = stack.enter_context(patch.object(controller, 'mutate'))
            reserve = stack.enter_context(patch.object(controller, 'reserve'))
            yield p, saved, retained, log_calls, mutate, reserve

    def test_full_runtime_event_acceptance_and_fresh_verification_never_dispatch_or_resave(self):
        with self.fixture([console_row()]) as (_, saved, retained, query, mutate, reserve):
            result = controller.collect(); result_before = copy.deepcopy(result)
            verified = controller.collect(True)
            self.assertEqual(result_before, result); self.assertEqual(1, len(saved)); self.assertEqual(4, query.call_count)
            self.assertEqual('independent_secretless_valid_name_aca_runtime_verified', verified['status'])
        mutate.assert_not_called(); reserve.assert_not_called()
        self.assertEqual('acceptance.json', saved[0][1]); self.assertIs(result['acceptance_recorded'], True)
        self.assertEqual(observed(), result['observed']); self.assertEqual([console_row()], result['complete_console_events'])
        self.assertIs(result['registry_pull_success_observed'], True)
        for key in controller.FALSE_FLAGS: self.assertIs(result[key], False)
        self.assertIs(result['complete_console_observation']['platform_replacement_excluded'], False)
        self.assertEqual(0, result['sql_calls']); self.assertEqual(0, result['model_calls'])

    def test_extra_console_event_is_preserved_unqualified_not_filtered_or_restarted(self):
        rows = [console_row(), dict(console_row(), Container='other-container', Log='unexpected full event')]
        with self.fixture(rows) as (_, saved, retained, query, mutate, reserve):
            result = controller.collect(); verified = controller.collect(True)
        self.assertEqual('terminal_valid_name_aca_runtime_unqualified', result['status']); self.assertIsNone(result['observed'])
        self.assertEqual(sorted(rows, key=controller.canonical), result['complete_console_events'])
        self.assertIs(result['acceptance_recorded'], False); self.assertIs(result['registry_pull_success_observed'], False)
        self.assertEqual('terminal-diagnostic.json', saved[0][1]); self.assertEqual(1, len(saved)); self.assertEqual(4, query.call_count)
        self.assertEqual('independent_valid_name_aca_diagnostic_verified', verified['status']); mutate.assert_not_called(); reserve.assert_not_called()

    def test_missing_logs_family_drift_or_changed_terminal_execution_never_records_acceptance(self):
        bad_after = native_execution(); bad_after['properties']['endTime'] = '2026-10-04T20:32:02Z'
        duplicate = {'value': [native_execution(), native_execution()]}
        for rows, inventory_value, after in (([], None, None), ([console_row()], duplicate, None), ([console_row()], None, bad_after)):
            with self.fixture(rows, inventory_value=inventory_value, get_after=after) as (_, saved, _, _, mutate, reserve), \
                    self.subTest(rows=len(rows), duplicate=inventory_value is not None, changed=after is not None), self.assertRaises(ValueError):
                controller.collect()
            self.assertEqual([], saved); mutate.assert_not_called(); reserve.assert_not_called()

    def test_verify_rejects_changed_retained_evidence_and_unknown_boolean_without_resave(self):
        for change in ('flag', 'counter', 'hash', 'events'):
            with self.fixture([console_row()]) as (_, saved, retained, _, mutate, reserve), self.subTest(change=change):
                controller.collect(); value = retained['acceptance.json']
                if change == 'flag': value[controller.FALSE_FLAGS[0]] = 0
                elif change == 'counter': value['sql_calls'] = False
                elif change == 'hash': value['complete_console_sha256'] = 'f' * 64
                else: value['complete_console_events'] = []
                with self.assertRaisesRegex(ValueError, 'live_and_retained_evidence_differs'): controller.collect(True)
                self.assertEqual(1, len(saved)); mutate.assert_not_called(); reserve.assert_not_called()
        for invalid in (None, 0, 1, 'true'):
            with patch.object(controller, 'capture_permitted') as guard, self.subTest(invalid=invalid), self.assertRaises(ValueError):
                controller.collect(invalid)
            guard.assert_not_called()


if __name__ == '__main__':
    unittest.main()
