"""Offline one-shot native-controller tests, without private retained evidence."""
import contextlib
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import qualify_owner_task_aca_native as controller
from test_owner_task_aca_native import execution as fixture_execution, nested_replica
from test_owner_task_aca_runtime_named import (EXECUTION, REPLICA, Credential, Session,
    Response, permission, observed, console_row, console_response, checks)
from test_owner_task_aca_runtime_metadata import metadata_pf, metadata_job
from test_owner_task_aca_runtime_contract import source, descriptor
from test_owner_task_aca_runtime_transport import assignment

PRIOR = EXECUTION
CURRENT = controller.JOB + '-n4tivex'
CURRENT_REPLICA = CURRENT + '-abc12'
VERSION = '2026-10-04T23:10:00.0000000Z'


def execution(*, terminal=False, name=CURRENT):
    value = fixture_execution(terminal=terminal)
    value['name'] = name
    value['id'] = controller.JOB_ID + '/executions/' + name
    return value


def replicas(*, ready=True):
    value = json.loads(json.dumps(nested_replica(ready=ready)).replace(REPLICA, CURRENT_REPLICA).replace(EXECUTION, CURRENT))
    if not ready:
        container = value['value'][0]['properties']['containers'][0]
        container['runningState'] = 'Waiting'
        container['runningStateDetails'] = 'Container is waiting to start.'
    return value


def receipt(value=None, name='preflight.json'):
    sha = controller.digest({} if value is None else value)
    return dict(blob='acceptance/gateway-read-owner-task-pg-' + controller.KIND +
        name.removesuffix('.json') + '-' + sha + '.json', sha256=sha,
        version_id=VERSION, protected_readback_verified=True)


def protected(value, name):
    return dict(copy.deepcopy(value), protected_receipt=receipt(value, name))


def prior_context():
    prior = metadata_pf()
    return dict(pf=prior, prior_execution=PRIOR, prior_raw_execution=execution(terminal=True, name=PRIOR),
        receipts={name: dict(blob='acceptance/gateway-read-owner-task-pg-' + controller.old.KIND_PREFIX +
            name.removesuffix('.json') + '-' + pin[0] + '.json', sha256=pin[0], version_id=pin[1],
            protected_readback_verified=True) for name, pin in controller.PINS.items()})


def assignment_value():
    value = assignment()
    value['properties']['principalId'] = prior_context()['pf']['identity']['properties']['principalId']
    return value


def live_context(*, current=False):
    rows = [execution(terminal=True, name=PRIOR)]
    if current: rows.append(execution(terminal=True))
    return dict(job=metadata_job(), roles=permission(), assignment=assignment_value(), executions={'value': rows},
        prior_execution_readback=execution(terminal=True, name=PRIOR))


def new_preflight():
    prior = prior_context()
    return dict(status='prepared_native_response_qualification', operation_id=controller.OPERATION_ID,
        sources=dict(prior['pf']['sources'], **{name: 'c' * 64 for name in controller.ADDITIONS}),
        prior=prior, live=live_context(), local_checks=checks(), local_qa_policy=controller.old.n.local_qa_policy(),
        checked_at_utc='2026-10-04T23:10:00+00:00', sql_calls=0, model_calls=0,
        **{key: False for key in controller.FALSE_FLAGS})


def ack_value(*, name=CURRENT, status=200):
    return dict(http_status=status, raw_ack={'id': controller.JOB_ID + '/executions/' + name, 'name': name})


def running_binding():
    return dict(raw_execution=execution(), raw_replicas=replicas(), replica=replicas()['value'][0],
        observed_at_utc='2026-10-04T23:11:00+00:00')


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.offline = contextlib.ExitStack(); self.addCleanup(self.offline.close)
        for target in ('azure.identity.AzureCliCredential.get_token', 'requests.sessions.Session.request',
                'subprocess.run', 'subprocess.check_output',
                'azure.storage.blob.BlobClient.get_blob_properties',
                'azure.storage.blob.BlobClient.download_blob', 'azure.storage.blob.BlobClient.upload_blob'):
            self.offline.enter_context(patch(target, side_effect=AssertionError('offline fixture attempted external access')))
        self.offline.enter_context(patch.object(controller.old.previous.q.readback, 'read_local',
            side_effect=AssertionError('offline fixture attempted private retained-evidence read')))


class SourceScopeAndEvidenceTests(OfflineTestCase):
    def test_exact_four_additions_preserve_all731_prior_sources(self):
        old = prior_context()['pf']['sources']; new = new_preflight()['sources']
        controller.validate_sources(old, new)
        self.assertEqual(731, len(old)); self.assertEqual(735, len(new))
        self.assertEqual({'scripts/owner_task_aca_native.py', 'tests/test_owner_task_aca_native.py',
            'scripts/qualify_owner_task_aca_native.py', 'tests/test_owner_task_aca_native_controller.py'}, set(controller.ADDITIONS))
        for mutation in ('oldhash', 'missing', 'extra', 'badnewhash', 'badoldcount'):
            before, after = copy.deepcopy(old), copy.deepcopy(new)
            if mutation == 'oldhash': after[next(iter(old))] = 'd' * 64
            elif mutation == 'missing': after.pop(next(iter(controller.ADDITIONS)))
            elif mutation == 'extra': after['unexpected.py'] = 'a' * 64
            elif mutation == 'badnewhash': after[next(iter(controller.ADDITIONS))] = True
            else: before.pop(next(iter(before)))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): controller.validate_sources(before, after)

    def test_source_hook_types_do_not_run_mapping_callbacks(self):
        class Hook(dict):
            def items(self): raise AssertionError('mapping callback ran')
            def __iter__(self): raise AssertionError('mapping iteration ran')
        old = prior_context()['pf']['sources']; new = new_preflight()['sources']
        for before, after in ((Hook(old), new), (old, Hook(new))):
            with self.assertRaises(ValueError): controller.validate_sources(before, after)

    def test_real_save_accepts_registered_filename_and_is_create_only(self):
        value = dict(status='synthetic', sql_calls=0, model_calls=0)
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller.old.previous.q.deploy, 'archive_protection'), \
                patch.object(controller.old.previous.q.deploy, 'retain', return_value=receipt(value)) as retain, \
                patch.object(controller.old.previous.q.deploy, 'protected') as verify:
            raw = controller.save(value, 'preflight.json')
            self.assertEqual(value, {k: v for k, v in raw.items() if k != 'protected_receipt'})
            self.assertEqual(raw, json.loads(Path(folder, 'preflight.json').read_text(encoding='utf-8')))
            with self.assertRaises(FileExistsError): controller.save(value, 'preflight.json')
            for name in ('../bad.json', 'bad.txt', 'Bad.json', 'bad\\json'):
                with self.subTest(name=name), self.assertRaises(ValueError): controller.save(value, name)
        retain.assert_called_once(); verify.assert_called_once()

    def test_prepared_rejects_source_drift_or_unverified_qa_without_effect(self):
        for change in ('source', 'count', 'flag', 'policy'):
            pf = new_preflight(); current = copy.deepcopy(pf['sources'])
            if change == 'source': current[next(iter(controller.ADDITIONS))] = 'd' * 64
            elif change == 'count': pf['local_checks']['test_count'] = True
            elif change == 'flag': pf[controller.FALSE_FLAGS[0]] = 0
            else: pf['local_qa_policy']['regression_timeout_seconds'] = 300
            with patch.object(controller, 'load', return_value=(protected(pf, 'preflight.json'), pf)), \
                    patch.object(controller.old.previous, 'sources', return_value=current), \
                    patch.object(controller, 'reserve') as reserve, patch.object(controller.old, 'arm') as arm, \
                    self.subTest(change=change), self.assertRaises(ValueError): controller.prepared()
            reserve.assert_not_called(); arm.assert_not_called()

    def test_failed_prepare_qa_cannot_save_or_dispatch(self):
        prior = prior_context()
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller, 'absent'), patch.object(controller, 'history', return_value=prior), \
                patch.object(controller.old.previous, 'sources', return_value=new_preflight()['sources']), \
                patch.object(controller.old.n, 'run_local_checks', side_effect=ValueError('qa incomplete')), \
                patch.object(controller, 'save') as save, patch.object(controller, 'guards') as guards, \
                patch.object(controller, 'reserve') as reserve, self.assertRaises(ValueError): controller.prepare()
        save.assert_not_called(); guards.assert_not_called(); reserve.assert_not_called()

    def test_prior_history_uses_exact_four_pins_and_preserves_original_stopped_attempt(self):
        context = prior_context(); old_pf = context['pf']; native_prior = context['prior_raw_execution']
        old_ack = dict(status='one_exact_cloud_effect_acknowledged', attempt_consumed=True, **ack_value(name=PRIOR))
        old_ack_raw = dict(old_ack, protected_receipt=context['receipts']['start-ack.json'])
        stop_value = dict(status='capture_validation_unqualified_inspect_only', capture_or_collection_authorized=False,
            start_ack_receipt=old_ack_raw['protected_receipt'], automatic_start_retry=False,
            sql_calls=0, model_calls=0, **{key: False for key in controller.FALSE_FLAGS})
        payload = controller.canonical(stop_value)
        stop_receipt = dict(blob=controller.old.PREFIX+'capture-stop.json', version_id=VERSION, etag='"0xABC"',
            sha256=controller.digest(stop_value), protected_readback_verified=True)
        stop = dict(stop_value, capture_stop_receipt=stop_receipt)
        diag = dict(status='stopped_runtime_current_native_diagnosis_not_acceptance', runtime_acceptance_recorded=False,
            capture_diagnostic_receipt=context['receipts']['capture-diagnostic.json'], capture_stop_receipt=stop_receipt,
            sources=old_pf['sources'], raw_execution_before=native_prior, raw_execution_after=native_prior)
        values = {'preflight.json': old_pf, 'start-ack.json': old_ack, 'capture-diagnostic.json': stop,
            'native-capture-diagnosis.json': diag}
        blob = Mock(); blob.get_blob_properties.return_value = SimpleNamespace(etag='"0xABC"',
            version_id=VERSION, size=len(payload), blob_type='BlockBlob')
        blob.download_blob.return_value.readall.return_value = payload
        records = {name: dict(value, protected_receipt=context['receipts'][name]) for name, value in values.items()}
        before = copy.deepcopy(records)
        with patch.object(controller.old, 'load', side_effect=lambda name: (records[name], values[name])) as load, \
                patch.object(controller.old, 'start_chain', return_value=(old_ack_raw, PRIOR)), \
                patch.object(controller.old.previous, 'sources', return_value=new_preflight()['sources']), \
                patch.object(controller.old, 'capture_control_blob', return_value=blob), \
                patch.object(controller, 'reserve') as reserve, patch.object(controller.old, 'arm') as arm:
            returned = controller.history()
        self.assertEqual(context, returned); self.assertEqual(before, records)
        self.assertEqual(list(controller.PINS), [call.args[0] for call in load.call_args_list])
        blob.get_blob_properties.assert_called_once_with(timeout=10, retry_total=0)
        self.assertEqual(0, blob.download_blob.call_args.kwargs['retry_total'])
        reserve.assert_not_called(); arm.assert_not_called()


class InventoryAndReservationTests(OfflineTestCase):
    def test_guards_bind_job_identity_environment_roles_assignment_and_original_execution(self):
        p = prior_context()['pf']; terminal = execution(terminal=True, name=PRIOR)
        for change in (None, 'identity', 'environment', 'job', 'roles', 'assignment', 'priorstatus'):
            who, env, job, roles, grant, prior = copy.deepcopy(p['identity']), {}, metadata_job(), permission(), assignment_value(), copy.deepcopy(terminal)
            if change == 'identity': who['properties']['clientId'] = '33333333-3333-4333-8333-333333333333'
            elif change == 'environment': env['extra'] = 'drift'
            elif change == 'job': job['properties']['outboundIpAddresses'].pop()
            elif change == 'roles': roles = {}
            elif change == 'assignment': grant['properties']['principalId'] = '33333333-3333-4333-8333-333333333333'
            elif change == 'priorstatus': prior['properties']['status'] = 'Failed'
            def get(client, path, api):
                if path == controller.JOB_ID: return job
                if path == controller.old.transport.ASSIGNMENT_ID: return grant
                return prior
            with patch.object(controller.old, 'arm', return_value=Mock()), \
                    patch.object(controller.old.c, 'identity_observation', return_value=who), \
                    patch.object(controller.old, 'environment', return_value=env), \
                    patch.object(controller.old, 'get', side_effect=get), \
                    patch.object(controller.native, 'validate_job_metadata', return_value=job), \
                    patch.object(controller.old, 'roles', return_value=roles), \
                    patch.object(controller, 'inventory', return_value={'value':[terminal]}):
                if change is None:
                    result = controller.guards(p, PRIOR)
                    self.assertEqual(grant, result['assignment']); self.assertEqual(prior, result['prior_execution_readback'])
                else:
                    with self.subTest(change=change), self.assertRaises(ValueError): controller.guards(p, PRIOR)

    def test_inventory_requires_exact_original_and_one_distinct_new_execution(self):
        controller.check_inventory({'value': [execution(terminal=True, name=PRIOR)]}, PRIOR)
        value = live_context(current=True)['executions']; controller.check_inventory(value, PRIOR, CURRENT)
        for change in ('missingprior', 'extra', 'duplicate', 'foreignid', 'next', 'wrongname', 'nonrow'):
            changed = copy.deepcopy(value)
            if change == 'missingprior': changed['value'].pop(0)
            elif change == 'extra': changed['value'].append(execution(name=controller.JOB + '-unknown1'))
            elif change == 'duplicate': changed['value'][1] = copy.deepcopy(changed['value'][0])
            elif change == 'foreignid': changed['value'][1]['id'] += '-wrong'
            elif change == 'next': changed['nextLink'] = 'opaque-next'
            elif change == 'wrongname': changed['value'][1]['name'] += '-wrong'
            else: changed['value'][1] = False
            with self.subTest(change=change), self.assertRaises(ValueError): controller.check_inventory(changed, PRIOR, CURRENT)
        with self.assertRaises(ValueError): controller.check_inventory({'value': [execution(name=PRIOR)]}, PRIOR, PRIOR)

    def test_inventory_native_response_bounds_reject_more_than_two_and_pagination(self):
        for value in ({'value': [execution()] * 3}, {'value': [], 'nextLink': 'next'}, {'value': (), 'nextLink': None}):
            with patch.object(controller.old, 'get', return_value=value), self.assertRaises(ValueError): controller.inventory(Mock())

    def test_local_journal_or_consumed_record_blocks_new_remote_start(self):
        for name in ('start-intent.json', 'start-ack.json', 'start-outcome.json', 'replica-binding.json',
                'capture-diagnostic.json', 'acceptance.json', 'native-observations.jsonl'):
            with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                    patch.object(controller, 'reservation_blob') as blob:
                Path(folder, name).touch()
                with self.subTest(name=name), self.assertRaises(ValueError): controller.absent()
            blob.assert_not_called()

    def test_reservation_is_conditional_create_only_then_exact_blockblob_readback(self):
        pf = new_preflight(); value = controller.intent_value(protected(pf, 'preflight.json'), pf)
        payload = controller.canonical(value); blob = Mock()
        blob.upload_blob.return_value = dict(version_id=VERSION, etag='"0xABC"')
        blob.get_blob_properties.return_value = SimpleNamespace(version_id=VERSION, etag='"0xABC"',
            size=len(payload), blob_type='BlockBlob')
        blob.download_blob.return_value.readall.return_value = payload
        with patch.object(controller, 'reservation_blob', return_value=blob): result = controller.reserve(value)
        self.assertTrue(result['protected_readback_verified']); self.assertEqual(controller.digest(value), result['sha256'])
        kw = blob.upload_blob.call_args.kwargs
        self.assertIs(kw['overwrite'], False); self.assertEqual(controller.old.MatchConditions.IfMissing, kw['match_condition'])
        self.assertEqual(0, kw['retry_total']); self.assertEqual(1, kw['max_concurrency'])
        for change in ('blobtype', 'bytes', 'version', 'etag', 'sha'):
            props = SimpleNamespace(version_id=VERSION, etag='"0xABC"', size=len(payload), blob_type='BlockBlob')
            checked = copy.deepcopy(result); returned = payload
            if change == 'blobtype': props.blob_type = 'PageBlob'
            elif change == 'bytes': returned = payload + b'x'
            elif change == 'version': props.version_id = 'different'
            elif change == 'etag': props.etag = '"0xDEF"'
            else: checked['sha256'] = 'f' * 64
            blob.get_blob_properties.return_value = props; blob.download_blob.return_value.readall.return_value = returned
            with patch.object(controller, 'reservation_blob', return_value=blob), self.subTest(change=change), self.assertRaises(ValueError):
                controller.reservation_readback(checked, value)

    def test_existing_transport_rejects_put_grant_identity_delete_before_authentication(self):
        for method, path, body in (('PUT', controller.JOB_ID, {}), ('PUT', controller.old.contract.IDENTITY_ID, {}),
                ('PUT', controller.old.transport.ASSIGNMENT_ID, {}), ('DELETE', controller.JOB_ID, None),
                ('POST', controller.JOB_ID + '/stop', {})):
            credential, session = Credential(), Session([])
            client = controller.old.ArmTransport(credential, session, runtime_source_bytes=source(), expected_runtime=descriptor())
            with self.subTest(method=method, path=path), self.assertRaises(ValueError):
                client.request(path, controller.old.transport.JOB_API, method=method, body=body)
            self.assertEqual([], credential.calls); self.assertEqual([], session.calls)


class StartTests(OfflineTestCase):
    @contextlib.contextmanager
    def fixture(self, *, request_error=None, capture_error=None, failed_outcome_save=False, returned_ack=None):
        pf = new_preflight(); raw = protected(pf, 'preflight.json'); saved = []; events = []; client = Mock()
        client.request.side_effect = request_error
        client.request.return_value = (ack_value()['raw_ack'], 200) if returned_ack is None else returned_ack
        def save(value, name):
            events.append(('save', name))
            if failed_outcome_save and name == 'start-outcome.json': raise ValueError('sensitive-save-value')
            saved.append((copy.deepcopy(value), name)); Path(folder, name).touch()
            return protected(value, name)
        def capture(client, prior, name, record):
            events.append(('capture', name)); record('execution_before', execution())
            if capture_error is not None: raise capture_error
            record('replicas', replicas()); record('execution_after', execution())
            return running_binding()
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(controller, 'FOLDER', Path(folder)))
            stack.enter_context(patch.object(controller, 'prepared', return_value=(raw, pf)))
            stack.enter_context(patch.object(controller, 'history', return_value=pf['prior']))
            guards = stack.enter_context(patch.object(controller, 'guards', return_value=pf['live']))
            stack.enter_context(patch.object(controller.old.previous, 'sources', return_value=pf['sources']))
            stack.enter_context(patch.object(controller.old.n, 'resource_guards', return_value=pf['prior']['pf']['resource_guards']))
            stack.enter_context(patch.object(controller, 'reservation_blob', return_value=Mock()))
            stack.enter_context(patch.object(controller.old, 'blob_absent'))
            reservation = dict(blob=controller.PREFIX + 'start-reservation.json', sha256='a' * 64,
                version_id=VERSION, etag='"0xABC"', protected_readback_verified=True)
            reserve = stack.enter_context(patch.object(controller, 'reserve', return_value=reservation))
            stack.enter_context(patch.object(controller, 'save', side_effect=save))
            stack.enter_context(patch.object(controller.old, 'arm', return_value=client))
            capture_mock = stack.enter_context(patch.object(controller, 'capture', side_effect=capture))
            yield pf, saved, events, client, reserve, guards, capture_mock, Path(folder)

    def test_intent_is_exact_new_post_with_old_stopped_receipt_links(self):
        pf = new_preflight(); raw = protected(pf, 'preflight.json'); value = controller.intent_value(raw, pf)
        self.assertEqual({'operation_id', 'preflight_receipt', 'prior_execution', 'prior_receipts', 'path', 'api',
            'method', 'body', 'automatic_retry'}, set(value))
        self.assertEqual(controller.OPERATION_ID, value['operation_id']); self.assertEqual(PRIOR, value['prior_execution'])
        self.assertEqual(raw['protected_receipt'], value['preflight_receipt']); self.assertEqual(pf['prior']['receipts'], value['prior_receipts'])
        self.assertEqual(controller.JOB_ID + '/start', value['path']); self.assertEqual('POST', value['method'])
        self.assertEqual(controller.old.transport.JOB_API, value['api'])
        self.assertEqual(controller.old.start_body(pf['prior']['pf']), value['body']); self.assertIs(value['automatic_retry'], False)

    def test_intent_snapshot_cannot_mutate_preflight_receipts_or_template(self):
        pf = new_preflight(); raw = protected(pf, 'preflight.json'); before = copy.deepcopy(pf)
        value = controller.intent_value(raw, pf)
        value['body']['containers'][0]['image'] = 'attacker.invalid/image'
        value['prior_receipts']['start-ack.json']['sha256'] = 'f' * 64
        value['preflight_receipt']['sha256'] = 'f' * 64
        self.assertEqual(before, pf); self.assertEqual(receipt(before, 'preflight.json'), raw['protected_receipt'])

    def test_running_capture_happens_before_slow_start_ack_archive_writes(self):
        with self.fixture() as (_, saved, events, client, reserve, guards, capture, folder):
            result = controller.start(); journal = [json.loads(line) for line in (folder/'native-observations.jsonl').read_text().splitlines()]
            with self.assertRaises(ValueError): controller.start()
        reserve.assert_called_once(); client.request.assert_called_once(); capture.assert_called_once()
        self.assertEqual(2, guards.call_count)
        self.assertLess(events.index(('capture', CURRENT)), events.index(('save', 'start-ack.json')))
        self.assertEqual(['start-intent.json', 'start-ack.json', 'replica-binding.json'], [name for _, name in saved])
        self.assertEqual(['start_ack', 'execution_before', 'replicas', 'execution_after'], [item['kind'] for item in journal])
        self.assertEqual(running_binding(), result['binding'])
        for key in controller.FALSE_FLAGS: self.assertIs(result[key], False)

    def test_unknown_dispatch_or_unretained_outcome_is_consumed_not_retried_or_leaked(self):
        for failed_save in (False, True):
            with self.fixture(request_error=ValueError('sensitive-provider-value'), failed_outcome_save=failed_save) as \
                    (_, saved, _, client, reserve, _, capture, folder), self.subTest(failed_save=failed_save):
                with self.assertRaises(ValueError) as caught: controller.start()
                self.assertNotIn('sensitive', str(caught.exception)); self.assertTrue((folder/'native-observations.jsonl').exists())
                with self.assertRaises(ValueError): controller.start()
            reserve.assert_called_once(); client.request.assert_called_once(); capture.assert_not_called()
            self.assertEqual('start-intent.json', saved[0][1])
            if not failed_save:
                outcome = saved[-1][0]; self.assertIs(outcome['attempt_consumed'], True)
                self.assertIs(outcome['automatic_retry'], False); self.assertEqual([], outcome['observations'])
                self.assertEqual(0, outcome['sql_calls']); self.assertEqual(0, outcome['model_calls'])

    def test_replayed_ambiguous_or_foreign_ack_never_enters_capture(self):
        for ack, status in ((ack_value(name=PRIOR)['raw_ack'], 200), (ack_value()['raw_ack'], 202),
                ({'name': CURRENT}, 200), ({'id': 'foreign', 'name': CURRENT}, 200)):
            with self.fixture(returned_ack=(ack, status)) as (_, saved, _, client, reserve, _, capture, _), \
                    self.subTest(status=status), self.assertRaises(ValueError): controller.start()
            capture.assert_not_called(); client.request.assert_called_once(); reserve.assert_called_once()
            self.assertEqual('start-outcome.json', saved[-1][1])

    def test_capture_failure_preserves_raw_journal_and_protected_diagnostic_without_acceptance(self):
        for error, expected in ((ValueError('native_execution_invalid'), 'native_execution_invalid'),
                (ValueError('sensitive-native-value'), 'native_capture_validation_failed')):
            with self.fixture(capture_error=error) as (_, saved, _, client, reserve, _, _, folder), self.subTest(expected=expected):
                with self.assertRaises(ValueError) as caught: controller.start()
                self.assertNotIn('sensitive', str(caught.exception))
                journal = [json.loads(line) for line in (folder/'native-observations.jsonl').read_text().splitlines()]
                with self.assertRaises(ValueError): controller.start()
            self.assertEqual(['start-intent.json', 'start-ack.json', 'capture-diagnostic.json'], [name for _, name in saved])
            diagnostic = saved[-1][0]; self.assertEqual(expected, diagnostic['failure_code'])
            self.assertEqual(execution(), diagnostic['observations'][-1]['raw']); self.assertEqual(journal, diagnostic['observations'])
            self.assertIs(diagnostic['acceptance_recorded'], False); self.assertIs(diagnostic['attempt_consumed'], True)
            self.assertIs(diagnostic['automatic_retry'], False); reserve.assert_called_once(); client.request.assert_called_once()

    def test_aggregate_capture_budget_keeps_complete_local_raw_and_bounded_failure(self):
        response = {'descriptive_metadata': 'x' * (400 * 1024)}
        def oversized_capture(client, pf, name, record):
            record('execution_before', response)
            record('replicas', response)
            self.fail('aggregate budget did not abort capture')
        with self.fixture() as (_, saved, _, client, reserve, _, capture, folder):
            capture.side_effect = oversized_capture
            with self.assertRaisesRegex(ValueError, '^native_capture_unqualified_inspect_only$'): controller.start()
            journal = [json.loads(line) for line in (folder/'native-observations.jsonl').read_text().splitlines()]
            with self.assertRaises(ValueError): controller.start()
        self.assertEqual(['start_ack', 'execution_before', 'replicas'], [item['kind'] for item in journal])
        self.assertEqual(response, journal[-1]['raw'])
        self.assertEqual(['start-intent.json', 'start-ack.json', 'capture-diagnostic.json'], [name for _, name in saved])
        diagnostic = saved[-1][0]; limit = diagnostic['observations'][-1]
        self.assertEqual('native_observation_budget_exceeded', diagnostic['failure_code'])
        self.assertEqual('observation_limit', limit['kind'])
        self.assertIs(limit['complete_raw_retained_in_local_journal'], True)
        self.assertIs(limit['complete_raw_retained_in_protected_record'], False)
        self.assertEqual(len(controller.canonical(journal[-1])), limit['bytes'])
        self.assertEqual(controller.digest(journal[-1]), limit['sha256'])
        self.assertLess(len(controller.canonical(diagnostic)), controller.old.MAX_BYTES)
        self.assertIs(diagnostic['acceptance_recorded'], False); reserve.assert_called_once(); client.request.assert_called_once()

    def test_post_reservation_authority_drift_consumes_intent_but_never_dispatches(self):
        with self.fixture() as (pf, saved, _, client, reserve, guards, capture, _):
            guards.side_effect = [copy.deepcopy(pf['live']), dict(pf['live'], roles={})]
            with self.assertRaises(ValueError): controller.start()
            with self.assertRaises(ValueError): controller.start()
        reserve.assert_called_once(); client.request.assert_not_called(); capture.assert_not_called()
        self.assertEqual(['start-intent.json'], [name for _, name in saved])


class CaptureTests(OfflineTestCase):
    def run_capture(self, responses, *, times=None):
        recorded = []; getter = Mock(side_effect=responses); clock = Mock(return_value=0) if times is None else Mock(side_effect=times)
        sleep = Mock()
        def record(kind, value): recorded.append(dict(kind=kind, raw=copy.deepcopy(value)))
        return getter, clock, sleep, record, recorded

    def test_valid_nested_running_replicas_are_recorded_before_validated_binding(self):
        running = execution(); getter, clock, sleep, record, recorded = self.run_capture([running, replicas(), running])
        with patch.object(controller.old, 'get', getter):
            result = controller.capture(Mock(), prior_context()['pf'], CURRENT, record, monotonic=clock, sleep=sleep)
        self.assertEqual(['execution_before', 'replicas', 'execution_after'], [item['kind'] for item in recorded])
        self.assertEqual(running, result['raw_execution']); self.assertEqual(replicas(), result['raw_replicas'])
        self.assertEqual(replicas()['value'][0], result['replica']); sleep.assert_not_called()

    def test_processing_and_valid_not_ready_replica_are_bounded_readiness_only(self):
        running = execution(); processing = copy.deepcopy(running); processing['properties']['status'] = 'Processing'
        for responses in ([processing, running, replicas(), running], [running, replicas(ready=False), running, replicas(), running]):
            getter, clock, sleep, record, recorded = self.run_capture(responses)
            with patch.object(controller.old, 'get', getter):
                result = controller.capture(Mock(), prior_context()['pf'], CURRENT, record, monotonic=clock, sleep=sleep)
            self.assertEqual(running, result['raw_execution']); sleep.assert_called_once_with(1)

    def test_only_exact_resource_not_found_is_read_visibility_pending(self):
        running = execution()
        missing = controller.old.NativeHttpError(404, b'{"error":{"code":"ResourceNotFound","message":"not yet visible"}}')
        getter, clock, sleep, record, recorded = self.run_capture([missing, running, replicas(), running])
        with patch.object(controller.old, 'get', getter):
            result = controller.capture(Mock(), prior_context()['pf'], CURRENT, record, monotonic=clock, sleep=sleep)
        self.assertEqual(running, result['raw_execution']); sleep.assert_called_once_with(1)
        self.assertEqual('replica_visibility_pending', recorded[0]['kind'])
        for error in (controller.old.NativeHttpError(404, b'{"error":{"code":"Forbidden","message":"wrong absence"}}'),
                controller.old.NativeHttpError(403, b'{"error":{"code":"ResourceNotFound","message":"denied"}}')):
            getter, clock, sleep, record, recorded = self.run_capture([error, running, replicas(), running])
            with patch.object(controller.old, 'get', getter), self.assertRaises(ValueError):
                controller.capture(Mock(), prior_context()['pf'], CURRENT, record, monotonic=clock, sleep=sleep)
            getter.assert_called_once(); sleep.assert_not_called()

    def test_structural_failure_cannot_be_washed_by_later_clean_observation(self):
        running = execution()
        for change in ('image', 'foreign', 'terminal', 'extra', 'duplicate', 'restart', 'signedendpoint', 'drift'):
            before, rows, after = copy.deepcopy(running), replicas(), copy.deepcopy(running)
            if change == 'image': before['properties']['template']['containers'][0]['imageType'] = 'Artifact'
            elif change == 'foreign': before['id'] += '-foreign'
            elif change == 'terminal': before = execution(terminal=True)
            elif change == 'extra': rows['value'][0]['properties']['unknown'] = 'x'
            elif change == 'duplicate': rows['value'].append(copy.deepcopy(rows['value'][0]))
            elif change == 'restart': rows['value'][0]['properties']['containers'][0]['restartCount'] = 1
            elif change == 'signedendpoint': rows['value'][0]['properties']['containers'][0]['execEndpoint'] += '?sig=unsafe'
            else: after['properties']['startTime'] = '2026-10-04T20:30:02Z'
            getter, clock, sleep, record, recorded = self.run_capture([before, rows, after, running, replicas(), running])
            with patch.object(controller.old, 'get', getter), self.subTest(change=change), self.assertRaises(ValueError):
                controller.capture(Mock(), prior_context()['pf'], CURRENT, record, monotonic=clock, sleep=sleep)
            self.assertLessEqual(getter.call_count, 3); self.assertTrue(recorded); sleep.assert_not_called()

    def test_capture_attempts_and_deadline_are_bounded_without_dispatch(self):
        processing = execution(); processing['properties']['status'] = 'Processing'
        getter, clock, sleep, record, recorded = self.run_capture([processing] * 45)
        with patch.object(controller.old, 'get', getter), patch.object(controller, 'reserve') as reserve, \
                patch.object(controller.old, 'arm') as arm, self.assertRaises(ValueError):
            controller.capture(Mock(), prior_context()['pf'], CURRENT, record, monotonic=clock, sleep=sleep)
        self.assertEqual(45, getter.call_count); self.assertLessEqual(sleep.call_count, 45); self.assertEqual(45, len(recorded))
        reserve.assert_not_called(); arm.assert_not_called()
        getter, clock, sleep, record, recorded = self.run_capture([], times=[0, 90])
        with patch.object(controller.old, 'get', getter), self.assertRaises(ValueError):
            controller.capture(Mock(), prior_context()['pf'], CURRENT, record, monotonic=clock, sleep=sleep)
        getter.assert_not_called(); self.assertEqual([], recorded)


class ChainAndCollectionTests(OfflineTestCase):
    def chain_records(self):
        pf = new_preflight(); raw = protected(pf, 'preflight.json'); expected = controller.intent_value(raw, pf)
        reservation = dict(blob=controller.PREFIX + 'start-reservation.json', sha256=controller.digest(expected),
            version_id=VERSION, etag='"0xABC"', protected_readback_verified=True)
        intent = dict(expected, status='reserved_single_qualification', reservation_receipt=reservation)
        intent_raw = protected(intent, 'start-intent.json')
        ack = dict(status='new_qualification_execution_acknowledged', operation_id=controller.OPERATION_ID,
            intent_receipt=intent_raw['protected_receipt'], attempt_consumed=True, automatic_retry=False, **ack_value())
        return raw, pf, intent_raw, intent, protected(ack, 'start-ack.json'), ack

    def test_exact_chain_rejects_mutated_intent_or_ack_and_prior_execution_replay(self):
        for change in (None, 'intentbody', 'intentpath', 'intentprior', 'ackstatus', 'ackextra', 'retry', 'counter', 'replay'):
            raw, pf, iraw, intent, araw, ack = self.chain_records()
            if change == 'intentbody': intent['body']['containers'][0]['image'] += '-bad'
            elif change == 'intentpath': intent['path'] = controller.old.transport.ASSIGNMENT_ID
            elif change == 'intentprior': intent['prior_receipts'] = {}
            elif change == 'ackstatus': ack['status'] = 'wrong-positive-status'
            elif change == 'ackextra': ack['extra'] = True
            elif change == 'retry': ack['automatic_retry'] = True
            elif change == 'counter': ack['attempt_consumed'] = 1
            elif change == 'replay': ack.update(ack_value(name=PRIOR))
            def load(name): return (iraw, intent) if name == 'start-intent.json' else (araw, ack)
            with patch.object(controller, 'load', side_effect=load), patch.object(controller, 'reservation_readback') as readback:
                if change is None:
                    self.assertEqual((araw, CURRENT), controller.chain(raw, pf)); readback.assert_called_once()
                else:
                    with self.subTest(change=change), self.assertRaises(ValueError): controller.chain(raw, pf)

    @contextlib.contextmanager
    def fixture(self, *, rows=None, drift=None):
        pf = new_preflight(); raw = protected(pf, 'preflight.json'); ack = protected(dict(ack_value(),
            status='new_qualification_execution_acknowledged', operation_id=controller.OPERATION_ID,
            attempt_consumed=True, automatic_retry=False), 'start-ack.json')
        bind = running_binding(); captured = dict(status='native_running_replica_observed', operation_id=controller.OPERATION_ID,
            start_ack_receipt=ack['protected_receipt'], binding=bind)
        terminal = execution(terminal=True); prior = execution(terminal=True, name=PRIOR)
        row = json.loads(json.dumps(console_row()).replace(REPLICA, CURRENT_REPLICA)); console_rows = [row] if rows is None else rows
        responses = Mock(side_effect=lambda scope: console_response(console_rows)); saved = []; retained = {}
        def save(value, name):
            saved.append((copy.deepcopy(value), name)); retained[name] = copy.deepcopy(value); return protected(value, name)
        def load(name):
            if name == 'replica-binding.json': return protected(captured, name), copy.deepcopy(captured)
            return protected(retained[name], name), copy.deepcopy(retained[name])
        def get(client, path, api):
            if path.endswith('/' + PRIOR): return copy.deepcopy(prior)
            value = copy.deepcopy(terminal)
            if drift == 'end' and getter.call_count % 3 == 2: value['properties']['endTime'] = '2026-10-04T20:32:02Z'
            return value
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(controller, 'FOLDER', Path(folder)))
            stack.enter_context(patch.object(controller, 'prepared', return_value=(raw, pf)))
            stack.enter_context(patch.object(controller, 'chain', return_value=(ack, CURRENT)))
            stack.enter_context(patch.object(controller, 'load', side_effect=load))
            stack.enter_context(patch.object(controller, 'save', side_effect=save))
            stack.enter_context(patch.object(controller.old, 'arm', return_value=Mock()))
            getter = stack.enter_context(patch.object(controller.old, 'get', side_effect=get))
            stack.enter_context(patch.object(controller, 'guards', return_value=live_context(current=True)))
            stack.enter_context(patch.object(controller.old.n, 'resource_guards', return_value=pf['prior']['pf']['resource_guards']))
            stack.enter_context(patch.object(controller.old.previous, 'sources', return_value=pf['sources']))
            stack.enter_context(patch.object(controller.old, 'LogsTransport', return_value=SimpleNamespace(call=responses)))
            stop_blob = Mock(); service = Mock(); service.get_blob_client.return_value = stop_blob
            stack.enter_context(patch.object(controller.old, 'storage', return_value=service))
            stack.enter_context(patch.object(controller.old, 'blob_absent'))
            responses.stop_blob = stop_blob; responses.execution_getter = getter
            reserve = stack.enter_context(patch.object(controller, 'reserve')); start = stack.enter_context(patch.object(controller, 'start'))
            yield pf, saved, retained, responses, reserve, start, Path(folder)

    def test_fresh_collection_verification_recomputes_without_resave_or_dispatch(self):
        with self.fixture() as (_, saved, retained, query, reserve, start, _):
            value = controller.collect(); verified = controller.collect(True)
        self.assertEqual('independent_native_runtime_readback_verified', verified['status'])
        self.assertEqual(1, len(saved)); self.assertEqual('acceptance.json', saved[0][1]); self.assertEqual(4, query.call_count)
        self.assertEqual(observed(), value['observed']); self.assertEqual(2, len(value['complete_execution_inventory']['value']))
        self.assertIs(value['original_stopped_attempt_requalified'], False); self.assertEqual(0, value['sql_calls']); self.assertEqual(0, value['model_calls'])
        for key in controller.FALSE_FLAGS: self.assertIs(value[key], False)
        reserve.assert_not_called(); start.assert_not_called()

    def test_failed_capture_or_unknown_start_record_blocks_all_fresh_collection_before_reads(self):
        for filename in ('capture-diagnostic.json', 'start-outcome.json'):
            with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                    patch.object(controller, 'prepared') as prepared, patch.object(controller, 'load') as load, \
                    patch.object(controller.old, 'arm') as arm:
                Path(folder, filename).touch()
                for verify in (False, True):
                    with self.subTest(filename=filename, verify=verify), self.assertRaises(ValueError): controller.collect(verify)
            prepared.assert_not_called(); load.assert_not_called(); arm.assert_not_called()

    def test_nonboolean_verify_aliases_are_rejected_before_private_evidence_read(self):
        for flag in (None, 0, 1, 'true', []):
            with patch.object(controller, 'prepared') as prepared, self.subTest(flag=flag), self.assertRaises(ValueError): controller.collect(flag)
            prepared.assert_not_called()

    def test_full_console_extra_events_or_response_drift_never_produce_acceptance(self):
        row = json.loads(json.dumps(console_row()).replace(REPLICA, CURRENT_REPLICA))
        extra = dict(row, Container='foreign-container', Log='unexpected-event')
        for rows, drift in (([row, extra], None), ([row], 'end')):
            with self.fixture(rows=rows, drift=drift) as (_, saved, _, query, reserve, start, folder):
                with self.assertRaises(ValueError): controller.collect()
                self.assertTrue((folder/'collection-stop.json').is_file())
                with self.assertRaises(ValueError): controller.collect()
                with self.assertRaises(ValueError): controller.collect(True)
            self.assertEqual(['collection-diagnostic.json'], [name for _, name in saved])
            self.assertIs(saved[0][0]['acceptance_recorded'], False); query.stop_blob.upload_blob.assert_called_once()
            self.assertIs(query.stop_blob.upload_blob.call_args.kwargs['overwrite'], False)
            self.assertEqual(0, query.stop_blob.upload_blob.call_args.kwargs['retry_total'])
            reserve.assert_not_called(); start.assert_not_called()

    def test_collection_stop_write_failure_stays_consumed_without_detail_or_future_acceptance(self):
        row = json.loads(json.dumps(console_row()).replace(REPLICA, CURRENT_REPLICA))
        extra = dict(row, Container='foreign-container', Log='unexpected-event')
        for failed_phase in ('remote-stop', 'protected-diagnostic'):
            with self.fixture(rows=[row, extra]) as (_, saved, _, query, reserve, start, folder):
                if failed_phase == 'remote-stop':
                    query.stop_blob.upload_blob.side_effect = ValueError('sensitive-remote-value')
                    patcher = contextlib.nullcontext()
                else:
                    patcher = patch.object(controller, 'save', side_effect=ValueError('sensitive-evidence-value'))
                with patcher, self.subTest(failed_phase=failed_phase):
                    with self.assertRaisesRegex(ValueError, '^native_collection_stop_unretained_inspect_only$'):
                        controller.collect()
                    local = json.loads((folder/'collection-stop.json').read_text())
                    self.assertIs(local['acceptance_recorded'], False); self.assertIs(local['automatic_retry'], False)
                    self.assertEqual(0, local['sql_calls']); self.assertEqual(0, local['model_calls'])
                    reads = query.call_count
                    for verify in (False, True):
                        with self.assertRaisesRegex(ValueError, '^native_attempt_unqualified_inspect_only$'):
                            controller.collect(verify)
                    self.assertEqual(reads, query.call_count)
                self.assertEqual([], saved); query.stop_blob.upload_blob.assert_called_once()
                reserve.assert_not_called(); start.assert_not_called()

    def test_valid_running_or_stable_empty_logs_are_read_only_pending_not_failed_acceptance(self):
        for pending in ('running', 'logs'):
            with self.fixture(rows=[] if pending == 'logs' else None) as (_, saved, _, query, reserve, start, folder):
                if pending == 'running': query.execution_getter.side_effect = lambda *a: execution()
                with self.subTest(pending=pending), self.assertRaises(controller.CollectionPending): controller.collect()
                self.assertFalse((folder/'collection-stop.json').exists()); self.assertEqual([], saved)
                query.stop_blob.upload_blob.assert_not_called(); reserve.assert_not_called(); start.assert_not_called()

    def test_reprotected_acceptance_mutation_fails_fresh_verification_without_write(self):
        for mutation in ('flag', 'counter', 'job', 'console', 'prior'):
            with self.fixture() as (_, saved, retained, _, reserve, start, _):
                controller.collect(); value = retained['acceptance.json']
                if mutation == 'flag': value[controller.FALSE_FLAGS[0]] = 0
                elif mutation == 'counter': value['sql_calls'] = False
                elif mutation == 'job': value['current_job']['properties']['outboundIpAddresses'].pop()
                elif mutation == 'console': value['raw_console_responses'] = []
                else: value['raw_prior_execution']['name'] = CURRENT
                with self.subTest(mutation=mutation), self.assertRaises(ValueError): controller.collect(True)
                self.assertEqual(1, len(saved)); reserve.assert_not_called(); start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
