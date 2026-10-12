"""Reviewed one-shot Step 3 synthetic SQL exercise; never reuses consumed jobs.

Only the new fixed job and its one execution can be written. The SQL worker's
new database/roles are on the existing private test host. Each effect requires
protected intent and create-only reservation; no automatic retries/deletion.
Predecessor files, job definitions, executions and direct grants are preserved.
"""
from __future__ import annotations

import argparse
import base64
import copy
from datetime import datetime, timedelta
import hashlib
import json
import lzma
import re
import time

from azure.core import MatchConditions
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import requests
from requests.adapters import HTTPAdapter

import qualify_owner_task_execution_fence as previous
import owner_task_isolated_sql_root_launch as bundle
import owner_task_provider_launch_contract as image
import run_owner_task_execution_fence_trial as worker

p = previous.prior
ROOT = p.old.ROOT
JOB = worker.JOB_ID
MI = p.old.prior.MI
PREFIX = 'probes/owner-codex-execution-fence-20261005/'
ADDITIONS = frozenset((worker.PATH, worker.CONSTRAINT_PATH,
    'scripts/deploy_owner_task_execution_fence_trial.py', 'tests/test_owner_task_execution_fence_trial.py'))
# Bundle only helpers used by this new worker; the predecessor permission
# installer and SQL test harness are not executed or shipped in this trial.
MODULES = tuple(pair for pair in bundle.SOURCE_MODULES if pair[1] not in (
    'run_owner_task_isolated_sql_root', 'run_owner_task_isolated_trial', 'owner_task_isolated_permissions')) + (
    ('src/ibkr_etl/owner_codex_worker.py', 'ibkr_etl.owner_codex_worker'),
    ('src/ibkr_etl/owner_codex_execution_fence.py', 'ibkr_etl.owner_codex_execution_fence'),
    (worker.PATH, 'run_owner_task_execution_fence_trial'))
PATHS = tuple(path for path, _ in MODULES) + (worker.DDL_PATH, worker.CONSTRAINT_PATH)
MAX_BYTES = 2 * 1024 * 1024


def require(value, code):
    worker.require(value, code)


def digest(value):
    return hashlib.sha256(worker.canonical(value)).hexdigest()


def sources_valid(before, after):
    require(type(before) is dict and len(before) == 807 and type(after) is dict
        and set(after) == set(before) | ADDITIONS and not set(before) & ADDITIONS
        and all(after[k] == v for k, v in before.items())
        and all(type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for v in after.values()), 'fence_source_drift')


def build(source, hashes, runtime):
    require(set(source) == set(hashes) == set(PATHS) and sum(len(v) for v in source.values()) <= 512 * 1024
        and all(type(v) is bytes and hashlib.sha256(v).hexdigest() == hashes[k] for k, v in source.items()), 'fence_bundle_invalid')
    require(hashes[worker.DDL_PATH] == worker.DDL_SHA, 'fence_schema_source_invalid')
    image.runtime.validate_descriptor(runtime)
    decoded = {k: v.decode('utf-8') for k, v in source.items()}
    for path, value in decoded.items():
        require('\x00' not in value, 'fence_bundle_invalid')
        if path.endswith('.py'):
            compile(value, path, 'exec')
    launcher = bundle.LAUNCHER.replace(
        'sys.modules["run_owner_task_isolated_sql_root"].main(_secret,_sources[_migration],connect=psycopg.connect)',
        'sys.modules["run_owner_task_execution_fence_trial"].main(_secret,_sources[_ddl],_sources[_constraints],connect=psycopg.connect)')
    require('run_owner_task_isolated_sql_root' not in launcher
        and 'run_owner_task_execution_fence_trial' in launcher, 'fence_launcher_invalid')
    launcher = launcher.replace('OWNER_TASK_ISOLATED_SQL_ROOT_LAUNCH_FAILURE_V1 ', 'OWNER_TASK_EXECUTION_FENCE_LAUNCH_FAILURE_V1 ')
    script = '_sources=' + repr(decoded) + '\n_hashes=' + repr(hashes) + '\n_modules=' + repr(MODULES)
    script += '\n_runtime=' + repr(runtime) + '\n_ddl=' + repr(worker.DDL_PATH) + '\n_constraints=' + repr(worker.CONSTRAINT_PATH) + '\n' + launcher
    packed = base64.b85encode(lzma.compress(script.encode(), preset=9)).decode('ascii').replace(chr(36), ':')
    args = ['-I', '-B', '-c', "import base64,lzma;exec(compile(lzma.decompress(base64.b85decode('" + packed
        + "'.replace(':',chr(36)))),'owner-fence-isolated-launch','exec'))"]
    require(len(worker.canonical(args)) <= 65536 and chr(36) not in worker.canonical(args).decode(), 'fence_argv_invalid')
    definition = dict(location=worker.host.LOCATION,
        identity=dict(type='UserAssigned', userAssignedIdentities={MI: {}}),
        tags=dict(purpose='owner-execution-fence-trial', operation=worker.OPERATION,
            sourceSha256=digest(hashes), fullSourceSha256=hashlib.sha256(script.encode()).hexdigest()),
        properties=dict(environmentId=worker.host.ENVIRONMENT,
            configuration=dict(triggerType='Manual', replicaTimeout=600, replicaRetryLimit=0,
                identitySettings=[dict(identity=MI, lifecycle='None')],
                manualTriggerConfig=dict(parallelism=1, replicaCompletionCount=1),
                secrets=[dict(name=worker.host.SECRET_NAME, keyVaultUrl=image.SECRET_VERSION_URL, identity=MI)],
                registries=[dict(server=image.REGISTRY_SERVER, identity=MI)]),
            template=dict(containers=[dict(name='execution-fence-trial', image=image.IMAGE, command=['python'],
                args=args, env=[dict(name='OWNERTRIAL_ADMIN_URL', secretRef=worker.host.SECRET_NAME)],
                resources=dict(cpu=.5, memory='1Gi'))])))
    return dict(definition=definition, definition_sha256=digest(definition), source_hashes=hashes,
        expected_runtime=runtime, full_script_sha256=hashlib.sha256(script.encode()).hexdigest())


class Transport:
    def __init__(self):
        self.credential = AzureCliCredential()
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount('https://', HTTPAdapter(max_retries=0))
        self.token = None

    def call(self, method, path, *, body=None, stage=None, plan=None, missing=False):
        allowed = {JOB, JOB + '/executions'}
        if path.startswith(JOB + '/executions/'):
            name = path.removeprefix(JOB + '/executions/')
            require(re.fullmatch(re.escape(worker.JOB) + '-[a-z0-9]{7,12}', name), 'fence_execution_invalid')
            allowed.add(path)
        if method == 'GET':
            require(path in allowed and body is None, 'fence_route_invalid')
        else:
            require(plan is not None and plan['definition_sha256'] == digest(plan['definition']), 'fence_plan_invalid')
            expected = ('PUT', JOB, plan['definition']) if stage == 'job' else (
                'POST', JOB + '/start', plan['definition']['properties']['template']) if stage == 'start' else None
            require(expected is not None and (method, path, body) == expected, 'fence_route_invalid')
        if self.token is None or self.token.expires_on < time.time() + 120:
            self.token = self.credential.get_token('https://management.azure.com/.default')
        with self.session.request(method, 'https://management.azure.com' + path, params={'api-version': '2025-07-01'},
            json=body, headers={'Authorization': 'Bearer ' + self.token.token}, allow_redirects=False,
            stream=True, timeout=(5, 30)) as response:
            if missing and method == 'GET' and response.status_code == 404:
                return None
            require(response.status_code in ((200,) if method == 'GET' else (200, 201, 202)), 'fence_http_unaccepted')
            raw = bytearray()
            for part in response.iter_content(65536):
                raw.extend(part)
                require(len(raw) <= MAX_BYTES, 'fence_response_oversized')
            result = json.loads(raw)
            require(type(result) is dict, 'fence_response_invalid')
            return result


def guards(pf):
    require(p.old.prior.host.sources() == pf['sources'], 'fence_source_drift')
    sources_valid(pf['predecessor_sources'], pf['sources'])
    _, predecessor = p.load()
    p.old.prior.host.checked_owner()
    baseline = p.old.baseline()
    # Preserve the predecessor's explicitly registered exclusions, not a
    # wildcard for new jobs. Each excluded predecessor is checked below.
    for name in (p.w.JOB, 'ibkrv2stg-ownmid-20261005', worker.JOB):
        baseline['other_jobs'].pop(name, None)
    require(baseline == predecessor['baseline'] and p.old.prior.host.observe(p.old.prior.host.Transport()) == predecessor['host']
        and p.old.frozen_native_jobs(None) == predecessor['frozen_native_jobs'], 'fence_predecessor_resource_drift')
    client = p.Transport()
    require(digest(client.call('GET', p.DIAG)) == predecessor['diagnostic_native_job_sha256'], 'fence_predecessor_resource_drift')
    p.native(client.call('GET', p.JOB), predecessor['plan'], predecessor['registered_identity'])
    run = client.call('GET', p.JOB + '/executions/ibkrv2stg-ownmod2-20261005-2u4sv88')
    require(run['properties']['status'] == 'Succeeded', 'fence_predecessor_execution_drift')
    model = p.old.Transport()
    registered = p.old.identity(model.call('GET', p.old.MI, '2023-01-31'))
    require(registered == predecessor['registered_identity'], 'fence_model_identity_drift')
    p.old.direct_roles(model, registered['principalId'], ['acr', 'ai'])
    sql_client = p.old.prior.parent.Transport()
    identity = p.old.prior.parent.live_identity(sql_client.call('GET', MI, '2023-01-31'))
    require(identity == predecessor['sql_identity'], 'fence_sql_identity_drift')
    p.old.prior.parent.roles(sql_client, identity['principalId'], ['acr', 'secret'])
    require(p.old.prior.Transport().call('GET', p.old.prior.JOB + '/executions/ibkrv2stg-ownobjv-20261005-n9y23ym') == predecessor['provider_native_execution'], 'fence_provider_execution_drift')
    return identity


def checked_job(value, plan, identity):
    expected = plan['definition']
    require(value.get('id') == JOB and value.get('name') == worker.JOB and value.get('type') == 'Microsoft.App/jobs'
        and value.get('location') in ('eastasia', 'East Asia') and value.get('tags') == expected['tags']
        and set(value) <= {'id', 'name', 'type', 'location', 'tags', 'identity', 'properties', 'systemData'}, 'fence_native_job_invalid')
    assigned = value['identity'].get('userAssignedIdentities')
    require(value['identity'].get('type') == 'UserAssigned' and type(assigned) is dict and len(assigned) == 1
        and value['identity'].get('principalId') is None and value['identity'].get('tenantId') is None
        and set(value['identity']) <= {'type', 'principalId', 'tenantId', 'userAssignedIdentities'}
        and next(iter(assigned)).casefold() == MI.casefold()
        and next(iter(assigned.values())) == {k: identity[k] for k in ('clientId', 'principalId')}, 'fence_native_identity_invalid')
    props = value['properties']
    require(props.get('provisioningState') == 'Succeeded' and props.get('environmentId') == expected['properties']['environmentId']
        and props.get('workloadProfileName') in (None, 'Consumption')
        and set(props) <= {'environmentId', 'configuration', 'template', 'provisioningState',
            'outboundIpAddresses', 'workloadProfileName', 'eventStreamEndpoint'}, 'fence_native_job_invalid')
    config = copy.deepcopy(props['configuration'])
    for field in ('scheduleTriggerConfig', 'eventTriggerConfig', 'dapr'):
        require(config.pop(field, None) is None, 'fence_native_job_invalid')
    for field in ('secrets', 'registries', 'identitySettings'):
        require(len(config[field]) == 1 and config[field][0]['identity'].casefold() == MI.casefold(), 'fence_native_identity_invalid')
        config[field][0]['identity'] = MI
    for field in ('username', 'passwordSecretRef'):
        if field in config['registries'][0]:
            require(config['registries'][0].pop(field) == '', 'fence_native_job_invalid')
    require(config == expected['properties']['configuration'], 'fence_native_job_invalid')
    template = checked_template(props['template'], expected['properties']['template'])
    return dict(native_sha256=digest(value), definition_sha256=plan['definition_sha256'])


def checked_template(value, expected):
    template = copy.deepcopy(value)
    require(type(template) is dict, 'fence_native_template_invalid')
    for field in ('initContainers', 'volumes'):
        require(template.pop(field, None) is None, 'fence_native_job_invalid')
    template = p.old.prior.parent.compatibility.template_shadow(template)
    resources = template['containers'][0]['resources']
    if 'ephemeralStorage' in resources:
        require(resources.pop('ephemeralStorage') == '', 'fence_native_job_invalid')
    for env in template['containers'][0].get('env', []):
        if env.get('secretRef') == 'cappjob-' + worker.host.SECRET_NAME:
            env['secretRef'] = worker.host.SECRET_NAME
    require(template == expected, 'fence_native_template_invalid')
    return template


def save(value, name):
    return p.old.save(value, 'serviceaud-step3-fence-' + name + '.json')


def load(name):
    return p.old.load('serviceaud-step3-fence-' + name + '.json')


def prepare(client):
    require(not (p.old.FOLDER / 'serviceaud-step3-fence-live-preflight.json').exists(), 'fence_preflight_consumed')
    sr, started = p.old.load('serviceaud-step3-live-start.json')
    require(sr['protected_receipt']['sha256'] == 'ced04b4c3581dd37f122e12769bd57064e7f028d87565d5fda7aa9416956cb53'
        and started['step_3_complete'] is False and started['step_4_started'] is False, 'fence_owner_instruction_invalid')
    _, earlier = p.old.load('serviceaud-step3-fence-readback.json')
    _, sql_preflight = p.old.prior.load('preflight.json')
    sources = p.old.prior.host.sources()
    sources_valid(earlier['sources'], sources)
    source = {path: (ROOT / path).read_bytes() for path in PATHS}
    hashes = {path: hashlib.sha256(data).hexdigest() for path, data in source.items()}
    runtime = sql_preflight['plan']['expected_runtime']
    pf = dict(status='step_3_isolated_fence_live_trial_prepared', operation_id=worker.OPERATION,
        sources=sources, predecessor_sources=earlier['sources'], start_receipt=sr['protected_receipt'],
        plan=build(source, hashes, runtime), step_3_complete=False, step_4_started=False,
        production_changed=False, received_ibkr_rows_read=0, received_ibkr_rows_modified=0)
    identity = guards(pf)
    require(client.call('GET', JOB, missing=True) is None, 'fence_job_exists')
    checks = p.old.prior.parent.reviewed.run_local_checks()
    require(guards(pf) == identity and client.call('GET', JOB, missing=True) is None, 'fence_preflight_drift')
    return save(dict(pf, sql_identity=identity, local_checks=checks, checked_at_utc=p.old.now()), 'live-preflight')


def reserve(stage, value):
    p.old.prior.host.deploy.archive_protection()
    service = BlobServiceClient('https://' + p.old.prior.host.deploy.ACCOUNT + '.blob.core.windows.net',
        credential=AzureCliCredential(), retry_total=0, connection_timeout=5, read_timeout=10)
    name = PREFIX + stage + '-reservation.json'
    data = worker.canonical(value)
    blob = service.get_blob_client(p.old.prior.host.deploy.CONTAINER, name)
    ack = blob.upload_blob(data, length=len(data), overwrite=False, match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10)
    exact = service.get_blob_client(p.old.prior.host.deploy.CONTAINER, name, version_id=ack['version_id'])
    props = exact.get_blob_properties()
    require(props.size == len(data) and props.etag == ack['etag'] and props.version_id == ack['version_id']
        and exact.download_blob(etag=props.etag, match_condition=MatchConditions.IfNotModified).readall() == data, 'fence_reservation_unverified')
    p.old.prior.host.deploy.archive_protection()
    return dict(blob=name, sha256=hashlib.sha256(data).hexdigest(), version_id=ack['version_id'], etag=ack['etag'])


def dispatch(client, stage):
    require(stage in ('job', 'start') and not any((p.old.FOLDER / ('serviceaud-step3-fence-' + stage + '-' + suffix + '.json')).exists()
        for suffix in ('intent', 'ack', 'accepted')), 'fence_effect_consumed_inspect_only')
    rr, pf = load('live-preflight')
    identity = guards(pf)
    if stage == 'job':
        require(client.call('GET', JOB, missing=True) is None, 'fence_job_exists')
        method, path, body = 'PUT', JOB, pf['plan']['definition']
    else:
        ar, accepted = load('job-accepted')
        require(accepted['preflight_receipt'] == rr['protected_receipt'], 'fence_job_unaccepted')
        checked_job(client.call('GET', JOB), pf['plan'], identity)
        inventory = client.call('GET', JOB + '/executions')
        require(inventory.get('value') == [] and inventory.get('nextLink') in (None, ''), 'fence_execution_exists')
        method, path, body = 'POST', JOB + '/start', pf['plan']['definition']['properties']['template']
    value = dict(operation_id=worker.OPERATION, stage=stage, preflight_receipt=rr['protected_receipt'],
        method=method, path=path, body=body, automatic_retry=False)
    recorded = save(dict(value, reservation=reserve(stage, value)), stage + '-intent')
    guards(pf)
    if stage == 'job':
        require(client.call('GET', JOB, missing=True) is None, 'fence_job_exists')
    else:
        checked_job(client.call('GET', JOB), pf['plan'], identity)
        require(client.call('GET', JOB + '/executions').get('value') == [], 'fence_execution_exists')
    ack = client.call(method, path, body=body, stage=stage, plan=pf['plan'])
    return save(dict(status='step_3_fence_effect_acknowledged', operation_id=worker.OPERATION,
        stage=stage, intent_receipt=recorded['protected_receipt'], raw_ack=ack,
        checked_at_utc=p.old.now(), automatic_retry=False, step_3_complete=False, step_4_started=False), stage + '-ack')


def accept_job(client):
    rr, pf = load('live-preflight')
    ir, intent_record = load('job-intent')
    ar, ack = load('job-ack')
    require(ack['intent_receipt'] == ir['protected_receipt'] and intent_record['preflight_receipt'] == rr['protected_receipt'], 'fence_job_chain_invalid')
    checked = checked_job(client.call('GET', JOB), pf['plan'], guards(pf))
    return save(dict(status='step_3_fence_job_verified', preflight_receipt=rr['protected_receipt'],
        ack_receipt=ar['protected_receipt'], native_verification=checked, step_3_complete=False, step_4_started=False), 'job-accepted')


def observe(client):
    rr, pf = load('live-preflight')
    ar, ack = load('start-ack')
    name = ack['raw_ack']['name']
    require(re.fullmatch(re.escape(worker.JOB) + '-[a-z0-9]{7,12}', name), 'fence_execution_invalid')
    path = JOB + '/executions/' + name
    value = client.call('GET', path)
    require(ack['raw_ack']['id'] == path and value['id'] == path, 'fence_execution_invalid')
    return dict(status='step_3_fence_execution_observed', execution=name, native_status=value['properties']['status'])


def collect(client):
    rr, pf = load('live-preflight')
    ar, ack = load('start-ack')
    ir, intent_record = load('start-intent')
    require(ack['intent_receipt'] == ir['protected_receipt'] and intent_record['preflight_receipt'] == rr['protected_receipt'], 'fence_start_chain_invalid')
    name = ack['raw_ack']['name']
    require(re.fullmatch(re.escape(worker.JOB) + '-[a-z0-9]{7,12}', name), 'fence_execution_invalid')
    path = JOB + '/executions/' + name
    value = client.call('GET', path)
    require(value.get('id') == path and ack['raw_ack'].get('id') == path
        and value.get('name') == name and value.get('type') == 'Microsoft.App/jobs/executions', 'fence_execution_invalid')
    require(value['properties']['status'] in ('Succeeded', 'Failed'), 'fence_execution_not_terminal')
    checked_template(value['properties']['template'], pf['plan']['definition']['properties']['template'])
    start = datetime.fromisoformat(value['properties']['startTime'].replace('Z', '+00:00'))
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + name + "-' | where TimeGenerated between (datetime(" + (start - timedelta(seconds=45)).isoformat() + ") .. datetime(" + (start + timedelta(minutes=15)).isoformat() + ")) | project Group=ContainerGroupName_s,Container=ContainerName_s,LogTime=TimeGenerated,Log=Log_s | order by LogTime asc"
    token = client.credential.get_token('https://api.loganalytics.io/.default')
    with client.session.post('https://api.loganalytics.io/v1/workspaces/5264a124-10cd-45f6-93a5-6d65c37e9bfb/query', json={'query': query},
        headers={'Authorization': 'Bearer ' + token.token}, allow_redirects=False, timeout=(5, 30)) as response:
        require(response.status_code == 200 and len(response.content) <= MAX_BYTES, 'fence_logs_invalid')
        family = response.json()
    require(type(family) is dict and set(family) == {'tables'} and len(family['tables']) == 1, 'fence_logs_invalid')
    table = family['tables'][0]
    require([column['name'] for column in table['columns']] == ['Group', 'Container', 'LogTime', 'Log']
        and type(table['rows']) is list and 0 < len(table['rows']) <= 10000
        and all(len(row) == 4 and all(type(x) is str for x in row) and row[1] == 'execution-fence-trial'
            and re.fullmatch(re.escape(name) + '-[a-z0-9-]{1,80}', row[0]) for row in table['rows']), 'fence_logs_invalid')
    records = [json.loads(row[3].removeprefix(worker.PREFIX)) for row in table['rows'] if row[3].startswith(worker.PREFIX)]
    require(len(records) == 1, 'fence_worker_record_missing_or_duplicate')
    record = records[0]
    require(record.get('operation_id') == worker.OPERATION and record.get('job_resource_id') == JOB
        and record.get('source_rows_read') == record.get('source_rows_written') == record.get('model_calls') == 0,
        'fence_worker_binding_invalid')
    require(client.call('GET', path) == value, 'fence_execution_drift')
    guards(pf)
    result = dict(status='step_3_synthetic_postgres_fence_accepted' if record['status'] == 'synthetic_postgres_fence_verified' else 'step_3_fence_trial_failed_retained',
        checked_at_utc=p.old.now(), preflight_receipt=rr['protected_receipt'], start_ack_receipt=ar['protected_receipt'],
        native_execution=value, full_bounded_observed_family=family, query=query, worker_result=record,
        step_3_complete=False, step_4_started=False, owner_runtime_active=False, six_specialist_runtimes_active=False)
    if result['status'] == 'step_3_synthetic_postgres_fence_accepted':
        require(value['properties']['status'] == 'Succeeded' and record['checks'] == list(worker.CHECKS)
            and record['synthetic_receipts_only'] is True and record['restricted_sql_fence_verified'] is True
            and record['protected_worker_receipts_verified'] is False, 'fence_worker_acceptance_invalid')
    return save(result, 'live-observation')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'job', 'accept-job', 'start', 'observe', 'collect'))
    command = parser.parse_args().command
    try:
        client = Transport()
        result = prepare(client) if command == 'prepare' else dispatch(client, command) if command in ('job', 'start') else accept_job(client) if command == 'accept-job' else observe(client) if command == 'observe' else collect(client)
        print(json.dumps({key: result[key] for key in ('status', 'protected_receipt', 'execution', 'native_status') if key in result}), flush=True)
    except Exception as error:
        print(json.dumps(dict(status='step_3_fence_trial_unaccepted_inspect_only', code=str(error) if isinstance(error, ValueError) and re.fullmatch('fence_[a-z_]+', str(error)) else type(error).__name__, automatic_retry=False)), flush=True)
        raise SystemExit(1) from None
