"""One fixed no-secret/no-SQL Azure log canary, not an agent execution tool.

Only this newly named Manual job may be created or started, once per protected
stage. Unknown replies consume the stage; inspect, never redispatch. Conditional
reservations coordinate cooperating controllers, not unrelated administrators.
No identity, secret, registry credential, SQL, model, source-data or production
change is requested. Host acceptance is historical provenance, not current QA.
"""
import argparse
import base64
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient

import deploy_owner_task_postgres_trial as deploy
import qualify_owner_task_isolated_host as host
import read_owner_task_catalog_logs as logs
import run_owner_task_catalog_canary as worker
import verify_owner_task_source_checkpoint as qa
from ibkr_etl import owner_codex_catalog_transport as codec
from ibkr_etl.owner_codex_isolated_target import IsolatedTarget


TARGET = IsolatedTarget()
JOB = 'ibkrv2stg-ownertrial-log-canary'
CONTAINER = 'catalog-log-canary'
PATH = codec.CANARY_JOB_ID
ARM = 'https://management.azure.com'
API = '2024-03-01'
REPLICA_API = '2023-11-02-preview'
IMAGE = 'python:3.12-slim-bookworm@sha256:9901e0a8d75037d8242ed43155cbcb2d1f61be1356383d8054afb59fd50e39c4'
OPERATION = 'b86898e4-09db-4e79-92e3-114b2ebc209e'
FOLDER = deploy.ROOT / '.work/owner-task-catalog-canary-20261004'
PREFIX = 'probes/owner-codex-catalog-canary-20261004/'
MAX_BYTES = 2 * 1024 * 1024
FILES = ('src/ibkr_etl/owner_codex_isolated_target.py',
    'src/ibkr_etl/owner_codex_catalog_transport.py', 'scripts/run_owner_task_catalog_canary.py')
NAMES = ('preflight.json', 'install-intent.json', 'install-ack.json', 'install-acceptance.json',
    'start-intent.json', 'start-ack.json', 'replica-binding.json', 'acceptance.json', 'readback-correction.json')
CORRECTION_PREDECESSORS = {
    'scripts/qualify_owner_task_catalog_canary.py': '7197e146fad0f3dd70e001b0561c84e3c00562abe6f2a97d51586e5dfd5a080b',
    'tests/test_owner_task_catalog_canary_controller.py': 'f819dabf6eddc5d6aeee238b3f02fb22785544d9aa357d9f2c95dac0714dceba'}
ORIGINAL_PREFLIGHT_SHA = 'ae7e1836cc94f7e5995bf2ec0524e2bca7e5b0abcd59a8ed29eb9c33aa42a34c'
ORIGINAL_PREFLIGHT_VERSION = '2026-10-04T12:38:53.2891355Z'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(payload):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('canary_arm_duplicate_json_key')
            value[key] = item
        return value
    def constant(_):
        raise ValueError('canary_arm_nonfinite_json')
    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('canary_arm_nonfinite_json')
        return result
    return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite)


def sources():
    return {k: v for k, v in qa.source_manifest().items() if k != qa.DOCUMENT}


def binding():
    hashes = {name: hashlib.sha256((deploy.ROOT / name).read_bytes()).hexdigest() for name in FILES}
    return codec.Binding('canary', TARGET.profile_key, PATH, OPERATION, sha(hashes))


def bundle():
    before = {name: (deploy.ROOT / name).read_bytes() for name in FILES}
    source = "import sys,types\np=types.ModuleType('ibkr_etl');p.__path__=[];sys.modules[p.__name__]=p\n"
    for name in ('owner_codex_isolated_target', 'owner_codex_catalog_transport'):
        code = before['src/ibkr_etl/' + name + '.py'].decode('utf-8')
        source += ("m=types.ModuleType('ibkr_etl." + name + "');m.__package__='ibkr_etl';"
            "sys.modules[m.__name__]=m;setattr(p,'" + name + "',m)\n"
            "exec(compile(" + repr(code) + ",'canary-module','exec'),m.__dict__)\n")
    source += "w=types.ModuleType('canary_worker');sys.modules[w.__name__]=w\n"
    source += "exec(compile(" + repr(before[FILES[-1]].decode('utf-8')) + ",'canary-worker','exec'),w.__dict__)\n"
    source += 'w.main(' + repr(asdict(binding())) + ')\n'
    if before != {name: (deploy.ROOT / name).read_bytes() for name in FILES}:
        raise ValueError('canary_source_changed_while_bundling')
    encoded = base64.b64encode(gzip.compress(source.encode(), mtime=0)).decode('ascii')
    args = ['-I', '-S', '-B', '-c', "import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"
        + encoded + "')),'catalog-transport-canary','exec'))"]
    if len(canonical(args)) > 65536:
        raise ValueError('canary_worker_bundle_oversized')
    return args, hashlib.sha256(source.encode()).hexdigest()


def definition():
    args, digest = bundle()
    return dict(location='eastasia', identity=dict(type='None'),
        tags=dict(purpose='owner-catalog-log-canary', operation=OPERATION, sourceSha256=binding().source_sha256,
            workerSha256=digest, profile=TARGET.profile_key),
        properties=dict(environmentId=TARGET.environment_resource_id,
            configuration=dict(triggerType='Manual', replicaTimeout=600, replicaRetryLimit=0,
                manualTriggerConfig=dict(parallelism=1, replicaCompletionCount=1), secrets=[], registries=[]),
            template=dict(containers=[dict(name=CONTAINER, image=IMAGE, command=['python'],
                args=args, env=[], resources=dict(cpu=0.5, memory='1Gi'))])))


class ArmTransport:
    def __init__(self, credential=None, session=None):
        self.credential = credential or AzureCliCredential()
        self.session = session or requests.Session()
        if session is None:
            self.session.mount('https://', HTTPAdapter(max_retries=0))
        self.token = None

    def call(self, method, path, *, body=None, missing=False):
        parsed = urlsplit(path)
        execution = re.escape(PATH) + r'/executions/' + re.escape(JOB) + r'-[a-z0-9]{7,12}'
        allowed = {PATH + '?api-version=' + API, PATH + '/executions?api-version=' + API,
            TARGET.environment_resource_id + '?api-version=' + API}
        if method == 'GET':
            valid = body is None and (path in allowed or re.fullmatch(execution + r'\?api-version=' + API, path)
                or re.fullmatch(execution + r'/replicas\?api-version=' + REPLICA_API, path))
        elif method == 'PUT':
            valid = path == PATH + '?api-version=' + API and canonical(body) == canonical(definition())
        elif method == 'POST':
            valid = path == PATH + '/start?api-version=' + API and canonical(body) == canonical(definition()['properties']['template'])
        else:
            valid = False
        if not valid or parsed.scheme or parsed.netloc or parsed.fragment:
            raise ValueError('canary_unregistered_arm_endpoint_or_body')
        if self.token is None or self.token.expires_on <= time.time() + 120:
            self.token = self.credential.get_token('https://management.azure.com/.default')
        try:
            response = self.session.request(method, ARM + path, json=body,
                headers={'Authorization': 'Bearer ' + self.token.token}, timeout=(5, 20),
                stream=True, allow_redirects=False)
            try:
                if missing and method == 'GET' and response.status_code == 404:
                    return None, 404
                if response.status_code not in ((200,) if method == 'GET' else (200, 201, 202)):
                    raise ValueError('canary_http_status_unaccepted')
                pieces, size = [], 0
                for piece in response.iter_content(16384):
                    size += len(piece)
                    if size > MAX_BYTES:
                        raise ValueError('canary_http_response_oversized')
                    pieces.append(piece)
                value = strict_json(b''.join(pieces))
                if type(value) is not dict:
                    raise ValueError('canary_http_response_shape_invalid')
                return value, response.status_code
            finally:
                response.close()
        except Exception:
            raise RuntimeError('canary_http_outcome_requires_inspection') from None


def get(client, path, *, missing=False):
    return client.call('GET', path, missing=missing)[0]


def kind(name):
    if name not in NAMES and re.fullmatch(r'part-[0-9]{3}\.json', name) is None:
        raise ValueError('canary_record_name_unregistered')
    return 'catalog-canary-' + name.removesuffix('.json')


def save(value, name):
    deploy.FOLDER = FOLDER
    raw = deploy.save(value, name, kind(name))
    deploy.protected(raw, kind(name))
    return raw


def load(name):
    raw = json.loads((FOLDER / name).read_text(encoding='utf-8'))
    return raw, deploy.protected(raw, kind(name))


def baseline():
    value = deploy.baseline()
    value['other_jobs'].pop(JOB, None)  # Exactly the newly registered canary, no other exclusion.
    if value.get('job_inventory_complete') is not True or len(value['other_jobs']) != 34:
        raise ValueError('canary_complete_existing_inventory_required')
    return value


def environment_logs(client):
    value = get(client, TARGET.environment_resource_id + '?api-version=' + API)
    config = value.get('properties', {}).get('appLogsConfiguration', {})
    if (value.get('id') != TARGET.environment_resource_id or config.get('destination') != 'log-analytics'
            or config.get('logAnalyticsConfiguration', {}).get('customerId') != logs.WORKSPACE):
        raise ValueError('canary_registered_log_environment_required')
    return dict(environment_id=TARGET.environment_resource_id, destination='log-analytics', workspace=logs.WORKSPACE)


def historical_host():
    raw = json.loads((host.FOLDER / 'host-acceptance.json').read_text(encoding='utf-8'))
    receipt = raw.get('protected_receipt', {})
    if (receipt.get('sha256') != 'e8e103df668996a00fe8d07a9e45f71f23078c358d6586f97609817d27f92589'
            or receipt.get('version_id') != '2026-10-04T11:45:48.1413402Z'):
        raise ValueError('canary_exact_historical_host_receipt_required')
    value = deploy.protected(raw, host.KINDS['host-acceptance.json'])
    observed = host.observe(host.Transport())
    if (value.get('status') != 'new_private_host_verified_sql_pending'
            or value.get('original_default_request_verified') is not True
            or value.get('owner_runtime_active') is not False or value.get('six_specialist_runtimes_active') is not False
            or observed != value.get('observation')):
        raise ValueError('canary_historical_host_or_current_metadata_unverified')
    # Historical source, deliberately NOT asserted equal to the new source manifest.
    for name in ('src/ibkr_etl/owner_codex_isolated_target.py', 'scripts/qualify_owner_task_isolated_host.py'):
        if hashlib.sha256((deploy.ROOT / name).read_bytes()).hexdigest() != value['sources'][name]:
            raise ValueError('canary_historical_host_guard_changed')
    return raw['protected_receipt']


def reservation_blob(stage, version=None):
    if stage not in ('install', 'start'):
        raise ValueError('canary_reservation_stage_unregistered')
    deploy.archive_protection()
    service = BlobServiceClient('https://' + deploy.ACCOUNT + '.blob.core.windows.net',
        credential=AzureCliCredential(), connection_timeout=5, read_timeout=10,
        retry_total=0, retry_to_secondary=False, permit_redirects=False, redirect_max=0,
        max_single_put_size=MAX_BYTES)
    return service.get_blob_client(deploy.CONTAINER, PREFIX + stage + '-reservation.json', version_id=version)


def reservation_readback(stage, receipt, payload):
    if (type(receipt) is not dict or set(receipt) != {'blob', 'sha256', 'version_id', 'etag', 'protected_readback_verified'}
            or receipt.get('blob') != PREFIX + stage + '-reservation.json'
            or receipt.get('protected_readback_verified') is not True
            or receipt.get('sha256') != hashlib.sha256(payload).hexdigest()
            or type(receipt.get('version_id')) is not str
            or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id']) is None
            or type(receipt.get('etag')) is not str or re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag']) is None
            or not 0 < len(payload) <= MAX_BYTES):
        raise ValueError('canary_reservation_receipt_invalid')
    datetime.fromisoformat(receipt['version_id'].replace('Z', '+00:00'))
    pinned = reservation_blob(stage, receipt['version_id'])
    props = pinned.get_blob_properties(timeout=10, retry_total=0)
    if (props.size != len(payload) or props.version_id != receipt['version_id'] or props.etag != receipt['etag']
            or props.blob_type != 'BlockBlob' or pinned.download_blob(offset=0, length=len(payload)+1,
                etag=props.etag, match_condition=MatchConditions.IfNotModified, max_concurrency=1,
                timeout=10, retry_total=0).readall() != payload):
        raise ValueError('canary_reservation_ack_unverified')
    deploy.archive_protection()


def reserve(stage, value):
    payload = canonical(value)
    if not 0 < len(payload) <= MAX_BYTES:
        raise ValueError('canary_reservation_size_invalid')
    ack = reservation_blob(stage).upload_blob(payload, length=len(payload), blob_type='BlockBlob',
        overwrite=False, match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
    receipt = dict(blob=PREFIX + stage + '-reservation.json', sha256=hashlib.sha256(payload).hexdigest(),
        version_id=ack.get('version_id'), etag=ack.get('etag'), protected_readback_verified=True)
    reservation_readback(stage, receipt, payload)
    return receipt


def require_start_unreserved():
    try:
        reservation_blob('start').get_blob_properties(timeout=10, retry_total=0)
    except ResourceNotFoundError as error:
        if type(error.status_code) is int and error.status_code == 404 and error.error_code == 'BlobNotFound':
            return
        raise ValueError('canary_start_reservation_absence_unverified') from None
    except Exception:
        raise ValueError('canary_start_reservation_absence_unverified') from None
    raise ValueError('canary_start_reservation_already_consumed')


def empty_execution_inventory(value):
    return (type(value) is dict and not set(value) - {'value', 'nextLink'}
        and type(value.get('value')) is list and len(value['value']) == 0
        and value.get('nextLink') in (None, ''))


def validate_template(value, desired):
    def empty_list(item):
        return item is None or (type(item) is list and len(item) == 0)
    if type(value) is not dict or not empty_list(value.get('volumes')) or not empty_list(value.get('initContainers')):
        raise ValueError('canary_template_extension_unregistered')
    actual, wanted = value.get('containers'), desired['containers']
    if (type(actual) is not list or len(actual) != 1 or type(actual[0]) is not dict
            or any(canonical(actual[0].get(k)) != canonical(wanted[0][k]) for k in ('name', 'image', 'command', 'args'))
            or any(not empty_list(actual[0].get(k)) for k in ('env', 'volumeMounts', 'probes'))):
        raise ValueError('canary_executed_template_differs')
    # Azure's actual GET adds this empty resource field. It is not accepted in
    # the creation/start body: ArmTransport still requires the original exact
    # canonical definition. Nonempty values, aliases and unknown keys fail.
    resources = actual[0].get('resources')
    if (type(resources) is not dict or set(resources) - {'cpu', 'memory', 'ephemeralStorage'}
            or ('ephemeralStorage' in resources and (type(resources['ephemeralStorage']) is not str
                or resources['ephemeralStorage'] != ''))
            or canonical({k:v for k,v in resources.items() if k != 'ephemeralStorage'}) != canonical(wanted[0]['resources'])):
        raise ValueError('canary_executed_template_differs')


def validate_job(value):
    desired = definition()
    if (type(value) is not dict or value.get('id') != PATH or value.get('name') != JOB
            or value.get('location') not in ('eastasia', 'East Asia') or value.get('tags') != desired['tags']):
        raise ValueError('canary_job_identity_or_source_differs')
    identity = value.get('identity')
    if identity is None:
        identity = {}
    if type(identity) is not dict:
        raise ValueError('canary_job_identity_not_empty')
    if identity.get('type', 'None') != 'None' or identity.get('userAssignedIdentities') or identity.get('principalId'):
        raise ValueError('canary_job_identity_not_empty')
    properties = value.get('properties', {})
    config = properties.get('configuration', {})
    if (properties.get('environmentId') != TARGET.environment_resource_id
            or properties.get('provisioningState') != 'Succeeded'
            or any(canonical(config.get(k)) != canonical(desired['properties']['configuration'][k]) for k in
                ('triggerType', 'replicaTimeout', 'replicaRetryLimit', 'manualTriggerConfig'))
            or any(config.get(k) is not None and (type(config[k]) is not list or config[k])
                for k in ('secrets', 'registries')) or config.get('scheduleTriggerConfig')
            or config.get('eventTriggerConfig')):
        raise ValueError('canary_job_configuration_differs')
    validate_template(properties.get('template'), desired['properties']['template'])
    return dict(job_id=PATH, source_sha256=binding().source_sha256, definition_sha256=sha(desired), image=IMAGE)


def execution_name(value):
    if type(value) is not str or re.fullmatch(re.escape(JOB) + r'-[a-z0-9]{7,12}', value) is None:
        raise ValueError('canary_execution_name_unregistered')
    return value


def utc(value):
    if type(value) is not str or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z', value) is None:
        raise ValueError('canary_execution_time_invalid')
    return datetime.fromisoformat(value.removesuffix('Z') + '+00:00')


def validate_execution(value, name):
    execution_name(name)
    if type(value) is not dict or value.get('id') != PATH + '/executions/' + name or value.get('name') != name:
        raise ValueError('canary_actual_execution_identity_differs')
    properties = value.get('properties', {})
    validate_template(properties.get('template'), definition()['properties']['template'])
    if properties.get('status') not in ('Running', 'Succeeded', 'Failed', 'Stopped'):
        raise ValueError('canary_execution_state_unknown')
    start = utc(properties.get('startTime'))
    end = properties.get('endTime')
    if end is not None and not start <= utc(end) <= start + timedelta(minutes=20):
        raise ValueError('canary_execution_window_invalid')
    return dict(execution=name, execution_id=value['id'], status=properties['status'], start_time=properties['startTime'],
        end_time=end, image=IMAGE,
        validated_requested_template_sha256=sha(definition()['properties']['template']),
        actual_template_readback_sha256=sha(properties['template']))


def replica_mapping(value, execution):
    if (type(value) is not dict or value.get('nextLink') not in (None, '')
            or set(value) - {'replicas', 'nextLink'} or type(value.get('replicas')) is not list
            or len(value['replicas']) != 1):
        raise ValueError('canary_one_complete_actual_replica_required')
    row = value['replicas'][0]
    name = row.get('name') if type(row) is dict else None
    if type(name) is not str or re.fullmatch(re.escape(execution) + r'-[a-z0-9]{5,12}', name) is None:
        raise ValueError('canary_actual_replica_name_invalid')
    containers = row.get('properties', {}).get('containers')
    if type(containers) is not list or len(containers) != 1 or containers[0].get('name') != CONTAINER:
        raise ValueError('canary_actual_replica_container_differs')
    if containers[0].get('image', IMAGE) != IMAGE:
        raise ValueError('canary_actual_replica_image_differs')
    return dict(log_group=name, container_name=CONTAINER)


def correction_sources(prepared, current):
    original = prepared.get('sources')
    if type(original) is not dict or set(original) != set(current):
        raise ValueError('canary_correction_source_inventory_changed')
    changed = {name for name in current if current[name] != original[name]}
    if changed != set(CORRECTION_PREDECESSORS) or any(original.get(name) != digest
            for name, digest in CORRECTION_PREDECESSORS.items()):
        raise ValueError('canary_only_reviewed_region_readback_correction_allowed')


def correction_preflight_receipt():
    raw, value = load('preflight.json')
    receipt = raw['protected_receipt']
    if receipt.get('sha256') != ORIGINAL_PREFLIGHT_SHA or receipt.get('version_id') != ORIGINAL_PREFLIGHT_VERSION:
        raise ValueError('canary_exact_original_preflight_required_for_correction')
    return receipt, value


def validate_correction_checks(value):
    keys = {'test_count', 'passed_test_count', 'skipped_tests', 'unittest_exit_code', 'compilation_exit_code',
        'repository_policy', 'live_sql_tests_passed', 'financial_or_visual_acceptance', 'os_or_dependency_security_certification'}
    if type(value) is not dict or set(value) != keys:
        raise ValueError('canary_complete_current_regression_result_required')
    skips = value['skipped_tests']
    allowed = {
        'test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_actual_fifo_entry_is_rejected_without_blocking': 'POSIX FIFO fixture',
        'test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_symlink_entry_never_followed': 'OS does not permit unprivileged test symlink creation'}
    if (type(skips) is not list or any(type(row) is not dict or set(row) != {'test', 'reason', 'result'}
            or type(row.get('test')) is not str or row['test'] not in allowed
            or row.get('reason') != allowed[row['test']] or row.get('result') != 'not_exercised' for row in skips)
            or len({row['test'] for row in skips}) != len(skips)
            or type(value['test_count']) is not int or value['test_count'] < 1963
            or type(value['passed_test_count']) is not int or value['passed_test_count'] != value['test_count'] - len(skips)
            or any(type(value[k]) is not int or value[k] != 0 for k in ('unittest_exit_code', 'compilation_exit_code'))
            or any(value[k] is not False for k in ('live_sql_tests_passed', 'financial_or_visual_acceptance', 'os_or_dependency_security_certification'))
            or type(value['repository_policy']) is not str or not value['repository_policy'].startswith('PASS: ')):
        raise ValueError('canary_strict_current_regression_result_required')


def accepted_sources(prepared):
    current = sources()
    if canonical(prepared.get('sources')) == canonical(current):
        return current
    correction_sources(prepared, current)
    preflight_receipt, original = correction_preflight_receipt()
    _, value = load('readback-correction.json')
    local = value.get('local_checks', {})
    validate_correction_checks(local)
    if (value.get('status') != 'reviewed_canary_readback_only_correction'
            or value.get('preflight_receipt') != preflight_receipt or canonical(original) != canonical(prepared)
            or canonical(value.get('sources')) != canonical(current)
            or canonical(value.get('runtime')) != canonical(host.runtime_descriptor())
            or value.get('definition_sha256') != sha(definition()) or value.get('binding') != asdict(binding())
            or value.get('allowed_predecessors') != CORRECTION_PREDECESSORS
            or value.get('requested_location') != 'eastasia' or value.get('observed_location') != 'East Asia'
            or canonical(value.get('observed_resource_defaults')) != canonical({'ephemeralStorage':''})
            or value.get('creation_redispatch_authorized') is not False or value.get('start_was_absent') is not True
            or value.get('credentials_supplied') is not False or value.get('ibkr_source_writes') is not False
            or type(value.get('sql_calls')) is not int or value['sql_calls'] != 0
            or type(local.get('test_count')) is not int or local['test_count'] < 1963
            or any(type(local.get(k)) is not int or local[k] != 0 for k in ('unittest_exit_code', 'compilation_exit_code'))
            or local.get('live_sql_tests_passed') is not False
            or local.get('financial_or_visual_acceptance') is not False
            or type(local.get('repository_policy')) is not str or not local['repository_policy'].startswith('PASS: ')):
        raise ValueError('canary_fresh_protected_readback_correction_qa_required')
    _, _, install_ack_raw, _ = intent_and_ack('install', {'protected_receipt':preflight_receipt}, original)
    if value.get('install_ack_receipt') != install_ack_raw['protected_receipt']:
        raise ValueError('canary_correction_original_install_chain_differs')
    return current


def frozen(prepared, client):
    checked_sources = accepted_sources(prepared)
    if (prepared.get('status') != 'prepared_no_secret_catalog_canary'
            or canonical(checked_sources) != canonical(sources())
            or canonical(prepared.get('runtime')) != canonical(host.runtime_descriptor())
            or canonical(prepared.get('definition')) != canonical(definition())
            or canonical(prepared.get('binding')) != canonical(asdict(binding()))
            or prepared.get('registration_sha256') != TARGET.registration_sha256
            or canonical(prepared.get('baseline')) != canonical(baseline())
            or canonical(prepared.get('environment_logs')) != canonical(environment_logs(client))
            or canonical(prepared.get('historical_host_receipt')) != canonical(historical_host())):
        raise ValueError('canary_protected_preflight_or_current_guard_differs')


def prepare_correction(prepared_raw, prepared, client):
    unused('start')
    require_start_unreserved()
    if (FOLDER / 'readback-correction.json').exists():
        raise ValueError('canary_readback_correction_evidence_already_exists')
    before, runtime = sources(), host.runtime_descriptor()
    correction_sources(prepared, before)
    receipt, original = correction_preflight_receipt()
    if receipt != prepared_raw['protected_receipt'] or canonical(original) != canonical(prepared):
        raise ValueError('canary_correction_preflight_differs')
    if (canonical(prepared.get('definition')) != canonical(definition())
            or canonical(prepared.get('binding')) != canonical(asdict(binding()))
            or canonical(prepared.get('runtime')) != canonical(runtime)
            or canonical(prepared.get('baseline')) != canonical(baseline())
            or canonical(prepared.get('environment_logs')) != canonical(environment_logs(client))
            or canonical(prepared.get('historical_host_receipt')) != canonical(historical_host())):
        raise ValueError('canary_correction_cannot_change_runtime_or_existing_resources')
    _, _, ack_raw, ack = intent_and_ack('install', prepared_raw, prepared)
    if ack.get('http_status') not in (200, 201, 202) or ack.get('readiness_verified') is not False:
        raise ValueError('canary_original_creation_ack_required')
    job = get(client, PATH + '?api-version=' + API)
    if job.get('location') != 'East Asia':
        raise ValueError('canary_exact_observed_region_alias_required')
    validate_job(job)
    if job['properties']['template']['containers'][0]['resources'].get('ephemeralStorage') != '':
        raise ValueError('canary_exact_observed_empty_resource_default_required')
    runs = get(client, PATH + '/executions?api-version=' + API)
    if not empty_execution_inventory(runs):
        raise ValueError('canary_correction_requires_unstarted_job')
    local = qa.run_local_checks()
    validate_correction_checks(local)
    if sources() != before or host.runtime_descriptor() != runtime:
        raise ValueError('canary_source_changed_during_correction_qa')
    unused('start')
    require_start_unreserved()
    validate_job(get(client, PATH + '?api-version=' + API))
    runs = get(client, PATH + '/executions?api-version=' + API)
    if not empty_execution_inventory(runs):
        raise ValueError('canary_started_during_readback_correction_qa')
    raw = save(dict(status='reviewed_canary_readback_only_correction', preflight_receipt=receipt,
        install_ack_receipt=ack_raw['protected_receipt'], sources=before, runtime=runtime,
        local_checks=local, definition_sha256=sha(definition()), binding=asdict(binding()),
        allowed_predecessors=CORRECTION_PREDECESSORS, requested_location='eastasia', observed_location='East Asia',
        observed_resource_defaults={'ephemeralStorage':''},
        creation_redispatch_authorized=False, start_was_absent=True, credentials_supplied=False,
        ibkr_source_writes=False, sql_calls=0), 'readback-correction.json')
    frozen(prepared, client)
    return raw


def unused(stage):
    if any((FOLDER / name).exists() for name in (stage + '-intent.json', stage + '-ack.json')):
        raise ValueError('canary_attempt_consumed_inspect_no_redispatch')


def intent_and_ack(stage, prepared_raw, prepared):
    intent_raw, intent = load(stage + '-intent.json')
    ack_raw, ack = load(stage + '-ack.json')
    expected_intent_keys = {'stage', 'preflight_receipt', 'definition_sha256', 'binding',
        'automatic_retry', 'credentials_supplied', 'sql_calls'}
    expected_ack_keys = {'stage', 'intent_receipt', 'reservation', 'http_status'}
    if stage == 'install':
        expected_intent_keys.add('unrelated_administrator_race_excluded')
        expected_ack_keys.add('readiness_verified')
    else:
        expected_intent_keys.add('install_acceptance_receipt')
        expected_ack_keys.update(('execution', 'execution_id'))
    if (set(intent) != expected_intent_keys or set(ack) != expected_ack_keys
            or intent.get('automatic_retry') is not False or intent.get('credentials_supplied') is not False
            or type(intent.get('sql_calls')) is not int or intent['sql_calls'] != 0
            or (stage == 'install' and intent.get('unrelated_administrator_race_excluded') is not False)
            or type(ack.get('http_status')) is not int
            or intent.get('stage') != stage or intent.get('preflight_receipt') != prepared_raw['protected_receipt']
            or intent.get('definition_sha256') != sha(prepared['definition'])
            or intent.get('binding') != prepared['binding'] or ack.get('stage') != stage
            or ack.get('intent_receipt') != intent_raw['protected_receipt']):
        raise ValueError('canary_protected_attempt_chain_invalid')
    reservation_readback(stage, ack.get('reservation'), canonical(intent))
    return intent_raw, intent, ack_raw, ack


def install(prepared_raw, prepared, client):
    unused('install')
    frozen(prepared, client)
    if get(client, PATH + '?api-version=' + API, missing=True) is not None:
        raise ValueError('canary_new_job_must_be_absent')
    intent = save(dict(stage='install', preflight_receipt=prepared_raw['protected_receipt'],
        definition_sha256=sha(prepared['definition']), binding=prepared['binding'], automatic_retry=False,
        unrelated_administrator_race_excluded=False, credentials_supplied=False, sql_calls=0), 'install-intent.json')
    reservation = reserve('install', {k: v for k, v in intent.items() if k != 'protected_receipt'})
    frozen(prepared, client)
    if get(client, PATH + '?api-version=' + API, missing=True) is not None:
        raise ValueError('canary_new_job_must_still_be_absent')
    _, status = client.call('PUT', PATH + '?api-version=' + API, body=prepared['definition'])
    return save(dict(stage='install', intent_receipt=intent['protected_receipt'], reservation=reservation,
        http_status=status, readiness_verified=False), 'install-ack.json')


def install_acceptance(prepared_raw, prepared, client):
    frozen(prepared, client)
    _, _, ack_raw, ack = intent_and_ack('install', prepared_raw, prepared)
    if ack.get('http_status') not in (200, 201, 202) or ack.get('readiness_verified') is not False:
        raise ValueError('canary_install_ack_invalid')
    value = dict(status='installed_no_secret_manual_canary', install_ack_receipt=ack_raw['protected_receipt'],
        preflight_receipt=prepared_raw['protected_receipt'], job=validate_job(get(client, PATH + '?api-version=' + API)))
    if (FOLDER / 'install-acceptance.json').exists():
        _, existing = load('install-acceptance.json')
        if canonical(existing) != canonical(value):
            raise ValueError('canary_install_acceptance_drift')
        return existing
    return save(value, 'install-acceptance.json')


def start(prepared_raw, prepared, client):
    unused('start')
    install_acceptance(prepared_raw, prepared, client)
    install_raw, _ = load('install-acceptance.json')
    runs = get(client, PATH + '/executions?api-version=' + API)
    if not empty_execution_inventory(runs):
        raise ValueError('canary_complete_empty_execution_inventory_required')
    intent = save(dict(stage='start', preflight_receipt=prepared_raw['protected_receipt'],
        install_acceptance_receipt=install_raw['protected_receipt'], definition_sha256=sha(prepared['definition']),
        binding=prepared['binding'], automatic_retry=False, credentials_supplied=False, sql_calls=0), 'start-intent.json')
    reservation = reserve('start', {k: v for k, v in intent.items() if k != 'protected_receipt'})
    frozen(prepared, client)
    validate_job(get(client, PATH + '?api-version=' + API))
    runs = get(client, PATH + '/executions?api-version=' + API)
    if not empty_execution_inventory(runs):
        raise ValueError('canary_execution_inventory_changed_no_dispatch')
    reply, status = client.call('POST', PATH + '/start?api-version=' + API,
        body=prepared['definition']['properties']['template'])
    name = execution_name(reply.get('name'))
    if reply.get('id') != PATH + '/executions/' + name:
        raise ValueError('canary_start_ack_identity_invalid_inspect_only')
    return save(dict(stage='start', intent_receipt=intent['protected_receipt'], reservation=reservation,
        execution=name, execution_id=reply['id'], http_status=status), 'start-ack.json')


def current_execution(prepared_raw, prepared, client):
    install_acceptance(prepared_raw, prepared, client)
    _, intent, ack_raw, ack = intent_and_ack('start', prepared_raw, prepared)
    install_raw, _ = load('install-acceptance.json')
    if intent.get('install_acceptance_receipt') != install_raw['protected_receipt'] or ack.get('http_status') not in (200, 201, 202):
        raise ValueError('canary_start_install_chain_invalid')
    name = execution_name(ack.get('execution'))
    if ack.get('execution_id') != PATH + '/executions/' + name:
        raise ValueError('canary_start_ack_invalid')
    actual = validate_execution(get(client, ack['execution_id'] + '?api-version=' + API), name)
    return actual, ack_raw


def capture(prepared_raw, prepared, client):
    actual, ack_raw = current_execution(prepared_raw, prepared, client)
    if actual['status'] != 'Running' or actual['end_time'] is not None:
        raise ValueError('canary_live_replica_capture_required_no_redispatch')
    mapping = replica_mapping(get(client, actual['execution_id'] + '/replicas?api-version=' + REPLICA_API), actual['execution'])
    # A second exact read guards the immediate capture; this is not a proof that
    # Azure can never replace a replica later or that every log has arrived.
    after = validate_execution(get(client, actual['execution_id'] + '?api-version=' + API), actual['execution'])
    if canonical(after) != canonical(actual):
        raise ValueError('canary_execution_changed_during_replica_capture')
    start_time = utc(actual['start_time'])
    fmt = lambda dt: dt.isoformat(timespec='seconds').replace('+00:00', 'Z')
    value = dict(status='actual_running_replica_captured', start_ack_receipt=ack_raw['protected_receipt'],
        execution=actual, mapping=mapping, binding=prepared['binding'], image=IMAGE,
        start_utc=fmt(start_time - timedelta(minutes=2)), end_utc=fmt(start_time + timedelta(minutes=20)),
        platform_replacement_excluded=False, provider_sql_authority=False)
    return save(value, 'replica-binding.json')


def complete_evidence(evidence, scope):
    if type(evidence) is not dict or set(evidence) != {'record', 'lines', 'header_inventory_sha256'}:
        raise ValueError('canary_complete_transport_evidence_required')
    expected = worker.synthetic_record(binding())
    digest = evidence.get('header_inventory_sha256')
    lines = evidence.get('lines')
    if (type(digest) is not str or re.fullmatch(r'[0-9a-f]{64}', digest) is None
            or type(lines) is not list or len(lines) != 1025 or codec.canonical(evidence['record']) != codec.canonical(expected)
            or codec.canonical(codec.decode_record(lines, scope.binding)) != codec.canonical(expected)):
        raise ValueError('canary_full_exact_4_mib_record_required')
    return hashlib.sha256(codec.canonical(expected)).hexdigest()


def retain_parts(lines, prepared, actual, replica_raw):
    receipts = []
    for offset in range(0, len(lines), 128):
        index = offset // 128
        value = dict(kind='complete_canary_wire_part', index=index, offset=offset,
            lines=lines[offset:offset+128], binding=prepared['binding'], execution_id=actual['execution_id'],
            replica_receipt=replica_raw['protected_receipt'])
        name = f'part-{index:03d}.json'
        if len(canonical(value)) > MAX_BYTES:
            raise ValueError('canary_part_exceeds_unchanged_protected_blob_cap')
        if (FOLDER / name).exists():
            raw, old = load(name)
            if canonical(old) != canonical(value):
                raise ValueError('canary_existing_protected_part_differs')
        else:
            raw = save(value, name)
        receipts.append(dict(name=name, receipt=raw['protected_receipt'], index=index, offset=offset, count=len(value['lines'])))
    return receipts


def verify_parts(manifest, prepared, actual, replica_raw):
    entries = manifest.get('parts')
    if type(entries) is not list or len(entries) != 9:
        raise ValueError('canary_complete_protected_part_manifest_required')
    lines = []
    for index, entry in enumerate(entries):
        name = f'part-{index:03d}.json'
        raw, value = load(name)
        expected_count = 128 if index < 8 else 1
        if (canonical(entry) != canonical(dict(name=name, receipt=raw['protected_receipt'], index=index, offset=index*128, count=expected_count))
                or set(value) != {'kind', 'index', 'offset', 'lines', 'binding', 'execution_id', 'replica_receipt'}
                or type(value.get('index')) is not int or type(value.get('offset')) is not int
                or value.get('kind') != 'complete_canary_wire_part' or value.get('index') != index
                or value.get('offset') != index*128 or value.get('binding') != prepared['binding']
                or value.get('execution_id') != actual['execution_id']
                or value.get('replica_receipt') != replica_raw['protected_receipt']
                or type(value.get('lines')) is not list or len(value['lines']) != expected_count):
            raise ValueError('canary_protected_part_chain_invalid')
        lines.extend(value['lines'])
    expected = worker.synthetic_record(binding())
    if (codec.canonical(codec.decode_record(lines, binding())) != codec.canonical(expected)
            or manifest.get('record_sha256') != hashlib.sha256(codec.canonical(expected)).hexdigest()):
        raise ValueError('canary_protected_record_hash_differs')
    return lines


def verify(prepared_raw, prepared, client, query):
    actual, ack_raw = current_execution(prepared_raw, prepared, client)
    if actual['status'] != 'Succeeded' or actual['end_time'] is None:
        raise ValueError('canary_successful_execution_required_inspect_no_redispatch')
    replica_raw, replica = load('replica-binding.json')
    captured = replica.get('execution', {})
    if (replica.get('status') != 'actual_running_replica_captured' or captured.get('status') != 'Running'
            or captured.get('end_time') is not None or replica.get('start_ack_receipt') != ack_raw['protected_receipt']
            or any(captured.get(k) != actual[k] for k in ('execution', 'execution_id', 'start_time', 'image',
                'validated_requested_template_sha256', 'actual_template_readback_sha256'))
            or replica.get('binding') != prepared['binding'] or replica.get('image') != IMAGE
            or replica.get('platform_replacement_excluded') is not False or replica.get('provider_sql_authority') is not False):
        raise ValueError('canary_protected_actual_replica_binding_required')
    mapping = replica.get('mapping', {})
    if (type(mapping.get('log_group')) is not str or re.fullmatch(re.escape(actual['execution']) + r'-[a-z0-9]{5,12}',
            mapping['log_group']) is None or mapping.get('container_name') != CONTAINER):
        raise ValueError('canary_replica_mapping_invalid')
    fmt = lambda dt: dt.isoformat(timespec='seconds').replace('+00:00', 'Z')
    start_time = utc(actual['start_time'])
    if (replica.get('start_utc') != fmt(start_time-timedelta(minutes=2))
            or replica.get('end_utc') != fmt(start_time+timedelta(minutes=20))):
        raise ValueError('canary_protected_time_window_differs')
    scope = logs.CatalogLogScope(binding(), mapping['log_group'], CONTAINER, replica['start_utc'], replica['end_utc'])
    evidence = logs.collect_evidence(query, scope)
    record_hash = complete_evidence(evidence, scope)
    parts = retain_parts(evidence['lines'], prepared, actual, replica_raw)
    value = dict(status='captured_canary_replica_complete_transport_verified', preflight_receipt=prepared_raw['protected_receipt'],
        start_ack_receipt=ack_raw['protected_receipt'], replica_receipt=replica_raw['protected_receipt'], binding=prepared['binding'],
        execution=actual, mapping=mapping, header_inventory_sha256=evidence['header_inventory_sha256'],
        record_bytes=codec.MAX_RECORD_BYTES, chunk_count=1024, frame_count=1025, record_sha256=record_hash, parts=parts,
        protected_complete_record_readback=True, baseline_unchanged=True, existing_jobs_unchanged=34,
        transport_scope='independently_captured_replica_only', platform_replacement_excluded=False,
        provider_sql_authority=False, provider_semantics_verified=False, fresh_restricted_login_verified=False,
        model_credential_containment_verified=False, owner_runtime_active=False, six_specialist_runtimes_active=False,
        production_migration_059_installed=False, ibkr_source_writes=False, credentials_supplied=False, sql_calls=0,
        model_calls=0, daily_schedule_hkt='18:00')
    protected_lines = verify_parts(value, prepared, actual, replica_raw)
    if protected_lines != evidence['lines']:
        raise ValueError('canary_complete_protected_wire_differs')
    frozen(prepared, client)
    if (FOLDER / 'acceptance.json').exists():
        raw, old = load('acceptance.json')
        if canonical(old) != canonical(value):
            raise ValueError('canary_fresh_transport_or_protected_acceptance_drift')
        return dict(status='independent_complete_transport_readback_verified', protected_receipt=raw['protected_receipt'])
    raw = save(value, 'acceptance.json')
    return dict(status=value['status'], protected_receipt=raw['protected_receipt'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'prepare-readback-correction', 'install', 'verify-install', 'start', 'capture', 'inspect', 'verify'))
    args = parser.parse_args()
    host.checked_owner()
    FOLDER.mkdir(parents=True, exist_ok=True)
    client = ArmTransport()
    if args.stage == 'prepare':
        before, runtime = sources(), host.runtime_descriptor()
        local = qa.run_local_checks()
        baseline_value, env, anchor = baseline(), environment_logs(client), historical_host()
        if sources() != before or host.runtime_descriptor() != runtime:
            raise ValueError('canary_source_or_runtime_changed_during_checks')
        if get(client, PATH + '?api-version=' + API, missing=True) is not None:
            raise ValueError('canary_new_job_must_be_absent')
        raw = save(dict(status='prepared_no_secret_catalog_canary', sources=before, runtime=runtime, local_checks=local,
            definition=definition(), binding=asdict(binding()), registration_sha256=TARGET.registration_sha256,
            baseline=baseline_value, environment_logs=env, historical_host_receipt=anchor,
            owner_runtime_active=False, six_specialist_runtimes_active=False, sql_calls=0, model_calls=0,
            credentials_supplied=False, ibkr_source_writes=False), 'preflight.json')
        frozen({k: v for k, v in raw.items() if k != 'protected_receipt'}, client)
        result = dict(status=raw['status'], tests=local['test_count'], protected_receipt=raw['protected_receipt'])
    else:
        prepared_raw, prepared = load('preflight.json')
        if args.stage == 'prepare-readback-correction':
            raw = prepare_correction(prepared_raw, prepared, client)
            result = dict(status=raw['status'], tests=raw['local_checks']['test_count'], protected_receipt=raw['protected_receipt'])
        elif args.stage == 'install':
            raw = install(prepared_raw, prepared, client)
            result = dict(status='install_ack_received_not_yet_verified', protected_receipt=raw['protected_receipt'])
        elif args.stage == 'verify-install':
            install_acceptance(prepared_raw, prepared, client)
            result = dict(status='installed_no_secret_manual_canary_verified')
        elif args.stage == 'start':
            raw = start(prepared_raw, prepared, client)
            result = dict(status='canary_started', execution=raw['execution'], protected_receipt=raw['protected_receipt'])
        elif args.stage == 'capture':
            raw = capture(prepared_raw, prepared, client)
            result = dict(status=raw['status'], mapping=raw['mapping'], protected_receipt=raw['protected_receipt'])
        elif args.stage == 'inspect':
            actual, _ = current_execution(prepared_raw, prepared, client)
            result = actual
        else:
            result = verify(prepared_raw, prepared, client, logs.LogsTransport(AzureCliCredential()))
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()

