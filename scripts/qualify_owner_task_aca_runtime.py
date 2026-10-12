"""One separately reserved, pull-only-identity ACA runtime observation.

Preserves the accepted 719 sources; registers exactly six additive sources.
No Key Vault/database/model secret is configured, no SQL/model call is made,
and received IBKR data is never touched. Each cloud effect consumes an exact
protected reservation. Unknown outcomes permit inspection, never redispatch.
The MI is lifecycle None; ARM readback is not behavioral token-isolation proof.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from azure.core import MatchConditions
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient

import qualify_owner_task_standard_pull as previous
import owner_task_aca_runtime_contract as contract
import owner_task_aca_runtime_transport as transport

ROOT = previous.ROOT
FOLDER = ROOT / '.work/owner-task-aca-runtime-20261004'
OPERATION_ID = contract.OPERATION_ID
PREFIX = 'probes/owner-codex-aca-runtime-20261004/'
ADDITIONS = frozenset(('scripts/owner_task_aca_runtime_contract.py',
    'tests/test_owner_task_aca_runtime_contract.py', 'scripts/owner_task_aca_runtime_transport.py',
    'tests/test_owner_task_aca_runtime_transport.py', 'scripts/qualify_owner_task_aca_runtime.py',
    'tests/test_owner_task_aca_runtime_controller.py'))
PINS = {
    'preflight.json': ('313d68f2b27b951ffbce9424b4b9adb5b986c297560bc18e7a1ba7caa64d4090', '2026-10-04T18:17:57.1494470Z'),
    'dispatch-intent.json': ('9e8501b63724c5f5d27d0bd3ac51dd30c854cccfd6a094263517b60ef2ee0436', '2026-10-04T18:21:04.0205318Z'),
    'dispatch-ack.json': ('2611cd429ffffea273cd2120dfd98286e4b64d07e575da77ba2191cb25245325', '2026-10-04T18:23:07.9613358Z'),
    'acceptance.json': ('34f5eb8949ca6c5b34e9b436416ba292e630fd70b64de2df1cbdee2d4548355f', '2026-10-04T18:27:48.9637188Z')}
FALSE_FLAGS = tuple(sorted(set(previous.FALSE_FLAGS) | {
    'model_credential_containment_verified', 'tenant_wide_permission_closure_verified',
    'platform_injected_credentials_excluded', 'managed_identity_unavailability_behaviorally_verified',
    'independently_observed_aca_engine_digest', 'unlogged_replacement_replicas_excluded',
    'production_migration_059_installed'}))
STAGES = ('identity', 'grant', 'install', 'start')
MAX_BYTES = 2 * 1024 * 1024
require = previous.require
canonical = previous.canonical
strict_json = previous.strict_json
digest = previous.q.digest


def validate_source_extension(old, current):
    require(type(old) is dict and len(old) == 719 and type(current) is dict
        and set(current) == set(old) | ADDITIONS and not set(old) & ADDITIONS
        and all(type(name) is str and type(sha) is str and re.fullmatch('[0-9a-f]{64}', sha)
            for name, sha in current.items()) and all(current[name] == sha for name, sha in old.items()),
        'aca_runtime_only_six_additive_sources_allowed')


def source_bytes():
    raw = (ROOT / contract.RUNTIME_SOURCE_PATH).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == contract.RUNTIME_SOURCE_SHA256, 'aca_runtime_checker_source_changed')
    return raw


def prerequisite():
    """Fresh protected ACR chain plus native terminal/log readback; no old frozen() retargeting."""
    raws = {}; values = {}
    for name, pin in PINS.items():
        raw = previous.q.readback.read_local(previous.FOLDER, name)
        previous.q.readback.receipt_binding(raw, 'standard-pull-' + name.removesuffix('.json'), pin)
        raws[name] = raw; values[name] = previous.q.deploy.protected(raw, 'standard-pull-' + name.removesuffix('.json'))
    pf = values['preflight.json']; accepted = values['acceptance.json']
    validate_source_extension(pf['guards']['sources'], previous.sources())
    previous.q.canary.validate_correction_checks(pf['local_checks'])
    require(pf.get('status') == 'prepared_standard_pull_runtime_observation'
        and pf.get('operation_id') == previous.OPERATION_ID and pf.get('plan') == previous.probe_plan()
        and accepted.get('status') == 'secretless_default_user_runtime_observed'
        and accepted.get('operation_id') == previous.OPERATION_ID
        and accepted.get('preflight_receipt') == raws['preflight.json']['protected_receipt']
        and accepted.get('intent_receipt') == raws['dispatch-intent.json']['protected_receipt']
        and accepted.get('ack_receipt') == raws['dispatch-ack.json']['protected_receipt']
        and accepted.get('reservation_receipt') == values['dispatch-intent.json']['reservation_receipt']
        and accepted.get('acceptance_recorded') is True and accepted.get('engine_image_digest_observed') is True
        and accepted.get('candidate_runtime_metadata_verified') is True
        and all(accepted.get(key) is False for key in previous.FALSE_FLAGS), 'aca_runtime_acr_prerequisite_invalid')
    name = previous.chain(raws['preflight.json'], pf, raws['dispatch-intent.json'], values['dispatch-intent.json'], values['dispatch-ack.json'])
    require(name == 'ck84', 'aca_runtime_exact_accepted_run_required')
    before = previous.show(name); logs = previous.call_cli('logs', name); after = previous.show(name)
    observed = previous.parse_logs(logs, pf['plan'])
    require(before == after == accepted['raw_run_before'] == accepted['raw_run_after']
        and before['status'] == 'Succeeded' and logs.decode('utf-8') == accepted['full_logs']
        and len(logs) == accepted['full_logs_bytes'] and hashlib.sha256(logs).hexdigest() == accepted['full_logs_sha256']
        and observed == accepted['observed'], 'aca_runtime_acr_fresh_evidence_changed')
    return dict(receipts={name: raw['protected_receipt'] for name, raw in raws.items()},
        frozen_sources=pf['guards']['sources'], frozen_resources={k: v for k, v in pf['guards'].items() if k != 'sources'},
        expected_runtime=observed['runtime'], observed=observed)


def resource_guards():
    previous.q.canary.host.checked_owner()
    baseline = previous.q.deploy.baseline()
    baseline['other_jobs'].pop(previous.q.canary.JOB, None)
    baseline['other_jobs'].pop(contract.JOB, None)  # ONLY the two explicitly registered diagnostics.
    require(baseline.get('job_inventory_complete') is True and len(baseline['other_jobs']) == 34,
        'aca_runtime_complete_existing_job_inventory_required')
    old_job = previous.q.deploy.request('GET', previous.q.canary.PATH + '?api-version=2024-03-01')
    previous.q.canary.validate_job(old_job)
    return dict(baseline=baseline, canary_settings_sha256=previous.q.deploy.settings(old_job),
        registry=previous.q.registry_metadata(), isolated_host_receipt=previous.q.canary.historical_host())


def plan(dependency):
    return contract.build(source_bytes(), dependency['expected_runtime'])


def arm(dependency):
    return transport.ArmTransport(AzureCliCredential(), runtime_source_bytes=source_bytes(), expected_runtime=dependency['expected_runtime'])


def start_body(pf):
    template = pf['plan']['definition']['properties']['template']
    require(template.get('volumes') == [], 'aca_runtime_start_volume_scope_invalid')
    return {key: value for key, value in template.items() if key != 'volumes'}


def save(value, name):
    require(re.fullmatch(r'[a-z][a-z-]*\.json', name) is not None and 0 < len(canonical(value)) <= MAX_BYTES,
        'aca_runtime_evidence_name_or_size_invalid')
    previous.q.deploy.FOLDER = FOLDER
    raw = previous.q.deploy.save(value, name, 'aca-runtime-' + name.removesuffix('.json'))
    previous.q.deploy.protected(raw, 'aca-runtime-' + name.removesuffix('.json'))
    return raw


def load(name):
    raw = previous.q.readback.read_local(FOLDER, name)
    return raw, previous.q.deploy.protected(raw, 'aca-runtime-' + name.removesuffix('.json'))


def reservation_blob(stage, version=None):
    require(stage in STAGES, 'aca_runtime_reservation_stage_invalid')
    previous.q.deploy.archive_protection()
    service = BlobServiceClient('https://' + previous.q.deploy.ACCOUNT + '.blob.core.windows.net', credential=AzureCliCredential(),
        retry_total=0, retry_to_secondary=False, connection_timeout=5, read_timeout=10, permit_redirects=False, redirect_max=0)
    return service.get_blob_client(previous.q.deploy.CONTAINER, PREFIX + stage + '-reservation.json', version_id=version)


def reservation_readback(stage, receipt, value):
    payload = canonical(value)
    require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'etag', 'protected_readback_verified'}
        and receipt.get('blob') == PREFIX + stage + '-reservation.json'
        and receipt.get('sha256') == hashlib.sha256(payload).hexdigest() and receipt.get('protected_readback_verified') is True
        and type(receipt.get('version_id')) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id'])
        and type(receipt.get('etag')) is str and re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag'])
        and 0 < len(payload) <= MAX_BYTES, 'aca_runtime_reservation_binding_invalid')
    blob = reservation_blob(stage, receipt['version_id']); props = blob.get_blob_properties(timeout=10, retry_total=0)
    require(props.version_id == receipt['version_id'] and props.etag == receipt['etag'] and props.size == len(payload)
        and props.blob_type == 'BlockBlob' and blob.download_blob(offset=0, length=len(payload) + 1, etag=props.etag,
            match_condition=MatchConditions.IfNotModified, max_concurrency=1, timeout=10, retry_total=0).readall() == payload,
        'aca_runtime_reservation_readback_failed')
    previous.q.deploy.archive_protection()


def reserve(stage, value):
    payload = canonical(value)
    require(0 < len(payload) <= MAX_BYTES, 'aca_runtime_reservation_size_invalid')
    ack = reservation_blob(stage).upload_blob(payload, length=len(payload), blob_type='BlockBlob', overwrite=False,
        match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
    receipt = dict(blob=PREFIX + stage + '-reservation.json', sha256=hashlib.sha256(payload).hexdigest(),
        version_id=ack.get('version_id'), etag=ack.get('etag'), protected_readback_verified=True)
    reservation_readback(stage, receipt, value)
    return receipt


def get(client, path, api, *, missing=False):
    value, status = client.request(path, api, missing=missing)
    require(status == (404 if value is None and missing else 200), 'aca_runtime_get_status_invalid')
    return value


def environment(client):
    value = get(client, contract.ENVIRONMENT, transport.ENV_API)
    config = value.get('properties', {}).get('appLogsConfiguration', {})
    require(value.get('id') == contract.ENVIRONMENT and config.get('destination') == 'log-analytics'
        and config.get('logAnalyticsConfiguration', {}).get('customerId') == transport.WORKSPACE,
        'aca_runtime_environment_log_destination_changed')
    return dict(environment_id=contract.ENVIRONMENT, workspace=transport.WORKSPACE, destination='log-analytics')


def prepare():
    require(not (FOLDER / 'preflight.json').exists(), 'aca_runtime_preflight_already_reserved')
    before = previous.sources(); dep = prerequisite(); checks = previous.q.qa.run_local_checks()
    current = resource_guards(); client = arm(dep)
    require(get(client, contract.IDENTITY_ID, transport.MI_API, missing=True) is None
        and get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None,
        'aca_runtime_fresh_named_identity_and_job_required')
    require(get(client, transport.ASSIGNMENT_ID, transport.ROLE_API, missing=True) is None,
        'aca_runtime_fresh_named_role_assignment_required')
    env = environment(client)
    require(current == dep['frozen_resources'] and previous.sources() == before,
        'aca_runtime_resource_or_source_changed_during_qa')
    return save(dict(status='prepared_secretless_aca_runtime', operation_id=OPERATION_ID,
        checked_at_utc=datetime.now(timezone.utc).isoformat(), sources=before, resource_guards=current,
        prerequisite=dep, plan=plan(dep), environment=env, local_checks=checks,
        sql_calls=0, model_calls=0, daily_schedule_hkt='18:00', **{key: False for key in FALSE_FLAGS}), 'preflight.json')


def prepared(*, cloud=True):
    raw, value = load('preflight.json')
    require(value.get('status') == 'prepared_secretless_aca_runtime' and value.get('operation_id') == OPERATION_ID
        and all(value.get(key) is False for key in FALSE_FLAGS), 'aca_runtime_preflight_binding_invalid')
    previous.q.canary.validate_correction_checks(value['local_checks'])
    validate_source_extension(value['prerequisite']['frozen_sources'], previous.sources())
    require(previous.sources() == value['sources'] and plan(value['prerequisite']) == value['plan'], 'aca_runtime_source_or_plan_drift')
    if cloud:
        require(resource_guards() == value['resource_guards'] and prerequisite() == value['prerequisite'],
            'aca_runtime_cloud_prerequisite_drift')
    return raw, value


def intent_value(stage, pf_raw, path, api, method, body, prior):
    require(stage in STAGES and type(prior) is dict, 'aca_runtime_mutation_stage_invalid')
    return dict(operation_id=OPERATION_ID, stage=stage, preflight_receipt=pf_raw['protected_receipt'],
        path=path, api=api, method=method, body=body, body_sha256=digest(body), prior_receipts=prior)


def mutate(stage, pf_raw, pf, path, api, method, body, prior):
    require(stage in STAGES and not any((FOLDER / (stage + '-' + suffix + '.json')).exists()
        for suffix in ('intent', 'ack', 'outcome')), 'aca_runtime_effect_consumed_inspect_only')
    value = intent_value(stage, pf_raw, path, api, method, body, prior)
    receipt = reserve(stage, value)
    intent = save(dict(value, status='reserved_one_exact_cloud_effect', reservation_receipt=receipt), stage + '-intent.json')
    require(previous.sources() == pf['sources'], 'aca_runtime_source_changed_after_reservation')
    reservation_readback(stage, receipt, value)
    pre_effect_checks(stage, arm(pf['prerequisite']), pf, body)
    try:
        ack, status = arm(pf['prerequisite']).request(path, api, method=method, body=body)
    except Exception:
        try:
            save(dict(status='effect_unknown_ack_unverified_inspect_only', operation_id=OPERATION_ID, stage=stage,
                intent_receipt=intent['protected_receipt'], reservation_receipt=receipt, attempt_consumed=True,
                automatic_retry=False, no_effects_verified=False, sql_calls=0, model_calls=0), stage + '-outcome.json')
        except Exception:
            raise ValueError('aca_runtime_effect_outcome_unretained_attempt_consumed_inspect_only') from None
        raise ValueError('aca_runtime_effect_unknown_attempt_consumed_inspect_only') from None
    return save(dict(status='one_exact_cloud_effect_acknowledged', operation_id=OPERATION_ID, stage=stage,
        intent_receipt=intent['protected_receipt'], http_status=status, raw_ack=ack, attempt_consumed=True,
        automatic_retry=False), stage + '-ack.json')


def pre_effect_checks(stage, client, pf, body):
    """Native current target/RBAC checks AFTER retained reservation and BEFORE effect."""
    if stage == 'identity':
        require(get(client, contract.IDENTITY_ID, transport.MI_API, missing=True) is None
            and get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None,
            'aca_runtime_create_targets_changed_after_reservation')
        return
    identity = identity_observation(client); principal = identity['properties']['principalId']
    roles(client, principal, granted=stage != 'grant')
    if stage == 'grant':
        require(canonical(body) == canonical(transport.assignment_body(principal)), 'aca_runtime_reserved_principal_changed')
        require(get(client, transport.ASSIGNMENT_ID, transport.ROLE_API, missing=True) is None,
            'aca_runtime_grant_appeared_after_reservation')
    if stage in ('grant', 'install'):
        require(get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None,
            'aca_runtime_job_appeared_after_reservation')
    else:
        job_observation(client, pf, identity)
        require(not inventory(client)['value'], 'aca_runtime_execution_appeared_after_reservation')


def chain(stage, pf_raw, path, api, method, body, prior):
    intent_raw, intent = load(stage + '-intent.json'); ack_raw, ack = load(stage + '-ack.json')
    expected = intent_value(stage, pf_raw, path, api, method, body, prior)
    require({key: intent.get(key) for key in expected} == expected and intent.get('status') == 'reserved_one_exact_cloud_effect'
        and set(intent) == set(expected) | {'status', 'reservation_receipt'}
        and ack.get('status') == 'one_exact_cloud_effect_acknowledged' and ack.get('operation_id') == OPERATION_ID
        and ack.get('stage') == stage and ack.get('intent_receipt') == intent_raw['protected_receipt']
        and ack.get('attempt_consumed') is True and ack.get('automatic_retry') is False
        and type(ack.get('http_status')) is int and ack['http_status'] in (200, 201, 202), 'aca_runtime_effect_chain_invalid')
    reservation_readback(stage, intent['reservation_receipt'], expected)
    return ack_raw, ack


def identity_observation(client):
    native = get(client, contract.IDENTITY_ID, transport.MI_API)
    transport.validate_identity_metadata(native)
    return native


def identity_chain(pf_raw, pf, client):
    ack_raw, ack = chain('identity', pf_raw, contract.IDENTITY_ID, transport.MI_API, 'PUT', transport.identity_body(), {})
    native = identity_observation(client)
    transport.validate_identity_metadata(ack['raw_ack'])
    require(ack['raw_ack'] == native, 'aca_runtime_identity_ack_and_readback_differ')
    raw, retained = load('identity-acceptance.json')
    require(retained.get('operation_id') == OPERATION_ID and retained.get('status') == 'new_identity_metadata_observed'
        and retained.get('identity') == native and retained.get('ack_receipt') == ack_raw['protected_receipt'],
        'aca_runtime_identity_acceptance_changed')
    return raw, native


def roles(client, principal, *, granted):
    snapshots = transport.role_inventory(client, principal)
    definition = get(client, transport.ROLE_ID, transport.ROLE_API)
    transport.validate_role_definition(definition)
    require(type(snapshots) is dict and set(snapshots) == {'subscription', 'resource_group_inherited',
        'registry_inherited', 'observed_arm_inventory_complete', 'tenant_permission_closure_verified'}
        and snapshots['observed_arm_inventory_complete'] is True and snapshots['tenant_permission_closure_verified'] is False,
        'aca_runtime_role_inventory_invalid')
    for scope in ('subscription', 'resource_group_inherited', 'registry_inherited'):
        rows = snapshots[scope]
        wanted = 1 if granted and scope != 'resource_group_inherited' else 0
        require(type(rows) is list and len(rows) == wanted, 'aca_runtime_unexpected_visible_role_assignment')
        for row in rows: transport.validate_assignment(row, principal)
    return dict(inventories=snapshots, role_definition=definition,
        registered_subscription_assignment_inventory_verified=True, tenant_wide_permission_closure_verified=False)


def grant_chain(pf_raw, pf, client):
    identity_raw, identity = identity_chain(pf_raw, pf, client)
    principal = identity['properties']['principalId']; body = transport.assignment_body(principal)
    prior = {'identity': identity_raw['protected_receipt']}
    ack_raw, ack = chain('grant', pf_raw, transport.ASSIGNMENT_ID, transport.ROLE_API, 'PUT', body, prior)
    transport.validate_assignment(ack['raw_ack'], principal)
    native = get(client, transport.ASSIGNMENT_ID, transport.ROLE_API)
    transport.validate_assignment(native, principal)
    current = roles(client, principal, granted=True)
    raw, value = load('grant-acceptance.json')
    require(value.get('status') == 'exact_registry_pull_role_observed' and value.get('operation_id') == OPERATION_ID
        and value.get('identity_receipt') == identity_raw['protected_receipt'] and value.get('assignment') == native
        and value.get('roles') == current and value.get('ack_receipt') == ack_raw['protected_receipt'],
        'aca_runtime_registry_pull_role_changed')
    return raw, identity, current


def job_observation(client, pf, identity):
    native = get(client, contract.JOB_ID, transport.JOB_API)
    contract.validate_job(native, source_bytes(), pf['prerequisite']['expected_runtime'],
        principal_id=identity['properties']['principalId'], client_id=identity['properties']['clientId'])
    return native


def inventory(client):
    native = get(client, contract.JOB_ID + '/executions', transport.JOB_API)
    require(type(native) is dict and set(native) <= {'value', 'nextLink'} and type(native.get('value')) is list
        and native.get('nextLink') in (None, '') and len(native['value']) <= 1, 'aca_runtime_execution_inventory_incomplete_or_duplicate')
    return native


def install_chain(pf_raw, pf, client):
    grant_raw, identity, permission = grant_chain(pf_raw, pf, client)
    prior = {'grant': grant_raw['protected_receipt']}
    ack_raw, ack = chain('install', pf_raw, contract.JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'], prior)
    require(ack['raw_ack'].get('id') == contract.JOB_ID and ack['raw_ack'].get('name') == contract.JOB,
        'aca_runtime_job_ack_identity_invalid')
    job = job_observation(client, pf, identity)
    raw, value = load('install-acceptance.json')
    require(value.get('status') == 'secretless_job_with_container_identity_disabled_observed'
        and value.get('operation_id') == OPERATION_ID and value.get('ack_receipt') == ack_raw['protected_receipt']
        and value.get('grant_receipt') == grant_raw['protected_receipt'] and value.get('job') == job
        and value.get('environment') == environment(client), 'aca_runtime_installed_job_drift')
    return raw, job, identity, permission


def identity_create():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite'])
    require(get(client, contract.IDENTITY_ID, transport.MI_API, missing=True) is None
        and get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None, 'aca_runtime_named_targets_not_absent')
    return mutate('identity', pf_raw, pf, contract.IDENTITY_ID, transport.MI_API, 'PUT', transport.identity_body(), {})


def identity_verify():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite'])
    ack_raw, ack = chain('identity', pf_raw, contract.IDENTITY_ID, transport.MI_API, 'PUT', transport.identity_body(), {})
    native = identity_observation(client); permission = roles(client, native['properties']['principalId'], granted=False)
    transport.validate_identity_metadata(ack['raw_ack'])
    require(ack['raw_ack'] == native, 'aca_runtime_identity_ack_and_readback_differ')
    return save(dict(status='new_identity_metadata_observed', operation_id=OPERATION_ID,
        identity=native, ack_receipt=ack_raw['protected_receipt'], roles=permission,
        sql_calls=0, model_calls=0, **{key: False for key in FALSE_FLAGS}), 'identity-acceptance.json')


def grant_create():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); raw, identity = identity_chain(pf_raw, pf, client)
    principal = identity['properties']['principalId']; roles(client, principal, granted=False)
    require(get(client, transport.ASSIGNMENT_ID, transport.ROLE_API, missing=True) is None,
        'aca_runtime_grant_create_only_absence_required')
    require(get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None, 'aca_runtime_job_must_still_be_absent')
    return mutate('grant', pf_raw, pf, transport.ASSIGNMENT_ID, transport.ROLE_API, 'PUT',
        transport.assignment_body(principal), {'identity': raw['protected_receipt']})


def grant_verify():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); identity_raw, identity = identity_chain(pf_raw, pf, client)
    principal = identity['properties']['principalId']; prior = {'identity': identity_raw['protected_receipt']}
    ack_raw, ack = chain('grant', pf_raw, transport.ASSIGNMENT_ID, transport.ROLE_API, 'PUT', transport.assignment_body(principal), prior)
    transport.validate_assignment(ack['raw_ack'], principal)
    native = get(client, transport.ASSIGNMENT_ID, transport.ROLE_API); transport.validate_assignment(native, principal)
    permission = roles(client, principal, granted=True)
    return save(dict(status='exact_registry_pull_role_observed', operation_id=OPERATION_ID,
        identity_receipt=identity_raw['protected_receipt'], ack_receipt=ack_raw['protected_receipt'],
        assignment=native, roles=permission, sql_calls=0, model_calls=0, **{key: False for key in FALSE_FLAGS}), 'grant-acceptance.json')


def install():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); grant_raw, _, _ = grant_chain(pf_raw, pf, client)
    require(get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None, 'aca_runtime_job_create_only_absence_required')
    return mutate('install', pf_raw, pf, contract.JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'],
        {'grant': grant_raw['protected_receipt']})


def install_verify():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); grant_raw, identity, _ = grant_chain(pf_raw, pf, client)
    ack_raw, ack = chain('install', pf_raw, contract.JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'], {'grant': grant_raw['protected_receipt']})
    require(ack['raw_ack'].get('id') == contract.JOB_ID and ack['raw_ack'].get('name') == contract.JOB,
        'aca_runtime_job_ack_identity_invalid')
    job = job_observation(client, pf, identity); observed = inventory(client)
    require(not observed['value'], 'aca_runtime_job_not_unexecuted')
    return save(dict(status='secretless_job_with_container_identity_disabled_observed', operation_id=OPERATION_ID,
        ack_receipt=ack_raw['protected_receipt'], grant_receipt=grant_raw['protected_receipt'], job=job,
        environment=environment(client), empty_execution_inventory=observed,
        sql_calls=0, model_calls=0, **{key: False for key in FALSE_FLAGS}), 'install-acceptance.json')


def start():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); raw, _, identity, _ = install_chain(pf_raw, pf, client)
    require(not inventory(client)['value'], 'aca_runtime_start_requires_empty_execution_inventory')
    # Recheck permission closure immediately before the protected START reservation.
    roles(client, identity['properties']['principalId'], granted=True)
    ack_raw = mutate('start', pf_raw, pf, contract.JOB_ID + '/start', transport.JOB_API, 'POST',
        start_body(pf), {'install': raw['protected_receipt']})
    # Use the already bound in-process preflight/ACK to avoid losing the native
    # replica while re-reading large protected prerequisites during the hold.
    # Any pending capture remains a READ-only continuation of this consumed run.
    try:
        capture_bound(pf, ack_raw)
    except ValueError:
        print(json.dumps({'status': 'native_replica_capture_pending_read_only_inspection_required',
            'start_attempt_consumed': True, 'automatic_start_retry': False}), flush=True)
    return ack_raw


def acknowledged_execution(value):
    ack = value.get('raw_ack')
    require(value.get('http_status') == 200 and type(ack) is dict and set(ack) == {'id', 'name'},
        'aca_runtime_start_ack_ambiguous_inspect_only')
    name = transport.execution_name(ack['name'])
    require(ack['id'] == contract.JOB_ID + '/executions/' + name, 'aca_runtime_exact_start_execution_required')
    return name


def start_chain(pf_raw, pf):
    install_raw, _ = load('install-acceptance.json')
    raw, value = chain('start', pf_raw, contract.JOB_ID + '/start', transport.JOB_API, 'POST',
        start_body(pf), {'install': install_raw['protected_receipt']})
    name = acknowledged_execution(value)
    return raw, name


def validate_execution(value, pf, name, *, terminal):
    require(type(value) is dict and set(value) <= {'id', 'name', 'type', 'properties'}
        and value.get('id') == contract.JOB_ID + '/executions/' + name and value.get('name') == name
        and value.get('type') == 'Microsoft.App/jobs/executions', 'aca_runtime_native_execution_invalid')
    props = value.get('properties')
    require(type(props) is dict and set(props) <= {'status', 'startTime', 'endTime', 'template'}
        and props.get('status') in (('Succeeded', 'Failed', 'Stopped') if terminal else ('Running', 'Processing')),
        'aca_runtime_execution_status_invalid')
    contract.validate_template(props.get('template'), source_bytes(), pf['prerequisite']['expected_runtime'], execution=True)
    began = previous.q.readback.utc(props.get('startTime'))
    if terminal:
        ended = previous.q.readback.utc(props.get('endTime'))
        require(began < ended <= began + timedelta(seconds=660), 'aca_runtime_execution_window_invalid')
    else:
        require(props.get('endTime') is None, 'aca_runtime_running_execution_has_end_time')
    return props


def capture_bound(pf, ack_raw):
    require(previous.sources() == pf['sources'], 'aca_runtime_source_drift_before_capture')
    name = acknowledged_execution(ack_raw); client = arm(pf['prerequisite'])
    before = get(client, contract.JOB_ID + '/executions/' + name, transport.JOB_API)
    validate_execution(before, pf, name, terminal=False)
    native = get(client, contract.JOB_ID + '/executions/' + name + '/replicas', transport.REPLICA_API)
    binding = transport.validate_replica(native, name)
    after = get(client, contract.JOB_ID + '/executions/' + name, transport.JOB_API)
    require(before == after, 'aca_runtime_execution_drift_during_capture')
    return save(dict(status='native_running_replica_binding_observed', operation_id=OPERATION_ID,
        start_ack_receipt=ack_raw['protected_receipt'], raw_execution=before, raw_replicas=native, binding=binding,
        checked_at_utc=datetime.now(timezone.utc).isoformat(), sql_calls=0, model_calls=0,
        **{key: False for key in FALSE_FLAGS}), 'replica-binding.json')


def capture():
    # Fast read-only stage during the fixed hold; no long baseline checks before capture.
    pf_raw, pf = prepared(cloud=False); ack_raw, _ = start_chain(pf_raw, pf)
    _, ack = load('start-ack.json')
    return capture_bound(pf, dict(ack, protected_receipt=ack_raw['protected_receipt']))


def parse_console(rows, replica, expected):
    require(type(rows) is list and len(rows) == 1, 'aca_runtime_exactly_one_console_event_required')
    row = rows[0]
    require(type(row) is dict and set(row) == {'Group', 'Container', 'LogTime', 'Log'}
        and row['Group'] == replica['name'] and row['Container'] == contract.CONTAINER
        and type(row['Log']) is str and row['Log'].startswith(contract.PREFIX)
        and '\n' not in row['Log'] and '\r' not in row['Log'] and len(row['Log'].encode('utf-8')) <= 4096,
        'aca_runtime_console_binding_invalid')
    native = strict_json(row['Log'][len(contract.PREFIX):].encode('utf-8'))
    contract.validate_runtime_record(native, source_bytes(), expected['runtime'])
    require(canonical(native) == canonical(expected), 'aca_runtime_descriptor_differs_from_accepted_acr')
    return native


def collect(verify_only=False):
    require(type(verify_only) is bool, 'aca_runtime_verify_flag_invalid')
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); install_raw, job, identity, permission = install_chain(pf_raw, pf, client)
    ack_raw, name = start_chain(pf_raw, pf); capture_raw, captured = load('replica-binding.json')
    require(captured.get('status') == 'native_running_replica_binding_observed' and captured.get('operation_id') == OPERATION_ID
        and captured.get('start_ack_receipt') == ack_raw['protected_receipt'], 'aca_runtime_capture_chain_invalid')
    validate_execution(captured['raw_execution'], pf, name, terminal=False)
    replica = transport.validate_replica(captured['raw_replicas'], name)
    require(replica == captured['binding'], 'aca_runtime_captured_replica_mapping_changed')
    before = get(client, contract.JOB_ID + '/executions/' + name, transport.JOB_API)
    props = validate_execution(before, pf, name, terminal=True)
    require(props['startTime'] == captured['raw_execution']['properties']['startTime'], 'aca_runtime_execution_start_changed')
    # Fixed execution-family observation window tolerates collector timestamp lag.
    # Neither this bound nor two inventories proves future/zero-log absence.
    scope = transport.ConsoleScope(name,
        (transport.utc(props['startTime']) - timedelta(minutes=1)).isoformat(),
        (transport.utc(props['endTime']) + timedelta(minutes=10)).isoformat())
    reader = transport.LogsTransport(AzureCliCredential())
    console = transport.collect_console(reader.call, scope)
    rows_before = console['rows']
    after = get(client, contract.JOB_ID + '/executions/' + name, transport.JOB_API)
    inv = inventory(client)
    require(before == after and len(inv['value']) == 1 and inv['value'][0].get('name') == name
        and inv['value'][0].get('id') == contract.JOB_ID + '/executions/' + name, 'aca_runtime_execution_family_drift')
    require(job_observation(client, pf, identity) == job and roles(client, identity['properties']['principalId'], granted=True) == permission
        and resource_guards() == pf['resource_guards'] and previous.sources() == pf['sources'], 'aca_runtime_terminal_guards_changed')
    observed = None
    if props['status'] == 'Succeeded':
        require(rows_before, 'aca_runtime_logs_not_yet_observed_read_only_recollect_no_start')
        try: observed = parse_console(rows_before, replica, pf['prerequisite']['observed'])
        except ValueError: pass  # Complete unaccepted output retained; no relaxed parsing or retry.
    qualified = observed is not None
    value = dict(status='secretless_aca_runtime_observed' if qualified else 'terminal_aca_runtime_unqualified',
        operation_id=OPERATION_ID, preflight_receipt=pf_raw['protected_receipt'], install_receipt=install_raw['protected_receipt'],
        start_ack_receipt=ack_raw['protected_receipt'], capture_receipt=capture_raw['protected_receipt'],
        raw_execution_before=before, raw_execution_after=after, complete_execution_inventory=inv,
        complete_console_events=rows_before, complete_console_observation=console, complete_console_sha256=digest(rows_before), observed=observed,
        current_job=job, current_roles=permission, requested_image_digest_verified=True,
        image_runtime_metadata_verified=qualified, registry_pull_success_observed=qualified,
        configured_container_identity_lifecycle_none_verified=True, configured_database_model_keyvault_secrets_absent=True,
        attempt_consumed=True, automatic_retry=False, acceptance_recorded=qualified,
        sql_calls=0, model_calls=0, daily_schedule_hkt='18:00', **{key: False for key in FALSE_FLAGS})
    filename = 'acceptance.json' if qualified else 'terminal-diagnostic.json'
    if verify_only:
        raw, retained = load(filename)
        require(retained == value, 'aca_runtime_fresh_live_and_retained_evidence_differs')
        return dict(status='independent_secretless_aca_runtime_verified' if qualified else 'independent_aca_runtime_diagnostic_verified',
            protected_receipt=raw['protected_receipt'])
    return save(value, filename)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'identity', 'identity-verify', 'grant', 'grant-verify', 'install',
        'install-verify', 'start', 'capture', 'collect', 'verify'))
    stage = parser.parse_args().stage
    previous.q.canary.host.checked_owner()
    if stage != 'verify': FOLDER.mkdir(parents=True, exist_ok=True)
    actions = {'prepare': prepare, 'identity': identity_create, 'identity-verify': identity_verify,
        'grant': grant_create, 'grant-verify': grant_verify, 'install': install, 'install-verify': install_verify,
        'start': start, 'capture': capture, 'collect': collect, 'verify': lambda: collect(True)}
    result = actions[stage]()
    print(json.dumps({key: result[key] for key in ('status', 'protected_receipt')}, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
