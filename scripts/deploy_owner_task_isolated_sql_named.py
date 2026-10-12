"""New 25-character test job after protected rejection of the old 33-char name.

Protected source/intent/reservations precede new MI, scoped grants, job and START.
No credential values, arbitrary target/payload, production changes or retry.
An uncertain effect consumes its stage and requires read-only inspection.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import time

import requests
from requests.adapters import HTTPAdapter
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from azure.core import MatchConditions

import owner_task_isolated_sql_named_launch as launch
import owner_task_isolated_sql_readback as reviewed
import deploy_owner_task_isolated_sql as predecessor
import prepare_owner_task_isolated_catalog as old
import execute_owner_task_isolated_catalog as catalog
import owner_task_catalog_launch_v2 as compatibility
import verify_owner_task_source_checkpoint as qa

host, worker, target = old.host, launch.worker, launch.target
ROOT = host.deploy.ROOT
FOLDER = ROOT / '.work/owner-task-isolated-sql-named-20261005'
KIND = 'isolated-sql-named-'
PREFIX = 'probes/owner-codex-isolated-sql-named-20261005/'
STAGES = ('job', 'start')
MI, JOB = worker.IDENTITY_ID, worker.JOB_ID
SUB_SCOPE = '/subscriptions/' + target.SUBSCRIPTION
ROLE_BASE = SUB_SCOPE + '/providers/Microsoft.Authorization/roleDefinitions/'
ROLES = dict(acr=(target.REGISTRY, '7f951dda-4ed3-4680-a7ca-43fe172d538d', '5d1eabaf-16f7-4a94-88c0-243d54e6a7c5'),
    secret=(target.VAULT + '/secrets/' + target.SECRET_NAME, '4633458b-17de-408a-b874-0445c86b69e6', 'f00edb30-470a-44ad-a0d2-43e0be254e0c'))
ADDITIONS = frozenset(('scripts/run_owner_task_isolated_sql_named.py',
    'scripts/owner_task_isolated_sql_named_launch.py', 'scripts/deploy_owner_task_isolated_sql_named.py',
    'tests/test_owner_task_isolated_sql_named.py'))
canonical, digest, require = launch.canonical, launch.digest, launch.require
MAX_BYTES = 2 * 1024 * 1024


def now(): return datetime.now(timezone.utc).isoformat()


def validate_sources(before, after):
    require(type(before) is dict and len(before) == 762 and type(after) is dict
        and set(after) == set(before) | ADDITIONS and not set(before) & ADDITIONS
        and all(after[k] == v for k, v in before.items())
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v)
            for k, v in after.items()), 'isolated_sql_sources_not_additive')


def assignment(stage):
    require(stage in ROLES, 'isolated_sql_stage_invalid')
    return ROLES[stage][0] + '/providers/Microsoft.Authorization/roleAssignments/' + ROLES[stage][2]


def identity_body():
    return predecessor.identity_body()


def effect(stage, plan, principal=None):
    require(stage in STAGES, 'isolated_sql_stage_invalid')
    if stage == 'identity': return 'PUT', MI, '2023-01-31', identity_body()
    if stage in ROLES:
        require(type(principal) is str and re.fullmatch('[0-9a-f-]{36}', principal), 'isolated_sql_principal_invalid')
        return 'PUT', assignment(stage), '2022-04-01', dict(properties=dict(
            roleDefinitionId=ROLE_BASE + ROLES[stage][1], principalId=principal, principalType='ServicePrincipal'))
    require(type(plan) is dict and set(plan) == {'definition', 'definition_sha256', 'source_hashes', 'expected_runtime',
        'sql_installation_dispatched', 'model_credential_isolation_verified', 'step_2_complete'}
        and plan['definition_sha256'] == digest(plan['definition']), 'isolated_sql_plan_invalid')
    return ('PUT', JOB, '2025-07-01', plan['definition']) if stage == 'job' else (
        'POST', JOB + '/start', '2025-07-01', plan['definition']['properties']['template'])


def execution_name(value):
    require(type(value) is str and re.fullmatch(re.escape(worker.JOB) + '-[a-z0-9]{7,12}', value),
        'isolated_sql_execution_invalid')
    return value


class Transport:
    def __init__(self):
        self.credential = AzureCliCredential(); self.session = requests.Session(); self.token = None
        self.session.trust_env = False; self.session.mount('https://', HTTPAdapter(max_retries=0))

    def call(self, method, path, api, *, body=None, missing=False, stage=None, plan=None, principal=None):
        route = path + '?api-version=' + api
        if method == 'GET':
            allowed = {MI: '2023-01-31', JOB: '2025-07-01', JOB + '/executions': '2025-07-01', predecessor.JOB: '2025-07-01'}
            allowed.update({assignment(s): '2022-04-01' for s in ROLES})
            if path.startswith(JOB + '/executions/'):
                name = path.removeprefix(JOB + '/executions/')
                if name.endswith('/replicas'):
                    execution_name(name.removesuffix('/replicas')); allowed[path] = '2023-11-02-preview'
                else: execution_name(name); allowed[path] = '2025-07-01'
            if path == SUB_SCOPE + '/providers/Microsoft.Authorization/roleAssignments':
                require(type(principal) is str and re.fullmatch('[0-9a-f-]{36}', principal)
                    and api == '2022-04-01', 'isolated_sql_route_invalid')
                route += "&$filter=principalId eq '" + principal + "'"
            else: require(allowed.get(path) == api, 'isolated_sql_route_invalid')
            require(body is None, 'isolated_sql_route_invalid')
        else:
            wanted = effect(stage, plan, principal)
            require((method, path, api) == wanted[:3] and canonical(body) == canonical(wanted[3]),
                'isolated_sql_route_invalid')
        response = None
        try:
            if self.token is None or self.token.expires_on < time.time() + 120:
                self.token = self.credential.get_token('https://management.azure.com/.default')
            response = self.session.request(method, 'https://management.azure.com' + route, json=body,
                headers={'Authorization': 'Bearer ' + self.token.token}, allow_redirects=False,
                stream=True, timeout=(5, 20))
            if method == 'GET' and missing and response.status_code == 404: return None
            require(response.status_code in ((200,) if method == 'GET' else (200, 201, 202)),
                'isolated_sql_http_unaccepted')
            chunks, count = [], 0
            for chunk in response.iter_content(16384):
                count += len(chunk); require(count <= MAX_BYTES, 'isolated_sql_http_oversized'); chunks.append(chunk)
            value = json.loads(b''.join(chunks)); require(type(value) is dict, 'isolated_sql_http_invalid')
            return value
        except Exception: raise ValueError('isolated_sql_http_uncertain_inspect_only') from None
        finally:
            if response is not None: response.close()


def save(value, filename):
    require(re.fullmatch('[a-z][a-z0-9-]*\\.json', filename) and len(canonical(value)) <= MAX_BYTES,
        'isolated_sql_evidence_invalid')
    with (FOLDER / filename).open('x', encoding='utf-8') as output:
        host.deploy.archive_protection()
        raw = dict(value, protected_receipt=host.deploy.retain(value, 'owner-task-pg-' + KIND + filename[:-5]))
        output.write(json.dumps(raw, sort_keys=True, indent=2))
    host.deploy.protected(raw, KIND + filename[:-5]); return raw


def load(filename):
    raw = json.loads((FOLDER / filename).read_text(encoding='utf-8'))
    return raw, host.deploy.protected(raw, KIND + filename[:-5])


def baseline():
    value = old.baseline(); value['other_jobs'].pop(worker.JOB, None); return value


def live_identity(value):
    aliases = (MI, MI.replace('/resourceGroups/', '/resourcegroups/'))
    require(type(value) is dict and value.get('id') in aliases and value.get('name') == worker.IDENTITY
        and value.get('type') == 'Microsoft.ManagedIdentity/userAssignedIdentities'
        and value.get('location') in (target.LOCATION, 'East Asia') and value.get('tags') == identity_body()['tags'],
        'isolated_sql_identity_invalid')
    props = value.get('properties')
    require(type(props) is dict and set(props) == {'tenantId', 'principalId', 'clientId'}
        and props['tenantId'] == '0578a50b-a7a1-40a4-905b-8b20dba84124'
        and all(type(props[k]) is str and re.fullmatch('[0-9a-f-]{36}', props[k]) for k in ('principalId', 'clientId')),
        'isolated_sql_identity_invalid')
    return props


def roles(client, principal, stages):
    value = client.call('GET', SUB_SCOPE + '/providers/Microsoft.Authorization/roleAssignments', '2022-04-01', principal=principal)
    require(set(value) <= {'value', 'nextLink'} and value.get('nextLink') in (None, '')
        and type(value.get('value')) is list and len(value['value']) == len(stages), 'isolated_sql_role_inventory_invalid')
    wanted = {assignment(stage).lower(): stage for stage in stages}
    require({r.get('id', '').lower() for r in value['value']} == set(wanted), 'isolated_sql_role_inventory_invalid')
    for row in value['value']:
        stage = wanted[row['id'].lower()]; props = row.get('properties')
        require(type(props) is dict and props.get('principalId') == principal
            and props.get('principalType') == 'ServicePrincipal'
            and props.get('scope', '').lower() == ROLES[stage][0].lower()
            and props.get('roleDefinitionId', '').lower() == (ROLE_BASE + ROLES[stage][1]).lower()
            and props.get('condition') in (None, '') and props.get('conditionVersion') in (None, ''),
            'isolated_sql_role_inventory_invalid')
    return value


def validate_job(value, plan, identity):
    """Validate only known provider descriptive aliases; never retarget old jobs."""
    expected = plan['definition']; props = value.get('properties', {})
    require(value.get('id') == JOB and value.get('name') == worker.JOB and value.get('type') == 'Microsoft.App/jobs'
        and value.get('location') in (target.LOCATION, 'East Asia') and value.get('tags') == expected['tags']
        and set(value) <= {'id', 'name', 'type', 'location', 'tags', 'identity', 'properties', 'systemData'},
        'isolated_sql_job_invalid')
    observed = compatibility.copy(value.get('identity'))
    assigned = observed.get('userAssignedIdentities')
    require(observed.get('type') == 'UserAssigned' and observed.get('principalId') is None and observed.get('tenantId') is None
        and set(observed) <= {'type', 'principalId', 'tenantId', 'userAssignedIdentities'}
        and type(assigned) is dict and len(assigned) == 1
        and next(iter(assigned)).lower() == MI.lower()
        and next(iter(assigned.values())) == {k: identity[k] for k in ('principalId', 'clientId')},
        'isolated_sql_job_identity_invalid')
    require(type(props) is dict and set(props) <= {'environmentId', 'configuration', 'template', 'provisioningState',
        'outboundIpAddresses', 'workloadProfileName', 'eventStreamEndpoint'}
        and props.get('environmentId') == target.ENVIRONMENT and props.get('provisioningState') == 'Succeeded'
        and props.get('workloadProfileName') in (None, 'Consumption'), 'isolated_sql_job_invalid')
    config = compatibility.copy(props.get('configuration')); wanted = expected['properties']['configuration']
    require(type(config) is dict and set(config) <= set(wanted) | {'scheduleTriggerConfig', 'eventTriggerConfig', 'dapr'}
        and all(config.get(k) is None for k in ('scheduleTriggerConfig', 'eventTriggerConfig', 'dapr')), 'isolated_sql_job_invalid')
    for key in ('scheduleTriggerConfig', 'eventTriggerConfig', 'dapr'): config.pop(key, None)
    for field in ('secrets', 'registries', 'identitySettings'):
        require(type(config.get(field)) is list and len(config[field]) == 1
            and type(config[field][0]) is dict and config[field][0].get('identity', '').lower() == MI.lower(),
            'isolated_sql_job_identity_invalid')
        config[field][0]['identity'] = MI
    for field in ('username', 'passwordSecretRef'):
        if field in config['registries'][0]:
            require(config['registries'][0][field] == '', 'isolated_sql_job_invalid'); config['registries'][0].pop(field)
    require(config == wanted, 'isolated_sql_job_invalid')
    template = compatibility.template_shadow(props.get('template'))
    resources = template['containers'][0]['resources']
    if 'ephemeralStorage' in resources:
        require(type(resources['ephemeralStorage']) is str and resources['ephemeralStorage'] == '', 'isolated_sql_job_invalid')
        del resources['ephemeralStorage']
    # ACA may scope the secret reference with a fixed provider prefix.
    for container in template['containers']:
        for env in container.get('env', []):
            if env.get('secretRef') == 'cappjob-' + target.SECRET_NAME: env['secretRef'] = target.SECRET_NAME
    require(template == expected['properties']['template'], 'isolated_sql_job_invalid')
    return dict(native_job_sha256=digest(value), definition_sha256=plan['definition_sha256'])


def reviewed_origin():
    """Exact protected rejection, not a reset/replay of the consumed install."""
    pf_raw, pf = predecessor.load('preflight.json')
    review_raw, review = predecessor.load('readback-review.json')
    intent_raw, intent = predecessor.load('job-intent.json')
    diagnostic_raw, diagnostic = predecessor.load('job-validation.json')
    require(pf_raw['protected_receipt']['sha256'] == reviewed.PREFLIGHT_SHA
        and review_raw['protected_receipt']['sha256'] == '0c4286fba5ab1649034af1dbf82da913d110c52e87324c3370b41952bcd62482'
        and diagnostic_raw['protected_receipt']['sha256'] == '19ff1341852388478a95993d0cb4d1eec13fdde18f1bede8fc593417f6059d18'
        and review['previous_preflight_receipt'] == pf_raw['protected_receipt']
        and intent['preflight_receipt'] == pf_raw['protected_receipt']
        and intent['readback_review_receipt'] == review_raw['protected_receipt']
        and intent['body'] == pf['plan']['definition']
        and diagnostic['http_status'] == 400 and diagnostic['deployment_dispatched'] is False
        and diagnostic['sql_started'] is False and diagnostic['definition_sha256'] == pf['plan']['definition_sha256']
        and diagnostic['preflight_receipt'] == pf_raw['protected_receipt']
        and not any((predecessor.FOLDER / name).exists() for name in ('job-ack.json', 'job-accepted.json', 'start-intent.json', 'start-ack.json')),
        'isolated_sql_named_origin_invalid')
    errors = diagnostic['native'].get('error', {}).get('details', [])
    require(len(errors) == 1 and errors[0].get('code') == 'ValidationForResourceFailed'
        and len(errors[0].get('details', [])) == 1
        and errors[0]['details'][0].get('code') == 'ContainerAppInvalidName'
        and len(predecessor.worker.JOB) == 33 and 2 <= len(worker.JOB) <= 32
        and worker.JOB != predecessor.worker.JOB and worker.OPERATION != predecessor.worker.OPERATION
        and worker.IDENTITY_ID == predecessor.worker.IDENTITY_ID, 'isolated_sql_named_origin_invalid')
    return pf, review, dict(preflight=pf_raw['protected_receipt'], readback=review_raw['protected_receipt'],
        consumed_install_intent=intent_raw['protected_receipt'], validation=diagnostic_raw['protected_receipt'])


def validate_definition(client, plan):
    """Validate only; the exact new job has NOT been deployed by this call."""
    definition = dict(plan['definition'], type='Microsoft.App/jobs', apiVersion='2025-07-01', name=worker.JOB)
    template = {'$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#',
        'contentVersion': '1.0.0.0', 'resources': [definition]}
    endpoint = 'https://management.azure.com/subscriptions/' + target.SUBSCRIPTION + '/resourceGroups/' + target.RESOURCE_GROUP + '/providers/Microsoft.Resources/deployments/ownertrial-sql-named-20261005/validate?api-version=2025-04-01'
    token = client.credential.get_token('https://management.azure.com/.default')
    response = client.session.request('POST', endpoint, json={'properties': {'mode': 'Incremental',
        'validationLevel': 'ProviderNoRbac', 'template': template}},
        headers={'Authorization': 'Bearer ' + token.token}, allow_redirects=False, stream=True, timeout=(5,30))
    try:
        chunks, size = [], 0
        for chunk in response.iter_content(16384):
            size += len(chunk); require(size <= MAX_BYTES, 'isolated_sql_validation_oversized'); chunks.append(chunk)
        value = json.loads(b''.join(chunks))
        require(response.status_code == 200 and type(value) is dict and 'error' not in value,
            'isolated_sql_provider_validation_failed')
        return dict(http_status=200, native=value, definition_sha256=plan['definition_sha256'], deployed=False)
    finally: response.close()


def prepare(client):
    require(not (FOLDER / 'preflight.json').exists(), 'isolated_sql_preflight_consumed')
    origin, review, receipts = reviewed_origin()
    before = host.sources(); validate_sources(review['sources'], before); host.checked_owner()
    require(client.call('GET', predecessor.JOB, '2025-07-01', missing=True) is None
        and client.call('GET', JOB, '2025-07-01', missing=True) is None, 'isolated_sql_named_target_not_absent')
    identity = predecessor.live_identity(client.call('GET', MI, '2023-01-31'))
    inventory = predecessor.roles(predecessor.Transport(), identity['principalId'], ['acr','secret'])
    for stage in ('identity','acr','secret'): predecessor.load(stage + '-accepted.json')
    source = {k: (ROOT / k).read_bytes() for k in launch.SOURCE_PATHS}
    plan = launch.build(source, {k: hashlib.sha256(v).hexdigest() for k,v in source.items()}, origin['plan']['expected_runtime'])
    provider_validation = validate_definition(client, plan)
    checks = reviewed.run_local_checks()
    require(host.sources() == before and baseline() == origin['baseline']
        and host.observe(host.Transport()) == origin['host']
        and client.call('GET', predecessor.JOB, '2025-07-01', missing=True) is None
        and client.call('GET', JOB, '2025-07-01', missing=True) is None, 'isolated_sql_named_live_drift')
    return save(dict(status='prepared_private_sql_named_trial', operation_id=worker.OPERATION,
        sources=before, plan=plan, local_checks=checks, origin_receipts=receipts,
        baseline=origin['baseline'], host=origin['host'], reused_identity=identity, reused_role_inventory=inventory,
        provider_validation=provider_validation, old_install_attempt_preserved=True,
        existing_grants_changed=False, sql_started=False, checked_at_utc=now(), step_2_complete=False), 'preflight.json')


def reserve(stage, value):
    host.deploy.archive_protection(); payload = canonical(value)
    service = BlobServiceClient('https://' + host.deploy.ACCOUNT + '.blob.core.windows.net',
        credential=AzureCliCredential(), retry_total=0, connection_timeout=5, read_timeout=10)
    name = PREFIX + stage + '-reservation.json'; blob = service.get_blob_client(host.deploy.CONTAINER, name)
    ack = blob.upload_blob(payload, length=len(payload), overwrite=False, match_condition=MatchConditions.IfMissing,
        max_concurrency=1, timeout=10, retry_total=0)
    exact = service.get_blob_client(host.deploy.CONTAINER, name, version_id=ack['version_id'])
    props = exact.get_blob_properties()
    require(props.size == len(payload) and props.version_id == ack['version_id'] and props.etag == ack['etag']
        and exact.download_blob(etag=props.etag, match_condition=MatchConditions.IfNotModified).readall() == payload,
        'isolated_sql_reservation_invalid')
    host.deploy.archive_protection()
    return dict(blob=name, sha256=hashlib.sha256(payload).hexdigest(), version_id=ack['version_id'], etag=ack['etag'])


def dispatch(client, stage):
    require(stage in STAGES and not any((FOLDER / (stage + '-' + suffix + '.json')).exists()
        for suffix in ('intent', 'ack', 'accepted')), 'isolated_sql_stage_consumed_inspect_only')
    raw, pf = load('preflight.json'); host.checked_owner()
    _, review, receipts = reviewed_origin(); validate_sources(review['sources'], pf['sources'])
    require(pf['origin_receipts'] == receipts and pf['operation_id'] == worker.OPERATION
        and pf['status'] == 'prepared_private_sql_named_trial'
        and client.call('GET', predecessor.JOB, '2025-07-01', missing=True) is None, 'isolated_sql_named_origin_drift')
    require(host.sources() == pf['sources'] and baseline() == pf['baseline']
        and host.observe(host.Transport()) == pf['host'], 'isolated_sql_preflight_drift')
    prior_stages = STAGES[:STAGES.index(stage)]
    for previous in prior_stages:
        _, accepted = load(previous + '-accepted.json')
        require(accepted['status'] == 'private_sql_effect_verified', 'isolated_sql_prior_effect_unaccepted')
    identity = None
    if stage != 'identity':
        identity = live_identity(client.call('GET', MI, '2023-01-31'))
        roles(client, identity['principalId'], ['acr', 'secret'])
    plan = pf['plan']; method, path, api, body = effect(stage, plan, None if identity is None else identity['principalId'])
    if stage == 'start':
        validate_job(client.call('GET', JOB, '2025-07-01'), plan, identity)
        inv = client.call('GET', JOB + '/executions', '2025-07-01')
        require(inv.get('value') == [] and inv.get('nextLink') in (None, ''), 'isolated_sql_execution_already_exists')
    else: require(client.call('GET', path, api, missing=True) is None, 'isolated_sql_effect_target_exists')
    value = dict(operation_id=worker.OPERATION, stage=stage, preflight_receipt=raw['protected_receipt'],
        method=method, path=path, api=api, body=body, automatic_retry=False)
    intent = save(dict(value, reservation=reserve(stage, value)), stage + '-intent.json')
    require(host.sources() == pf['sources'] and baseline() == pf['baseline']
        and host.observe(host.Transport()) == pf['host']
        and client.call('GET', predecessor.JOB, '2025-07-01', missing=True) is None,
        'isolated_sql_preflight_drift')
    # Recheck exact absence/job immediately before the ONE consumed request.
    if stage != 'start': require(client.call('GET', path, api, missing=True) is None, 'isolated_sql_effect_target_exists')
    else:
        validate_job(client.call('GET', JOB, '2025-07-01'), plan, identity)
        inv = client.call('GET', JOB + '/executions', '2025-07-01')
        require(inv.get('value') == [] and inv.get('nextLink') in (None, ''), 'isolated_sql_execution_already_exists')
    ack = client.call(method, path, api, body=body, stage=stage, plan=plan,
        principal=None if identity is None else identity['principalId'])
    return save(dict(status='private_sql_effect_acknowledged_inspect_required', stage=stage,
        operation_id=worker.OPERATION, intent_receipt=intent['protected_receipt'], raw_ack=ack,
        attempt_consumed=True, automatic_retry=False, checked_at_utc=now()), stage + '-ack.json')


def inspect(client, stage):
    raw, pf = load('preflight.json'); ack_raw, ack = load(stage + '-ack.json')
    _, review, receipts = reviewed_origin(); validate_sources(review['sources'], pf['sources'])
    require(pf['origin_receipts'] == receipts and pf['operation_id'] == worker.OPERATION
        and client.call('GET', predecessor.JOB, '2025-07-01', missing=True) is None, 'isolated_sql_named_origin_drift')
    intent_raw, intent = load(stage + '-intent.json')
    require(ack['intent_receipt'] == intent_raw['protected_receipt'] and intent['preflight_receipt'] == raw['protected_receipt']
        and intent['stage'] == stage and intent['operation_id'] == worker.OPERATION
        and host.sources() == pf['sources'], 'isolated_sql_chain_invalid')
    host.checked_owner(); require(baseline() == pf['baseline'], 'isolated_sql_baseline_changed')
    identity = live_identity(client.call('GET', MI, '2023-01-31'))
    wanted_stages = ['acr', 'secret']
    inventory = roles(client, identity['principalId'], wanted_stages)
    if stage == 'job': validate_job(client.call('GET', JOB, '2025-07-01'), pf['plan'], identity)
    elif stage == 'start':
        execution = execution_name(ack['raw_ack'].get('name'))
        require(ack['raw_ack'].get('id') == JOB + '/executions/' + execution, 'isolated_sql_execution_invalid')
        observation = client.call('GET', JOB + '/executions/' + execution, '2025-07-01')
        return dict(status='private_sql_execution_observed', execution=execution, native=observation, step_2_complete=False)
    return save(dict(status='private_sql_effect_verified', stage=stage, operation_id=worker.OPERATION,
        preflight_receipt=raw['protected_receipt'], ack_receipt=ack_raw['protected_receipt'],
        identity=identity, role_inventory=inventory, checked_at_utc=now(), step_2_complete=False), stage + '-accepted.json')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('command', choices=('prepare', 'dispatch', 'inspect'))
    parser.add_argument('--stage', choices=STAGES); args = parser.parse_args(); FOLDER.mkdir(parents=True, exist_ok=True)
    try:
        client = Transport()
        result = prepare(client) if args.command == 'prepare' else dispatch(client, args.stage) if args.command == 'dispatch' else inspect(client, args.stage)
        print(json.dumps({k: result[k] for k in ('status', 'stage', 'execution', 'protected_receipt', 'native') if k in result}, sort_keys=True))
    except Exception as error:
        code = str(error); code = code if re.fullmatch('isolated_sql_[a-z_]+', code) else 'isolated_sql_unaccepted_inspect_only'
        print(json.dumps(dict(status='unaccepted', code=code, automatic_retry=False)), flush=True)
        raise SystemExit(1) from None


if __name__ == '__main__': main()
