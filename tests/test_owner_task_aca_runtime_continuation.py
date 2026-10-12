"""Offline continuation gates; no Azure, SQL, secrets or runtime acceptance."""
import contextlib
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from azure.core.exceptions import HttpResponseError
from azure.storage.blob import StorageErrorCode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import qualify_owner_task_aca_runtime_continuation as controller
import qualify_owner_task_aca_runtime as original
import owner_task_aca_runtime_contract as contract
import owner_task_aca_runtime_transport as transport
from test_owner_task_aca_runtime_contract import descriptor, observed_job, source

PRINCIPAL = '824dd8fe-7c16-45ac-8d6b-571779872bc9'
CLIENT = '1a2715b3-f843-42a2-b4e6-f49ee5fd3c11'
TENANT = '0578a50b-a7a1-40a4-905b-8b20dba84124'
EXECUTION = contract.JOB + '-abcdefgh'
REPLICA = EXECUTION + '-abcde'
RECEIPT = {'sha256': 'a' * 64, 'blob': 'test-only',
    'version_id': '2026-10-04T19:00:00.0000000Z', 'protected_readback_verified': True}
ALIAS = contract.IDENTITY_ID.replace('/resourceGroups/', '/resourcegroups/')


def identity(*, alias=True):
    return dict(id=ALIAS if alias else contract.IDENTITY_ID, name=contract.IDENTITY,
        type='Microsoft.ManagedIdentity/userAssignedIdentities', location='eastasia',
        tags=transport.identity_body()['tags'], properties=dict(principalId=PRINCIPAL,
            clientId=CLIENT, tenantId=TENANT))


def old_sources():
    return {'file-' + str(i): 'a' * 64 for i in range(725)}


def observed():
    return dict(operation_id=contract.PROBE_FAMILY_ID, image=contract.IMAGE,
        runtime_source_sha256=contract.RUNTIME_SOURCE_SHA256, runtime=descriptor())


def plan_pf():
    return dict(prerequisite={'expected_runtime': descriptor(), 'observed': observed()},
        plan=contract.build(source(), descriptor()), sources={'test-only': 'a' * 64},
        resource_guards={}, history={'identity': identity(), 'receipts': {'identity-ack.json': prior_receipt('grant')}})


def execution(*, terminal=True):
    return dict(id=contract.JOB_ID + '/executions/' + EXECUTION, name=EXECUTION,
        type='Microsoft.App/jobs/executions', properties=dict(
            status='Succeeded' if terminal else 'Running',
            startTime='2026-10-04T19:00:00.0000000Z',
            endTime='2026-10-04T19:02:01.0000000Z' if terminal else None,
            template=original.start_body(plan_pf())))


def alias_job():
    value = observed_job()
    assigned = value['identity']['userAssignedIdentities']
    assigned[ALIAS] = assigned.pop(contract.IDENTITY_ID)
    config = value['properties']['configuration']
    config['registries'][0]['identity'] = ALIAS
    config['identitySettings'][0]['identity'] = ALIAS
    return value


def console_row():
    return dict(Group=REPLICA, Container=contract.CONTAINER, LogTime='2026-10-04T19:00:01Z',
        Log=contract.PREFIX + json.dumps(observed(), sort_keys=True, separators=(',', ':')))


def replicas():
    return {'value': [dict(id=contract.JOB_ID + '/executions/' + EXECUTION + '/replicas/' + REPLICA,
        name=REPLICA, type='Microsoft.App/containerapps/revisions/replicas',
        createdTime='2026-10-04T19:00:00Z', runningState='Running', containers=[dict(
            name=contract.CONTAINER, ready=True, started=True, restartCount=0, runningState='Running')])], 'nextLink': None}


def console_response(rows):
    return {'tables': [dict(name='PrimaryResult', columns=[dict(name=n, type=t)
        for n, t in transport.CONSOLE_COLUMNS], rows=[[row[n] for n, _ in transport.CONSOLE_COLUMNS] for row in rows])]}


def checks():
    return dict(test_count=2235, passed_test_count=2235, skipped_tests=[], unittest_exit_code=0,
        compilation_exit_code=0, repository_policy='PASS: synthetic offline fixture',
        live_sql_tests_passed=False, financial_or_visual_acceptance=False,
        os_or_dependency_security_certification=False)


def protected(value, kind, *, version='2026-10-04T19:00:00.0000000Z'):
    value = copy.deepcopy(value)
    sha = hashlib.sha256(original.canonical(value)).hexdigest()
    value['protected_receipt'] = dict(blob='acceptance/gateway-read-owner-task-pg-' + kind + '-' + sha + '.json',
        sha256=sha, version_id=version, protected_readback_verified=True)
    return value


def prior_receipt(stage):
    kind = {'grant': 'aca-runtime-identity-ack', 'install': 'aca-runtime-continuation-grant-acceptance',
        'start': 'aca-runtime-continuation-install-acceptance'}[stage]
    return dict(RECEIPT, blob='acceptance/gateway-read-owner-task-pg-' + kind + '-' + RECEIPT['sha256'] + '.json')


def identity_history():
    """Embedded synthetic complete records; never depend on .work/live receipts."""
    p = plan_pf()
    p.update(status='prepared_secretless_aca_runtime', operation_id=original.OPERATION_ID,
        checked_at_utc='2026-10-04T19:00:00+00:00', sources=old_sources(), local_checks=checks(),
        sql_calls=0, model_calls=0, daily_schedule_hkt='18:00',
        **{key: False for key in original.FALSE_FLAGS})
    pf_raw = protected(p, 'aca-runtime-preflight')
    intent = original.intent_value('identity', pf_raw, contract.IDENTITY_ID, transport.MI_API,
        'PUT', transport.identity_body(), {})
    reservation = dict(blob=original.PREFIX + 'identity-reservation.json',
        sha256=hashlib.sha256(original.canonical(intent)).hexdigest(),
        version_id='2026-10-04T19:00:01.0000000Z', etag='"0xABC"', protected_readback_verified=True)
    intent_raw = protected(dict(intent, status='reserved_one_exact_cloud_effect', reservation_receipt=reservation),
        'aca-runtime-identity-intent', version='2026-10-04T19:00:02.0000000Z')
    ack_raw = protected(dict(status='one_exact_cloud_effect_acknowledged', operation_id=original.OPERATION_ID,
        stage='identity', intent_receipt=intent_raw['protected_receipt'], http_status=201,
        raw_ack=identity(), attempt_consumed=True, automatic_retry=False),
        'aca-runtime-identity-ack', version='2026-10-04T19:00:03.0000000Z')
    return pf_raw, intent_raw, ack_raw


def body(raw):
    return {key: value for key, value in raw.items() if key != 'protected_receipt'}


def pins(records):
    return {name: (raw['protected_receipt']['sha256'], raw['protected_receipt']['version_id'])
        for name, raw in zip(('preflight.json', 'identity-intent.json', 'identity-ack.json'), records)}


@contextlib.contextmanager
def synthetic_history_pins(records, *, reservation=None):
    with patch.object(controller, 'ORIGINAL_PINS', pins(records)), \
            patch.object(controller, 'ORIGINAL_RESERVATION', reservation or records[1]['reservation_receipt']):
        yield


def fresh_pf():
    value = plan_pf(); value['history']['sources'] = old_sources()
    value['sources'] = dict(old_sources(), **{name: 'b' * 64 for name in controller.ADDITIONS})
    value.update(status='prepared_secretless_aca_runtime_continuation', operation_id=controller.OPERATION_ID,
        trial_operation_id=controller.TRIAL_OPERATION_ID, identity=identity(), local_checks=checks(),
        **{key: False for key in controller.FALSE_FLAGS})
    return value


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.offline = contextlib.ExitStack()
        self.addCleanup(self.offline.close)
        for target in ('azure.identity.AzureCliCredential.get_token', 'requests.sessions.Session.request',
                'subprocess.check_output', 'subprocess.run'):
            self.offline.enter_context(patch(target, side_effect=AssertionError('offline fixture attempted external access')))


class SourceAndAliasTests(OfflineTestCase):
    def test_native_copy_supports_full_preflight_over_64k_but_enforces_two_mib_bound(self):
        value = {'metadata': ['x' * 60000] * 4, 'native': {'count': 0, 'enabled': False}}
        self.assertGreater(len(controller.canonical(value)), 65536)
        copied = controller.native_copy(value)
        self.assertEqual(value, copied); self.assertIsNot(value, copied); self.assertIsNot(value['native'], copied['native'])
        with self.assertRaises(ValueError): controller.native_copy({'metadata': ['x' * 60000] * 36})

    def test_native_copy_rejects_subclass_hooks_before_copy_or_serialization(self):
        touched = []
        class EvilDict(dict):
            def __iter__(self): touched.append('iterate'); raise AssertionError('must not iterate')
            def __deepcopy__(self, memo): touched.append('deepcopy'); raise AssertionError('must not deepcopy')
        class EvilList(list):
            def __iter__(self): touched.append('iterate'); raise AssertionError('must not iterate')
        class EvilString(str):
            def __eq__(self, value): touched.append('eq'); raise AssertionError('must not compare')
            __hash__ = str.__hash__
        for value in (EvilDict(x=1), {'nested': EvilDict(x=1)}, EvilList([1]),
                {'nested': EvilList([1])}, EvilString('value'), {EvilString('key'): 'value'}):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError): controller.native_copy(value)
        self.assertEqual([], touched)

    def test_exact_725_plus_two_additions_with_no_changed_original_sources(self):
        old = old_sources(); now = dict(old, **{name: 'b' * 64 for name in controller.ADDITIONS})
        controller.validate_sources(old, now)
        self.assertEqual(2, len(controller.ADDITIONS)); self.assertEqual(727, len(now))
        for change in ('extra', 'missing', 'changed', 'wronghash', 'wrongcount'):
            altered = dict(now); baseline = dict(old)
            if change == 'extra': altered['other.py'] = 'c' * 64
            elif change == 'missing': del altered[next(iter(controller.ADDITIONS))]
            elif change == 'changed': altered['file-1'] = 'c' * 64
            elif change == 'wronghash': altered[next(iter(controller.ADDITIONS))] = True
            else: del baseline['file-1']
            with self.subTest(change=change), self.assertRaises(ValueError):
                controller.validate_sources(baseline, altered)

    def test_only_known_resourcegroups_structural_alias_is_canonicalized(self):
        for value in (contract.IDENTITY_ID, ALIAS):
            self.assertEqual(contract.IDENTITY_ID, controller.normalized_mi_id(value))
        for value in (None, True, ALIAS + '/', ALIAS + '-other', ALIAS.lower(),
                ALIAS.replace('/resourcegroups/', '/RESOURCEGROUPS/'),
                ALIAS.replace('/subscriptions/', '/Subscriptions/'),
                ALIAS.replace('/providers/', '/Providers/'),
                ALIAS.replace('Microsoft.ManagedIdentity', 'microsoft.managedidentity'),
                ALIAS.replace('/resourcegroups/', '/resource%67roups/'),
                ALIAS.replace(contract.IDENTITY, 'other-identity'),
                ALIAS.replace(contract.RESOURCE_GROUP, contract.RESOURCE_GROUP + '-other')):
            with self.subTest(value=value), self.assertRaises(ValueError): controller.normalized_mi_id(value)

    def test_identity_readback_alias_validation_retains_exact_native_value(self):
        for alias in (False, True):
            native = identity(alias=alias); before = copy.deepcopy(native)
            checked = controller.validate_identity_metadata(native)
            self.assertEqual(before, checked); self.assertEqual(before, native)
            self.assertIsNot(native, checked); self.assertIsNot(native['properties'], checked['properties'])
            self.assertEqual(ALIAS if alias else contract.IDENTITY_ID, checked['id'])

    def test_identity_alias_does_not_allow_foreign_principal_tenant_metadata(self):
        for change in ('id', 'name', 'type', 'tenant', 'principal', 'client', 'purpose', 'operation', 'extra'):
            native = identity()
            if change == 'id': native['id'] += '-other'
            elif change == 'name': native['name'] += '-other'
            elif change == 'type': native['type'] = 'Microsoft.ManagedIdentity/other'
            elif change == 'tenant': native['properties']['tenantId'] = '11111111-1111-4111-8111-111111111111'
            elif change == 'principal': native['properties']['principalId'] = True
            elif change == 'client': native['properties']['clientId'] = None
            elif change == 'purpose': native['tags']['purpose'] = 'another-purpose'
            elif change == 'operation': native['tags']['operation'] = controller.OPERATION_ID
            else: native['properties']['anotherAuthority'] = True
            with self.subTest(change=change), self.assertRaises(ValueError): controller.validate_identity_metadata(native)

    def test_job_only_three_known_identity_references_are_normalized_in_copy(self):
        native = alias_job(); before = copy.deepcopy(native)
        normalized = controller.normalized_job(native)
        self.assertEqual(before, native)
        self.assertEqual({contract.IDENTITY_ID}, set(normalized['identity']['userAssignedIdentities']))
        config = normalized['properties']['configuration']
        self.assertEqual(contract.IDENTITY_ID, config['registries'][0]['identity'])
        self.assertEqual(contract.IDENTITY_ID, config['identitySettings'][0]['identity'])
        checked = controller.validate_job_metadata(native, descriptor(), PRINCIPAL, CLIENT)
        self.assertEqual(before, checked); self.assertIsNot(native, checked)
        self.assertEqual({ALIAS}, set(checked['identity']['userAssignedIdentities']))

    def test_conflicting_or_foreign_job_identity_references_are_not_aliases(self):
        for change in ('both', 'extraassigned', 'registry', 'lifecycleidentity', 'principal', 'client', 'sidecar', 'secret'):
            native = alias_job()
            if change == 'both': native['identity']['userAssignedIdentities'][contract.IDENTITY_ID] = dict(principalId=PRINCIPAL, clientId=CLIENT)
            elif change == 'extraassigned': native['identity']['userAssignedIdentities'][ALIAS + '-other'] = {}
            elif change == 'registry': native['properties']['configuration']['registries'][0]['identity'] = ALIAS + '-other'
            elif change == 'lifecycleidentity': native['properties']['configuration']['identitySettings'][0]['identity'] = ALIAS.lower()
            elif change == 'principal': native['identity']['userAssignedIdentities'][ALIAS]['principalId'] = CLIENT
            elif change == 'client': native['identity']['userAssignedIdentities'][ALIAS]['clientId'] = PRINCIPAL
            elif change == 'sidecar': native['properties']['template']['containers'].append(copy.deepcopy(native['properties']['template']['containers'][0]))
            else: native['properties']['configuration']['secrets'] = [{'name': 'forbidden', 'value': 'test-only'}]
            with self.subTest(change=change), self.assertRaises(ValueError):
                controller.validate_job_metadata(native, descriptor(), PRINCIPAL, CLIENT)

    def test_identity_stage_is_never_rearmed_by_continuation(self):
        self.assertEqual(('grant', 'install', 'start'), controller.STAGES)
        self.assertFalse(hasattr(controller, 'identity_create'))
        with patch.object(controller, 'reservation_blob') as blob, self.assertRaises(ValueError):
            controller.reserve('identity', {})
        blob.assert_not_called()


class HistoryTests(OfflineTestCase):
    def test_exact_three_original_pins_and_reservation_are_literal_consumed_scope(self):
        self.assertEqual({
            'preflight.json': ('74ec1f9e19244473dc13a3e8e766462c2ff5c22dc361e88bc004b4a51d7de9b9', '2026-10-04T19:04:32.2219643Z'),
            'identity-intent.json': ('9bb0ebd0ce764d54c115bf040d18e52e8332ba25d23b962e23e6771160c609d2', '2026-10-04T19:09:04.5826655Z'),
            'identity-ack.json': ('9a243b2f79205589301c7ac3486d1a3fec6af98357de9cabdb4eb29974c512e7', '2026-10-04T19:10:03.3770156Z')}, controller.ORIGINAL_PINS)
        self.assertEqual(original.PREFIX + 'identity-reservation.json', controller.ORIGINAL_RESERVATION['blob'])
        self.assertEqual('604eb7c26a2b6c469afb959c050c6b9823f4d0bac8a3a8732d3125bbc68a3601', controller.ORIGINAL_RESERVATION['sha256'])
        self.assertEqual('2026-10-04T19:08:40.1970296Z', controller.ORIGINAL_RESERVATION['version_id'])
        self.assertEqual('"0x8DF224AE69E7C78"', controller.ORIGINAL_RESERVATION['etag'])
        self.assertIs(controller.ORIGINAL_RESERVATION['protected_readback_verified'], True)

    def test_complete_pure_original_chain_preserves_native_ack_and_history(self):
        records = identity_history(); before = copy.deepcopy(records)
        with synthetic_history_pins(records), patch.object(controller.previous.q.deploy, 'protected', side_effect=AssertionError('pure only')), \
                patch.object(controller.frozen, 'reservation_readback', side_effect=AssertionError('pure only')):
            relation = controller.validate_original_identity_chain(*records)
        self.assertEqual(before, records)
        self.assertEqual(identity(), relation['identity']); self.assertEqual(ALIAS, relation['identity']['id'])
        self.assertEqual(old_sources(), relation['sources'])
        self.assertEqual(records[1]['reservation_receipt'], relation['reservation_receipt'])

    def test_changed_pin_or_receipt_hash_version_boolean_is_not_accepted(self):
        records = identity_history(); expected = pins(records)
        for change in ('sha', 'version', 'protectedbool', 'blob', 'payload'):
            altered = copy.deepcopy(records)
            if change == 'sha': altered[2]['protected_receipt']['sha256'] = 'f' * 64
            elif change == 'version': altered[2]['protected_receipt']['version_id'] = '2026-10-04T19:01:03.0000000Z'
            elif change == 'protectedbool': altered[2]['protected_receipt']['protected_readback_verified'] = 1
            elif change == 'blob': altered[2]['protected_receipt']['blob'] = 'other-scope'
            else: altered[2]['raw_ack']['properties']['principalId'] = CLIENT
            with patch.object(controller, 'ORIGINAL_PINS', expected), \
                    patch.object(controller, 'ORIGINAL_RESERVATION', records[1]['reservation_receipt']), \
                    self.subTest(change=change), self.assertRaises(ValueError):
                controller.validate_original_identity_chain(*altered)

    def test_reprotected_semantic_intent_and_ack_mutations_are_still_refused(self):
        for change in ('method', 'path', 'body', 'prior', 'reservation', 'status', 'ackstatus',
                'ackoperation', 'ackstage', 'ackhttp', 'consumed', 'retry', 'ackextra', 'foreignidentity'):
            records = identity_history(); reservation = copy.deepcopy(records[1]['reservation_receipt'])
            intent = body(records[1]); ack = body(records[2])
            if change == 'method': intent['method'] = 'POST'
            elif change == 'path': intent['path'] = transport.ASSIGNMENT_ID
            elif change == 'body': intent['body']['tags']['purpose'] = 'other'
            elif change == 'prior': intent['prior_receipts'] = {'another': RECEIPT}
            elif change == 'reservation': intent['reservation_receipt']['etag'] = '"0xDEF"'
            elif change == 'status': intent['status'] = 'unreserved'
            elif change == 'ackstatus': ack['status'] = 'unacknowledged'
            elif change == 'ackoperation': ack['operation_id'] = controller.OPERATION_ID
            elif change == 'ackstage': ack['stage'] = 'grant'
            elif change == 'ackhttp': ack['http_status'] = 201.0
            elif change == 'consumed': ack['attempt_consumed'] = 1
            elif change == 'retry': ack['automatic_retry'] = 0
            elif change == 'ackextra': ack['anotherProof'] = True
            else: ack['raw_ack']['id'] += '-foreign'
            records = (records[0], protected(intent, 'aca-runtime-identity-intent'))
            ack['intent_receipt'] = records[1]['protected_receipt']
            records += (protected(ack, 'aca-runtime-identity-ack'),)
            with synthetic_history_pins(records, reservation=reservation), self.subTest(change=change), self.assertRaises(ValueError):
                controller.validate_original_identity_chain(*records)

    def test_history_independently_rechecks_three_receipts_and_original_reservation(self):
        records = identity_history(); mapping = dict(zip(controller.ORIGINAL_PINS, records))
        events = []
        with tempfile.TemporaryDirectory() as folder, synthetic_history_pins(records), patch.object(controller.frozen, 'FOLDER', Path(folder)), \
                patch.object(controller.previous.q.readback, 'read_local', side_effect=lambda f, n: copy.deepcopy(mapping[n])), \
                patch.object(controller.previous.q.deploy, 'protected', side_effect=lambda raw, kind: events.append(kind) or body(raw)), \
                patch.object(controller.frozen, 'reservation_readback', side_effect=lambda *a: events.append(('reservation', a))) as remote, \
                patch.object(controller, 'original_reservation_absent') as absence:
            relation = controller.history()
        self.assertEqual(['aca-runtime-preflight', 'aca-runtime-identity-intent', 'aca-runtime-identity-ack'], events[:3])
        self.assertEqual('reservation', events[3][0]); remote.assert_called_once()
        self.assertEqual('identity', remote.call_args.args[0]); self.assertEqual(relation['reservation_receipt'], remote.call_args.args[1])
        self.assertEqual([(('grant',), {}), (('install',), {}), (('start',), {})], absence.call_args_list)

    def test_old_downstream_intent_ack_or_outcome_prevents_continuation(self):
        records = identity_history(); mapping = dict(zip(controller.ORIGINAL_PINS, records))
        for stage in controller.STAGES:
            for suffix in ('intent', 'ack', 'outcome'):
                with tempfile.TemporaryDirectory() as folder, synthetic_history_pins(records), patch.object(controller.frozen, 'FOLDER', Path(folder)), \
                        patch.object(controller.previous.q.readback, 'read_local', side_effect=lambda f, n: copy.deepcopy(mapping[n])), \
                        patch.object(controller.previous.q.deploy, 'protected', side_effect=lambda raw, kind: body(raw)), \
                        patch.object(controller.frozen, 'reservation_readback'), patch.object(controller, 'original_reservation_absent'):
                    Path(folder, stage + '-' + suffix + '.json').touch()
                    with self.subTest(stage=stage, suffix=suffix), self.assertRaisesRegex(ValueError, 'original_downstream_attempt_exists'):
                        controller.history()

    def test_fresh_identity_binding_requires_entire_native_ack_not_just_same_normalized_id(self):
        p = plan_pf()
        with patch.object(controller, 'identity_observation', return_value=identity()):
            self.assertEqual((prior_receipt('grant'), identity()), controller.identity_binding(p, Mock()))
        for change in ('canonicalid', 'principal', 'client', 'location', 'tags'):
            value = identity()
            if change == 'canonicalid': value['id'] = contract.IDENTITY_ID
            elif change == 'principal': value['properties']['principalId'] = CLIENT
            elif change == 'client': value['properties']['clientId'] = PRINCIPAL
            elif change == 'location': value['location'] = 'East Asia'
            else: value['tags']['extra'] = 'unexpected'
            with patch.object(controller, 'identity_observation', return_value=value), self.subTest(change=change), self.assertRaisesRegex(ValueError, 'native_identity_ack_changed'):
                controller.identity_binding(p, Mock())

    def test_original_reservation_absence_accepts_only_exact_native_string_or_storage_enum(self):
        for code in ('BlobNotFound', StorageErrorCode.blob_not_found):
            controller.validate_original_reservation_absence(404, code)
            error = HttpResponseError('synthetic missing')
            error.status_code = 404; error.error_code = code
            blob = Mock(); blob.get_blob_properties.side_effect = error
            with patch.object(controller.frozen, 'reservation_blob', return_value=blob) as factory, \
                    patch.object(controller.previous.q.deploy, 'archive_protection') as protection:
                controller.original_reservation_absent('grant')
            factory.assert_called_once_with('grant'); protection.assert_called_once()
            blob.get_blob_properties.assert_called_once_with(timeout=10, retry_total=0)
        for status, code in ((True, 'BlobNotFound'), (404.0, 'BlobNotFound'), (403, 'BlobNotFound'),
                (404, 'ContainerNotFound'), (404, 'ResourceNotFound'), (404, None),
                (404, StorageErrorCode.container_not_found), (404, StorageErrorCode.blob_already_exists)):
            with self.subTest(status=status, code=code), self.assertRaises(ValueError):
                controller.validate_original_reservation_absence(status, code)
            error = HttpResponseError('sensitive-test-response-not-retained')
            error.status_code = status; error.error_code = code
            blob = Mock(); blob.get_blob_properties.side_effect = error
            with patch.object(controller.frozen, 'reservation_blob', return_value=blob), \
                    patch.object(controller.previous.q.deploy, 'archive_protection'), self.assertRaises(ValueError) as denied:
                controller.original_reservation_absent('grant')
            self.assertNotIn('sensitive-test-response', str(denied.exception))
        with patch.object(controller.frozen, 'reservation_blob', return_value=Mock()), \
                self.assertRaisesRegex(ValueError, 'original_downstream_reservation_exists'):
            controller.original_reservation_absent('start')

    def test_remote_old_reservation_without_local_intent_still_blocks_continuation(self):
        records = identity_history(); mapping = dict(zip(controller.ORIGINAL_PINS, records))
        with tempfile.TemporaryDirectory() as folder, synthetic_history_pins(records), patch.object(controller.frozen, 'FOLDER', Path(folder)), \
                patch.object(controller.previous.q.readback, 'read_local', side_effect=lambda f, n: copy.deepcopy(mapping[n])), \
                patch.object(controller.previous.q.deploy, 'protected', side_effect=lambda raw, kind: body(raw)), \
                patch.object(controller.frozen, 'reservation_readback'), \
                patch.object(controller, 'original_reservation_absent', side_effect=ValueError('existing immutable reservation')), \
                self.assertRaisesRegex(ValueError, 'existing immutable reservation'):
            controller.history()


class PreparedTests(OfflineTestCase):
    def test_fast_capture_preflight_has_no_cloud_history_or_credentials(self):
        value = fresh_pf()
        with patch.object(controller, 'load', return_value=({'protected_receipt': RECEIPT}, value)), \
                patch.object(controller.previous, 'sources', return_value=value['sources']), \
                patch.object(controller, 'history', side_effect=AssertionError('fast no cloud')), \
                patch.object(controller, 'resource_guards', side_effect=AssertionError('fast no cloud')), \
                patch.object(controller, 'arm', side_effect=AssertionError('fast no cloud')):
            self.assertEqual(value, controller.prepared(cloud=False)[1])

    def test_preflight_unknown_cloud_bool_source_plan_identity_false_proof_refused(self):
        with patch.object(controller, 'load') as load:
            for flag in (0, 1, None, 'false'):
                with self.assertRaises(ValueError): controller.prepared(cloud=flag)
            load.assert_not_called()
        for change in ('source', 'plan', 'identity', 'flag', 'operation', 'trial', 'qa'):
            value = fresh_pf(); actual = copy.deepcopy(value['sources'])
            if change == 'source': actual['file-2'] = 'c' * 64
            elif change == 'plan': value['plan']['definition']['properties']['configuration']['replicaRetryLimit'] = 1
            elif change == 'identity': value['identity']['properties']['principalId'] = CLIENT
            elif change == 'flag': value['model_credential_containment_verified'] = 0
            elif change == 'operation': value['operation_id'] = original.OPERATION_ID
            elif change == 'trial': value['trial_operation_id'] = controller.OPERATION_ID
            else: value['local_checks']['live_sql_tests_passed'] = 0
            with patch.object(controller, 'load', return_value=({}, value)), patch.object(controller.previous, 'sources', return_value=actual), \
                    self.subTest(change=change), self.assertRaises(ValueError): controller.prepared(cloud=False)

    def test_prepare_validates_full_qa_then_current_native_identity_absence_and_sources_before_save(self):
        records = identity_history()
        with synthetic_history_pins(records): relation = controller.validate_original_identity_chain(*records)
        dep = dict(relation['prerequisite'], frozen_resources={})
        current = dict(old_sources(), **{name: 'b' * 64 for name in controller.ADDITIONS}); events = []
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller.previous, 'sources', return_value=current), \
                patch.object(controller, 'history', side_effect=lambda: events.append('history') or relation), \
                patch.object(controller, 'prerequisite', side_effect=lambda value: events.append('dependency') or dep), \
                patch.object(controller.previous.q.qa, 'run_local_checks', side_effect=lambda: events.append('qa') or checks()), \
                patch.object(controller, 'resource_guards', side_effect=lambda: events.append('guards') or {}), \
                patch.object(controller, 'arm', return_value=Mock()), \
                patch.object(controller, 'identity_observation', side_effect=lambda client: events.append('identity') or identity()), \
                patch.object(controller, 'roles', side_effect=lambda *a, **k: events.append('roles') or {}), \
                patch.object(controller, 'get', side_effect=lambda *a, **k: events.append('absence') or None), \
                patch.object(controller, 'environment', side_effect=lambda client: events.append('environment') or {}), \
                patch.object(controller, 'save', side_effect=lambda value, name: events.append('save') or value):
            result = controller.prepare()
        self.assertEqual(['history', 'dependency', 'qa', 'guards', 'identity', 'roles', 'absence', 'absence', 'environment', 'save'], events)
        self.assertEqual('prepared_secretless_aca_runtime_continuation', result['status'])
        self.assertEqual(relation, result['history']); self.assertEqual(identity(), result['identity'])
        self.assertEqual(current, result['sources']); self.assertEqual(checks(), result['local_checks'])
        for key in controller.FALSE_FLAGS: self.assertIs(result[key], False)

    def test_prepare_invalid_qa_is_denied_before_new_current_reads_or_protected_save(self):
        records = identity_history()
        with synthetic_history_pins(records): relation = controller.validate_original_identity_chain(*records)
        current = dict(old_sources(), **{name: 'b' * 64 for name in controller.ADDITIONS})
        altered = checks(); altered['live_sql_tests_passed'] = 0
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller.previous, 'sources', return_value=current), patch.object(controller, 'history', return_value=relation), \
                patch.object(controller, 'prerequisite', return_value=relation['prerequisite']), \
                patch.object(controller.previous.q.qa, 'run_local_checks', return_value=altered), \
                patch.object(controller, 'resource_guards') as cloud, patch.object(controller, 'save') as save, self.assertRaises(ValueError):
            controller.prepare()
        cloud.assert_not_called(); save.assert_not_called()


class EffectTests(OfflineTestCase):
    def grant_args(self):
        return ('grant', {'protected_receipt': RECEIPT}, plan_pf(), transport.ASSIGNMENT_ID,
            transport.ROLE_API, 'PUT', transport.assignment_body(PRINCIPAL), {'identity': prior_receipt('grant')})

    def test_cross_stage_routes_bodies_principal_prior_refused_before_reservation(self):
        p = plan_pf()
        valid = [
            ('grant', transport.ASSIGNMENT_ID, transport.ROLE_API, 'PUT', transport.assignment_body(PRINCIPAL), {'identity': prior_receipt('grant')}),
            ('install', contract.JOB_ID, transport.JOB_API, 'PUT', p['plan']['definition'], {'grant': prior_receipt('install')}),
            ('start', contract.JOB_ID + '/start', transport.JOB_API, 'POST', controller.start_body(p), {'install': prior_receipt('start')})]
        for stage, path, api, method, value, prior in valid:
            controller.validate_effect_request(stage, path, api, method, value, p, prior)
            for change in ('identity', 'crossstage', 'method', 'api', 'body', 'prior', 'prior-extra', 'operation'):
                target, version, verb, wanted, ancestry = path, api, method, copy.deepcopy(value), copy.deepcopy(prior)
                effect_stage = stage
                if change == 'identity': target, version, verb, wanted = contract.IDENTITY_ID, transport.MI_API, 'PUT', transport.identity_body()
                elif change == 'crossstage': target = contract.JOB_ID + '/start' if stage != 'start' else transport.ASSIGNMENT_ID
                elif change == 'method': verb = 'POST' if method == 'PUT' else 'PUT'
                elif change == 'api': version = '2024-03-01'
                elif change == 'body':
                    if stage == 'grant': wanted['properties']['principalId'] = CLIENT
                    elif stage == 'install': wanted['properties']['configuration']['replicaRetryLimit'] = 1
                    else: wanted['containers'][0]['image'] = 'foreign:latest'
                elif change == 'prior': ancestry = {'wrong': RECEIPT}
                elif change == 'prior-extra': ancestry['other'] = RECEIPT
                else: effect_stage = 'identity'
                with patch.object(controller, 'reserve') as reserve, patch.object(controller, 'arm') as arm, \
                        self.subTest(stage=stage, change=change), self.assertRaises(ValueError):
                    controller.mutate(effect_stage, {'protected_receipt': RECEIPT}, p, target, version, verb, wanted, ancestry)
                reserve.assert_not_called(); arm.assert_not_called()

    def test_exact_effect_order_reservation_intent_freshchecks_one_request_ack(self):
        events = []; args = self.grant_args(); caller = Mock()
        caller.request.side_effect = lambda *a, **k: (events.append('request') or ({'name': transport.ASSIGNMENT}, 201))
        def save(value, name): events.append(name); return dict(value, protected_receipt=RECEIPT)
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller, 'reserve', side_effect=lambda *a: events.append('reserve') or RECEIPT), \
                patch.object(controller, 'save', side_effect=save), \
                patch.object(controller, 'reservation_readback', side_effect=lambda *a: events.append('readback')), \
                patch.object(controller.previous, 'sources', return_value=args[2]['sources']), patch.object(controller, 'arm', return_value=caller), \
                patch.object(controller, 'pre_effect_checks', side_effect=lambda *a: events.append('freshchecks')):
            acknowledged = controller.mutate(*args)
        self.assertEqual(['reserve', 'grant-intent.json', 'readback', 'freshchecks', 'request', 'grant-ack.json'], events)
        self.assertIs(acknowledged['attempt_consumed'], True); self.assertIs(acknowledged['automatic_retry'], False)
        self.assertEqual(controller.OPERATION_ID, acknowledged['operation_id'])
        self.assertEqual(controller.TRIAL_OPERATION_ID, acknowledged['trial_operation_id'])
        caller.request.assert_called_once_with(transport.ASSIGNMENT_ID, transport.ROLE_API, method='PUT', body=transport.assignment_body(PRINCIPAL))

    def test_external_body_mutation_after_validation_cannot_change_reserved_principal(self):
        args = self.grant_args(); wanted = args[6]; prior = args[7]; expected = copy.deepcopy(wanted); saved = []
        caller = Mock(); caller.request.return_value = ({}, 201)
        def reserve(*a):
            wanted['properties']['principalId'] = CLIENT
            prior['identity']['sha256'] = 'f' * 64
            return RECEIPT
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller, 'reserve', side_effect=reserve), \
                patch.object(controller, 'save', side_effect=lambda value, name: saved.append(copy.deepcopy(value)) or dict(value, protected_receipt=RECEIPT)), \
                patch.object(controller, 'reservation_readback'), patch.object(controller, 'pre_effect_checks'), \
                patch.object(controller.previous, 'sources', return_value=args[2]['sources']), patch.object(controller, 'arm', return_value=caller):
            controller.mutate(*args)
        self.assertEqual(expected, saved[0]['body']); self.assertEqual('a' * 64, saved[0]['prior_receipts']['identity']['sha256'])
        self.assertEqual(expected, caller.request.call_args.kwargs['body'])

    def test_unknown_effect_is_sanitized_consumed_and_not_replayed(self):
        args = self.grant_args(); saved = []; caller = Mock()
        caller.request.side_effect = RuntimeError('sensitive-test-value-not-evidence')
        with tempfile.TemporaryDirectory() as folder:
            def save(value, name):
                Path(folder, name).touch(); saved.append((copy.deepcopy(value), name))
                return dict(value, protected_receipt=RECEIPT)
            with patch.object(controller, 'FOLDER', Path(folder)), patch.object(controller, 'reserve', return_value=RECEIPT) as reserve, \
                    patch.object(controller, 'save', side_effect=save), patch.object(controller, 'reservation_readback'), \
                    patch.object(controller, 'pre_effect_checks'), patch.object(controller.previous, 'sources', return_value=args[2]['sources']), \
                    patch.object(controller, 'arm', return_value=caller):
                with self.assertRaisesRegex(ValueError, 'effect_unknown_attempt_consumed_inspect_only'): controller.mutate(*args)
                with self.assertRaisesRegex(ValueError, 'effect_consumed_inspect_only'): controller.mutate(*args)
            reserve.assert_called_once(); caller.request.assert_called_once()
        value, name = saved[-1]; self.assertEqual('grant-outcome.json', name)
        self.assertIs(value['attempt_consumed'], True); self.assertIs(value['automatic_retry'], False); self.assertIs(value['no_effects_verified'], False)
        self.assertNotIn('sensitive-test-value', json.dumps(saved))

    def test_local_consumed_effect_blocks_all_new_reservations_or_tokens(self):
        for suffix in ('intent', 'ack', 'outcome'):
            with tempfile.TemporaryDirectory() as folder:
                Path(folder, 'grant-' + suffix + '.json').touch()
                with patch.object(controller, 'FOLDER', Path(folder)), patch.object(controller, 'reserve') as reserve, \
                        patch.object(controller, 'arm') as arm, self.assertRaisesRegex(ValueError, 'effect_consumed_inspect_only'):
                    controller.mutate(*self.grant_args())
                reserve.assert_not_called(); arm.assert_not_called()

    def test_reservation_lost_ack_or_fresh_guard_failure_never_reaches_effect(self):
        args = self.grant_args()
        for phase in ('reserve', 'save', 'readback', 'freshchecks', 'source'):
            caller = Mock()
            with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                    patch.object(controller, 'reserve', side_effect=RuntimeError('lost reservation ACK') if phase == 'reserve' else None, return_value=RECEIPT), \
                    patch.object(controller, 'save', side_effect=RuntimeError('retention unavailable') if phase == 'save' else lambda value, name: dict(value, protected_receipt=RECEIPT)), \
                    patch.object(controller, 'reservation_readback', side_effect=ValueError('not read back') if phase == 'readback' else None), \
                    patch.object(controller, 'pre_effect_checks', side_effect=ValueError('scope changed') if phase == 'freshchecks' else None), \
                    patch.object(controller.previous, 'sources', return_value={} if phase == 'source' else args[2]['sources']), \
                    patch.object(controller, 'arm', return_value=caller), self.subTest(phase=phase), self.assertRaises((ValueError, RuntimeError)):
                controller.mutate(*args)
            caller.request.assert_not_called()

    def test_post_reservation_grant_or_job_or_start_execution_appearance_blocks_effect(self):
        for stage in controller.STAGES:
            p = plan_pf()
            with patch.object(controller, 'identity_binding', return_value=(prior_receipt('grant'), identity())), \
                    patch.object(controller, 'roles') as roles, patch.object(controller, 'get', return_value={'appeared': True}), \
                    patch.object(controller, 'job_observation'), patch.object(controller, 'inventory', return_value={'value': [execution()]}), \
                    self.subTest(stage=stage), self.assertRaises(ValueError):
                controller.pre_effect_checks(stage, Mock(), p, transport.assignment_body(PRINCIPAL) if stage == 'grant' else {})
            self.assertEqual(stage != 'grant', roles.call_args.kwargs['granted'])

    def test_exact_conditional_reservation_sdk_contract_and_pinned_version_readback(self):
        p = plan_pf(); value = controller.intent_value('grant', {'protected_receipt': RECEIPT}, transport.ASSIGNMENT_ID,
            transport.ROLE_API, 'PUT', transport.assignment_body(PRINCIPAL), {'identity': prior_receipt('grant')})
        payload = controller.canonical(value); sha = hashlib.sha256(payload).hexdigest()
        receipt = dict(blob=controller.PREFIX + 'grant-reservation.json', sha256=sha,
            version_id='2026-10-04T19:01:00.0000000Z', etag='"0xABC"', protected_readback_verified=True)
        write_blob = Mock(); write_blob.upload_blob.return_value = dict(version_id=receipt['version_id'], etag=receipt['etag'])
        read_blob = Mock(); read_blob.get_blob_properties.return_value = Mock(
            version_id=receipt['version_id'], etag=receipt['etag'], size=len(payload), blob_type='BlockBlob')
        read_blob.download_blob.return_value.readall.return_value = payload
        with patch.object(controller, 'reservation_blob', side_effect=[write_blob, read_blob]) as factory, \
                patch.object(controller.previous.q.deploy, 'archive_protection'):
            self.assertEqual(receipt, controller.reserve('grant', value))
        self.assertEqual([(('grant',), {}), (('grant', receipt['version_id']), {})], factory.call_args_list)
        kwargs = write_blob.upload_blob.call_args.kwargs
        self.assertIs(kwargs['overwrite'], False); self.assertEqual(controller.MatchConditions.IfMissing, kwargs['match_condition'])
        self.assertEqual(0, kwargs['retry_total']); self.assertEqual(1, kwargs['max_concurrency'])
        read = read_blob.download_blob.call_args.kwargs
        self.assertEqual(controller.MatchConditions.IfNotModified, read['match_condition']); self.assertEqual(receipt['etag'], read['etag'])
        self.assertEqual(0, read['retry_total']); self.assertEqual(len(payload) + 1, read['length'])


class CaptureAndCollectionTests(OfflineTestCase):
    def test_start_consumes_one_effect_and_capture_failure_never_restarts(self):
        p = plan_pf(); ack = dict(http_status=200,
            raw_ack={'id': contract.JOB_ID + '/executions/' + EXECUTION, 'name': EXECUTION}, protected_receipt=RECEIPT)
        with patch.object(controller, 'prepared', return_value=({'protected_receipt': RECEIPT}, p)), patch.object(controller, 'arm'), \
                patch.object(controller, 'install_chain', return_value=({'protected_receipt': prior_receipt('start')}, {}, identity(), {})), \
                patch.object(controller, 'inventory', return_value={'value': []}), patch.object(controller, 'roles'), \
                patch.object(controller, 'mutate', return_value=ack) as effect, \
                patch.object(controller, 'capture_bound', side_effect=ValueError('pending native readback')) as capture, patch('builtins.print'):
            self.assertEqual(ack, controller.start())
        effect.assert_called_once(); capture.assert_called_once_with(p, ack)
        self.assertEqual('start', effect.call_args.args[0]); self.assertEqual('POST', effect.call_args.args[5])

    def test_fast_capture_requires_exact_unchanged_running_execution_and_one_actual_replica(self):
        p = plan_pf(); native = execution(terminal=False); replica = replicas()
        ack = dict(http_status=200, raw_ack={'id': contract.JOB_ID + '/executions/' + EXECUTION, 'name': EXECUTION}, protected_receipt=RECEIPT)
        with patch.object(controller.previous, 'sources', return_value=p['sources']), patch.object(controller, 'arm'), \
                patch.object(controller, 'get', side_effect=[native, replica, native]), patch.object(controller, 'save', side_effect=lambda v, n: v):
            result = controller.capture_bound(p, ack)
        self.assertEqual(replica, result['raw_replicas']); self.assertEqual(replica['value'][0], result['binding'])
        self.assertEqual(controller.OPERATION_ID, result['operation_id']); self.assertEqual(RECEIPT, result['start_ack_receipt'])
        for key in controller.FALSE_FLAGS: self.assertIs(result[key], False)
        altered = copy.deepcopy(native); altered['properties']['startTime'] = '2026-10-04T19:00:01Z'
        with patch.object(controller.previous, 'sources', return_value=p['sources']), patch.object(controller, 'arm'), \
                patch.object(controller, 'get', side_effect=[native, replica, altered]), patch.object(controller, 'save') as save, \
                self.assertRaisesRegex(ValueError, 'execution_drift_during_capture'):
            controller.capture_bound(p, ack)
        save.assert_not_called()

    def collection_fixture(self, stack, *, rows=None, terminal=None):
        p = plan_pf(); native = terminal or execution(); job = alias_job(); replica = replicas()
        captured = dict(status='native_running_replica_binding_observed', operation_id=controller.OPERATION_ID,
            trial_operation_id=controller.TRIAL_OPERATION_ID, start_ack_receipt=RECEIPT,
            raw_execution=execution(terminal=False), raw_replicas=replica, binding=replica['value'][0])
        retained = []; calls = Mock(); calls.call.side_effect = lambda query: console_response(rows if rows is not None else [console_row()])
        def save(value, name): retained.append((copy.deepcopy(value), name)); return dict(value, protected_receipt=RECEIPT)
        def load(name):
            return ({'protected_receipt': RECEIPT}, captured if name == 'replica-binding.json' else copy.deepcopy(retained[-1][0]))
        patches = (
            patch.object(controller, 'prepared', return_value=({'protected_receipt': RECEIPT}, p)),
            patch.object(controller, 'arm', return_value=Mock()),
            patch.object(controller, 'install_chain', return_value=({'protected_receipt': RECEIPT}, job, identity(), {})),
            patch.object(controller, 'start_chain', return_value=({'protected_receipt': RECEIPT}, EXECUTION)),
            patch.object(controller, 'load', side_effect=load), patch.object(controller, 'get', return_value=native),
            patch.object(controller, 'inventory', return_value={'value': [native]}),
            patch.object(transport, 'LogsTransport', return_value=calls), patch.object(controller, 'job_observation', return_value=job),
            patch.object(controller, 'roles', return_value={}), patch.object(controller, 'resource_guards', return_value={}),
            patch.object(controller.previous, 'sources', return_value=p['sources']), patch.object(controller, 'save', side_effect=save))
        for item in patches: stack.enter_context(item)
        return p, retained, calls

    def test_complete_all_console_events_then_independent_read_only_verification(self):
        with contextlib.ExitStack() as stack:
            p, retained, queries = self.collection_fixture(stack)
            result = controller.collect(False)
            self.assertEqual('secretless_aca_runtime_observed', result['status']); self.assertIs(result['acceptance_recorded'], True)
            self.assertEqual([console_row()], result['complete_console_events']); self.assertEqual(observed(), result['observed'])
            self.assertEqual(alias_job(), result['current_job']); self.assertEqual(controller.OPERATION_ID, result['operation_id'])
            for key in controller.FALSE_FLAGS: self.assertIs(result[key], False)
            self.assertEqual((0, 0), (result['sql_calls'], result['model_calls']))
            verified = controller.collect(True)
            self.assertEqual('independent_secretless_aca_runtime_verified', verified['status'])
            self.assertEqual(1, len(retained)); self.assertEqual(4, queries.call.call_count)
            scope = queries.call.call_args.args[0].scope
            self.assertEqual('2026-10-04T18:59:00+00:00', scope.start_utc)
            self.assertEqual('2026-10-04T19:12:01+00:00', scope.end_utc)

    def test_extra_console_event_is_retained_unqualified_not_discarded_or_restarted(self):
        rows = [console_row(), dict(console_row(), Container='other-container', Log='unexpected event')]
        with contextlib.ExitStack() as stack:
            _, retained, _ = self.collection_fixture(stack, rows=rows)
            result = controller.collect(False)
            self.assertEqual('terminal_aca_runtime_unqualified', result['status']); self.assertIs(result['acceptance_recorded'], False)
            self.assertEqual(sorted(rows, key=transport.canonical), result['complete_console_events']); self.assertIsNone(result['observed'])
            self.assertEqual('terminal-diagnostic.json', retained[0][1]); self.assertIs(result['automatic_retry'], False)

    def test_verify_only_altered_retained_evidence_refused_without_new_save(self):
        with contextlib.ExitStack() as stack:
            _, retained, _ = self.collection_fixture(stack)
            controller.collect(False)
            retained[0][0]['model_credential_containment_verified'] = True
            with self.assertRaisesRegex(ValueError, 'fresh_live_and_retained_evidence_differs'): controller.collect(True)
            self.assertEqual(1, len(retained))
        with patch.object(controller, 'prepared') as prepare:
            for value in (0, 1, None, 'false'):
                with self.assertRaises(ValueError): controller.collect(value)
            prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
