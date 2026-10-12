"""Separately reserved continuation of the consumed, acknowledged ACA identity.

All 725 original sources remain frozen. Only this controller and its tests are
added. Raw native metadata is retained unchanged; copied validation metadata
may normalize exactly one observed structural spelling of the registered MI.
There is no identity-create command and no call to the old dispatcher. The new
operation owns grant/install/start reservations, while the exact trial job body
and image remain frozen. Import is inert; no SQL, secrets or model calls occur.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient, StorageErrorCode

import qualify_owner_task_aca_runtime as frozen

previous = frozen.previous
contract = frozen.contract
transport = frozen.transport
ROOT = frozen.ROOT
FOLDER = ROOT / '.work/owner-task-aca-runtime-continuation-20261004'
OPERATION_ID = '2e9a28e0-935a-4121-87fe-bceed97ad556'
TRIAL_OPERATION_ID = contract.OPERATION_ID
PREFIX = 'probes/owner-codex-aca-runtime-continuation-20261004/'
ADDITIONS = frozenset(('scripts/qualify_owner_task_aca_runtime_continuation.py',
    'tests/test_owner_task_aca_runtime_continuation.py'))
STAGES = ('grant', 'install', 'start')
COMMANDS = ('prepare', 'grant', 'grant-verify', 'install', 'install-verify', 'start', 'capture', 'collect', 'verify')
FALSE_FLAGS = frozen.FALSE_FLAGS
MAX_BYTES = frozen.MAX_BYTES
require = frozen.require
canonical = frozen.canonical
strict_json = frozen.strict_json
digest = frozen.digest
source_bytes = frozen.source_bytes
resource_guards = frozen.resource_guards
arm = frozen.arm
get = frozen.get
environment = frozen.environment
roles = frozen.roles
inventory = frozen.inventory
start_body = frozen.start_body
validate_execution = frozen.validate_execution
acknowledged_execution = frozen.acknowledged_execution
parse_console = frozen.parse_console

MI_ALIAS = contract.IDENTITY_ID.replace('/resourceGroups/', '/resourcegroups/')
MI_IDS = frozenset((contract.IDENTITY_ID, MI_ALIAS))
ORIGINAL_PINS = {
    'preflight.json': ('74ec1f9e19244473dc13a3e8e766462c2ff5c22dc361e88bc004b4a51d7de9b9', '2026-10-04T19:04:32.2219643Z'),
    'identity-intent.json': ('9bb0ebd0ce764d54c115bf040d18e52e8332ba25d23b962e23e6771160c609d2', '2026-10-04T19:09:04.5826655Z'),
    'identity-ack.json': ('9a243b2f79205589301c7ac3486d1a3fec6af98357de9cabdb4eb29974c512e7', '2026-10-04T19:10:03.3770156Z')}
ORIGINAL_RESERVATION = dict(blob='probes/owner-codex-aca-runtime-20261004/identity-reservation.json',
    sha256='604eb7c26a2b6c469afb959c050c6b9823f4d0bac8a3a8732d3125bbc68a3601',
    version_id='2026-10-04T19:08:40.1970296Z', etag='"0x8DF224AE69E7C78"', protected_readback_verified=True)


def native_copy(value):
    # The frozen runtime-log decoder is bounded to 64 KiB; complete preflights
    # are larger. Use the frozen native codec before serialization, then this
    # controller's evidence bound, without any caller-defined copy/JSON hooks.
    payload = previous.q.canary.codec.canonical(value)
    require(0 < len(payload) <= MAX_BYTES, 'aca_continuation_native_metadata_size_invalid')
    return json.loads(payload.decode('utf-8'))


def normalized_mi_id(value):
    """Only the two registered spellings; never generic case folding."""
    require(type(value) is str and value in MI_IDS, 'aca_continuation_registered_mi_id_required')
    return contract.IDENTITY_ID


def validate_identity_metadata(value):
    """Validate an adapted copy; return native evidence without normalization."""
    raw = native_copy(value)
    require(type(raw) is dict, 'aca_continuation_native_identity_required')
    copied = native_copy(raw)
    copied['id'] = normalized_mi_id(copied.get('id'))
    transport.validate_identity_metadata(copied)
    return raw


def normalized_job(value):
    """Copy and adapt only registered MI references; all other fields stay exact."""
    copied = native_copy(value)
    require(type(copied) is dict and type(copied.get('identity')) is dict,
        'aca_continuation_native_job_identity_required')
    assigned = copied['identity'].get('userAssignedIdentities')
    require(type(assigned) is dict and len(assigned) == 1, 'aca_continuation_single_mi_reference_required')
    key = next(iter(assigned))
    copied['identity']['userAssignedIdentities'] = {normalized_mi_id(key): assigned[key]}
    properties = copied.get('properties')
    require(type(properties) is dict and type(properties.get('configuration')) is dict,
        'aca_continuation_native_job_configuration_required')
    configuration = properties['configuration']
    for field in ('registries', 'identitySettings'):
        rows = configuration.get(field)
        require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict,
            'aca_continuation_single_mi_reference_required')
        rows[0]['identity'] = normalized_mi_id(rows[0].get('identity'))
    return copied


def validate_job_metadata(value, expected_runtime, principal_id, client_id):
    raw = native_copy(value)
    contract.validate_job(normalized_job(raw), source_bytes(), expected_runtime,
        principal_id=principal_id, client_id=client_id)
    return raw


def validate_sources(old, current):
    require(type(old) is dict and len(old) == 725 and type(current) is dict
        and set(current) == set(old) | ADDITIONS and not set(old) & ADDITIONS
        and all(type(name) is str and type(sha) is str and re.fullmatch('[0-9a-f]{64}', sha)
            for name, sha in current.items()) and all(current[name] == sha for name, sha in old.items()),
        'aca_continuation_only_two_additive_sources_allowed')


def original_reservation_value(pf_raw):
    return frozen.intent_value('identity', pf_raw, contract.IDENTITY_ID, transport.MI_API,
        'PUT', transport.identity_body(), {})


def validate_original_identity_chain(pf_raw, intent_raw, ack_raw):
    """Pure exact history/pin/intent relation check; no readback or live authority."""
    values = {}
    for name, raw in (('preflight.json', pf_raw), ('identity-intent.json', intent_raw), ('identity-ack.json', ack_raw)):
        values[name] = previous.q.readback.receipt_binding(raw, 'aca-runtime-' + name.removesuffix('.json'), ORIGINAL_PINS[name])
    pf, intent, ack = (values[name] for name in ('preflight.json', 'identity-intent.json', 'identity-ack.json'))
    require(pf.get('status') == 'prepared_secretless_aca_runtime' and pf.get('operation_id') == TRIAL_OPERATION_ID
        and type(pf.get('sources')) is dict and len(pf['sources']) == 725
        and all(pf.get(key) is False for key in FALSE_FLAGS), 'aca_continuation_original_preflight_invalid')
    expected = original_reservation_value(pf_raw)
    require(set(intent) == set(expected) | {'status', 'reservation_receipt'}
        and canonical({key: intent.get(key) for key in expected}) == canonical(expected)
        and intent.get('status') == 'reserved_one_exact_cloud_effect'
        and canonical(intent.get('reservation_receipt')) == canonical(ORIGINAL_RESERVATION),
        'aca_continuation_original_identity_intent_invalid')
    require(type(ack) is dict and set(ack) == {'status', 'operation_id', 'stage', 'intent_receipt', 'http_status',
        'raw_ack', 'attempt_consumed', 'automatic_retry'} and ack.get('status') == 'one_exact_cloud_effect_acknowledged'
        and ack.get('operation_id') == TRIAL_OPERATION_ID and ack.get('stage') == 'identity'
        and ack.get('intent_receipt') == intent_raw['protected_receipt']
        and type(ack.get('http_status')) is int and ack['http_status'] == 201
        and ack.get('attempt_consumed') is True and ack.get('automatic_retry') is False,
        'aca_continuation_original_identity_ack_invalid')
    native = validate_identity_metadata(ack['raw_ack'])
    previous.q.canary.validate_correction_checks(pf['local_checks'])
    require(canonical(frozen.plan(pf['prerequisite'])) == canonical(pf['plan']),
        'aca_continuation_original_plan_changed')
    return dict(receipts={name: raw['protected_receipt'] for name, raw in
        (('preflight.json', pf_raw), ('identity-intent.json', intent_raw), ('identity-ack.json', ack_raw))},
        sources=pf['sources'], resource_guards=pf['resource_guards'], prerequisite=pf['prerequisite'],
        plan=pf['plan'], reservation_receipt=intent['reservation_receipt'], identity=native)


def history():
    records = {name: previous.q.readback.read_local(frozen.FOLDER, name) for name in ORIGINAL_PINS}
    relation = validate_original_identity_chain(records['preflight.json'], records['identity-intent.json'], records['identity-ack.json'])
    for name, raw in records.items():
        previous.q.deploy.protected(raw, 'aca-runtime-' + name.removesuffix('.json'))
    frozen.reservation_readback('identity', relation['reservation_receipt'], original_reservation_value(records['preflight.json']))
    require(not any((frozen.FOLDER / (stage + '-' + suffix + '.json')).exists()
        for stage in STAGES for suffix in ('intent', 'ack', 'outcome')),
        'aca_continuation_original_downstream_attempt_exists')
    for stage in STAGES:
        original_reservation_absent(stage)
    return relation


def validate_original_reservation_absence(status, code):
    """Only the native exact BlobNotFound 404 proves old reservation absence."""
    if type(code) is StorageErrorCode and code is StorageErrorCode.blob_not_found:
        code = code.value
    require(type(status) is int and status == 404 and type(code) is str and code == 'BlobNotFound',
        'aca_continuation_original_reservation_absence_unproven')


def original_reservation_absent(stage):
    require(stage in STAGES, 'aca_continuation_reservation_stage_invalid')
    try:
        frozen.reservation_blob(stage).get_blob_properties(timeout=10, retry_total=0)
    except HttpResponseError as error:
        try:
            validate_original_reservation_absence(error.status_code, getattr(error, 'error_code', None))
        except ValueError:
            raise ValueError('aca_continuation_original_reservation_absence_unproven') from None
        previous.q.deploy.archive_protection()
        return
    raise ValueError('aca_continuation_original_downstream_reservation_exists')


def prerequisite(original):
    """Reconstruct the old ACR chain explicitly; never retarget its frozen validator."""
    raws = {}; values = {}
    for name, pin in frozen.PINS.items():
        raw = previous.q.readback.read_local(previous.FOLDER, name)
        previous.q.readback.receipt_binding(raw, 'standard-pull-' + name.removesuffix('.json'), pin)
        raws[name] = raw; values[name] = previous.q.deploy.protected(raw, 'standard-pull-' + name.removesuffix('.json'))
    pf = values['preflight.json']; accepted = values['acceptance.json']
    frozen.validate_source_extension(pf['guards']['sources'], original['sources'])
    validate_sources(original['sources'], previous.sources())
    previous.q.canary.validate_correction_checks(pf['local_checks'])
    require(pf.get('status') == 'prepared_standard_pull_runtime_observation'
        and pf.get('operation_id') == previous.OPERATION_ID and canonical(pf.get('plan')) == canonical(previous.probe_plan())
        and accepted.get('status') == 'secretless_default_user_runtime_observed'
        and accepted.get('operation_id') == previous.OPERATION_ID
        and accepted.get('preflight_receipt') == raws['preflight.json']['protected_receipt']
        and accepted.get('intent_receipt') == raws['dispatch-intent.json']['protected_receipt']
        and accepted.get('ack_receipt') == raws['dispatch-ack.json']['protected_receipt']
        and accepted.get('reservation_receipt') == values['dispatch-intent.json']['reservation_receipt']
        and accepted.get('acceptance_recorded') is True and accepted.get('engine_image_digest_observed') is True
        and accepted.get('candidate_runtime_metadata_verified') is True
        and all(accepted.get(key) is False for key in previous.FALSE_FLAGS), 'aca_continuation_acr_prerequisite_invalid')
    name = previous.chain(raws['preflight.json'], pf, raws['dispatch-intent.json'], values['dispatch-intent.json'], values['dispatch-ack.json'])
    require(name == 'ck84', 'aca_continuation_exact_accepted_acr_run_required')
    before = previous.show(name); logs = previous.call_cli('logs', name); after = previous.show(name)
    observed = previous.parse_logs(logs, pf['plan'])
    require(before == after == accepted['raw_run_before'] == accepted['raw_run_after']
        and before['status'] == 'Succeeded' and logs.decode('utf-8') == accepted['full_logs']
        and len(logs) == accepted['full_logs_bytes'] and hashlib.sha256(logs).hexdigest() == accepted['full_logs_sha256']
        and canonical(observed) == canonical(accepted['observed']), 'aca_continuation_fresh_acr_evidence_changed')
    result = dict(receipts={name: raw['protected_receipt'] for name, raw in raws.items()},
        frozen_sources=pf['guards']['sources'], frozen_resources={key: val for key, val in pf['guards'].items() if key != 'sources'},
        expected_runtime=observed['runtime'], observed=observed)
    require(canonical(result) == canonical(original['prerequisite']), 'aca_continuation_original_acr_dependency_changed')
    return result


def save(value, name):
    """Exclusive local/private evidence creation, without changing old globals."""
    require(re.fullmatch(r'[a-z][a-z-]*\.json', name) is not None and 0 < len(canonical(value)) <= MAX_BYTES,
        'aca_continuation_evidence_name_or_size_invalid')
    kind = 'aca-runtime-continuation-' + name.removesuffix('.json')
    with (FOLDER / name).open('x', encoding='utf-8') as output:
        previous.q.deploy.archive_protection()
        raw = dict(value, protected_receipt=previous.q.deploy.retain(value, 'owner-task-pg-' + kind))
        previous.q.deploy.archive_protection()
        output.write(json.dumps(raw, sort_keys=True, indent=2))
    previous.q.deploy.protected(raw, kind)
    return raw


def load(name):
    raw = previous.q.readback.read_local(FOLDER, name)
    return raw, previous.q.deploy.protected(raw, 'aca-runtime-continuation-' + name.removesuffix('.json'))


def reservation_blob(stage, version=None):
    require(stage in STAGES, 'aca_continuation_reservation_stage_invalid')
    previous.q.deploy.archive_protection()
    service = BlobServiceClient('https://' + previous.q.deploy.ACCOUNT + '.blob.core.windows.net', credential=AzureCliCredential(),
        retry_total=0, retry_to_secondary=False, connection_timeout=5, read_timeout=10, permit_redirects=False, redirect_max=0)
    return service.get_blob_client(previous.q.deploy.CONTAINER, PREFIX + stage + '-reservation.json', version_id=version)


def reservation_readback(stage, receipt, value):
    require(stage in STAGES, 'aca_continuation_reservation_stage_invalid')
    payload = canonical(value)
    require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'etag', 'protected_readback_verified'}
        and receipt.get('blob') == PREFIX + stage + '-reservation.json'
        and receipt.get('sha256') == hashlib.sha256(payload).hexdigest() and receipt.get('protected_readback_verified') is True
        and type(receipt.get('version_id')) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id'])
        and type(receipt.get('etag')) is str and re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag'])
        and 0 < len(payload) <= MAX_BYTES, 'aca_continuation_reservation_binding_invalid')
    blob = reservation_blob(stage, receipt['version_id']); props = blob.get_blob_properties(timeout=10, retry_total=0)
    require(props.version_id == receipt['version_id'] and props.etag == receipt['etag'] and props.size == len(payload)
        and props.blob_type == 'BlockBlob' and blob.download_blob(offset=0, length=len(payload) + 1, etag=props.etag,
            match_condition=MatchConditions.IfNotModified, max_concurrency=1, timeout=10, retry_total=0).readall() == payload,
        'aca_continuation_reservation_readback_failed')
    previous.q.deploy.archive_protection()


def reserve(stage, value):
    require(stage in STAGES, 'aca_continuation_reservation_stage_invalid')
    payload = canonical(value)
    require(0 < len(payload) <= MAX_BYTES, 'aca_continuation_reservation_size_invalid')
    ack = reservation_blob(stage).upload_blob(payload, length=len(payload), blob_type='BlockBlob', overwrite=False,
        match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
    receipt = dict(blob=PREFIX + stage + '-reservation.json', sha256=hashlib.sha256(payload).hexdigest(),
        version_id=ack.get('version_id'), etag=ack.get('etag'), protected_readback_verified=True)
    reservation_readback(stage, receipt, value)
    return receipt


def identity_observation(client):
    return validate_identity_metadata(get(client, contract.IDENTITY_ID, transport.MI_API))


def identity_binding(pf, client):
    native = identity_observation(client)
    require(canonical(native) == canonical(pf['history']['identity']), 'aca_continuation_native_identity_ack_changed')
    return pf['history']['receipts']['identity-ack.json'], native


def plan(dependency):
    return frozen.plan(dependency)


def prepare():
    require(not (FOLDER / 'preflight.json').exists(), 'aca_continuation_preflight_already_reserved')
    before = previous.sources(); original = history(); validate_sources(original['sources'], before)
    dep = prerequisite(original); checks = previous.q.qa.run_local_checks()
    previous.q.canary.validate_correction_checks(checks)
    current = resource_guards(); client = arm(dep)
    identity = identity_observation(client)
    require(canonical(identity) == canonical(original['identity']), 'aca_continuation_native_identity_ack_changed')
    permission = roles(client, identity['properties']['principalId'], granted=False)
    require(get(client, transport.ASSIGNMENT_ID, transport.ROLE_API, missing=True) is None
        and get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None,
        'aca_continuation_grant_and_job_must_be_absent')
    env = environment(client)
    require(canonical(current) == canonical(original['resource_guards']) == canonical(dep['frozen_resources'])
        and previous.sources() == before, 'aca_continuation_resource_or_source_changed_during_qa')
    return save(dict(status='prepared_secretless_aca_runtime_continuation', operation_id=OPERATION_ID,
        trial_operation_id=TRIAL_OPERATION_ID, checked_at_utc=datetime.now(timezone.utc).isoformat(),
        sources=before, history=original, prerequisite=dep, resource_guards=current, plan=plan(dep), environment=env,
        identity=identity, initial_roles=permission, local_checks=checks, sql_calls=0, model_calls=0,
        daily_schedule_hkt='18:00', **{key: False for key in FALSE_FLAGS}), 'preflight.json')


def prepared(*, cloud=True):
    require(type(cloud) is bool, 'aca_continuation_cloud_flag_invalid')
    raw, value = load('preflight.json')
    require(value.get('status') == 'prepared_secretless_aca_runtime_continuation'
        and value.get('operation_id') == OPERATION_ID and value.get('trial_operation_id') == TRIAL_OPERATION_ID
        and all(value.get(key) is False for key in FALSE_FLAGS), 'aca_continuation_preflight_binding_invalid')
    previous.q.canary.validate_correction_checks(value['local_checks'])
    validate_sources(value['history']['sources'], previous.sources())
    require(previous.sources() == value['sources'] and canonical(plan(value['prerequisite'])) == canonical(value['plan'])
        and canonical(value['identity']) == canonical(value['history']['identity']), 'aca_continuation_source_or_plan_drift')
    if cloud:
        require(canonical(history()) == canonical(value['history'])
            and canonical(prerequisite(value['history'])) == canonical(value['prerequisite'])
            and canonical(resource_guards()) == canonical(value['resource_guards']), 'aca_continuation_cloud_prerequisite_drift')
        identity_binding(value, arm(value['prerequisite']))
    return raw, value


def validate_effect_route(stage, path, api, method):
    require(stage in STAGES, 'aca_continuation_mutation_stage_invalid')
    wanted = {'grant': (transport.ASSIGNMENT_ID, transport.ROLE_API, 'PUT'),
        'install': (contract.JOB_ID, transport.JOB_API, 'PUT'),
        'start': (contract.JOB_ID + '/start', transport.JOB_API, 'POST')}[stage]
    require((path, api, method) == wanted and all(type(value) is str for value in (path, api, method)),
        'aca_continuation_exact_stage_request_required')


def validate_prior(stage, prior):
    require(stage in STAGES and type(prior) is dict, 'aca_continuation_mutation_stage_invalid')
    key = {'grant': 'identity', 'install': 'grant', 'start': 'install'}[stage]
    require(set(prior) == {key}, 'aca_continuation_exact_stage_prior_required')
    receipt = prior[key]
    kind = {'grant': 'aca-runtime-identity-ack', 'install': 'aca-runtime-continuation-grant-acceptance',
        'start': 'aca-runtime-continuation-install-acceptance'}[stage]
    require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'protected_readback_verified'}
        and receipt.get('protected_readback_verified') is True and type(receipt.get('sha256')) is str
        and re.fullmatch('[0-9a-f]{64}', receipt['sha256']) is not None
        and receipt.get('blob') == 'acceptance/gateway-read-owner-task-pg-' + kind + '-' + receipt['sha256'] + '.json'
        and type(receipt.get('version_id')) is str and re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id']) is not None,
        'aca_continuation_exact_prior_receipt_required')
    transport.utc(receipt['version_id'])


def validate_effect_request(stage, path, api, method, body, pf, prior):
    """A reused transport must never broaden this controller's effect authority."""
    validate_effect_route(stage, path, api, method)
    validate_prior(stage, prior)
    require(type(pf) is dict and type(body) is dict, 'aca_continuation_native_effect_required')
    if stage == 'grant':
        wanted = transport.assignment_body(pf['history']['identity']['properties']['principalId'])
        require(canonical(prior['identity']) == canonical(pf['history']['receipts']['identity-ack.json']),
            'aca_continuation_original_identity_prior_required')
    elif stage == 'install':
        wanted = pf['plan']['definition']
    else:
        wanted = start_body(pf)
    require(canonical(body) == canonical(wanted), 'aca_continuation_exact_stage_body_required')


def intent_value(stage, pf_raw, path, api, method, body, prior):
    validate_effect_route(stage, path, api, method)
    validate_prior(stage, prior)
    return dict(operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID, stage=stage,
        preflight_receipt=pf_raw['protected_receipt'], path=path, api=api, method=method,
        body=body, body_sha256=digest(body), prior_receipts=prior)


def pre_effect_checks(stage, client, pf, body):
    require(stage in STAGES, 'aca_continuation_mutation_stage_invalid')
    _, identity = identity_binding(pf, client); principal = identity['properties']['principalId']
    roles(client, principal, granted=stage != 'grant')
    if stage == 'grant':
        require(canonical(body) == canonical(transport.assignment_body(principal))
            and get(client, transport.ASSIGNMENT_ID, transport.ROLE_API, missing=True) is None,
            'aca_continuation_grant_appeared_or_principal_changed_after_reservation')
    if stage in ('grant', 'install'):
        require(get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None,
            'aca_continuation_job_appeared_after_reservation')
    else:
        job_observation(client, pf, identity)
        require(not inventory(client)['value'], 'aca_continuation_execution_appeared_after_reservation')


def mutate(stage, pf_raw, pf, path, api, method, body, prior):
    pf_raw, pf, body, prior = (native_copy(value) for value in (pf_raw, pf, body, prior))
    validate_effect_request(stage, path, api, method, body, pf, prior)
    require(stage in STAGES and not any((FOLDER / (stage + '-' + suffix + '.json')).exists()
        for suffix in ('intent', 'ack', 'outcome')), 'aca_continuation_effect_consumed_inspect_only')
    value = intent_value(stage, pf_raw, path, api, method, body, prior)
    receipt = reserve(stage, native_copy(value))
    intent = save(dict(native_copy(value), status='reserved_one_exact_cloud_effect', reservation_receipt=receipt), stage + '-intent.json')
    require(previous.sources() == pf['sources'], 'aca_continuation_source_changed_after_reservation')
    reservation_readback(stage, receipt, native_copy(value))
    pre_effect_checks(stage, arm(pf['prerequisite']), native_copy(pf), native_copy(body))
    try:
        ack, status = arm(pf['prerequisite']).request(path, api, method=method, body=native_copy(body))
    except Exception:
        try:
            save(dict(status='effect_unknown_ack_unverified_inspect_only', operation_id=OPERATION_ID,
                trial_operation_id=TRIAL_OPERATION_ID, stage=stage, intent_receipt=intent['protected_receipt'],
                reservation_receipt=receipt, attempt_consumed=True, automatic_retry=False, no_effects_verified=False,
                sql_calls=0, model_calls=0), stage + '-outcome.json')
        except Exception:
            raise ValueError('aca_continuation_effect_outcome_unretained_attempt_consumed_inspect_only') from None
        raise ValueError('aca_continuation_effect_unknown_attempt_consumed_inspect_only') from None
    return save(dict(status='one_exact_cloud_effect_acknowledged', operation_id=OPERATION_ID,
        trial_operation_id=TRIAL_OPERATION_ID, stage=stage, intent_receipt=intent['protected_receipt'],
        http_status=status, raw_ack=ack, attempt_consumed=True, automatic_retry=False), stage + '-ack.json')


def chain(stage, pf_raw, path, api, method, body, prior):
    intent_raw, intent = load(stage + '-intent.json'); ack_raw, ack = load(stage + '-ack.json')
    expected = intent_value(stage, pf_raw, path, api, method, body, prior)
    require(set(intent) == set(expected) | {'status', 'reservation_receipt'}
        and canonical({key: intent.get(key) for key in expected}) == canonical(expected)
        and intent.get('status') == 'reserved_one_exact_cloud_effect'
        and ack.get('status') == 'one_exact_cloud_effect_acknowledged' and ack.get('operation_id') == OPERATION_ID
        and ack.get('trial_operation_id') == TRIAL_OPERATION_ID and ack.get('stage') == stage
        and ack.get('intent_receipt') == intent_raw['protected_receipt'] and ack.get('attempt_consumed') is True
        and ack.get('automatic_retry') is False and type(ack.get('http_status')) is int
        and ack['http_status'] in (200, 201, 202), 'aca_continuation_effect_chain_invalid')
    reservation_readback(stage, intent['reservation_receipt'], expected)
    return ack_raw, ack


def grant_chain(pf_raw, pf, client):
    identity_receipt, identity = identity_binding(pf, client); principal = identity['properties']['principalId']
    prior = {'identity': identity_receipt}
    ack_raw, ack = chain('grant', pf_raw, transport.ASSIGNMENT_ID, transport.ROLE_API, 'PUT', transport.assignment_body(principal), prior)
    transport.validate_assignment(ack['raw_ack'], principal)
    native = get(client, transport.ASSIGNMENT_ID, transport.ROLE_API); transport.validate_assignment(native, principal)
    permission = roles(client, principal, granted=True)
    raw, value = load('grant-acceptance.json')
    require(value.get('status') == 'exact_registry_pull_role_observed' and value.get('operation_id') == OPERATION_ID
        and value.get('trial_operation_id') == TRIAL_OPERATION_ID and value.get('identity_receipt') == identity_receipt
        and canonical(value.get('assignment')) == canonical(native) and canonical(value.get('roles')) == canonical(permission)
        and value.get('ack_receipt') == ack_raw['protected_receipt'], 'aca_continuation_registry_pull_role_changed')
    return raw, identity, permission


def job_observation(client, pf, identity):
    return validate_job_metadata(get(client, contract.JOB_ID, transport.JOB_API), pf['prerequisite']['expected_runtime'],
        identity['properties']['principalId'], identity['properties']['clientId'])


def install_chain(pf_raw, pf, client):
    grant_raw, identity, permission = grant_chain(pf_raw, pf, client)
    ack_raw, ack = chain('install', pf_raw, contract.JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'],
        {'grant': grant_raw['protected_receipt']})
    require(type(ack.get('raw_ack')) is dict and ack['raw_ack'].get('id') == contract.JOB_ID
        and ack['raw_ack'].get('name') == contract.JOB, 'aca_continuation_job_ack_identity_invalid')
    job = job_observation(client, pf, identity); raw, value = load('install-acceptance.json')
    require(value.get('status') == 'secretless_job_with_container_identity_disabled_observed'
        and value.get('operation_id') == OPERATION_ID and value.get('trial_operation_id') == TRIAL_OPERATION_ID
        and value.get('ack_receipt') == ack_raw['protected_receipt'] and value.get('grant_receipt') == grant_raw['protected_receipt']
        and canonical(value.get('job')) == canonical(job) and value.get('environment') == environment(client),
        'aca_continuation_installed_job_drift')
    return raw, job, identity, permission


def grant_create():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); receipt, identity = identity_binding(pf, client)
    principal = identity['properties']['principalId']; roles(client, principal, granted=False)
    require(get(client, transport.ASSIGNMENT_ID, transport.ROLE_API, missing=True) is None
        and get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None,
        'aca_continuation_grant_and_job_create_only_absence_required')
    return mutate('grant', pf_raw, pf, transport.ASSIGNMENT_ID, transport.ROLE_API, 'PUT',
        transport.assignment_body(principal), {'identity': receipt})


def grant_verify():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); receipt, identity = identity_binding(pf, client)
    principal = identity['properties']['principalId']
    ack_raw, ack = chain('grant', pf_raw, transport.ASSIGNMENT_ID, transport.ROLE_API, 'PUT',
        transport.assignment_body(principal), {'identity': receipt})
    transport.validate_assignment(ack['raw_ack'], principal)
    native = get(client, transport.ASSIGNMENT_ID, transport.ROLE_API); transport.validate_assignment(native, principal)
    permission = roles(client, principal, granted=True)
    return save(dict(status='exact_registry_pull_role_observed', operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
        identity_receipt=receipt, ack_receipt=ack_raw['protected_receipt'], assignment=native, roles=permission,
        sql_calls=0, model_calls=0, **{key: False for key in FALSE_FLAGS}), 'grant-acceptance.json')


def install():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); grant_raw, _, _ = grant_chain(pf_raw, pf, client)
    require(get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None, 'aca_continuation_job_create_only_absence_required')
    return mutate('install', pf_raw, pf, contract.JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'],
        {'grant': grant_raw['protected_receipt']})


def install_verify():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); grant_raw, identity, _ = grant_chain(pf_raw, pf, client)
    ack_raw, ack = chain('install', pf_raw, contract.JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'],
        {'grant': grant_raw['protected_receipt']})
    require(type(ack.get('raw_ack')) is dict and ack['raw_ack'].get('id') == contract.JOB_ID
        and ack['raw_ack'].get('name') == contract.JOB, 'aca_continuation_job_ack_identity_invalid')
    job = job_observation(client, pf, identity); observed = inventory(client)
    require(not observed['value'], 'aca_continuation_job_not_unexecuted')
    return save(dict(status='secretless_job_with_container_identity_disabled_observed', operation_id=OPERATION_ID,
        trial_operation_id=TRIAL_OPERATION_ID, ack_receipt=ack_raw['protected_receipt'], grant_receipt=grant_raw['protected_receipt'],
        job=job, environment=environment(client), empty_execution_inventory=observed,
        sql_calls=0, model_calls=0, **{key: False for key in FALSE_FLAGS}), 'install-acceptance.json')


def start():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); raw, _, identity, _ = install_chain(pf_raw, pf, client)
    require(not inventory(client)['value'], 'aca_continuation_start_requires_empty_execution_inventory')
    roles(client, identity['properties']['principalId'], granted=True)
    ack_raw = mutate('start', pf_raw, pf, contract.JOB_ID + '/start', transport.JOB_API, 'POST', start_body(pf),
        {'install': raw['protected_receipt']})
    try: capture_bound(pf, ack_raw)
    except ValueError:
        print(json.dumps(dict(status='native_replica_capture_pending_read_only_inspection_required',
            start_attempt_consumed=True, automatic_start_retry=False)), flush=True)
    return ack_raw


def start_chain(pf_raw, pf):
    install_raw, _ = load('install-acceptance.json')
    raw, value = chain('start', pf_raw, contract.JOB_ID + '/start', transport.JOB_API, 'POST', start_body(pf),
        {'install': install_raw['protected_receipt']})
    return raw, acknowledged_execution(value)


def capture_bound(pf, ack_raw):
    require(previous.sources() == pf['sources'], 'aca_continuation_source_drift_before_capture')
    name = acknowledged_execution(ack_raw); client = arm(pf['prerequisite'])
    before = get(client, contract.JOB_ID + '/executions/' + name, transport.JOB_API)
    validate_execution(before, pf, name, terminal=False)
    native = get(client, contract.JOB_ID + '/executions/' + name + '/replicas', transport.REPLICA_API)
    binding = transport.validate_replica(native, name)
    after = get(client, contract.JOB_ID + '/executions/' + name, transport.JOB_API)
    require(canonical(before) == canonical(after), 'aca_continuation_execution_drift_during_capture')
    return save(dict(status='native_running_replica_binding_observed', operation_id=OPERATION_ID,
        trial_operation_id=TRIAL_OPERATION_ID, start_ack_receipt=ack_raw['protected_receipt'], raw_execution=before,
        raw_replicas=native, binding=binding, checked_at_utc=datetime.now(timezone.utc).isoformat(),
        sql_calls=0, model_calls=0, **{key: False for key in FALSE_FLAGS}), 'replica-binding.json')


def capture():
    pf_raw, pf = prepared(cloud=False); ack_raw, _ = start_chain(pf_raw, pf)
    raw, ack = load('start-ack.json')
    require(raw['protected_receipt'] == ack_raw['protected_receipt'], 'aca_continuation_capture_ack_changed')
    return capture_bound(pf, dict(ack, protected_receipt=ack_raw['protected_receipt']))


def collect(verify_only=False):
    require(type(verify_only) is bool, 'aca_continuation_verify_flag_invalid')
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); install_raw, job, identity, permission = install_chain(pf_raw, pf, client)
    ack_raw, name = start_chain(pf_raw, pf); capture_raw, captured = load('replica-binding.json')
    require(captured.get('status') == 'native_running_replica_binding_observed' and captured.get('operation_id') == OPERATION_ID
        and captured.get('trial_operation_id') == TRIAL_OPERATION_ID and captured.get('start_ack_receipt') == ack_raw['protected_receipt'],
        'aca_continuation_capture_chain_invalid')
    validate_execution(captured['raw_execution'], pf, name, terminal=False)
    replica = transport.validate_replica(captured['raw_replicas'], name)
    require(canonical(replica) == canonical(captured['binding']), 'aca_continuation_captured_replica_mapping_changed')
    before = get(client, contract.JOB_ID + '/executions/' + name, transport.JOB_API)
    props = validate_execution(before, pf, name, terminal=True)
    require(props['startTime'] == captured['raw_execution']['properties']['startTime'], 'aca_continuation_execution_start_changed')
    scope = transport.ConsoleScope(name, (transport.utc(props['startTime']) - timedelta(minutes=1)).isoformat(),
        (transport.utc(props['endTime']) + timedelta(minutes=10)).isoformat())
    console = transport.collect_console(transport.LogsTransport(AzureCliCredential()).call, scope)
    after = get(client, contract.JOB_ID + '/executions/' + name, transport.JOB_API); inv = inventory(client)
    require(canonical(before) == canonical(after) and len(inv['value']) == 1 and inv['value'][0].get('name') == name
        and inv['value'][0].get('id') == contract.JOB_ID + '/executions/' + name, 'aca_continuation_execution_family_drift')
    require(canonical(job_observation(client, pf, identity)) == canonical(job)
        and canonical(roles(client, identity['properties']['principalId'], granted=True)) == canonical(permission)
        and canonical(resource_guards()) == canonical(pf['resource_guards']) and previous.sources() == pf['sources'],
        'aca_continuation_terminal_guards_changed')
    rows = console['rows']; observed = None
    if props['status'] == 'Succeeded':
        require(rows, 'aca_continuation_logs_not_yet_observed_read_only_recollect_no_start')
        try: observed = parse_console(rows, replica, pf['prerequisite']['observed'])
        except ValueError: pass
    qualified = observed is not None
    value = dict(status='secretless_aca_runtime_observed' if qualified else 'terminal_aca_runtime_unqualified',
        operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID, preflight_receipt=pf_raw['protected_receipt'],
        install_receipt=install_raw['protected_receipt'], start_ack_receipt=ack_raw['protected_receipt'],
        capture_receipt=capture_raw['protected_receipt'], raw_execution_before=before, raw_execution_after=after,
        complete_execution_inventory=inv, complete_console_events=rows, complete_console_observation=console,
        complete_console_sha256=digest(rows), observed=observed, current_job=job, current_roles=permission,
        requested_image_digest_verified=True, image_runtime_metadata_verified=qualified, registry_pull_success_observed=qualified,
        configured_container_identity_lifecycle_none_verified=True, configured_database_model_keyvault_secrets_absent=True,
        attempt_consumed=True, automatic_retry=False, acceptance_recorded=qualified, sql_calls=0, model_calls=0,
        daily_schedule_hkt='18:00', **{key: False for key in FALSE_FLAGS})
    filename = 'acceptance.json' if qualified else 'terminal-diagnostic.json'
    if verify_only:
        raw, retained = load(filename)
        require(canonical(retained) == canonical(value), 'aca_continuation_fresh_live_and_retained_evidence_differs')
        return dict(status='independent_secretless_aca_runtime_verified' if qualified else 'independent_aca_runtime_diagnostic_verified',
            protected_receipt=raw['protected_receipt'])
    return save(value, filename)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=COMMANDS)
    stage = parser.parse_args().stage
    previous.q.canary.host.checked_owner()
    if stage != 'verify': FOLDER.mkdir(parents=True, exist_ok=True)
    actions = {'prepare': prepare, 'grant': grant_create, 'grant-verify': grant_verify, 'install': install,
        'install-verify': install_verify, 'start': start, 'capture': capture, 'collect': collect, 'verify': lambda: collect(True)}
    result = actions[stage]()
    print(json.dumps({key: result[key] for key in ('status', 'protected_receipt')}, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
