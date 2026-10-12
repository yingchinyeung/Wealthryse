"""Prepare ONE idle test-host catalog job; no START, SQL, model or secret reads.

Trusted owner controller, not a specialist tool. Exact effect reservations are
create-only and protected; an uncertain outcome is inspected, never retried.
The admin test credential is resolved by ACA only after a later reviewed START.
No production credentials, role changes, SQL setup or agent activation routes.
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

import owner_task_catalog_launch_v2 as launch
import qualify_owner_task_aca_container_identity as runtime
import qualify_owner_task_isolated_host as host

ROOT = host.deploy.ROOT
FOLDER = ROOT / '.work/owner-task-isolated-catalog-20261005'
PREFIX = 'probes/owner-codex-isolated-catalog-20261005/'
KIND = 'isolated-catalog-preparation-'
OPERATION = 'c15c9e2d-0aa0-493c-b781-7d81f85d3988'
SUB = launch.registry.SUBSCRIPTION
SUB_SCOPE = '/subscriptions/' + SUB
BASE = SUB_SCOPE + '/resourceGroups/' + launch.registry.RESOURCE_GROUP + '/providers/'
MI, JOB = launch.registry.IDENTITY_ID, launch.registry.JOB_ID
ACR = BASE + 'Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo'
SECRET = BASE + 'Microsoft.KeyVault/vaults/ibkrv2stg-auhecwc4itaoo/secrets/' + launch.registry.SECRET_NAME
ROLE_BASE = SUB_SCOPE + '/providers/Microsoft.Authorization/roleDefinitions/'
ROLES = {
    'acr': (ACR, '7f951dda-4ed3-4680-a7ca-43fe172d538d', 'a3b859c9-6a61-448e-a0dd-662f46c60e52', 'AcrPull'),
    'secret': (SECRET, '4633458b-17de-408a-b874-0445c86b69e6', 'd21b25b3-3e3b-401a-a892-ab7ba8832937', 'Key Vault Secrets User'),
}
STAGES = ('identity', 'acr', 'secret', 'job')
ADDITIONS = frozenset(('scripts/owner_task_catalog_launch_v2.py',
    'scripts/owner_task_catalog_complete_logs.py', 'tests/test_owner_task_catalog_step2.py',
    'scripts/prepare_owner_task_isolated_catalog.py', 'tests/test_owner_task_isolated_catalog_preparation.py'))
require, canonical, digest = launch.require, launch.canonical, launch.digest
MAX_BYTES = 2 * 1024 * 1024


def now(): return datetime.now(timezone.utc).isoformat()


def validate_sources(previous, current):
    require(type(previous) is dict and len(previous) == 743 and type(current) is dict
        and set(current) == set(previous) | ADDITIONS and not set(previous) & ADDITIONS
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v)
            for values in (previous, current) for k, v in values.items())
        and all(current[k] == v for k, v in previous.items()), 'catalog_only_additive_sources_required')


def assignment_id(stage):
    require(type(stage) is str and stage in ROLES, 'catalog_stage_invalid')
    scope, _, identifier, _ = ROLES[stage]
    return scope + '/providers/Microsoft.Authorization/roleAssignments/' + identifier


def role_body(stage, principal):
    launch.prior._guid(principal)
    _, role, _, _ = ROLES[stage]
    return dict(properties=dict(roleDefinitionId=ROLE_BASE + role, principalId=principal, principalType='ServicePrincipal'))


def identity_body():
    return dict(location='eastasia', tags=dict(purpose='owner-isolated-provider-catalog', operation=OPERATION))


def effect(stage, plan, principal=None):
    require(type(stage) is str and stage in STAGES, 'catalog_stage_invalid')
    if stage == 'identity': return MI, '2023-01-31', identity_body()
    if stage in ROLES: return assignment_id(stage), '2022-04-01', role_body(stage, principal)
    return JOB, '2025-07-01', plan['definition']


class Transport:
    """Fixed ARM reads/PUT only. No KV data-plane, POST, DELETE or retries."""
    def __init__(self, credential=None, session=None):
        self.credential = credential or AzureCliCredential()
        self.session = requests.Session() if session is None else session
        if session is None:
            self.session.mount('https://', HTTPAdapter(max_retries=0)); self.session.trust_env = False
        self.token = None

    def call(self, method, path, api, *, body=None, missing=False, principal=None, stage=None, plan=None):
        route = path + '?api-version=' + api
        reads = {MI: '2023-01-31', JOB: '2025-07-01'}
        reads.update({assignment_id(s): '2022-04-01' for s in ROLES})
        reads.update({ROLE_BASE + r[1]: '2022-04-01' for r in ROLES.values()})
        if method == 'GET':
            require(body is None, 'catalog_route_invalid')
            if path == SUB_SCOPE + '/providers/Microsoft.Authorization/roleAssignments':
                launch.prior._guid(principal); require(api == '2022-04-01', 'catalog_route_invalid')
                route += "&$filter=principalId eq '" + principal + "'"
            else: require(reads.get(path) == api, 'catalog_route_invalid')
        elif method == 'PUT':
            wanted_path, wanted_api, wanted_body = effect(stage, plan, principal)
            require(path == wanted_path and api == wanted_api and canonical(body) == canonical(wanted_body), 'catalog_route_invalid')
        else: raise ValueError('catalog_route_invalid')
        response = None
        try:
            if self.token is None or self.token.expires_on <= time.time() + 120:
                self.token = self.credential.get_token('https://management.azure.com/.default')
            response = self.session.request(method, 'https://management.azure.com' + route,
                json=body, headers={'Authorization': 'Bearer ' + self.token.token},
                allow_redirects=False, stream=True, timeout=(5, 20))
            status = response.status_code
            if method == 'GET' and missing and status == 404: return None, status
            require(status in ((200,) if method == 'GET' else (200, 201, 202)), 'catalog_http_unaccepted')
            chunks, count = [], 0
            for chunk in response.iter_content(chunk_size=16384):
                require(type(chunk) is bytes, 'catalog_http_unaccepted'); count += len(chunk)
                require(count <= MAX_BYTES, 'catalog_http_oversized'); chunks.append(chunk)
            result = json.loads(b''.join(chunks))
            require(type(result) is dict, 'catalog_http_unaccepted'); return result, status
        except Exception: raise ValueError('catalog_http_outcome_requires_inspection') from None
        finally:
            if response is not None: response.close()


def save(value, name):
    require(type(name) is str and re.fullmatch('[a-z][a-z-]*\\.json', name)
        and 0 < len(canonical(value)) <= MAX_BYTES, 'catalog_evidence_invalid')
    with (FOLDER / name).open('x', encoding='utf-8') as output:
        host.deploy.archive_protection()
        raw = dict(value, protected_receipt=host.deploy.retain(value, 'owner-task-pg-' + KIND + name[:-5]))
        output.write(json.dumps(raw, sort_keys=True, indent=2))
    host.deploy.protected(raw, KIND + name[:-5]); return raw


def load(name):
    raw = json.loads((FOLDER / name).read_text(encoding='utf-8'))
    return raw, host.deploy.protected(raw, KIND + name[:-5])


def reserve(stage, value):
    require(stage in STAGES, 'catalog_stage_invalid'); host.deploy.archive_protection()
    blob = BlobServiceClient('https://' + host.deploy.ACCOUNT + '.blob.core.windows.net',
        credential=AzureCliCredential(), retry_total=0, connection_timeout=5, read_timeout=10).get_blob_client(
            host.deploy.CONTAINER, PREFIX + stage + '-reservation.json')
    payload = canonical(value)
    ack = blob.upload_blob(payload, length=len(payload), overwrite=False, match_condition=MatchConditions.IfMissing,
        max_concurrency=1, timeout=10, retry_total=0)
    require(type(ack.get('version_id')) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', ack['version_id']), 'catalog_reservation_invalid')
    exact = BlobServiceClient('https://' + host.deploy.ACCOUNT + '.blob.core.windows.net',
        credential=AzureCliCredential(), retry_total=0).get_blob_client(host.deploy.CONTAINER,
            PREFIX + stage + '-reservation.json', version_id=ack['version_id'])
    props = exact.get_blob_properties()
    require(props.size == len(payload) and props.version_id == ack['version_id'] and props.etag == ack['etag']
        and exact.download_blob(etag=props.etag, match_condition=MatchConditions.IfNotModified).readall() == payload,
        'catalog_reservation_invalid')
    host.deploy.archive_protection()
    return dict(blob=PREFIX + stage + '-reservation.json', sha256=hashlib.sha256(payload).hexdigest(),
        version_id=ack['version_id'], etag=ack['etag'], protected_readback_verified=True)


def baseline():
    value = host.deploy.baseline()
    value['other_jobs'].pop(launch.registry.JOB, None)
    require(value.get('job_inventory_complete') is True, 'catalog_baseline_incomplete')
    return value


def foundation():
    pf_raw, pf = runtime.load('preflight.json'); raw, accepted = runtime.load('acceptance.json')
    require((raw['protected_receipt']['sha256'], raw['protected_receipt']['version_id']) ==
        ('fec9ae19101fd9645bd2672b028a53862ef6510a988239ce344d3c0fa8fcbe30', '2026-10-05T00:16:16.3714121Z')
        and accepted['status'] == 'secretless_aca_container_identity_runtime_verified'
        and accepted['preflight_receipt'] == pf_raw['protected_receipt'], 'catalog_accepted_runtime_required')
    validate_sources(pf['sources'], host.sources())
    live = runtime.guards(pf['prior']['pf'], pf['prior']['prior_executions'], 'ibkrv2stg-ownerruntime-20261004-694nva8')
    require(live['job'] == accepted['current_job'] and live['roles'] == accepted['current_roles'], 'catalog_runtime_changed')
    host_raw, host_accept = host.load('host-acceptance.json')
    require(host_raw['protected_receipt']['sha256'] == 'e8e103df668996a00fe8d07a9e45f71f23078c358d6586f97609817d27f92589'
        and host_accept['status'] == 'new_private_host_verified_sql_pending', 'catalog_isolated_host_required')
    host_observed = host.observe(host.Transport())
    # Validator enforces exact private physical scope; this is metadata, not TLS proof.
    require(host_observed['server_exists'] is True and host_observed['server']['ready_observed'] is True
        and host_observed['new_secret_versions'] == [launch.prior.SECRET_VERSION_URL], 'catalog_private_host_ready_required')
    sources = {name: (ROOT / name).read_bytes() for name in launch.prior.SOURCE_PATHS}
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in sources.items()}
    plan = launch.build(sources, hashes, pf['prior']['pf']['prerequisite']['expected_runtime'])
    return dict(runtime_receipt=raw['protected_receipt'], host_receipt=host_raw['protected_receipt'],
        host_observed=host_observed, plan=plan), sources, hashes


def identity(value):
    require(type(value) is dict and value.get('id') in (MI, MI.replace('/resourceGroups/', '/resourcegroups/'))
        and value.get('name') == MI.rsplit('/', 1)[1]
        and value.get('location') in ('eastasia', 'East Asia') and value.get('tags') == identity_body()['tags']
        and value.get('type') == 'Microsoft.ManagedIdentity/userAssignedIdentities'
        and type(value.get('properties')) is dict, 'catalog_identity_invalid')
    props = value['properties']; require(set(props) == {'tenantId', 'principalId', 'clientId'}, 'catalog_identity_invalid')
    require(props['tenantId'] == '0578a50b-a7a1-40a4-905b-8b20dba84124', 'catalog_identity_invalid')
    launch.prior._guid(props['principalId']); launch.prior._guid(props['clientId']); return props


def role_inventory(client, principal, stages):
    value, _ = client.call('GET', SUB_SCOPE + '/providers/Microsoft.Authorization/roleAssignments',
        '2022-04-01', principal=principal)
    require(type(value) is dict and set(value) <= {'value', 'nextLink'} and value.get('nextLink') in (None, '')
        and type(value.get('value')) is list and len(value['value']) == len(stages), 'catalog_role_inventory_invalid')
    rows = value['value']; require(len({row.get('id') for row in rows}) == len(rows), 'catalog_role_inventory_invalid')
    for stage in stages:
        matches = [row for row in rows if row.get('id', '').casefold() == assignment_id(stage).casefold()]
        require(len(matches) == 1, 'catalog_role_inventory_invalid'); p = matches[0].get('properties', {})
        require(p.get('principalId') == principal and p.get('principalType') == 'ServicePrincipal'
            and p.get('scope', '').casefold() == ROLES[stage][0].casefold()
            and p.get('roleDefinitionId', '').casefold() == (ROLE_BASE + ROLES[stage][1]).casefold()
            and p.get('condition') in (None, ''), 'catalog_role_inventory_invalid')
    return rows


def prepare(client):
    require(not (FOLDER / 'preflight.json').exists(), 'catalog_preflight_consumed')
    host.checked_owner(); sources_before = host.sources(); found, _, _ = foundation(); before = baseline()
    require(client.call('GET', MI, '2023-01-31', missing=True)[0] is None
        and client.call('GET', JOB, '2025-07-01', missing=True)[0] is None, 'catalog_new_resources_required')
    definitions = {}
    for stage, row in ROLES.items():
        require(client.call('GET', assignment_id(stage), '2022-04-01', missing=True)[0] is None, 'catalog_assignment_exists')
        value, _ = client.call('GET', ROLE_BASE + row[1], '2022-04-01')
        require(value.get('properties', {}).get('roleName') == row[3]
            and value['properties'].get('type') == 'BuiltInRole', 'catalog_builtin_role_required')
        definitions[stage] = value
    checks = runtime.old.n.run_local_checks()
    require(host.sources() == sources_before and baseline() == before, 'catalog_preflight_drift')
    return save(dict(status='prepared_idle_test_host_catalog_only', operation_id=OPERATION,
        checked_at_utc=now(), sources=sources_before, foundation=found, baseline=before,
        role_definitions=definitions, local_checks=checks, sql_calls=0, model_calls=0,
        secret_values_read=0, production_changed=False, fresh_restricted_login_verified=False,
        owner_runtime_active=False, six_specialist_runtimes_active=False), 'preflight.json')


def frozen(client):
    raw, pf = load('preflight.json'); found, sources, hashes = foundation()
    require(pf['status'] == 'prepared_idle_test_host_catalog_only' and pf['operation_id'] == OPERATION
        and pf['sources'] == host.sources() and pf['baseline'] == baseline()
        and pf['foundation']['runtime_receipt'] == found['runtime_receipt']
        and pf['foundation']['host_receipt'] == found['host_receipt']
        and pf['foundation']['plan'] == found['plan'], 'catalog_exact_preflight_required')
    runtime.old.previous.q.canary.validate_correction_checks(pf['local_checks'])
    for stage, row in ROLES.items():
        value, _ = client.call('GET', ROLE_BASE + row[1], '2022-04-01')
        require(value == pf['role_definitions'][stage], 'catalog_builtin_role_changed')
    return raw, pf, sources, hashes


def run_stage(client, stage, *, verify=False):
    require(stage in STAGES and type(verify) is bool, 'catalog_stage_invalid'); host.checked_owner()
    raw, pf, sources, hashes = frozen(client); plan = pf['foundation']['plan']
    prior = STAGES[:STAGES.index(stage)]
    for old in prior: load(old + '-accepted.json')
    principal = None
    if stage != 'identity':
        mi, _ = client.call('GET', MI, '2023-01-31'); props = identity(mi); principal = props['principalId']
        _, accepted = load('identity-accepted.json')
        require(accepted['observation'] == mi, 'catalog_identity_changed')
        role_inventory(client, principal, [s for s in prior if s in ROLES]) if not verify else None
    path, api, body = effect(stage, plan, principal)
    if not verify:
        require(not any((FOLDER / (stage + suffix)).exists() for suffix in ('-intent.json', '-ack.json', '-accepted.json')),
            'catalog_effect_consumed_inspect_only')
        require(client.call('GET', path, api, missing=True)[0] is None, 'catalog_new_target_required')
        intent = dict(operation_id=OPERATION, stage=stage, method='PUT', path=path, api=api,
            body=body, preflight_receipt=raw['protected_receipt'], created_at_utc=now(), automatic_retry=False)
        reservation = reserve(stage, intent); intent_raw = save(dict(intent, reservation=reservation), stage + '-intent.json')
        require(client.call('GET', path, api, missing=True)[0] is None and baseline() == pf['baseline'], 'catalog_final_absence_required')
        value, status = client.call('PUT', path, api, body=body, principal=principal, stage=stage, plan=plan)
        if stage == 'job':
            config = value.get('properties', {}).get('configuration', {})
            require(type(config) is dict and type(config.get('secrets')) is list
                and all(type(row) is dict and row.get('value') in (None, '') for row in config['secrets']),
                'catalog_secret_value_response_rejected')
        return save(dict(status='put_acknowledged_inspection_required', operation_id=OPERATION, stage=stage,
            http_status=status, observation=value, intent_receipt=intent_raw['protected_receipt'],
            automatic_retry=False), stage + '-ack.json')
    ack_raw, ack = load(stage + '-ack.json'); intent_raw, intent = load(stage + '-intent.json')
    require(ack['intent_receipt'] == intent_raw['protected_receipt'] and intent['preflight_receipt'] == raw['protected_receipt']
        and intent['body'] == body and intent['path'] == path and intent['stage'] == stage
        and intent['automatic_retry'] is False and ack['automatic_retry'] is False, 'catalog_exact_effect_chain_required')
    value, _ = client.call('GET', path, api)
    if stage == 'identity':
        props = identity(value); role_inventory(client, props['principalId'], [])
    elif stage in ROLES: role_inventory(client, principal, [s for s in (*prior, stage) if s in ROLES])
    else:
        launch.validate_job(value, sources, hashes, plan['expected_runtime'], principal_id=principal, client_id=props['clientId'])
        role_inventory(client, principal, ['acr', 'secret'])
    require(baseline() == pf['baseline'], 'catalog_baseline_changed')
    record = dict(status='idle_catalog_preparation_effect_verified', operation_id=OPERATION, stage=stage,
        observation=value, ack_receipt=ack_raw['protected_receipt'], checked_at_utc=now(),
        job_started=False, sql_calls=0, model_calls=0, secret_values_read=0,
        provider_semantics_verified=False, fresh_restricted_login_verified=False,
        production_changed=False, owner_runtime_active=False, six_specialist_runtimes_active=False)
    if (FOLDER / (stage + '-accepted.json')).exists():
        _, accepted = load(stage + '-accepted.json')
        require(all(accepted[k] == v for k, v in record.items() if k != 'checked_at_utc'), 'catalog_accepted_effect_changed')
        return dict(status='fresh_idle_catalog_preparation_verified', stage=stage)
    return save(record, stage + '-accepted.json')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage', choices=('prepare',) + STAGES)
    parser.add_argument('--verify', action='store_true'); args = parser.parse_args()
    require(args.stage != 'prepare' or not args.verify, 'catalog_stage_invalid')
    FOLDER.mkdir(parents=True, exist_ok=True); client = Transport()
    result = prepare(client) if args.stage == 'prepare' else run_stage(client, args.stage, verify=args.verify)
    print(json.dumps({k: result[k] for k in ('status', 'stage', 'protected_receipt') if k in result}, sort_keys=True))


if __name__ == '__main__': main()
