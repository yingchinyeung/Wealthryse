"""Additive, single-START continuation of an installed secretless runtime job.

The protected 729-source programme, installed definition, identity and grant
are immutable. Only five observed descriptive/default metadata paths are
removed from a separately validated shadow. Native evidence is never changed;
an unsuffixed timestamp is not asserted to be UTC. Import performs no IO.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import re
import time
from urllib.parse import urlsplit

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.identity import AzureCliCredential

import qualify_owner_task_aca_runtime_named as n

c, previous, frozen, contract, transport = n.c, n.previous, n.frozen, n.contract, n.transport
ROOT, JOB, JOB_ID = n.ROOT, n.JOB, n.JOB_ID
FOLDER = ROOT / '.work/owner-task-aca-runtime-metadata-20261004'
PREFIX = 'probes/owner-codex-aca-runtime-metadata-20261004/'
KIND_PREFIX = 'aca-runtime-metadata-'
OPERATION_ID = '2153fcf3-10a5-4e21-ada1-2bd9c319e0d3'
TRIAL_OPERATION_ID = n.TRIAL_OPERATION_ID
COMMANDS, STAGES = ('prepare', 'start', 'capture', 'collect', 'verify'), ('start',)
ADDITIONS = frozenset(('scripts/qualify_owner_task_aca_runtime_metadata.py', 'tests/test_owner_task_aca_runtime_metadata.py'))
MAX_BYTES, FALSE_FLAGS = n.MAX_BYTES, n.FALSE_FLAGS
require, canonical, digest, native_copy = n.require, n.canonical, n.digest, n.native_copy
get, environment, roles = n.get, n.environment, n.roles
validate_name, validate_execution, validate_replica = n.validate_name, n.validate_execution, n.validate_replica
acknowledged_execution, parse_console = n.acknowledged_execution, n.parse_console
console_scope, collect_console, LogsTransport = n.console_scope, n.collect_console, n.LogsTransport
NativeHttpError, CapturePending = n.NativeHttpError, n.CapturePending
NATIVE_TIMESTAMP = '2026-10-04T21:33:45.8403256'
OUTBOUND_SHA256 = '080a8f5966396c6439ae8ec19074a0fbf2b07da9a733450ad53f918b904b80fb'
OUTBOUND_COUNT = 161
FAILURE_FIELDS = ['systemData.createdAt', 'systemData.lastModifiedAt', 'properties.outboundIpAddresses',
    'properties.configuration.registries[0].username', 'properties.configuration.registries[0].passwordSecretRef']
HISTORY_PINS = {
    'preflight.json': ('1e4c6e1d98bfd5223e52b53f6a69ee69d88250d6824a6bd788259e97b58ef255', '2026-10-04T21:22:55.0834538Z'),
    'install-intent.json': ('7d838c096120d677eab3b2f6ddbe73e2697b276479fc0995e41c800fc452f0d0', '2026-10-04T21:32:44.8174965Z'),
    'install-ack.json': ('3cd4573bd0a23cadc51d27513e3be13bc1856808a5935908cb3bf4ac6ffa3f4c', '2026-10-04T21:34:04.0914916Z'),
    'definition-diagnosis.json': ('87653f0ce87d2fccd3625b7ac8e35a3bf8c395226029e958538a2937a4299162', '2026-10-04T21:45:54.6465988Z')}
INSTALLED_RESERVATION = dict(blob=n.PREFIX + 'install-reservation.json',
    sha256='7fabbd3526428808d456ee5a3ccf166c4bbe9fa1f881dfe473b7ec0d8a76db43',
    version_id='2026-10-04T21:32:19.1533258Z', etag='"0x8DF225EF7EAD8CA"', protected_readback_verified=True)
FROZEN_SOURCE_HASHES = {
    'scripts/qualify_owner_task_aca_runtime_named.py': '652097c10b431c1b87525a8b5adcb5774fc78c731d28060869cedee1b7c46d1e',
    'tests/test_owner_task_aca_runtime_named.py': '5f4f03c794bdc624521df1caa9da3b458302bb614c8bf4b9741a79b12cc5c181'}


def validate_sources(old, current):
    old, current = native_copy(old), native_copy(current)
    require(type(old) is dict and len(old) == 729 and type(current) is dict and len(current) == 731
        and not set(old) & ADDITIONS and set(current) == set(old) | ADDITIONS
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for k, v in current.items())
        and all(current[k] == v for k, v in old.items())
        and all(old.get(k) == v for k, v in FROZEN_SOURCE_HASHES.items()), 'aca_metadata_exact_two_additive_sources_required')


def native_calendar(value):
    require(type(value) is str and value.isascii() and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{7}', value) is not None,
        'aca_metadata_exact_unsuffixed_timestamp_required')
    try: datetime(*(int(value[a:b]) for a, b in ((0, 4), (5, 7), (8, 10), (11, 13), (14, 16), (17, 19))))
    except Exception: raise ValueError('aca_metadata_native_calendar_invalid') from None
    return dict(native_wire_value=value, wire_timezone='unspecified', utc_independently_verified=False)


def calendar_timestamp(value):
    require(type(value) is str and value == NATIVE_TIMESTAMP, 'aca_metadata_exact_unsuffixed_timestamp_required')
    return native_calendar(value)


def metadata_shadow_core(value, expected_native):
    """Pure native comparison, not a pin check or permission to execute.

    An independently supplied observation makes synthetic offline fixtures
    possible. Every live caller uses validated_shadow's fixed literal gate.
    """
    raw = native_copy(value); shadow = native_copy(raw)
    expected = native_copy(expected_native)
    require(canonical(raw) == canonical(expected), 'aca_metadata_expected_native_observation_changed')
    require(type(raw) is dict and type(raw.get('systemData')) is dict,
        'aca_metadata_native_system_data_required')
    data = raw['systemData']; fields = {'createdAt', 'createdBy', 'createdByType', 'lastModifiedAt', 'lastModifiedBy', 'lastModifiedByType'}
    require(set(data) <= fields and {'createdAt', 'lastModifiedAt'} <= set(data), 'aca_metadata_known_system_data_required')
    for key, item in data.items():
        require(item is None or type(item) is str and 0 < len(item) <= 512 and item.isascii()
            and all(ch.isprintable() for ch in item), 'aca_metadata_descriptive_field_invalid')
        if key.endswith('Type') and item is not None:
            require(item in ('User', 'Application', 'ManagedIdentity', 'Key'), 'aca_metadata_descriptive_type_invalid')
    native_calendar(data['createdAt']); native_calendar(data['lastModifiedAt'])
    del shadow['systemData']['createdAt']; del shadow['systemData']['lastModifiedAt']
    props = raw.get('properties'); require(type(props) is dict, 'aca_metadata_native_properties_invalid')
    addresses = props.get('outboundIpAddresses')
    require(type(addresses) is list and len(addresses) == OUTBOUND_COUNT,
        'aca_metadata_exact_complete_outbound_array_required')
    for item in addresses:
        require(type(item) is str and 0 < len(item) <= 45, 'aca_metadata_native_address_invalid')
        try: ipaddress.ip_address(item)
        except Exception: raise ValueError('aca_metadata_native_address_invalid') from None
    del shadow['properties']['outboundIpAddresses']
    config = props.get('configuration'); require(type(config) is dict, 'aca_metadata_configuration_invalid')
    registries = config.get('registries')
    require(type(registries) is list and len(registries) == 1 and type(registries[0]) is dict
        and set(registries[0]) == {'identity', 'server', 'username', 'passwordSecretRef'}
        and type(registries[0]['username']) is str and registries[0]['username'] == ''
        and type(registries[0]['passwordSecretRef']) is str and registries[0]['passwordSecretRef'] == '',
        'aca_metadata_exact_empty_registry_defaults_required')
    del shadow['properties']['configuration']['registries'][0]['username']
    del shadow['properties']['configuration']['registries'][0]['passwordSecretRef']
    return raw, shadow


def validated_shadow(value):
    """Live wrapper: exact observed timestamp pair and complete array hash."""
    raw = native_copy(value)
    require(type(raw) is dict and type(raw.get('systemData')) is dict and type(raw.get('properties')) is dict,
        'aca_metadata_fixed_native_observation_required')
    calendar_timestamp(raw['systemData'].get('createdAt')); calendar_timestamp(raw['systemData'].get('lastModifiedAt'))
    addresses = raw['properties'].get('outboundIpAddresses')
    require(type(addresses) is list and len(addresses) == OUTBOUND_COUNT and digest(addresses) == OUTBOUND_SHA256,
        'aca_metadata_exact_complete_outbound_array_required')
    return metadata_shadow_core(raw, raw)


def validate_job_metadata(value, expected_runtime, principal_id, client_id):
    raw, shadow = validated_shadow(value)
    n.validate_job_metadata(shadow, expected_runtime, principal_id, client_id)
    return raw


def metadata_relation():
    return dict(validated_shadow_paths=list(FAILURE_FIELDS), native_timestamp=calendar_timestamp(NATIVE_TIMESTAMP),
        outbound_address_count=OUTBOUND_COUNT, outbound_addresses_sha256=OUTBOUND_SHA256,
        all_addresses_parsed=True, public_egress_or_network_authority_verified=False,
        registry_empty_optional_defaults=True, raw_native_preserved=True)


def validate_diagnosis(record, pf_raw, ack_raw, pf):
    raw = native_copy(record)
    value = previous.q.readback.receipt_binding(raw, n.KIND_PREFIX + 'definition-diagnosis', HISTORY_PINS['definition-diagnosis.json'])
    fixed = dict(status='acknowledged_unstarted_job_native_metadata_readback_unqualified', operation_id=n.OPERATION_ID,
        trial_operation_id=TRIAL_OPERATION_ID, preflight_receipt=pf_raw['protected_receipt'], install_ack_receipt=ack_raw['protected_receipt'],
        failure_fields=FAILURE_FIELDS, native_failure_code='aca_runtime_readback_invalid', native_timestamps=NATIVE_TIMESTAMP,
        native_wire_timezone_suffix_absent=True, native_timestamp_utc_independently_verified=False,
        outbound_address_count=OUTBOUND_COUNT, outbound_addresses_sha256=OUTBOUND_SHA256, all_outbound_addresses_parse=True,
        registry_empty_optional_defaults_observed=True, raw_native_metadata_preserved=True, diagnostic_shadow_only=True,
        remaining_definition_fields_match_frozen_contract=True, source_or_validator_rewritten=False,
        install_attempt_consumed=True, automatic_install_retry=False, definition_acceptance_recorded=False,
        runtime_acceptance_recorded=False, local_start_and_acceptance_records_absent=True, start_reservation_absent=True,
        capture_stop_absent=True, accepted_permissions_unchanged=True, existing_resource_guards_unchanged=True,
        initial_diagnosis_stopped_before_storage_at_additional_metadata_mismatch=True,
        api_reference='https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/jobs/get?view=rest-resource-manager-containerapps-2025-07-01',
        sql_calls=0, model_calls=0, **{k: False for k in FALSE_FLAGS})
    require(set(value) == set(fixed) | {'checked_at_utc', 'raw_job_before', 'raw_job_after',
        'complete_execution_inventory_before', 'complete_execution_inventory_after', 'sources'}
        and all(type(value.get(k)) is type(v) and canonical(value[k]) == canonical(v) for k, v in fixed.items()),
        'aca_metadata_exact_protected_diagnosis_required')
    transport.utc(value['checked_at_utc'])
    require(value['sources'] == pf['sources'] and canonical(value['raw_job_before']) == canonical(value['raw_job_after'])
        and canonical(value['complete_execution_inventory_before']) == canonical({'value': []})
        and canonical(value['complete_execution_inventory_after']) == canonical({'value': []}), 'aca_metadata_unstarted_native_diagnosis_changed')
    identity = pf['identity']['properties']
    validate_job_metadata(value['raw_job_before'], pf['prerequisite']['expected_runtime'], identity['principalId'], identity['clientId'])
    return value


def validate_history(records):
    require(type(records) is dict and set(records) == set(HISTORY_PINS), 'aca_metadata_complete_installed_history_required')
    raws = {name: native_copy(raw) for name, raw in records.items()}
    values = {name: previous.q.readback.receipt_binding(raw, n.KIND_PREFIX + name.removesuffix('.json'), HISTORY_PINS[name])
        for name, raw in raws.items()}
    pf, intent, ack = (values[k] for k in ('preflight.json', 'install-intent.json', 'install-ack.json'))
    require(pf.get('status') == 'prepared_secretless_valid_name_aca_runtime' and pf.get('operation_id') == n.OPERATION_ID
        and pf.get('trial_operation_id') == TRIAL_OPERATION_ID and pf.get('job') == JOB and pf.get('job_id') == JOB_ID
        and all(pf.get(k) is False for k in FALSE_FLAGS), 'aca_metadata_exact_installed_preflight_required')
    n.validate_sources(pf['history']['sources'], pf['sources']); previous.q.canary.validate_correction_checks(pf['local_checks'])
    n.validate_local_qa_policy(pf['local_qa_policy']); superseded = n.validate_supersession(pf['supersession'])
    n.validate_supersession_sources(superseded['sources'], pf['sources'])
    require(all(pf['sources'].get(k) == v for k, v in FROZEN_SOURCE_HASHES.items())
        and canonical(pf['plan']) == canonical(n.plan(pf['prerequisite']))
        and canonical(pf['identity']) == canonical(pf['history']['identity'])
        and pf['accepted_grant_receipt'] == pf['history']['receipts']['grant-acceptance.json'], 'aca_metadata_installed_plan_or_source_changed')
    expected = n.intent_value('install', raws['preflight.json'], JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'],
        {'grant': pf['accepted_grant_receipt']})
    require(set(intent) == set(expected) | {'status', 'reservation_receipt'}
        and canonical({k: intent.get(k) for k in expected}) == canonical(expected)
        and intent.get('status') == 'reserved_one_exact_cloud_effect' and intent['reservation_receipt'] == INSTALLED_RESERVATION,
        'aca_metadata_installed_intent_changed')
    require(set(ack) == {'status', 'operation_id', 'trial_operation_id', 'stage', 'intent_receipt', 'http_status',
        'raw_ack', 'attempt_consumed', 'automatic_retry'} and ack.get('status') == 'one_exact_cloud_effect_acknowledged'
        and ack.get('operation_id') == n.OPERATION_ID and ack.get('trial_operation_id') == TRIAL_OPERATION_ID
        and ack.get('stage') == 'install' and ack.get('intent_receipt') == raws['install-intent.json']['protected_receipt']
        and type(ack.get('http_status')) is int and ack['http_status'] == 201
        and ack.get('attempt_consumed') is True and ack.get('automatic_retry') is False
        and type(ack.get('raw_ack')) is dict and ack['raw_ack'].get('id') == JOB_ID and ack['raw_ack'].get('name') == JOB,
        'aca_metadata_installed_ack_changed')
    diagnosis = validate_diagnosis(raws['definition-diagnosis.json'], raws['preflight.json'], raws['install-ack.json'], pf)
    return dict(raws=raws, pf=pf, intent=intent, ack=ack, diagnosis=diagnosis,
        receipts={name: raw['protected_receipt'] for name, raw in raws.items()})


def storage():
    return n.superseded_storage()


def blob_absent(blob):
    try: blob.get_blob_properties(timeout=10, retry_total=0)
    except HttpResponseError as error:
        try: c.validate_original_reservation_absence(error.status_code, getattr(error, 'error_code', None))
        except Exception: raise ValueError('aca_metadata_exact_blob_absence_unproven') from None
        return
    except Exception: raise ValueError('aca_metadata_exact_blob_absence_unproven') from None
    raise ValueError('aca_metadata_prior_attempt_or_stop_exists')


def old_absence():
    n.superseded_absence(); n.old_start_absent()
    require(not any((frozen.FOLDER / (stage + '-' + suffix + '.json')).exists()
        for stage in c.STAGES for suffix in ('intent', 'ack', 'outcome')),
        'aca_metadata_original_downstream_local_attempt_exists')
    require(not any((frozen.FOLDER / name).exists() for name in ('replica-binding.json', 'acceptance.json',
        'terminal-diagnostic.json', 'capture-stop-intent.json', 'capture-diagnostic.json')),
        'aca_metadata_original_runtime_evidence_exists')
    forbidden = ('install-acceptance.json', 'install-outcome.json', 'start-intent.json', 'start-ack.json', 'start-outcome.json',
        'replica-binding.json', 'acceptance.json', 'terminal-diagnostic.json', 'capture-stop-intent.json', 'capture-diagnostic.json')
    require(not any((n.FOLDER / name).exists() for name in forbidden), 'aca_metadata_old_start_or_acceptance_exists')
    service = storage()
    expected = {(('acceptance/gateway-read-owner-task-pg-' + n.KIND_PREFIX + name.removesuffix('.json') + '-' + pin[0] + '.json'), pin[1])
        for name, pin in HISTORY_PINS.items()}
    try:
        pages = service.get_container_client(previous.q.deploy.CONTAINER).list_blobs(
            name_starts_with='acceptance/gateway-read-owner-task-pg-' + n.KIND_PREFIX, include=['versions'],
            results_per_page=8, timeout=10, retry_total=0).by_page()
        seen = set()
        for row in next(pages):
            require(len(seen) < 4 and type(row.name) is str and type(row.version_id) is str
                and (row.name, row.version_id) in expected and (row.name, row.version_id) not in seen,
                'aca_metadata_old_protected_attempt_exists')
            seen.add((row.name, row.version_id))
        require(seen == expected and (pages.continuation_token is None
            or type(pages.continuation_token) is str and pages.continuation_token == ''), 'aca_metadata_old_complete_inventory_required')
    except Exception: raise ValueError('aca_metadata_old_protected_absence_unproven') from None
    for prefix in (n.PREFIX, c.PREFIX, frozen.PREFIX):
        for name in ('start-reservation.json', 'capture-stop.json'):
            blob_absent(service.get_blob_client(previous.q.deploy.CONTAINER, prefix + name))
    previous.q.deploy.archive_protection()


def fresh_acr(pf):
    """Historical maps, exact protected records, actual show/log/show readback."""
    raws, values = {}, {}
    for name, pin in frozen.PINS.items():
        raw = previous.q.readback.read_local(previous.FOLDER, name)
        previous.q.readback.receipt_binding(raw, 'standard-pull-' + name.removesuffix('.json'), pin)
        raws[name] = raw; values[name] = previous.q.deploy.protected(raw, 'standard-pull-' + name.removesuffix('.json'))
    old, accepted = values['preflight.json'], values['acceptance.json']
    frozen.validate_source_extension(old['guards']['sources'], pf['history']['continuation_preflight']['history']['sources'])
    c.validate_sources(pf['history']['continuation_preflight']['history']['sources'], pf['history']['sources'])
    n.validate_sources(pf['history']['sources'], pf['sources']); previous.q.canary.validate_correction_checks(old['local_checks'])
    require(old.get('status') == 'prepared_standard_pull_runtime_observation' and old.get('operation_id') == previous.OPERATION_ID
        and canonical(old.get('plan')) == canonical(previous.probe_plan())
        and accepted.get('status') == 'secretless_default_user_runtime_observed' and accepted.get('operation_id') == previous.OPERATION_ID
        and accepted.get('preflight_receipt') == raws['preflight.json']['protected_receipt']
        and accepted.get('intent_receipt') == raws['dispatch-intent.json']['protected_receipt']
        and accepted.get('ack_receipt') == raws['dispatch-ack.json']['protected_receipt']
        and accepted.get('reservation_receipt') == values['dispatch-intent.json']['reservation_receipt']
        and accepted.get('acceptance_recorded') is True and accepted.get('engine_image_digest_observed') is True
        and accepted.get('candidate_runtime_metadata_verified') is True and all(accepted.get(k) is False for k in previous.FALSE_FLAGS),
        'aca_metadata_exact_acr_history_required')
    name = previous.chain(raws['preflight.json'], old, raws['dispatch-intent.json'], values['dispatch-intent.json'], values['dispatch-ack.json'])
    require(name == 'ck84', 'aca_metadata_exact_acr_run_required')
    before = previous.show(name); logs = previous.call_cli('logs', name); after = previous.show(name)
    observed = previous.parse_logs(logs, old['plan'])
    require(before == after == accepted['raw_run_before'] == accepted['raw_run_after'] and before['status'] == 'Succeeded'
        and logs.decode('utf-8') == accepted['full_logs'] and len(logs) == accepted['full_logs_bytes']
        and hashlib.sha256(logs).hexdigest() == accepted['full_logs_sha256'] and canonical(observed) == canonical(accepted['observed']),
        'aca_metadata_fresh_acr_evidence_changed')
    result = dict(receipts={name: raw['protected_receipt'] for name, raw in raws.items()}, frozen_sources=old['guards']['sources'],
        frozen_resources={key: val for key, val in old['guards'].items() if key != 'sources'}, expected_runtime=observed['runtime'], observed=observed)
    require(canonical(result) == canonical(pf['prerequisite']), 'aca_metadata_original_acr_dependency_changed')
    return result


def historical_context():
    records = {name: previous.q.readback.read_local(n.FOLDER, name) for name in HISTORY_PINS}
    ctx = validate_history(records); pf = ctx['pf']
    for name, raw in records.items(): previous.q.deploy.protected(raw, n.KIND_PREFIX + name.removesuffix('.json'))
    older = {name: previous.q.readback.read_local(c.FOLDER, name) for name in n.HISTORY_PINS}
    relation = n.validate_history(older)
    for name, raw in older.items(): previous.q.deploy.protected(raw, 'aca-runtime-continuation-' + name.removesuffix('.json'))
    require(canonical(relation) == canonical(pf['history']), 'aca_metadata_continuation_history_changed')
    originals = {name: previous.q.readback.read_local(frozen.FOLDER, name) for name in c.ORIGINAL_PINS}
    identity = c.validate_original_identity_chain(originals['preflight.json'], originals['identity-intent.json'], originals['identity-ack.json'])
    for name, raw in originals.items(): previous.q.deploy.protected(raw, 'aca-runtime-' + name.removesuffix('.json'))
    require(canonical(identity) == canonical(relation['continuation_preflight']['history']), 'aca_metadata_original_identity_history_changed')
    frozen.reservation_readback('identity', identity['reservation_receipt'], c.original_reservation_value(originals['preflight.json']))
    for stage in c.STAGES: c.original_reservation_absent(stage)
    c.reservation_readback('grant', relation['grant_reservation'], c.intent_value('grant', relation['continuation_preflight_record'],
        transport.ASSIGNMENT_ID, transport.ROLE_API, 'PUT', transport.assignment_body(identity['identity']['properties']['principalId']),
        {'identity': identity['receipts']['identity-ack.json']}))
    c.reservation_readback('install', relation['install_reservation'], c.intent_value('install', relation['continuation_preflight_record'],
        contract.JOB_ID, transport.JOB_API, 'PUT', relation['continuation_preflight']['plan']['definition'],
        {'grant': relation['receipts']['grant-acceptance.json']}))
    superseded = n.validate_supersession(pf['supersession'])
    require(canonical(previous.q.deploy.protected(pf['supersession'], n.SUPERSESSION_KIND)) == canonical(superseded),
        'aca_metadata_supersession_protected_readback_changed')
    fresh_acr(pf); old_absence()
    expected = n.intent_value('install', records['preflight.json'], JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'],
        {'grant': pf['accepted_grant_receipt']})
    n.reservation_readback('install', ctx['intent']['reservation_receipt'], expected)
    validate_sources(pf['sources'], previous.sources())
    return ctx


def route(method, path, body, missing, expected):
    require(type(method) is str and type(path) is str and type(missing) is bool, 'aca_metadata_native_route_required')
    target = urlsplit(path).path
    require(method == 'GET' or method == 'POST' and target == JOB_ID + '/start', 'aca_metadata_only_start_effect_allowed')
    return n.route(method, path, body, missing, expected)


class ArmTransport:
    """Narrow composition: frozen transport can never receive a PUT here."""
    def __init__(self, credential, session=None, *, runtime_source_bytes, expected_runtime):
        self.__expected = canonical(contract.definition(runtime_source_bytes, expected_runtime))
        self.__inner = n.ArmTransport(credential, session, runtime_source_bytes=runtime_source_bytes, expected_runtime=expected_runtime)
    def call(self, method, path, *, body=None, missing=False):
        snapshot = None if body is None else native_copy(body)
        route(method, path, snapshot, missing, json.loads(self.__expected))
        return self.__inner.call(method, path, body=snapshot, missing=missing)
    def request(self, path, api, *, method='GET', body=None, missing=False):
        require(type(path) is str and type(api) is str and '?' not in path, 'aca_metadata_exact_request_required')
        return self.call(method, path + '?api-version=' + api, body=body, missing=missing)
    def assignments(self, scope, principal_id, *, at_scope=False):
        return transport.role_assignments(self, scope, principal_id, at_scope=at_scope)


def arm(dependency):
    return ArmTransport(AzureCliCredential(), runtime_source_bytes=c.source_bytes(), expected_runtime=dependency['expected_runtime'])


def inventory(client):
    return n.inventory(client)


def job_observation(client, pf, identity):
    return validate_job_metadata(get(client, JOB_ID, transport.JOB_API), pf['prerequisite']['expected_runtime'],
        identity['properties']['principalId'], identity['properties']['clientId'])


def current_guards(ctx, client, *, unstarted):
    old = ctx['pf']; receipt, identity, permission = n.grant_binding(old, client)
    job = job_observation(client, old, identity)
    require(canonical(job) == canonical(ctx['diagnosis']['raw_job_after']), 'aca_metadata_native_installed_job_changed')
    require(canonical(n.resource_guards()) == canonical(old['resource_guards'])
        and canonical(environment(client)) == canonical(old['environment']), 'aca_metadata_resource_or_environment_changed')
    if unstarted: require(canonical(inventory(client)) == canonical({'value': []}), 'aca_metadata_job_no_longer_unstarted')
    return dict(job=job, identity=identity, roles=permission, grant_receipt=receipt)


def compact_binding(ctx):
    pf = ctx['pf']
    return dict(receipts=ctx['receipts'], installed_sources_sha256=digest(pf['sources']), installed_plan_sha256=digest(pf['plan']),
        installed_resources_sha256=digest(pf['resource_guards']), installed_native_job_sha256=digest(ctx['diagnosis']['raw_job_after']),
        installed_environment_sha256=digest(pf['environment']), accepted_grant_receipt=pf['accepted_grant_receipt'],
        metadata_relation=metadata_relation())


def save(value, name):
    require(type(name) is str and re.fullmatch('[a-z][a-z-]*\\.json', name) is not None and 0 < len(canonical(value)) <= MAX_BYTES,
        'aca_metadata_evidence_name_or_size_invalid')
    kind = KIND_PREFIX + name.removesuffix('.json')
    with (FOLDER / name).open('x', encoding='utf-8') as output:
        previous.q.deploy.archive_protection()
        raw = dict(value, protected_receipt=previous.q.deploy.retain(value, 'owner-task-pg-' + kind))
        previous.q.deploy.archive_protection(); output.write(json.dumps(raw, sort_keys=True, indent=2))
    previous.q.deploy.protected(raw, kind); return raw


def load(name):
    raw = previous.q.readback.read_local(FOLDER, name)
    return raw, previous.q.deploy.protected(raw, KIND_PREFIX + name.removesuffix('.json'))


def prepare():
    require(not (FOLDER / 'preflight.json').exists(), 'aca_metadata_preflight_already_reserved')
    new_family_absent([])
    before = previous.sources(); ctx = historical_context(); validate_sources(ctx['pf']['sources'], before)
    checks = n.run_local_checks(); previous.q.canary.validate_correction_checks(checks)
    policy = n.validate_local_qa_policy(n.local_qa_policy()); old_absence(); capture_permitted(); new_attempt_absent()
    live = current_guards(ctx, arm(ctx['pf']['prerequisite']), unstarted=True)
    require(previous.sources() == before, 'aca_metadata_source_changed_during_qa')
    dep = ctx['pf']['prerequisite']
    return save(dict(status='prepared_installed_secretless_native_metadata_continuation', operation_id=OPERATION_ID,
        trial_operation_id=TRIAL_OPERATION_ID, checked_at_utc=datetime.now(timezone.utc).isoformat(), job=JOB, job_id=JOB_ID,
        sources=before, history_binding=compact_binding(ctx), plan=ctx['pf']['plan'],
        prerequisite=dict(expected_runtime=dep['expected_runtime'], observed=dep['observed']),
        resource_guards=ctx['pf']['resource_guards'], identity=live['identity'], initial_roles=live['roles'],
        local_checks=checks, local_qa_policy=policy, sql_calls=0, model_calls=0, daily_schedule_hkt='18:00',
        **{k: False for k in FALSE_FLAGS}), 'preflight.json')


def validate_preflight(pf):
    pf = native_copy(pf)
    require(type(pf) is dict and set(pf) == {'status', 'operation_id', 'trial_operation_id', 'checked_at_utc', 'job', 'job_id',
        'sources', 'history_binding', 'plan', 'prerequisite', 'resource_guards', 'identity', 'initial_roles', 'local_checks',
        'local_qa_policy', 'sql_calls', 'model_calls', 'daily_schedule_hkt'} | set(FALSE_FLAGS)
        and pf.get('status') == 'prepared_installed_secretless_native_metadata_continuation' and pf.get('operation_id') == OPERATION_ID
        and pf.get('trial_operation_id') == TRIAL_OPERATION_ID and pf.get('job') == JOB and pf.get('job_id') == JOB_ID
        and all(pf.get(k) is False for k in FALSE_FLAGS) and type(pf.get('sql_calls')) is int and pf['sql_calls'] == 0
        and type(pf.get('model_calls')) is int and pf['model_calls'] == 0, 'aca_metadata_exact_preflight_required')
    transport.utc(pf['checked_at_utc']); require(pf['daily_schedule_hkt'] == '18:00', 'aca_metadata_daily_schedule_changed')
    previous.q.canary.validate_correction_checks(pf['local_checks']); n.validate_local_qa_policy(pf['local_qa_policy'])
    binding = pf['history_binding']
    require(type(binding) is dict and set(binding) == {'receipts', 'installed_sources_sha256', 'installed_plan_sha256',
        'installed_resources_sha256', 'installed_native_job_sha256', 'installed_environment_sha256', 'accepted_grant_receipt', 'metadata_relation'}
        and all(type(binding.get(key)) is str and re.fullmatch('[0-9a-f]{64}', binding[key])
            for key in ('installed_sources_sha256', 'installed_plan_sha256', 'installed_resources_sha256',
                'installed_native_job_sha256', 'installed_environment_sha256'))
        and type(binding['receipts']) is dict and set(binding['receipts']) == set(HISTORY_PINS)
        and type(pf['prerequisite']) is dict and set(pf['prerequisite']) == {'expected_runtime', 'observed'}
        and all(type(pf[key]) is dict for key in ('sources', 'plan', 'resource_guards', 'identity', 'initial_roles')),
        'aca_metadata_compact_binding_invalid')
    old_sources = {key: sha for key, sha in pf['sources'].items() if key not in ADDITIONS}
    validate_sources(old_sources, pf['sources'])
    require(pf['sources'] == previous.sources() and canonical(pf['plan']) == canonical(n.plan(pf['prerequisite']))
        and canonical(binding['metadata_relation']) == canonical(metadata_relation())
        and binding['installed_sources_sha256'] == digest(old_sources)
        and binding['installed_plan_sha256'] == digest(pf['plan'])
        and binding['installed_resources_sha256'] == digest(pf['resource_guards']), 'aca_metadata_preflight_source_or_plan_changed')
    c.validate_identity_metadata(pf['identity'])
    validate_receipt(binding['accepted_grant_receipt'], 'aca-runtime-continuation-grant-acceptance', n.HISTORY_PINS['grant-acceptance.json'])
    for name, receipt in pf['history_binding']['receipts'].items():
        validate_receipt(receipt, n.KIND_PREFIX + name.removesuffix('.json'), HISTORY_PINS[name])
    return pf


def prepared(*, cloud=True):
    require(type(cloud) is bool, 'aca_metadata_cloud_flag_invalid'); raw, pf = load('preflight.json'); validate_preflight(pf)
    if cloud:
        ctx = historical_context()
        bind_context(pf, ctx)
        current_guards(ctx, arm(pf['prerequisite']), unstarted=False)
    return raw, pf


def bind_context(pf, ctx):
    require(canonical(compact_binding(ctx)) == canonical(pf['history_binding'])
        and canonical(ctx['pf']['plan']) == canonical(pf['plan'])
        and canonical(ctx['pf']['resource_guards']) == canonical(pf['resource_guards'])
        and canonical(ctx['pf']['identity']) == canonical(pf['identity'])
        and canonical(ctx['pf']['initial_roles']) == canonical(pf['initial_roles'])
        and canonical({key: ctx['pf']['prerequisite'][key] for key in ('expected_runtime', 'observed')}) == canonical(pf['prerequisite']),
        'aca_metadata_referenced_history_changed')


def validate_receipt(receipt, kind, pin):
    require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'protected_readback_verified'}
        and receipt.get('protected_readback_verified') is True and (receipt.get('sha256'), receipt.get('version_id')) == pin
        and receipt.get('blob') == 'acceptance/gateway-read-owner-task-pg-' + kind + '-' + pin[0] + '.json',
        'aca_metadata_exact_protected_receipt_required')


def start_body(pf):
    return n.start_body(pf)


def validate_effect_request(stage, path, api, method, body, pf, prior):
    require(type(stage) is str and stage == 'start' and type(path) is str and path == JOB_ID + '/start'
        and type(api) is str and api == transport.JOB_API and type(method) is str and method == 'POST',
        'aca_metadata_only_exact_start_request_allowed')
    require(type(prior) is dict and set(prior) == {'installed_ack', 'diagnosis'}, 'aca_metadata_exact_installed_prior_required')
    for key, name in (('installed_ack', 'install-ack.json'), ('diagnosis', 'definition-diagnosis.json')):
        validate_receipt(prior[key], n.KIND_PREFIX + name.removesuffix('.json'), HISTORY_PINS[name])
        require(prior[key] == pf['history_binding']['receipts'][name], 'aca_metadata_prior_reference_changed')
    require(type(body) is dict and canonical(body) == canonical(start_body(pf)), 'aca_metadata_exact_start_body_required')


def intent_value(pf_raw, pf, body, prior):
    validate_effect_request('start', JOB_ID + '/start', transport.JOB_API, 'POST', body, pf, prior)
    return dict(operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID, stage='start', job=JOB, job_id=JOB_ID,
        preflight_receipt=pf_raw['protected_receipt'], path=JOB_ID + '/start', api=transport.JOB_API, method='POST',
        body=body, body_sha256=digest(body), prior_receipts=prior)


def reservation_blob(stage, version=None):
    require(type(stage) is str and stage == 'start', 'aca_metadata_only_start_reservation_allowed')
    return storage().get_blob_client(previous.q.deploy.CONTAINER, PREFIX + 'start-reservation.json', version_id=version)


def reservation_readback(stage, receipt, value):
    require(type(stage) is str and stage == 'start', 'aca_metadata_only_start_reservation_allowed'); payload = canonical(value)
    require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'etag', 'protected_readback_verified'}
        and receipt.get('blob') == PREFIX + 'start-reservation.json' and receipt.get('sha256') == hashlib.sha256(payload).hexdigest()
        and receipt.get('protected_readback_verified') is True and type(receipt.get('version_id')) is str
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id'])
        and type(receipt.get('etag')) is str and re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag']), 'aca_metadata_exact_reservation_required')
    blob = reservation_blob(stage, receipt['version_id']); props = blob.get_blob_properties(timeout=10, retry_total=0)
    require(props.version_id == receipt['version_id'] and props.etag == receipt['etag'] and props.size == len(payload)
        and props.blob_type == 'BlockBlob' and blob.download_blob(offset=0, length=len(payload) + 1, etag=props.etag,
            match_condition=MatchConditions.IfNotModified, max_concurrency=1, timeout=10, retry_total=0).readall() == payload,
        'aca_metadata_reservation_readback_failed'); previous.q.deploy.archive_protection()


def reserve(stage, value):
    require(type(stage) is str and stage == 'start', 'aca_metadata_only_start_reservation_allowed'); payload = canonical(value)
    require(0 < len(payload) <= MAX_BYTES, 'aca_metadata_reservation_size_invalid')
    ack = reservation_blob(stage).upload_blob(payload, length=len(payload), blob_type='BlockBlob', overwrite=False,
        match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
    receipt = dict(blob=PREFIX + 'start-reservation.json', sha256=hashlib.sha256(payload).hexdigest(),
        version_id=ack.get('version_id'), etag=ack.get('etag'), protected_readback_verified=True)
    reservation_readback(stage, receipt, value); return receipt


def new_attempt_absent():
    require(not any((FOLDER / name).exists() for name in ('start-intent.json', 'start-ack.json', 'start-outcome.json',
        'replica-binding.json', 'acceptance.json', 'terminal-diagnostic.json')), 'aca_metadata_start_attempt_consumed_inspect_only')
    blob_absent(reservation_blob('start'))


def new_family_absent(receipts):
    """Only this preflight/reserved intent may precede the one START effect."""
    require(type(receipts) is list and len(receipts) <= 2, 'aca_metadata_new_family_receipts_invalid')
    expected = set()
    for receipt in receipts:
        receipt = native_copy(receipt)
        require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'protected_readback_verified'}
            and receipt.get('protected_readback_verified') is True and type(receipt.get('sha256')) is str
            and re.fullmatch('[0-9a-f]{64}', receipt['sha256']) and type(receipt.get('version_id')) is str
            and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{7}Z', receipt['version_id'])
            and receipt.get('blob') in ('acceptance/gateway-read-owner-task-pg-' + KIND_PREFIX + kind + '-' + receipt['sha256'] + '.json'
                for kind in ('preflight', 'start-intent')), 'aca_metadata_new_family_receipts_invalid')
        transport.utc(receipt['version_id']); pair = (receipt['blob'], receipt['version_id'])
        require(pair not in expected, 'aca_metadata_duplicate_new_family_receipt'); expected.add(pair)
    try:
        pages = storage().get_container_client(previous.q.deploy.CONTAINER).list_blobs(
            name_starts_with='acceptance/gateway-read-owner-task-pg-' + KIND_PREFIX, include=['versions'],
            results_per_page=4, timeout=10, retry_total=0).by_page()
        seen = set()
        try: page = next(pages)
        except StopIteration: page = ()
        for row in page:
            require(len(seen) < 2 and type(row.name) is str and type(row.version_id) is str
                and (row.name, row.version_id) in expected and (row.name, row.version_id) not in seen,
                'aca_metadata_protected_new_attempt_or_acceptance_exists')
            seen.add((row.name, row.version_id))
        require(seen == expected and (pages.continuation_token is None
            or type(pages.continuation_token) is str and pages.continuation_token == ''),
            'aca_metadata_complete_new_family_absence_unproven')
    except Exception: raise ValueError('aca_metadata_protected_new_family_absence_unproven') from None
    previous.q.deploy.archive_protection()


def pre_effect_checks(pf, pf_raw, intent_raw):
    ctx = historical_context()
    bind_context(pf, ctx)
    require(previous.sources() == pf['sources'], 'aca_metadata_source_changed_before_start')
    require(not any((FOLDER / name).exists() for name in ('start-ack.json', 'start-outcome.json', 'replica-binding.json',
        'acceptance.json', 'terminal-diagnostic.json')), 'aca_metadata_conflicting_start_or_capture_evidence_exists')
    capture_permitted(); new_family_absent([pf_raw['protected_receipt'], intent_raw['protected_receipt']])
    current_guards(ctx, arm(pf['prerequisite']), unstarted=True)


def mutate(stage, pf_raw, pf, path, api, method, body, prior):
    pf_raw, pf, body, prior = (native_copy(value) for value in (pf_raw, pf, body, prior))
    validate_effect_request(stage, path, api, method, body, pf, prior); old_absence(); capture_permitted(); new_attempt_absent()
    new_family_absent([pf_raw['protected_receipt']])
    value = intent_value(pf_raw, pf, body, prior); receipt = reserve('start', native_copy(value))
    intent = save(dict(native_copy(value), status='reserved_one_exact_cloud_effect', reservation_receipt=receipt), 'start-intent.json')
    require(previous.sources() == pf['sources'], 'aca_metadata_source_changed_after_reservation')
    reservation_readback('start', receipt, value); pre_effect_checks(native_copy(pf), native_copy(pf_raw), native_copy(intent))
    try: ack, status = arm(pf['prerequisite']).request(path, api, method=method, body=native_copy(body))
    except Exception as error:
        try:
            diagnostic_unretained = False
            try: private = error.diagnostic() if type(error) is NativeHttpError else None
            except Exception: private = None; diagnostic_unretained = True
            save(dict(status='effect_unknown_ack_unverified_inspect_only', operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
                stage='start', intent_receipt=intent['protected_receipt'], reservation_receipt=receipt, attempt_consumed=True,
                automatic_retry=False, no_effects_verified=False, private_native_http_diagnostic=private,
                native_http_diagnostic_unretained=diagnostic_unretained, sql_calls=0, model_calls=0), 'start-outcome.json')
        except Exception: raise ValueError('aca_metadata_outcome_unretained_attempt_consumed_inspect_only') from None
        raise ValueError('aca_metadata_start_unknown_attempt_consumed_inspect_only') from None
    return save(dict(status='one_exact_cloud_effect_acknowledged', operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
        stage='start', intent_receipt=intent['protected_receipt'], http_status=status, raw_ack=ack,
        attempt_consumed=True, automatic_retry=False), 'start-ack.json')


def prior(pf):
    return dict(installed_ack=pf['history_binding']['receipts']['install-ack.json'],
        diagnosis=pf['history_binding']['receipts']['definition-diagnosis.json'])


def start_chain(pf_raw, pf):
    intent_raw, intent = load('start-intent.json'); ack_raw, ack = load('start-ack.json')
    expected = intent_value(pf_raw, pf, start_body(pf), prior(pf))
    require(set(intent) == set(expected) | {'status', 'reservation_receipt'}
        and canonical({k: intent.get(k) for k in expected}) == canonical(expected)
        and intent.get('status') == 'reserved_one_exact_cloud_effect'
        and set(ack) == {'status', 'operation_id', 'trial_operation_id', 'stage', 'intent_receipt', 'http_status', 'raw_ack', 'attempt_consumed', 'automatic_retry'}
        and ack.get('status') == 'one_exact_cloud_effect_acknowledged' and ack.get('operation_id') == OPERATION_ID
        and ack.get('trial_operation_id') == TRIAL_OPERATION_ID and ack.get('stage') == 'start'
        and ack.get('intent_receipt') == intent_raw['protected_receipt'] and ack.get('attempt_consumed') is True
        and ack.get('automatic_retry') is False, 'aca_metadata_exact_start_chain_required')
    reservation_readback('start', intent['reservation_receipt'], expected)
    return ack_raw, acknowledged_execution(ack)


def start():
    pf_raw, pf = prepared(); old_absence(); capture_permitted(); new_attempt_absent()
    require(canonical(inventory(arm(pf['prerequisite']))) == canonical({'value': []}), 'aca_metadata_start_requires_empty_inventory')
    ack_raw = mutate('start', pf_raw, pf, JOB_ID + '/start', transport.JOB_API, 'POST', start_body(pf), prior(pf))
    try: capture_bound(pf, ack_raw)
    except CapturePending:
        print(json.dumps(dict(status='native_replica_capture_pending_read_only_inspection_required', start_attempt_consumed=True,
            automatic_start_retry=False)), flush=True)
    return ack_raw


def capture_control_blob(version=None):
    return storage().get_blob_client(previous.q.deploy.CONTAINER, PREFIX + 'capture-stop.json', version_id=version)


def capture_permitted():
    require(not any((FOLDER / name).exists() for name in ('capture-stop-intent.json', 'capture-diagnostic.json')),
        'aca_metadata_capture_previously_unqualified_inspect_only')
    blob_absent(capture_control_blob()); previous.q.deploy.archive_protection()


def capture_stop(ack_raw):
    value = dict(status='capture_validation_unqualified_inspect_only', operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
        start_ack_receipt=ack_raw['protected_receipt'], reason='native_capture_validation_failed', start_attempt_consumed=True,
        automatic_start_retry=False, capture_or_collection_authorized=False, sql_calls=0, model_calls=0, **{k: False for k in FALSE_FLAGS})
    payload = canonical(value)
    try:
        with (FOLDER / 'capture-stop-intent.json').open('x', encoding='utf-8') as output:
            output.write(json.dumps(value, sort_keys=True, indent=2))
        ack = capture_control_blob().upload_blob(payload, length=len(payload), blob_type='BlockBlob', overwrite=False,
            match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
        receipt = dict(blob=PREFIX + 'capture-stop.json', sha256=hashlib.sha256(payload).hexdigest(),
            version_id=ack.get('version_id'), etag=ack.get('etag'), protected_readback_verified=True)
        require(type(receipt['version_id']) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id'])
            and type(receipt['etag']) is str and re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag']), 'aca_metadata_capture_stop_ack_invalid')
        blob = capture_control_blob(receipt['version_id']); props = blob.get_blob_properties(timeout=10, retry_total=0)
        require(props.version_id == receipt['version_id'] and props.etag == receipt['etag'] and props.size == len(payload)
            and props.blob_type == 'BlockBlob' and blob.download_blob(offset=0, length=len(payload) + 1, etag=props.etag,
                match_condition=MatchConditions.IfNotModified, max_concurrency=1, timeout=10, retry_total=0).readall() == payload,
            'aca_metadata_capture_stop_readback_failed'); previous.q.deploy.archive_protection()
        return save(dict(value, capture_stop_receipt=receipt), 'capture-diagnostic.json')
    except Exception: raise ValueError('aca_metadata_capture_stop_outcome_unverified_inspect_only') from None


def capture_bound(pf, ack_raw):
    capture_permitted()
    try: return capture_readiness_bound(pf, ack_raw)
    except CapturePending: raise
    except Exception:
        capture_stop(ack_raw)
        raise ValueError('aca_metadata_capture_validation_unqualified_inspect_only') from None


def capture_readiness_bound(pf, ack_raw):
    require(previous.sources() == pf['sources'], 'aca_metadata_source_drift_before_capture')
    name = acknowledged_execution(ack_raw); client = arm(pf['prerequisite']); deadline = time.monotonic() + 90
    def capture_get(path, api):
        if time.monotonic() >= deadline: raise CapturePending()
        try: result = get(client, path, api)
        except NativeHttpError as error:
            if n.capture_not_found(error): raise CapturePending() from None
            raise
        if time.monotonic() >= deadline: raise CapturePending()
        return result
    for attempt in range(45):
        try:
            before = capture_get(JOB_ID + '/executions/' + name, transport.JOB_API)
            if type(before) is dict and type(before.get('properties')) is dict:
                require(before['properties'].get('status') not in ('Succeeded', 'Failed', 'Stopped'), 'aca_metadata_running_window_missed_inspect_only')
            validate_execution(before, pf, name, terminal=False)
            if before['properties']['status'] == 'Processing': raise CapturePending()
            replicas = capture_get(JOB_ID + '/executions/' + name + '/replicas', transport.REPLICA_API)
            require(type(replicas) is dict and set(replicas) <= {'value', 'nextLink'} and replicas.get('nextLink') in (None, '')
                and type(replicas.get('value')) is list, 'aca_metadata_complete_replica_inventory_required')
            if not replicas['value']: raise CapturePending()
            binding = validate_replica(replicas, name)
            after = capture_get(JOB_ID + '/executions/' + name, transport.JOB_API)
            require(canonical(before) == canonical(after), 'aca_metadata_execution_drift_during_capture')
            break
        except CapturePending:
            if attempt == 44 or time.monotonic() >= deadline: raise CapturePending() from None
            time.sleep(1)
    require(previous.sources() == pf['sources'], 'aca_metadata_source_drift_after_capture')
    return save(dict(status='native_running_replica_binding_observed', operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
        start_ack_receipt=ack_raw['protected_receipt'], raw_execution=before, raw_replicas=replicas, binding=binding,
        checked_at_utc=datetime.now(timezone.utc).isoformat(), sql_calls=0, model_calls=0, **{k: False for k in FALSE_FLAGS}), 'replica-binding.json')


def capture():
    capture_permitted(); pf_raw, pf = prepared(cloud=False); ack_raw, _ = start_chain(pf_raw, pf)
    raw, ack = load('start-ack.json'); require(raw['protected_receipt'] == ack_raw['protected_receipt'], 'aca_metadata_capture_ack_changed')
    return capture_bound(pf, dict(ack, protected_receipt=raw['protected_receipt']))


def collect(verify_only=False):
    require(type(verify_only) is bool, 'aca_metadata_verify_flag_invalid'); capture_permitted()
    pf_raw, pf = prepared(cloud=False); client = arm(pf['prerequisite']); ctx = historical_context()
    bind_context(pf, ctx); live = current_guards(ctx, client, unstarted=False)
    ack_raw, name = start_chain(pf_raw, pf); capture_raw, captured = load('replica-binding.json')
    require(captured.get('status') == 'native_running_replica_binding_observed' and captured.get('operation_id') == OPERATION_ID
        and captured.get('trial_operation_id') == TRIAL_OPERATION_ID and captured.get('start_ack_receipt') == ack_raw['protected_receipt'],
        'aca_metadata_capture_chain_invalid')
    validate_execution(captured['raw_execution'], pf, name, terminal=False); replica = validate_replica(captured['raw_replicas'], name)
    require(canonical(replica) == canonical(captured['binding']), 'aca_metadata_replica_mapping_changed')
    before = get(client, JOB_ID + '/executions/' + name, transport.JOB_API); props = validate_execution(before, pf, name, terminal=True)
    require(props['startTime'] == captured['raw_execution']['properties']['startTime'], 'aca_metadata_execution_start_changed')
    scope = console_scope(name, (transport.utc(props['startTime']) - timedelta(minutes=1)).isoformat(),
        (transport.utc(props['endTime']) + timedelta(minutes=10)).isoformat())
    console = collect_console(LogsTransport(AzureCliCredential()).call, scope)
    after = get(client, JOB_ID + '/executions/' + name, transport.JOB_API); inv = inventory(client)
    require(canonical(before) == canonical(after) and len(inv['value']) == 1 and inv['value'][0].get('name') == name
        and inv['value'][0].get('id') == JOB_ID + '/executions/' + name, 'aca_metadata_execution_family_drift')
    require(canonical(job_observation(client, pf, live['identity'])) == canonical(live['job'])
        and canonical(roles(client, live['identity']['properties']['principalId'], granted=True)) == canonical(live['roles'])
        and canonical(n.resource_guards()) == canonical(pf['resource_guards']) and previous.sources() == pf['sources'],
        'aca_metadata_terminal_guards_changed')
    rows, observed = console['rows'], None
    if props['status'] == 'Succeeded':
        require(rows, 'aca_metadata_logs_pending_read_only_recollect_no_start')
        try: observed = parse_console(rows, replica, pf['prerequisite']['observed'])
        except ValueError: pass
    qualified = observed is not None
    value = dict(status='secretless_native_metadata_aca_runtime_observed' if qualified else 'terminal_native_metadata_aca_runtime_unqualified',
        operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID, preflight_receipt=pf_raw['protected_receipt'],
        installed_ack_receipt=pf['history_binding']['receipts']['install-ack.json'],
        definition_diagnosis_receipt=pf['history_binding']['receipts']['definition-diagnosis.json'],
        start_ack_receipt=ack_raw['protected_receipt'], capture_receipt=capture_raw['protected_receipt'],
        raw_execution_before=before, raw_execution_after=after, complete_execution_inventory=inv,
        complete_console_events=rows, complete_console_observation=console, complete_console_sha256=digest(rows), observed=observed,
        current_job=live['job'], current_roles=live['roles'], metadata_relation=metadata_relation(), requested_image_digest_verified=True,
        image_runtime_metadata_verified=qualified, registry_pull_success_observed=qualified,
        configured_container_identity_lifecycle_none_verified=True, configured_database_model_keyvault_secrets_absent=True,
        attempt_consumed=True, automatic_retry=False, acceptance_recorded=qualified, sql_calls=0, model_calls=0,
        daily_schedule_hkt='18:00', **{k: False for k in FALSE_FLAGS})
    filename = 'acceptance.json' if qualified else 'terminal-diagnostic.json'
    if verify_only:
        raw, retained = load(filename); require(canonical(retained) == canonical(value), 'aca_metadata_fresh_and_retained_evidence_differs')
        return dict(status='independent_secretless_native_metadata_runtime_verified' if qualified else 'independent_native_metadata_diagnostic_verified',
            protected_receipt=raw['protected_receipt'])
    return save(value, filename)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage', choices=COMMANDS); stage = parser.parse_args().stage
    validate_name(JOB); previous.q.canary.host.checked_owner()
    if stage != 'verify': FOLDER.mkdir(parents=True, exist_ok=True)
    actions = {'prepare': prepare, 'start': start, 'capture': capture, 'collect': collect, 'verify': lambda: collect(True)}
    result = actions[stage](); print(json.dumps({k: result[k] for k in ('status', 'protected_receipt')}, sort_keys=True), flush=True)


if __name__ == '__main__': main()
