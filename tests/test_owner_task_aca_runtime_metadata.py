"""Offline metadata-only continuation tests; no cloud, SQL or model authority."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import qualify_owner_task_aca_runtime_metadata as controller
import qualify_owner_task_aca_runtime_named as named
import owner_task_aca_runtime_contract as contract
import owner_task_aca_runtime_transport as transport
from test_owner_task_aca_runtime_named import (PRINCIPAL, CLIENT, ALIAS, RECEIPT, EXECUTION, REPLICA,
    Credential, Session, Response, identity, permission, new_job, observed, plan_pf, replicas,
    native_execution, console_row, console_response, protected, body, start_ack, checks,
    history_records, supersession_record)
from test_owner_task_aca_runtime_contract import source, descriptor

NAIVE_AT = '2026-10-04T21:33:45.8403256'
APPROVED_IP_SHA = '080a8f5966396c6439ae8ec19074a0fbf2b07da9a733450ad53f918b904b80fb'
DIAGNOSIS_PIN = ('87653f0ce87d2fccd3625b7ac8e35a3bf8c395226029e958538a2937a4299162',
    '2026-10-04T21:45:54.6465988Z')


def synthetic_ips():
    return ['198.51.100.' + str(index) for index in range(1, 162)]


def metadata_job():
    value = new_job()
    value['systemData'] = dict(createdBy='synthetic-principal', createdByType='Application',
        lastModifiedBy='synthetic-principal', lastModifiedByType='Application', createdAt=NAIVE_AT,
        lastModifiedAt=NAIVE_AT)
    value['properties']['outboundIpAddresses'] = synthetic_ips()
    value['properties']['configuration']['registries'][0].update(username='', passwordSecretRef='')
    return value


def old_sources():
    return dict({'old-source-' + str(index): 'a' * 64 for index in range(727)},
        **controller.FROZEN_SOURCE_HASHES)


def pinned_receipt(name):
    sha, version = controller.HISTORY_PINS[name]
    return dict(blob='acceptance/gateway-read-owner-task-pg-' + named.KIND_PREFIX +
        name.removesuffix('.json') + '-' + sha + '.json', sha256=sha, version_id=version,
        protected_readback_verified=True)


def grant_receipt():
    sha, version = named.HISTORY_PINS['grant-acceptance.json']
    return dict(blob='acceptance/gateway-read-owner-task-pg-aca-runtime-continuation-grant-acceptance-' + sha + '.json',
        sha256=sha, version_id=version, protected_readback_verified=True)


def installed_pf():
    p = plan_pf(); earlier = {k: v for k, v in old_sources().items() if k not in named.ADDITIONS}
    p.update(status='prepared_secretless_valid_name_aca_runtime', operation_id=named.OPERATION_ID,
        trial_operation_id=controller.TRIAL_OPERATION_ID, job=controller.JOB, job_id=controller.JOB_ID,
        identity=identity(), initial_roles=permission(), environment={}, sources=old_sources(),
        local_checks=checks(), local_qa_policy=named.local_qa_policy(), supersession={'synthetic': True},
        accepted_grant_receipt=grant_receipt(),
        history=dict(sources=earlier, identity=identity(), receipts={'grant-acceptance.json': grant_receipt()}))
    p.update({key: False for key in controller.FALSE_FLAGS})
    return p


def diagnosis_value(p, pf_raw, ack_raw):
    return dict(status='acknowledged_unstarted_job_native_metadata_readback_unqualified', operation_id=named.OPERATION_ID,
        trial_operation_id=controller.TRIAL_OPERATION_ID, preflight_receipt=pf_raw['protected_receipt'],
        install_ack_receipt=ack_raw['protected_receipt'], failure_fields=list(controller.FAILURE_FIELDS),
        native_failure_code='aca_runtime_readback_invalid', native_timestamps=NAIVE_AT,
        native_wire_timezone_suffix_absent=True, native_timestamp_utc_independently_verified=False,
        outbound_address_count=161, outbound_addresses_sha256=APPROVED_IP_SHA, all_outbound_addresses_parse=True,
        registry_empty_optional_defaults_observed=True, raw_native_metadata_preserved=True, diagnostic_shadow_only=True,
        remaining_definition_fields_match_frozen_contract=True, source_or_validator_rewritten=False,
        install_attempt_consumed=True, automatic_install_retry=False, definition_acceptance_recorded=False,
        runtime_acceptance_recorded=False, local_start_and_acceptance_records_absent=True, start_reservation_absent=True,
        capture_stop_absent=True, accepted_permissions_unchanged=True, existing_resource_guards_unchanged=True,
        initial_diagnosis_stopped_before_storage_at_additional_metadata_mismatch=True,
        api_reference='https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/jobs/get?view=rest-resource-manager-containerapps-2025-07-01',
        checked_at_utc='2026-10-04T21:45:44+00:00', raw_job_before=metadata_job(), raw_job_after=metadata_job(),
        complete_execution_inventory_before={'value': []}, complete_execution_inventory_after={'value': []},
        sources=p['sources'], sql_calls=0, model_calls=0, **{key: False for key in controller.FALSE_FLAGS})


def installed_records():
    p = installed_pf(); pf_raw = dict(p, protected_receipt=pinned_receipt('preflight.json'))
    intent = named.intent_value('install', pf_raw, controller.JOB_ID, transport.JOB_API, 'PUT',
        p['plan']['definition'], {'grant': p['accepted_grant_receipt']})
    intent.update(status='reserved_one_exact_cloud_effect', reservation_receipt=copy.deepcopy(controller.INSTALLED_RESERVATION))
    intent_raw = dict(intent, protected_receipt=pinned_receipt('install-intent.json'))
    ack = dict(status='one_exact_cloud_effect_acknowledged', operation_id=named.OPERATION_ID,
        trial_operation_id=controller.TRIAL_OPERATION_ID, stage='install', intent_receipt=intent_raw['protected_receipt'],
        http_status=201, raw_ack=metadata_job(), attempt_consumed=True, automatic_retry=False)
    ack_raw = dict(ack, protected_receipt=pinned_receipt('install-ack.json'))
    diag = diagnosis_value(p, pf_raw, ack_raw)
    return {'preflight.json': pf_raw, 'install-intent.json': intent_raw, 'install-ack.json': ack_raw,
        'definition-diagnosis.json': dict(diag, protected_receipt=pinned_receipt('definition-diagnosis.json'))}


def metadata_pf():
    earlier = installed_pf(); records = installed_records()
    ctx = dict(pf=earlier, receipts={name: pinned_receipt(name) for name in controller.HISTORY_PINS},
        diagnosis=body(records['definition-diagnosis.json']))
    return dict(status='prepared_installed_secretless_native_metadata_continuation', operation_id=controller.OPERATION_ID,
        trial_operation_id=controller.TRIAL_OPERATION_ID, checked_at_utc='2026-10-04T22:00:00+00:00',
        job=controller.JOB, job_id=controller.JOB_ID,
        sources=dict(old_sources(), **{name: 'b' * 64 for name in controller.ADDITIONS}),
        history_binding=controller.compact_binding(ctx), plan=earlier['plan'], prerequisite=earlier['prerequisite'],
        resource_guards=earlier['resource_guards'], identity=identity(), initial_roles=permission(),
        local_checks=checks(), local_qa_policy=named.local_qa_policy(), sql_calls=0, model_calls=0,
        daily_schedule_hkt='18:00', **{key: False for key in controller.FALSE_FLAGS})


def synthetic_shadow_validation(value, runtime, principal, client):
    """Offline core/contract boundary only; never replaces the live pin gate."""
    raw, shadow = controller.metadata_shadow_core(value, metadata_job())
    named.validate_job_metadata(shadow, runtime, principal, client)
    return raw


def fixture_receipt_decode(raw, kind, pin):
    name = kind.removeprefix(named.KIND_PREFIX) + '.json'
    if name not in controller.HISTORY_PINS or pin != controller.HISTORY_PINS[name]:
        raise AssertionError('fixture did not request the exact literal historical pin')
    if raw.get('protected_receipt') != pinned_receipt(name):
        raise ValueError('synthetic fixture receipt changed')
    return copy.deepcopy(body(raw))


@contextlib.contextmanager
def history_fixture():
    with patch.object(controller.previous.q.readback, 'receipt_binding', side_effect=fixture_receipt_decode) as decode, \
            patch.object(named, 'validate_supersession', return_value={'sources': old_sources()}), \
            patch.object(named, 'validate_supersession_sources'), \
            patch.object(controller, 'validate_job_metadata', side_effect=synthetic_shadow_validation) as adapter:
        yield decode, adapter


def missing_blob(*, status=404, code='BlobNotFound'):
    error = controller.HttpResponseError('synthetic storage response'); error.status_code = status; error.error_code = code
    return error


class PageFixture:
    def __init__(self, rows, token=None): self.rows, self.continuation_token, self.calls = rows, token, 0
    def by_page(self): return self
    def __next__(self):
        self.calls += 1
        if self.calls != 1: raise AssertionError('offline fixture must not request another page')
        return iter(self.rows)


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.offline = contextlib.ExitStack(); self.addCleanup(self.offline.close)
        for target in ('azure.identity.AzureCliCredential.get_token', 'requests.sessions.Session.request',
                'subprocess.check_output', 'subprocess.run'):
            self.offline.enter_context(patch(target, side_effect=AssertionError('offline fixture attempted external access')))


class SourceAndScopeTests(OfflineTestCase):
    def test_exact_729_preserved_with_only_two_additive_sources(self):
        old = old_sources(); current = dict(old, **{path: 'b' * 64 for path in controller.ADDITIONS})
        controller.validate_sources(old, current)
        self.assertEqual(731, len(current)); self.assertEqual({'scripts/qualify_owner_task_aca_runtime_metadata.py',
            'tests/test_owner_task_aca_runtime_metadata.py'}, set(controller.ADDITIONS))
        for change in ('extra', 'missing', 'old_hash', 'new_hash', 'old_count'):
            earlier, changed = dict(old), dict(current)
            if change == 'extra': changed['another.py'] = 'f' * 64
            elif change == 'missing': del changed[next(iter(controller.ADDITIONS))]
            elif change == 'old_hash': changed['old-source-1'] = 'f' * 64
            elif change == 'new_hash': changed[next(iter(controller.ADDITIONS))] = True
            else: del earlier['old-source-1']
            with self.subTest(change=change), self.assertRaises(ValueError): controller.validate_sources(earlier, changed)

    def test_exact_existing_job_new_operation_and_start_only_commands(self):
        self.assertEqual('2153fcf3-10a5-4e21-ada1-2bd9c319e0d3', controller.OPERATION_ID)
        self.assertEqual(named.JOB_ID, controller.JOB_ID); self.assertEqual(named.JOB, controller.JOB)
        self.assertEqual(31, len(controller.JOB)); self.assertEqual(('start',), controller.STAGES)
        self.assertNotEqual(named.PREFIX, controller.PREFIX)
        self.assertEqual(('prepare', 'start', 'capture', 'collect', 'verify'), controller.COMMANDS)
        for name in ('install', 'install_verify', 'identity_create', 'grant_create'):
            self.assertFalse(hasattr(controller, name))
        self.assertEqual(contract.definition(source(), descriptor()), plan_pf()['plan']['definition'])


class MetadataShadowTests(OfflineTestCase):
    def test_core_preserves_raw_and_deletes_exactly_five_paths_only(self):
        value = metadata_job(); before = copy.deepcopy(value)
        raw, shadow = controller.metadata_shadow_core(value, copy.deepcopy(value))
        expected = copy.deepcopy(value)
        for key in ('createdAt', 'lastModifiedAt'): del expected['systemData'][key]
        del expected['properties']['outboundIpAddresses']
        for key in ('username', 'passwordSecretRef'): del expected['properties']['configuration']['registries'][0][key]
        self.assertEqual(expected, shadow); self.assertEqual(before, raw); self.assertEqual(before, value)
        named.validate_job_metadata(shadow, descriptor(), PRINCIPAL, CLIENT)
        shadow['properties']['configuration']['registries'][0]['server'] = 'changed.invalid'
        self.assertEqual(before, value); self.assertEqual(before, raw)

    def test_pure_core_requires_exact_independently_supplied_native_observation(self):
        value = metadata_job()
        for mutation in ('created', 'lastip', 'order', 'image', 'identity'):
            changed = copy.deepcopy(value)
            if mutation == 'created': changed['systemData']['createdAt'] = '2026-10-04T21:33:46.8403256'
            elif mutation == 'lastip': changed['properties']['outboundIpAddresses'][-1] = '198.51.100.162'
            elif mutation == 'order': changed['properties']['outboundIpAddresses'].reverse()
            elif mutation == 'image': changed['properties']['template']['containers'][0]['image'] += '-bad'
            else: changed['identity']['userAssignedIdentities'] = {}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                controller.metadata_shadow_core(changed, value)

    def test_unsuffixed_seven_digit_calendar_is_not_utc_or_an_execution_timestamp(self):
        result = controller.native_calendar(NAIVE_AT)
        self.assertEqual(dict(native_wire_value=NAIVE_AT, wire_timezone='unspecified', utc_independently_verified=False), result)
        for value in (None, True, NAIVE_AT + 'Z', NAIVE_AT + '+00:00', NAIVE_AT[:-1], NAIVE_AT + '0',
                '2026-02-30T21:33:45.8403256', '2026-10-04T24:00:00.8403256', '2026-13-04T21:33:45.8403256'):
            with self.subTest(value=value), self.assertRaises(ValueError): controller.native_calendar(value)
        with self.assertRaises(ValueError): transport.utc(NAIVE_AT)
        with self.assertRaises(ValueError): controller.calendar_timestamp('2026-10-04T21:33:46.8403256')

    def test_core_rejects_last_invalid_ip_wrong_count_unknown_systemdata_and_defaults(self):
        changes = ('lastip', 'count', 'tuple', 'unknown', 'type', 'control', 'username', 'secret', 'nullusername', 'registryextra')
        for change in changes:
            value = metadata_job()
            if change == 'lastip': value['properties']['outboundIpAddresses'][-1] = '999.1.1.1'
            elif change == 'count': value['properties']['outboundIpAddresses'].pop()
            elif change == 'tuple': value['properties']['outboundIpAddresses'] = tuple(value['properties']['outboundIpAddresses'])
            elif change == 'unknown': value['systemData']['newField'] = 'x'
            elif change == 'type': value['systemData']['createdByType'] = 'Owner'
            elif change == 'control': value['systemData']['createdBy'] = 'unsafe\nvalue'
            elif change == 'username': value['properties']['configuration']['registries'][0]['username'] = 'owner'
            elif change == 'secret': value['properties']['configuration']['registries'][0]['passwordSecretRef'] = 'secret'
            elif change == 'nullusername': value['properties']['configuration']['registries'][0]['username'] = None
            else: value['properties']['configuration']['registries'][0]['extra'] = ''
            with self.subTest(change=change), self.assertRaises(ValueError): controller.metadata_shadow_core(value, value)

    def test_live_wrapper_pins_are_literal_and_synthetic_core_is_not_live_authority(self):
        self.assertEqual(NAIVE_AT, controller.NATIVE_TIMESTAMP); self.assertEqual(APPROVED_IP_SHA, controller.OUTBOUND_SHA256)
        self.assertEqual(161, controller.OUTBOUND_COUNT)
        self.assertNotEqual(APPROVED_IP_SHA, controller.digest(synthetic_ips()))
        with patch.object(controller, 'metadata_shadow_core') as core, self.assertRaises(ValueError):
            controller.validated_shadow(metadata_job())
        core.assert_not_called()
        for change in ('created', 'modified', 'lastip', 'order', 'count'):
            value = metadata_job()
            if change == 'created': value['systemData']['createdAt'] = '2026-10-04T21:33:46.8403256'
            elif change == 'modified': value['systemData']['lastModifiedAt'] += 'Z'
            elif change == 'lastip': value['properties']['outboundIpAddresses'][-1] = '198.51.100.162'
            elif change == 'order': value['properties']['outboundIpAddresses'].reverse()
            else: value['properties']['outboundIpAddresses'].pop()
            with patch.object(controller, 'metadata_shadow_core') as core, self.subTest(change=change), self.assertRaises(ValueError):
                controller.validated_shadow(value)
            core.assert_not_called()

    def test_outer_adapter_has_no_expected_native_override_and_still_uses_frozen_contract(self):
        import inspect
        self.assertEqual(['value', 'expected_runtime', 'principal_id', 'client_id'],
            list(inspect.signature(controller.validate_job_metadata).parameters))
        value = metadata_job(); raw, shadow = controller.metadata_shadow_core(value, value)
        with patch.object(controller, 'validated_shadow', return_value=(raw, shadow)) as gate, \
                patch.object(named, 'validate_job_metadata', wraps=named.validate_job_metadata) as contract_check:
            returned = controller.validate_job_metadata(value, descriptor(), PRINCIPAL, CLIENT)
        self.assertEqual(value, returned); gate.assert_called_once_with(value)
        contract_check.assert_called_once_with(shadow, descriptor(), PRINCIPAL, CLIENT)
        shadow['properties']['configuration']['secrets'] = [{'name': 'not-allowed', 'value': 'synthetic'}]
        with patch.object(controller, 'validated_shadow', return_value=(raw, shadow)), self.assertRaises(ValueError):
            controller.validate_job_metadata(value, descriptor(), PRINCIPAL, CLIENT)

    def test_metadata_relation_retains_timezone_and_network_authority_limitations(self):
        relation = controller.metadata_relation()
        self.assertEqual(list(controller.FAILURE_FIELDS), relation['validated_shadow_paths'])
        self.assertEqual(APPROVED_IP_SHA, relation['outbound_addresses_sha256'])
        self.assertIs(relation['native_timestamp']['utc_independently_verified'], False)
        self.assertIs(relation['public_egress_or_network_authority_verified'], False)
        self.assertIs(relation['raw_native_preserved'], True)

    def test_non_native_hook_objects_are_rejected_without_copy_or_string_hooks(self):
        class Hook(dict):
            def __deepcopy__(self, memo): raise AssertionError('deepcopy hook ran')
            def items(self): raise AssertionError('items hook ran')
        class StringHook(str):
            def __str__(self): raise AssertionError('string hook ran')
        for value in (Hook(metadata_job()), {'systemData': StringHook('unsafe')}):
            with self.subTest(type=type(value).__name__), self.assertRaises(ValueError):
                controller.metadata_shadow_core(value, value)


class HistoryAndPreflightTests(OfflineTestCase):
    def test_all_four_historical_literal_pins_and_installed201_are_preserved(self):
        self.assertEqual(DIAGNOSIS_PIN, controller.HISTORY_PINS['definition-diagnosis.json'])
        records = installed_records(); before = copy.deepcopy(records)
        with history_fixture() as (decode, adapter): value = controller.validate_history(records)
        self.assertEqual(201, value['ack']['http_status']); self.assertIs(value['ack']['attempt_consumed'], True)
        self.assertIs(value['ack']['automatic_retry'], False); self.assertEqual(before, records)
        self.assertEqual(copy.deepcopy(controller.INSTALLED_RESERVATION), value['intent']['reservation_receipt'])
        self.assertEqual(5, decode.call_count); adapter.assert_called_once()
        for call in decode.call_args_list:
            name = call.args[1].removeprefix(named.KIND_PREFIX) + '.json'
            self.assertEqual(controller.HISTORY_PINS[name], call.args[2])
        self.assertIs(value['diagnosis']['runtime_acceptance_recorded'], False)

    def test_history_modified_pins_missing_records_intent_ack_and_old_source_are_denied(self):
        for change in ('missing', 'receipt', 'intent', 'reservation', 'ack200', 'ack202', 'boolack', 'retry', 'oldsource', 'image'):
            records = installed_records()
            if change == 'missing': del records['definition-diagnosis.json']
            elif change == 'receipt': records['install-ack.json']['protected_receipt']['sha256'] = 'f' * 64
            elif change == 'intent': records['install-intent.json']['path'] = transport.ASSIGNMENT_ID
            elif change == 'reservation': records['install-intent.json']['reservation_receipt']['etag'] = '"0xBAD"'
            elif change in ('ack200', 'ack202', 'boolack'):
                records['install-ack.json']['http_status'] = True if change == 'boolack' else 200 if change == 'ack200' else 202
            elif change == 'retry': records['install-ack.json']['automatic_retry'] = True
            elif change == 'oldsource': records['preflight.json']['sources'][next(iter(controller.FROZEN_SOURCE_HASHES))] = 'f' * 64
            else: records['preflight.json']['plan']['definition']['properties']['template']['containers'][0]['image'] += '-bad'
            with history_fixture(), self.subTest(change=change), self.assertRaises(ValueError): controller.validate_history(records)

    def test_real_receipt_decoder_denies_fixture_without_rewriting_literal_pins(self):
        records = installed_records()
        with self.assertRaises(ValueError): controller.validate_history(records)

    def test_reprotected_diagnosis_shapes_counters_metadata_and_unstarted_evidence_are_denied(self):
        changes = ('flag', 'flagalias', 'counter', 'accepted', 'fields', 'extra', 'source', 'rawdrift', 'executions', 'utc')
        for change in changes:
            records = installed_records(); value = records['definition-diagnosis.json']
            if change == 'flag': value[controller.FALSE_FLAGS[0]] = True
            elif change == 'flagalias': value[controller.FALSE_FLAGS[0]] = 0
            elif change == 'counter': value['sql_calls'] = False
            elif change == 'accepted': value['runtime_acceptance_recorded'] = True
            elif change == 'fields': value['failure_fields'].pop()
            elif change == 'extra': value['new_authority'] = True
            elif change == 'source': value['sources'] = {}
            elif change == 'rawdrift': value['raw_job_after']['properties']['outboundIpAddresses'][-1] = '198.51.100.162'
            elif change == 'executions': value['complete_execution_inventory_after']['value'] = [native_execution()]
            else: value['native_timestamp_utc_independently_verified'] = True
            with history_fixture(), self.subTest(change=change), self.assertRaises(ValueError): controller.validate_history(records)

    def test_compact_preflight_binds_exact731_frozen_plan_policy_and_all_false_boundaries(self):
        p = metadata_pf(); before = copy.deepcopy(p)
        with patch.object(controller.previous, 'sources', return_value=p['sources']): value = controller.validate_preflight(p)
        self.assertEqual(before, value); self.assertEqual(before, p)
        self.assertEqual(731, len(value['sources'])); self.assertEqual(600, value['local_qa_policy']['regression_timeout_seconds'])
        self.assertEqual('18:00', value['daily_schedule_hkt'])
        for key in controller.FALSE_FLAGS: self.assertIs(value[key], False)

    def test_preflight_mutations_and_stale_current_sources_are_denied(self):
        for change in ('counter', 'flag', 'schedule', 'policy', 'hash', 'receipt', 'rawsha', 'plan', 'extra', 'current'):
            p = metadata_pf(); current = copy.deepcopy(p['sources'])
            if change == 'counter': p['model_calls'] = False
            elif change == 'flag': p[controller.FALSE_FLAGS[0]] = 0
            elif change == 'schedule': p['daily_schedule_hkt'] = '09:00'
            elif change == 'policy': p['local_qa_policy']['regression_timeout_seconds'] = 300
            elif change == 'hash': p['history_binding']['installed_sources_sha256'] = 'f' * 64
            elif change == 'receipt': p['history_binding']['receipts']['install-ack.json']['version_id'] = '2026-10-04T00:00:00.0000000Z'
            elif change == 'rawsha': p['history_binding']['installed_native_job_sha256'] = False
            elif change == 'plan': p['plan']['definition']['properties']['template']['containers'][0]['image'] += '-bad'
            elif change == 'extra': p['extra'] = 'x'
            else: current[next(iter(controller.ADDITIONS))] = 'f' * 64
            with patch.object(controller.previous, 'sources', return_value=current), self.subTest(change=change), self.assertRaises(ValueError):
                controller.validate_preflight(p)

    def test_local_prepared_mode_never_reads_cloud_history_and_boolean_alias_is_denied(self):
        p = metadata_pf(); raw = dict(p, protected_receipt=RECEIPT)
        with patch.object(controller, 'load', return_value=(raw, p)), patch.object(controller.previous, 'sources', return_value=p['sources']), \
                patch.object(controller, 'historical_context') as history, patch.object(controller, 'arm') as arm:
            self.assertEqual((raw, p), controller.prepared(cloud=False))
            for invalid in (0, 1, None, 'false'):
                with self.assertRaises(ValueError): controller.prepared(cloud=invalid)
        history.assert_not_called(); arm.assert_not_called()

    def test_fresh_history_remote_protection_failure_stops_before_acr_or_new_effect(self):
        records = installed_records()
        with history_fixture(), patch.object(controller.previous.q.readback, 'read_local',
                side_effect=lambda folder, name: copy.deepcopy(records[name])) as local, \
                patch.object(controller.previous.q.deploy, 'protected', side_effect=ValueError('protected history unavailable')) as remote, \
                patch.object(controller, 'fresh_acr') as acr, patch.object(controller, 'old_absence') as absence, \
                patch.object(controller, 'reserve') as reserve, patch.object(controller, 'arm') as arm, self.assertRaises(ValueError):
            controller.historical_context()
        self.assertEqual(4, local.call_count); remote.assert_called_once()
        acr.assert_not_called(); absence.assert_not_called(); reserve.assert_not_called(); arm.assert_not_called()

    def test_prepare_timeout_or_invalid_complete_qa_never_retains_preflight_or_effect(self):
        ctx = dict(pf=installed_pf())
        for qa in (ValueError('full regression unverified'), dict(checks(), test_count=True)):
            with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                    patch.object(controller.previous, 'sources', return_value=metadata_pf()['sources']), \
                    patch.object(controller, 'historical_context', return_value=ctx), \
                    patch.object(controller, 'new_family_absent'), \
                    patch.object(named, 'run_local_checks', side_effect=qa if isinstance(qa, Exception) else None,
                        return_value=qa if type(qa) is dict else None), \
                    patch.object(controller, 'save') as save, patch.object(controller, 'reserve') as reserve, \
                    patch.object(controller, 'arm') as arm, self.assertRaises(ValueError): controller.prepare()
            save.assert_not_called(); reserve.assert_not_called(); arm.assert_not_called()


class StartTransportAndEffectsTests(OfflineTestCase):
    def args(self):
        p = metadata_pf()
        return ('start', {'protected_receipt': RECEIPT}, p, controller.JOB_ID + '/start', transport.JOB_API,
            'POST', copy.deepcopy(controller.start_body(p)), copy.deepcopy(controller.prior(p)))

    def test_transport_denies_all_install_identity_grant_delete_or_foreign_routes_before_token(self):
        denied = [('PUT', controller.JOB_ID, plan_pf()['plan']['definition']),
            ('PUT', contract.IDENTITY_ID, transport.identity_body()),
            ('PUT', transport.ASSIGNMENT_ID, transport.assignment_body(PRINCIPAL)),
            ('DELETE', controller.JOB_ID, None), ('POST', contract.JOB_ID + '/start', controller.start_body(plan_pf())),
            ('POST', controller.JOB_ID + '/stop', {}), ('PATCH', controller.JOB_ID, {})]
        for method, path, payload in denied:
            credential, session = Credential(), Session([])
            client = controller.ArmTransport(credential, session, runtime_source_bytes=source(), expected_runtime=descriptor())
            with self.subTest(method=method, path=path), self.assertRaises(ValueError):
                client.request(path, transport.JOB_API, method=method, body=payload)
            self.assertEqual([], credential.calls); self.assertEqual([], session.calls)

    def test_exact_start_is_single_attempt_even_on_unknown_reply_or_close_failure(self):
        for response in (Response({'id': controller.JOB_ID + '/executions/' + EXECUTION, 'name': EXECUTION}),
                RuntimeError('sensitive provider value'), Response({}, close_error=True)):
            credential, session = Credential(), Session([response])
            client = controller.ArmTransport(credential, session, runtime_source_bytes=source(), expected_runtime=descriptor())
            with self.subTest(response=type(response).__name__):
                if type(response) is Response and not response.close_error:
                    value, status = client.request(controller.JOB_ID + '/start', transport.JOB_API,
                        method='POST', body=controller.start_body(plan_pf())); self.assertEqual(200, status)
                    self.assertEqual(EXECUTION, value['name'])
                else:
                    with self.assertRaises(ValueError) as caught:
                        client.request(controller.JOB_ID + '/start', transport.JOB_API, method='POST', body=controller.start_body(plan_pf()))
                    self.assertNotIn('sensitive provider value', str(caught.exception))
                with self.assertRaises(ValueError):
                    client.request(controller.JOB_ID + '/start', transport.JOB_API, method='POST', body=controller.start_body(plan_pf()))
            self.assertEqual(1, len(session.calls)); self.assertEqual(1, len(credential.calls))
            kwargs = session.calls[0][2]; self.assertIs(kwargs['allow_redirects'], False)
            self.assertIs(kwargs['stream'], True); self.assertEqual((5, 20), kwargs['timeout'])

    def test_effect_stage_route_prior_and_body_cannot_be_swapped_before_reservation(self):
        for change in ('stage', 'path', 'api', 'method', 'body', 'prior', 'pin', 'reference'):
            args = list(self.args())
            if change == 'stage': args[0] = 'install'
            elif change == 'path': args[3] = transport.ASSIGNMENT_ID
            elif change == 'api': args[4] = transport.ROLE_API
            elif change == 'method': args[5] = 'PUT'
            elif change == 'body': args[6]['containers'][0]['image'] += '-bad'
            elif change == 'prior': args[7]['grant'] = grant_receipt()
            elif change == 'pin': args[7]['diagnosis']['sha256'] = 'f' * 64
            else: args[2]['history_binding']['receipts']['install-ack.json']['version_id'] = 'unknown'
            with patch.object(controller, 'reserve') as reserve, patch.object(controller, 'arm') as arm, \
                    patch.object(controller, 'old_absence') as old, self.subTest(change=change), self.assertRaises(ValueError):
                controller.mutate(*args)
            reserve.assert_not_called(); arm.assert_not_called(); old.assert_not_called()

    def test_old_new_or_stopped_attempt_conflict_blocks_effect_before_reservation(self):
        for hook in ('old_absence', 'capture_permitted', 'new_attempt_absent', 'new_family_absent'):
            with contextlib.ExitStack() as stack:
                for name in ('old_absence', 'capture_permitted', 'new_attempt_absent', 'new_family_absent'):
                    stack.enter_context(patch.object(controller, name,
                        side_effect=ValueError('prior attempt exists') if name == hook else None))
                reserve = stack.enter_context(patch.object(controller, 'reserve'))
                client = stack.enter_context(patch.object(controller, 'arm'))
                with self.subTest(hook=hook), self.assertRaises(ValueError): controller.mutate(*self.args())
            reserve.assert_not_called(); client.assert_not_called()

    @contextlib.contextmanager
    def mutation_fixture(self, *, request_error=None, save_error=False):
        args = self.args(); p = args[2]; recorded = []; client = Mock()
        client.request.side_effect = request_error
        client.request.return_value = ({'id': controller.JOB_ID + '/executions/' + EXECUTION, 'name': EXECUTION}, 200)
        reservation = dict(blob=controller.PREFIX + 'start-reservation.json', sha256='a' * 64,
            version_id='2026-10-04T22:00:00.0000000Z', etag='"0xABC"', protected_readback_verified=True)
        def save(value, name):
            if save_error and name == 'start-outcome.json': raise ValueError('sensitive save failure')
            recorded.append((copy.deepcopy(value), name)); return dict(value, protected_receipt=RECEIPT)
        with contextlib.ExitStack() as stack:
            for name in ('old_absence', 'capture_permitted', 'new_attempt_absent', 'new_family_absent', 'reservation_readback', 'pre_effect_checks'):
                stack.enter_context(patch.object(controller, name))
            reserve = stack.enter_context(patch.object(controller, 'reserve', return_value=reservation))
            stack.enter_context(patch.object(controller, 'save', side_effect=save))
            stack.enter_context(patch.object(controller.previous, 'sources', return_value=p['sources']))
            stack.enter_context(patch.object(controller, 'arm', return_value=client))
            yield args, recorded, client, reserve

    def test_single_start_effect_retains_intent_then_ack_and_zero_sql_or_model_authority(self):
        with self.mutation_fixture() as (args, recorded, client, reserve): result = controller.mutate(*args)
        reserve.assert_called_once(); client.request.assert_called_once()
        self.assertEqual(['start-intent.json', 'start-ack.json'], [name for _, name in recorded])
        self.assertEqual(list(args[3:6]), list(client.request.call_args.args) + [client.request.call_args.kwargs['method']])
        self.assertIs(result['attempt_consumed'], True); self.assertIs(result['automatic_retry'], False)

    def test_unknown_ack_or_failed_outcome_retention_is_consumed_and_never_retry(self):
        for failed_save in (False, True):
            with self.mutation_fixture(request_error=ValueError('sensitive provider value'), save_error=failed_save) as (args, recorded, client, reserve), \
                    self.subTest(failed_save=failed_save), self.assertRaises(ValueError) as caught:
                controller.mutate(*args)
            self.assertNotIn('sensitive provider value', str(caught.exception)); reserve.assert_called_once(); client.request.assert_called_once()
            self.assertEqual('start-intent.json', recorded[0][1])
            if not failed_save:
                outcome = recorded[-1][0]; self.assertIs(outcome['attempt_consumed'], True)
                self.assertIs(outcome['automatic_retry'], False); self.assertIs(outcome['no_effects_verified'], False)
                self.assertEqual(0, outcome['sql_calls']); self.assertEqual(0, outcome['model_calls'])

    def test_callback_mutation_cannot_change_body_authority_or_original_pf(self):
        args = self.args(); original = copy.deepcopy(args); p = args[2]; sent = []; client = Mock()
        def reserve(stage, value):
            value['body']['containers'][0]['image'] = 'attacker.invalid/image'
            return dict(blob=controller.PREFIX + 'start-reservation.json', sha256='a' * 64,
                version_id='2026-10-04T22:00:00.0000000Z', etag='"0xABC"', protected_readback_verified=True)
        def precheck(pf, *unused): pf['plan']['definition']['properties']['template']['containers'][0]['image'] = 'attacker.invalid/image'
        client.request.side_effect = lambda *a, **kw: sent.append(copy.deepcopy(kw['body'])) or ({'name': EXECUTION}, 200)
        with contextlib.ExitStack() as stack:
            for name in ('old_absence', 'capture_permitted', 'new_attempt_absent', 'new_family_absent', 'reservation_readback'):
                stack.enter_context(patch.object(controller, name))
            stack.enter_context(patch.object(controller, 'reserve', side_effect=reserve))
            stack.enter_context(patch.object(controller, 'pre_effect_checks', side_effect=precheck))
            stack.enter_context(patch.object(controller, 'save', side_effect=lambda value, name: dict(value, protected_receipt=RECEIPT)))
            stack.enter_context(patch.object(controller.previous, 'sources', return_value=p['sources']))
            stack.enter_context(patch.object(controller, 'arm', return_value=client))
            controller.mutate(*args)
        self.assertEqual(original, args); self.assertEqual([original[6]], sent)


class AttemptAbsenceTests(OfflineTestCase):
    def test_blob_absence_requires_exact404_code_and_native_status(self):
        from azure.storage.blob import StorageErrorCode
        for status, code, accepted in ((404, 'BlobNotFound', True), (404, StorageErrorCode.blob_not_found, True),
                (404, 'ContainerNotFound', False), (404, 'ResourceNotFound', False), (403, 'BlobNotFound', False),
                (True, 'BlobNotFound', False), ('404', 'BlobNotFound', False)):
            blob = Mock(); blob.get_blob_properties.side_effect = missing_blob(status=status, code=code)
            with self.subTest(status=status, code=str(code)):
                if accepted: controller.blob_absent(blob)
                else:
                    with self.assertRaises(ValueError): controller.blob_absent(blob)
            blob.get_blob_properties.assert_called_once_with(timeout=10, retry_total=0)
        for error in (None, ValueError('sensitive storage value')):
            blob = Mock(); blob.get_blob_properties.side_effect = error
            with self.assertRaises(ValueError) as caught: controller.blob_absent(blob)
            self.assertNotIn('sensitive storage value', str(caught.exception))

    def test_local_consumed_start_attempt_blocks_remote_or_fresh_start_checks(self):
        for filename in ('start-intent.json', 'start-ack.json', 'start-outcome.json', 'replica-binding.json', 'acceptance.json', 'terminal-diagnostic.json'):
            with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                    patch.object(controller, 'reservation_blob') as blob, self.subTest(filename=filename):
                Path(folder, filename).touch()
                with self.assertRaises(ValueError): controller.new_attempt_absent()
            blob.assert_not_called()

    def test_new_family_unknown_extra_version_or_pagination_denies_before_effect(self):
        for change in ('extra', 'wrongversion', 'empty', 'next', 'boolnext', 'duplicate'):
            p = metadata_pf(); receipt = dict(RECEIPT,
                blob='acceptance/gateway-read-owner-task-pg-' + controller.KIND_PREFIX + 'preflight-' + RECEIPT['sha256'] + '.json')
            rows = [SimpleNamespace(name=receipt['blob'], version_id=receipt['version_id'])]
            token = None
            if change == 'extra': rows.append(SimpleNamespace(name='unknown.json', version_id='unknown'))
            elif change == 'wrongversion': rows[0].version_id = 'unknown'
            elif change == 'empty': rows = []
            elif change == 'next': token = 'opaque-next'
            elif change == 'boolnext': token = False
            else: rows.append(copy.deepcopy(rows[0]))
            service = Mock(); page = PageFixture(rows, token)
            service.get_container_client.return_value.list_blobs.return_value = page
            with patch.object(controller, 'storage', return_value=service), self.subTest(change=change), self.assertRaises(ValueError):
                controller.new_family_absent([receipt])
            self.assertEqual(1, page.calls)

    def test_new_family_complete_empty_pf_only_or_pf_and_intent_are_exact_bounded_inventories(self):
        pf_receipt = dict(RECEIPT,
            blob='acceptance/gateway-read-owner-task-pg-' + controller.KIND_PREFIX + 'preflight-' + RECEIPT['sha256'] + '.json')
        intent_receipt = dict(RECEIPT, sha256='b' * 64,
            blob='acceptance/gateway-read-owner-task-pg-' + controller.KIND_PREFIX + 'start-intent-' + 'b' * 64 + '.json')
        for receipts in ([], [pf_receipt], [pf_receipt, intent_receipt]):
            service = Mock(); page = PageFixture([SimpleNamespace(name=r['blob'], version_id=r['version_id']) for r in receipts])
            service.get_container_client.return_value.list_blobs.return_value = page
            with patch.object(controller, 'storage', return_value=service), patch.object(controller.previous.q.deploy, 'archive_protection'):
                controller.new_family_absent(receipts)
            self.assertEqual(1, page.calls); call = service.get_container_client.return_value.list_blobs.call_args
            self.assertEqual(['versions'], call.kwargs['include']); self.assertEqual(4, call.kwargs['results_per_page'])
            self.assertEqual(10, call.kwargs['timeout']); self.assertEqual(0, call.kwargs['retry_total'])

    def test_post_reservation_conflict_source_drift_or_new_family_drift_blocks_request(self):
        p = metadata_pf(); pf_raw = {'protected_receipt': RECEIPT}; intent_raw = {'protected_receipt': dict(RECEIPT, sha256='b' * 64)}
        for change in ('ack', 'outcome', 'capture', 'accepted', 'source', 'family'):
            with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(controller, 'FOLDER', Path(folder)))
                stack.enter_context(patch.object(controller, 'historical_context', return_value={'pf': installed_pf()}))
                stack.enter_context(patch.object(controller, 'bind_context'))
                stack.enter_context(patch.object(controller.previous, 'sources', return_value={} if change == 'source' else p['sources']))
                stack.enter_context(patch.object(controller, 'capture_permitted'))
                family = stack.enter_context(patch.object(controller, 'new_family_absent',
                    side_effect=ValueError('unexpected remote ACK') if change == 'family' else None))
                current = stack.enter_context(patch.object(controller, 'current_guards')); arm = stack.enter_context(patch.object(controller, 'arm'))
                if change in ('ack', 'outcome', 'capture', 'accepted'):
                    filename = {'ack': 'start-ack.json', 'outcome': 'start-outcome.json', 'capture': 'replica-binding.json', 'accepted': 'acceptance.json'}[change]
                    Path(folder, filename).touch()
                with self.subTest(change=change), self.assertRaises(ValueError): controller.pre_effect_checks(p, pf_raw, intent_raw)
                current.assert_not_called(); arm.assert_not_called()
                if change == 'family': family.assert_called_once_with([pf_raw['protected_receipt'], intent_raw['protected_receipt']])
                else: family.assert_not_called()

    def test_old_family_requires_exact_four_pinned_versions_and_all_prior_start_stop_probes_absent(self):
        rows = [SimpleNamespace(name='acceptance/gateway-read-owner-task-pg-' + named.KIND_PREFIX +
            name.removesuffix('.json') + '-' + pin[0] + '.json', version_id=pin[1]) for name, pin in controller.HISTORY_PINS.items()]
        service = Mock(); service.get_container_client.return_value.list_blobs.return_value = PageFixture(rows)
        service.get_blob_client.return_value.get_blob_properties.side_effect = missing_blob()
        with tempfile.TemporaryDirectory() as folder, patch.object(named, 'FOLDER', Path(folder)), \
                patch.object(controller.frozen, 'FOLDER', Path(folder)), patch.object(named, 'superseded_absence') as superseded, \
                patch.object(named, 'old_start_absent') as oldstart, patch.object(controller, 'storage', return_value=service), \
                patch.object(controller.previous.q.deploy, 'archive_protection'):
            controller.old_absence()
        superseded.assert_called_once(); oldstart.assert_called_once()
        self.assertEqual(6, service.get_blob_client.call_count)
        self.assertEqual({prefix + name for prefix in (named.PREFIX, controller.c.PREFIX, controller.frozen.PREFIX)
            for name in ('start-reservation.json', 'capture-stop.json')}, {call.args[1] for call in service.get_blob_client.call_args_list})
        for change in ('extra', 'wrongversion', 'missing', 'token'):
            changed = copy.deepcopy(rows); token = None
            if change == 'extra': changed.append(SimpleNamespace(name='unexpected.json', version_id='unknown'))
            elif change == 'wrongversion': changed[-1].version_id = 'unknown'
            elif change == 'missing': changed.pop()
            else: token = False
            service = Mock(); service.get_container_client.return_value.list_blobs.return_value = PageFixture(changed, token)
            with tempfile.TemporaryDirectory() as folder, patch.object(named, 'FOLDER', Path(folder)), \
                    patch.object(controller.frozen, 'FOLDER', Path(folder)), patch.object(named, 'superseded_absence'), \
                    patch.object(named, 'old_start_absent'), patch.object(controller, 'storage', return_value=service), \
                    self.subTest(change=change), self.assertRaises(ValueError): controller.old_absence()
            service.get_blob_client.assert_not_called()


class CaptureTests(OfflineTestCase):
    @contextlib.contextmanager
    def fixture(self, responses, *, times=None):
        p = metadata_pf(); saved = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(controller, 'capture_permitted'))
            stop = stack.enter_context(patch.object(controller, 'capture_stop'))
            getter = stack.enter_context(patch.object(controller, 'get', side_effect=responses))
            stack.enter_context(patch.object(controller.previous, 'sources', return_value=p['sources']))
            stack.enter_context(patch.object(controller, 'arm', return_value=Mock()))
            stack.enter_context(patch.object(controller, 'save', side_effect=lambda value, name:
                saved.append((copy.deepcopy(value), name)) or dict(value, protected_receipt=RECEIPT)))
            stack.enter_context(patch.object(controller.time, 'monotonic', side_effect=times) if times is not None
                else patch.object(controller.time, 'monotonic', return_value=0))
            sleep = stack.enter_context(patch.object(controller.time, 'sleep'))
            mutate = stack.enter_context(patch.object(controller, 'mutate')); reserve = stack.enter_context(patch.object(controller, 'reserve'))
            yield p, saved, getter, stop, sleep, mutate, reserve

    def test_fast_running_capture_preserves_native_mapping_and_never_dispatches(self):
        running = native_execution(terminal=False)
        with self.fixture([running, replicas(), running]) as (p, saved, getter, stop, sleep, mutate, reserve):
            result = controller.capture_bound(p, start_ack())
        self.assertEqual(3, getter.call_count); stop.assert_not_called(); sleep.assert_not_called()
        mutate.assert_not_called(); reserve.assert_not_called(); self.assertEqual('replica-binding.json', saved[0][1])
        self.assertEqual(running, result['raw_execution']); self.assertEqual(replicas(), result['raw_replicas'])
        for key in controller.FALSE_FLAGS: self.assertIs(result[key], False)

    def test_only_processing_exact_notfound_or_complete_empty_inventory_may_poll(self):
        running = native_execution(terminal=False); processing = copy.deepcopy(running); processing['properties']['status'] = 'Processing'
        missing = controller.NativeHttpError(404, b'{"error":{"code":"ResourceNotFound","message":"not ready"}}')
        for values in ([processing, running, replicas(), running],
                [running, {'value': [], 'nextLink': None}, running, replicas(), running], [missing, running, replicas(), running]):
            with self.fixture(values) as (p, saved, getter, stop, sleep, mutate, reserve): controller.capture_bound(p, start_ack())
            self.assertEqual(1, len(saved)); sleep.assert_called_once(); stop.assert_not_called(); mutate.assert_not_called(); reserve.assert_not_called()

    def test_invalid_first_then_clean_metadata_cannot_wash_structural_failure(self):
        running = native_execution(terminal=False)
        for change in ('foreign', 'template', 'terminal', 'duplicate', 'restart', 'partial', 'foreignreplica', 'drift'):
            before, rows, after = copy.deepcopy(running), replicas(), copy.deepcopy(running)
            if change == 'foreign': before['id'] += '-foreign'
            elif change == 'template': before['properties']['template']['containers'][0]['image'] += '-bad'
            elif change == 'terminal': before = native_execution()
            elif change == 'duplicate': rows['value'].append(copy.deepcopy(rows['value'][0]))
            elif change == 'restart': rows['value'][0]['containers'][0]['restartCount'] = 1
            elif change == 'partial': rows['nextLink'] = 'unknown-next'
            elif change == 'foreignreplica': rows['value'][0]['name'] += '-foreign'
            else: after['properties']['startTime'] = '2026-10-04T20:30:02Z'
            with self.fixture([before, rows, after, running, replicas(), running]) as (p, saved, getter, stop, sleep, mutate, reserve), \
                    self.subTest(change=change), self.assertRaises(ValueError): controller.capture_bound(p, start_ack())
            self.assertEqual([], saved); stop.assert_called_once_with(start_ack()); sleep.assert_not_called()
            self.assertLessEqual(getter.call_count, 3); mutate.assert_not_called(); reserve.assert_not_called()

    def test_readiness_is_bounded45_attempts90_seconds_without_start_retry(self):
        processing = native_execution(terminal=False); processing['properties']['status'] = 'Processing'
        with self.fixture([processing] * 45) as (p, saved, getter, stop, sleep, mutate, reserve), self.assertRaises(controller.CapturePending):
            controller.capture_bound(p, start_ack())
        self.assertEqual(45, getter.call_count); self.assertEqual(44, sleep.call_count); self.assertEqual([], saved)
        stop.assert_not_called(); mutate.assert_not_called(); reserve.assert_not_called()
        with self.fixture([], times=[0, 91, 91]) as (p, saved, getter, stop, sleep, mutate, reserve), self.assertRaises(controller.CapturePending):
            controller.capture_bound(p, start_ack())
        getter.assert_not_called(); stop.assert_not_called()

    def test_start_retains_only_readiness_pending_and_propagates_structural_failure(self):
        p = metadata_pf(); ack = start_ack()
        for error in (controller.CapturePending(), ValueError('structural failure')):
            output = io.StringIO()
            with patch.object(controller, 'prepared', return_value=({'protected_receipt': RECEIPT}, p)), \
                    patch.object(controller, 'old_absence'), patch.object(controller, 'capture_permitted'), \
                    patch.object(controller, 'new_attempt_absent'), patch.object(controller, 'new_family_absent'), \
                    patch.object(controller, 'arm', return_value=Mock()), patch.object(controller, 'inventory', return_value={'value': []}), \
                    patch.object(controller, 'mutate', return_value=ack) as mutate, \
                    patch.object(controller, 'capture_bound', side_effect=error), contextlib.redirect_stdout(output):
                if type(error) is controller.CapturePending: self.assertEqual(ack, controller.start())
                else:
                    with self.assertRaisesRegex(ValueError, 'structural failure'): controller.start()
            mutate.assert_called_once()
            if type(error) is controller.CapturePending: self.assertIs(json.loads(output.getvalue())['automatic_start_retry'], False)
            else: self.assertEqual('', output.getvalue())

    def test_local_or_remote_durable_stop_blocks_capture_collect_verify_before_queries(self):
        for local in ('capture-stop-intent.json', 'capture-diagnostic.json', None):
            with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                    patch.object(controller, 'capture_control_blob', return_value=Mock()) as blob, \
                    patch.object(controller, 'prepared') as prepared, patch.object(controller, 'arm') as arm, \
                    patch.object(controller, 'mutate') as mutate, self.subTest(local=local):
                if local: Path(folder, local).touch()
                for action in (controller.capture, controller.collect, lambda: controller.collect(True)):
                    with self.assertRaises(ValueError): action()
                prepared.assert_not_called(); arm.assert_not_called(); mutate.assert_not_called()
                if local: blob.assert_not_called()
                else: self.assertEqual(3, blob.call_count)

    def test_lost_stop_ack_leaves_local_one_shot_stop_and_never_leaks_provider_values(self):
        blob = Mock(); blob.upload_blob.side_effect = ValueError('sensitive-stop-value')
        with tempfile.TemporaryDirectory() as folder, patch.object(controller, 'FOLDER', Path(folder)), \
                patch.object(controller, 'capture_control_blob', return_value=blob) as control, patch.object(controller, 'save') as save:
            with self.assertRaises(ValueError) as caught: controller.capture_stop(start_ack())
            self.assertNotIn('sensitive-stop-value', str(caught.exception)); self.assertTrue(Path(folder, 'capture-stop-intent.json').is_file())
            with self.assertRaises(ValueError): controller.capture_permitted()
            control.assert_called_once(); blob.upload_blob.assert_called_once(); save.assert_not_called()
            value = json.loads(Path(folder, 'capture-stop-intent.json').read_text(encoding='utf-8'))
            self.assertIs(value['capture_or_collection_authorized'], False); self.assertNotIn('sensitive-stop-value', json.dumps(value))
            self.assertIs(blob.upload_blob.call_args.kwargs['overwrite'], False); self.assertEqual(0, blob.upload_blob.call_args.kwargs['retry_total'])


class TerminalCollectionTests(OfflineTestCase):
    @contextlib.contextmanager
    def fixture(self, rows, *, inventory_value=None, get_after=None):
        p = metadata_pf(); ack = start_ack(); running = native_execution(terminal=False); terminal = native_execution()
        ctx = {'pf': installed_pf()}; live = dict(job=metadata_job(), identity=identity(), roles=permission())
        captured = dict(status='native_running_replica_binding_observed', operation_id=controller.OPERATION_ID,
            trial_operation_id=controller.TRIAL_OPERATION_ID, start_ack_receipt=ack['protected_receipt'],
            raw_execution=running, raw_replicas=replicas(), binding=replicas()['value'][0])
        saved, retained = [], {}; query = Mock(side_effect=lambda scope: console_response(rows))
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
            stack.enter_context(patch.object(controller, 'historical_context', return_value=ctx))
            stack.enter_context(patch.object(controller, 'bind_context'))
            stack.enter_context(patch.object(controller, 'current_guards', return_value=live))
            stack.enter_context(patch.object(controller, 'start_chain', return_value=(ack, EXECUTION)))
            stack.enter_context(patch.object(controller, 'load', side_effect=load))
            stack.enter_context(patch.object(controller, 'save', side_effect=save))
            getter = stack.enter_context(patch.object(controller, 'get', side_effect=lambda *a, **kw:
                copy.deepcopy(terminal if get_after is None or getter.call_count % 2 else get_after)))
            stack.enter_context(patch.object(controller, 'inventory', return_value={'value': [terminal]} if inventory_value is None else inventory_value))
            stack.enter_context(patch.object(controller, 'job_observation', return_value=metadata_job()))
            stack.enter_context(patch.object(controller, 'roles', return_value=permission()))
            stack.enter_context(patch.object(named, 'resource_guards', return_value=p['resource_guards']))
            stack.enter_context(patch.object(controller.previous, 'sources', return_value=p['sources']))
            stack.enter_context(patch.object(controller, 'LogsTransport', return_value=SimpleNamespace(call=query)))
            mutate = stack.enter_context(patch.object(controller, 'mutate')); reserve = stack.enter_context(patch.object(controller, 'reserve'))
            yield p, saved, retained, query, mutate, reserve

    def test_complete_runtime_event_and_fresh_readonly_verification_no_resave_or_dispatch(self):
        with self.fixture([console_row()]) as (_, saved, retained, query, mutate, reserve):
            result = controller.collect(); verified = controller.collect(True)
        self.assertEqual('independent_secretless_native_metadata_runtime_verified', verified['status'])
        self.assertEqual(1, len(saved)); self.assertEqual(4, query.call_count); mutate.assert_not_called(); reserve.assert_not_called()
        self.assertEqual('acceptance.json', saved[0][1]); self.assertIs(result['acceptance_recorded'], True)
        self.assertEqual(observed(), result['observed']); self.assertEqual([console_row()], result['complete_console_events'])
        self.assertEqual(metadata_job(), result['current_job']); self.assertEqual(controller.metadata_relation(), result['metadata_relation'])
        self.assertIs(result['complete_console_observation']['platform_replacement_excluded'], False)
        self.assertEqual(0, result['sql_calls']); self.assertEqual(0, result['model_calls'])
        for key in controller.FALSE_FLAGS: self.assertIs(result[key], False)

    def test_extra_console_event_preserved_unqualified_without_filtering_or_restart(self):
        rows = [console_row(), dict(console_row(), Container='other-container', Log='unexpected event')]
        with self.fixture(rows) as (_, saved, retained, query, mutate, reserve):
            result = controller.collect(); verified = controller.collect(True)
        self.assertEqual('terminal_native_metadata_aca_runtime_unqualified', result['status'])
        self.assertIsNone(result['observed']); self.assertEqual(sorted(rows, key=controller.canonical), result['complete_console_events'])
        self.assertIs(result['acceptance_recorded'], False); self.assertEqual('terminal-diagnostic.json', saved[0][1])
        self.assertEqual('independent_native_metadata_diagnostic_verified', verified['status'])
        mutate.assert_not_called(); reserve.assert_not_called(); self.assertEqual(4, query.call_count)

    def test_empty_partial_error_changed_console_or_extra_replica_is_not_acceptance(self):
        bad_after = native_execution(); bad_after['properties']['endTime'] = '2026-10-04T20:32:02Z'
        for rows, inv, after in (([], None, None), ([console_row()], {'value': [native_execution(), native_execution()]}, None),
                ([console_row()], None, bad_after)):
            with self.fixture(rows, inventory_value=inv, get_after=after) as (_, saved, _, _, mutate, reserve), self.assertRaises(ValueError):
                controller.collect()
            self.assertEqual([], saved); mutate.assert_not_called(); reserve.assert_not_called()
        scope = controller.console_scope(EXECUTION, '2026-10-04T20:29:00Z', '2026-10-04T20:42:01Z')
        for change in ('partial', 'error', 'drift'):
            first = console_response([console_row()]); second = copy.deepcopy(first)
            if change == 'partial': first['partialError'] = {'message': 'synthetic partial'}
            elif change == 'error': first['error'] = {'message': 'synthetic error'}
            else: second['tables'][0]['rows'][0][-1] += 'changed'
            with self.subTest(change=change), self.assertRaises(ValueError): controller.collect_console(Mock(side_effect=[first, second]), scope)

    def test_verify_rejects_reprotected_result_mutation_without_resave_and_boolean_alias(self):
        for change in ('flag', 'counter', 'hash', 'nativejob', 'events'):
            with self.fixture([console_row()]) as (_, saved, retained, _, mutate, reserve), self.subTest(change=change):
                controller.collect(); value = retained['acceptance.json']
                if change == 'flag': value[controller.FALSE_FLAGS[0]] = 0
                elif change == 'counter': value['sql_calls'] = False
                elif change == 'hash': value['complete_console_sha256'] = 'f' * 64
                elif change == 'nativejob': del value['current_job']['properties']['outboundIpAddresses']
                else: value['complete_console_events'] = []
                with self.assertRaises(ValueError): controller.collect(True)
                self.assertEqual(1, len(saved)); mutate.assert_not_called(); reserve.assert_not_called()
        for flag in (None, 0, 1, 'true'):
            with patch.object(controller, 'capture_permitted') as guard, self.assertRaises(ValueError): controller.collect(flag)
            guard.assert_not_called()


if __name__ == '__main__':
    unittest.main()
