"""Separately reserved valid-name ACA diagnostic; never retries the rejected job.

Preserves all 727 sources. The received IBKR source, image, runtime payload,
identity and accepted registry grant remain unchanged. V2 supersedes only the
unaccepted named candidate pair with pinned no-effects provenance and bounded
QA timing. No identity/grant mutation command or route exists. All raw native
observations survive copied validation adapters. Import is inert.
"""
import argparse
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import parse_qsl, urlsplit

import requests
from requests.adapters import HTTPAdapter
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient

import qualify_owner_task_aca_runtime_continuation as c

previous, frozen, contract, transport = c.previous, c.frozen, c.contract, c.transport
ROOT = c.ROOT
FOLDER = ROOT / '.work/owner-task-aca-runtime-named-v2-20261004'
JOB = 'ibkrv2stg-ownerruntime-20261004'
JOB_ID = contract.BASE + 'Microsoft.App/jobs/' + JOB
EVENT_STREAM_ENDPOINT = contract.EVENT_STREAM_ENDPOINT.replace('/' + contract.JOB + '/', '/' + JOB + '/')
OPERATION_ID = '6ef9173d-1aa3-4ff7-810a-d2ce23e75d6a'
TRIAL_OPERATION_ID = c.TRIAL_OPERATION_ID
PREFIX = 'probes/owner-codex-aca-runtime-named-v2-20261004/'
KIND_PREFIX = 'aca-runtime-namedv2-'
SUPERSEDED_OPERATION_ID = '2a2cf3cc-fe8a-498b-89a5-7445075cdec4'
SUPERSEDED_FOLDER = ROOT / '.work/owner-task-aca-runtime-named-20261004'
SUPERSEDED_PREFIX = 'probes/owner-codex-aca-runtime-named-20261004/'
SUPERSESSION_KIND = 'aca-runtime-named-qa-timing-diagnosis'
SUPERSESSION_PIN = ('2eab0fb509b6a2cb38203e050bddc094320ede5ea73b9c3bec234ed341cfd43c', '2026-10-04T20:57:57.0738821Z')
SUPERSEDED_SOURCE_HASHES = {
    'scripts/qualify_owner_task_aca_runtime_named.py': '684f5e2aabeef6fad4f431167c462b0ae30292c4f3fe18d2c1506819ea193aec',
    'tests/test_owner_task_aca_runtime_named.py': 'db0e044f904be4d7861fa6cf0ca2c849ba95a6123eadd7ba8959e431763d9093'}
ADDITIONS = frozenset(('scripts/qualify_owner_task_aca_runtime_named.py', 'tests/test_owner_task_aca_runtime_named.py'))
STAGES = ('install', 'start')
COMMANDS = ('prepare', 'install', 'install-verify', 'start', 'capture', 'collect', 'verify')
MAX_BYTES, FALSE_FLAGS = c.MAX_BYTES, c.FALSE_FLAGS
MAX_PRIVATE_ERROR_BODY = 1024 * 1024
require, canonical, digest, native_copy = c.require, c.canonical, c.digest, c.native_copy
source_bytes, get, environment, roles = c.source_bytes, c.get, c.environment, c.roles
HISTORY_PINS = {
    'preflight.json': ('498f325e8ff0b4c01bdc940cdeb0ac1dc4613810f520648a8932240c527edff1', '2026-10-04T19:39:38.8552840Z'),
    'grant-intent.json': ('d4fd50eb498aa5274535796cc1a34b2dfa82ea1cf60ed0aa8d8537226fe8dbd3', '2026-10-04T19:46:08.0613699Z'),
    'grant-ack.json': ('0ec2684ede92d89d389fb1b94ec4a35dff9b80bd62b04b22b12553297ee31035', '2026-10-04T19:47:12.5934465Z'),
    'grant-acceptance.json': ('3cfacaeb900638181e294d606caf118f07bf09c72a88504beb41f0b7025fafee', '2026-10-04T19:53:27.2196314Z'),
    'install-intent.json': ('943f9a6559415a285399ec46cccbd25944502cd3f0614ecb0746d8e5e4b00b3b', '2026-10-04T20:00:13.4912443Z'),
    'install-outcome.json': ('5a76fc801474b1fb6917df4e214e3cec48d777e860fcaf59e35d0a43fc90226c', '2026-10-04T20:01:17.6649630Z'),
    'install-diagnosis.json': ('8b0f9d190f6fbcb93087475495a6422d7524d8f24e2f750ddb1a889d251aa0fa', '2026-10-04T20:06:48.3111684Z')}
CORRELATION_ID = '867625d0-98f7-437e-be41-028bccbaf941'
FAILURE_STARTED = '2026-10-04T20:01:03.3917952Z'
FAILURE_ENDED = '2026-10-04T20:01:04.923118Z'


def validate_name(value):
    require(type(value) is str and value == JOB and 2 <= len(value) < 32
        and re.fullmatch('[a-z][a-z0-9-]*[a-z0-9]', value) is not None and '--' not in value,
        'aca_named_exact_valid_job_name_required')
    return value


def validate_sources(old, current):
    require(type(old) is dict and len(old) == 727 and type(current) is dict
        and set(current) == set(old) | ADDITIONS and not set(old) & ADDITIONS
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for k, v in current.items())
        and all(current[k] == v for k, v in old.items()), 'aca_named_only_two_additive_sources_allowed')


def local_qa_policy():
    """Fresh fixed policy; never a caller-selected timeout or command surface."""
    return dict(schema_version=1, full_discovery=True, sharding=False, automatic_retry=False,
        regression_argv=['-m', 'unittest', 'discover', '-s', 'tests', '-v'], regression_timeout_seconds=600,
        repository_policy_argv=['scripts/ci_policy_check.py'], repository_policy_timeout_seconds=90,
        compilation_argv=['-m', 'compileall', '-q', '-x', 'node_modules', 'src', 'scripts', 'tests'],
        compilation_timeout_seconds=90, result_validator='scripts/verify_owner_task_source_checkpoint.py:validate_regression_result',
        result_validator_source_sha256='3be945e836b897b162c37613ae038e2169a2a2b85b9aeacc8575514909b7eff3')


def validate_local_qa_policy(value):
    value = native_copy(value)
    require(canonical(value) == canonical(local_qa_policy()), 'aca_named_exact_local_qa_policy_required')
    return value


def run_local_checks():
    """Same full regression gates; bounded timing is not a passing result."""
    policy = local_qa_policy()
    def phase(name, argv, timeout, environment=None):
        started = time.monotonic()
        print(json.dumps(dict(status='local_qa_phase_started', phase=name)), flush=True)
        try:
            return subprocess.run([sys.executable] + argv, cwd=ROOT, capture_output=True, text=True,
                timeout=timeout, **({} if environment is None else {'env': environment})), started
        except Exception:
            raise ValueError('aca_named_local_qa_phase_unverified') from None
    def passed(name, started):
        print(json.dumps(dict(status='local_qa_phase_passed', phase=name,
            elapsed_seconds=round(time.monotonic() - started, 3))), flush=True)
    result, started = phase('full_regression', policy['regression_argv'], 600)
    try: count, skipped = previous.q.qa.validate_regression_result(result)
    except Exception: raise ValueError('aca_named_full_regression_unverified') from None
    passed('full_regression', started)
    environment = dict(os.environ, GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='safe.directory', GIT_CONFIG_VALUE_0=ROOT.as_posix())
    result, started = phase('repository_policy', policy['repository_policy_argv'], 90, environment)
    require(type(result.returncode) is int and result.returncode == 0 and type(result.stdout) is str
        and result.stdout.startswith('PASS: '), 'aca_named_repository_policy_unverified')
    repository_policy = result.stdout.strip(); passed('repository_policy', started)
    result, started = phase('compilation', policy['compilation_argv'], 90)
    require(type(result.returncode) is int and result.returncode == 0, 'aca_named_compilation_unverified')
    passed('compilation', started)
    checks = dict(test_count=count, passed_test_count=count - len(skipped), skipped_tests=skipped,
        unittest_exit_code=0, compilation_exit_code=0, repository_policy=repository_policy,
        live_sql_tests_passed=False, financial_or_visual_acceptance=False, os_or_dependency_security_certification=False)
    previous.q.canary.validate_correction_checks(checks)
    return checks


def validate_supersession(record):
    """Pure pinned absence provenance; neither old QA result authorizes effects."""
    raw = native_copy(record)
    value = previous.q.readback.receipt_binding(raw, SUPERSESSION_KIND, SUPERSESSION_PIN)
    fixed = dict(status='unaccepted_named_candidate_absence_verified_before_supersession',
        operation_id=SUPERSEDED_OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
        local_records_absent=True, protected_candidate_family_absent=True, effect_reservations_absent=True,
        capture_stop_absent=True, new_job_absent=True, rejected_original_job_absent=True,
        source_supersession_authorized_only_before_effect=True, initial_diagnosis_not_retained_due_to_duplicate_keyword=True,
        failed_preflight_deadline_seconds=300, sql_calls=0, model_calls=0, **{k: False for k in FALSE_FLAGS})
    require(set(value) == set(fixed) | {'checked_at_utc', 'prior_preflight_receipt', 'sources',
        'observed_preflight_failures', 'standalone_discovery', 'local_captured_qa'}
        and all(type(value.get(k)) is type(v) and value[k] == v for k, v in fixed.items()),
        'aca_named_exact_unaccepted_supersession_required')
    transport.utc(value['checked_at_utc'])
    receipt = value['prior_preflight_receipt']
    require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'protected_readback_verified'}
        and receipt['protected_readback_verified'] is True and (receipt['sha256'], receipt['version_id']) == HISTORY_PINS['preflight.json']
        and receipt['blob'] == 'acceptance/gateway-read-owner-task-pg-aca-runtime-continuation-preflight-' + receipt['sha256'] + '.json',
        'aca_named_supersession_original_preflight_required')
    require(value['observed_preflight_failures'] == [
        dict(status='regression_deadline_exceeded_not_passed', reported_completion_utc='2026-10-04T20:33:00Z'),
        dict(status='regression_deadline_exceeded_not_passed', reported_completion_utc='2026-10-04T20:48:52Z')]
        and canonical(value['standalone_discovery']) == canonical(dict(tests=2313, passed=2311, skipped=2,
            elapsed_seconds=273.85, protected_preflight_acceptance=False))
        and canonical(value['local_captured_qa']) == canonical(dict(tests=2313, passed=2311, skipped=2, elapsed_seconds=291.869,
            compilation_exit_code=0, repository_policy='PASS: 293 tracked files, 710 source files scanned, 59 contiguous migrations, no prohibited paths or secret patterns',
            protected_preflight_acceptance=False)), 'aca_named_supersession_observations_changed')
    source_map = value['sources']
    require(type(source_map) is dict and len(source_map) == 729 and set(SUPERSEDED_SOURCE_HASHES) <= set(source_map)
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for k, v in source_map.items())
        and all(source_map[k] == v for k, v in SUPERSEDED_SOURCE_HASHES.items())
        and source_map.get('scripts/verify_owner_task_source_checkpoint.py') == local_qa_policy()['result_validator_source_sha256'],
        'aca_named_exact_superseded_sources_required')
    return value


def validate_supersession_sources(old, current):
    old, current = native_copy(old), native_copy(current)
    require(type(old) is dict and type(current) is dict and len(old) == len(current) == 729 and set(old) == set(current)
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for k, v in current.items())
        and all(old.get(k) == v for k, v in SUPERSEDED_SOURCE_HASHES.items())
        and all(current[k] == v for k, v in old.items() if k not in ADDITIONS)
        and all(current[k] != old[k] for k in ADDITIONS), 'aca_named_only_unaccepted_candidate_pair_may_be_superseded')


def superseded_storage():
    previous.q.deploy.archive_protection()
    return BlobServiceClient('https://' + previous.q.deploy.ACCOUNT + '.blob.core.windows.net', credential=AzureCliCredential(),
        retry_total=0, retry_to_secondary=False, connection_timeout=5, read_timeout=10, permit_redirects=False, redirect_max=0)


def superseded_absence():
    """Fresh v1 no-effects guard; v2 evidence has a disjoint protected kind."""
    try:
        entries = list(SUPERSEDED_FOLDER.iterdir())
        require(len(entries) == 1 and entries[0].name == 'qa-timing-diagnosis.json'
            and entries[0].is_file() and not entries[0].is_symlink(), 'aca_named_superseded_local_effect_evidence_exists')
    except Exception: raise ValueError('aca_named_superseded_local_absence_unproven') from None
    service = superseded_storage()
    wanted = 'acceptance/gateway-read-owner-task-pg-' + SUPERSESSION_KIND + '-' + SUPERSESSION_PIN[0] + '.json'
    try:
        rows = service.get_container_client(previous.q.deploy.CONTAINER).list_blobs(
            name_starts_with='acceptance/gateway-read-owner-task-pg-aca-runtime-named-', include=['versions'],
            results_per_page=2, timeout=10, retry_total=0)
        pages = rows.by_page()
        page = next(pages)  # Exactly one complete page; any continuation blocks.
        seen = 0
        for row in page:
            seen += 1
            require(seen == 1 and type(row.name) is str and row.name == wanted
                and type(row.version_id) is str and row.version_id == SUPERSESSION_PIN[1],
                'aca_named_superseded_protected_effect_evidence_exists')
        require(seen == 1, 'aca_named_superseded_diagnosis_absence_unproven')
        require(pages.continuation_token is None or type(pages.continuation_token) is str and pages.continuation_token == '',
            'aca_named_superseded_inventory_continuation_unproven')
    except Exception: raise ValueError('aca_named_superseded_protected_absence_unproven') from None
    for name in ('install-reservation.json', 'start-reservation.json', 'capture-stop.json'):
        blob = service.get_blob_client(previous.q.deploy.CONTAINER, SUPERSEDED_PREFIX + name)
        try: blob.get_blob_properties(timeout=10, retry_total=0)
        except HttpResponseError as error:
            try: c.validate_original_reservation_absence(error.status_code, getattr(error, 'error_code', None))
            except Exception: raise ValueError('aca_named_superseded_reservation_absence_unproven') from None
            continue
        except Exception: raise ValueError('aca_named_superseded_reservation_absence_unproven') from None
        raise ValueError('aca_named_superseded_effect_or_stop_exists')
    previous.q.deploy.archive_protection()


def supersession():
    raw = previous.q.readback.read_local(SUPERSEDED_FOLDER, 'qa-timing-diagnosis.json')
    value = validate_supersession(raw)
    require(canonical(previous.q.deploy.protected(raw, SUPERSESSION_KIND)) == canonical(value),
        'aca_named_supersession_protected_readback_changed')
    validate_supersession_sources(value['sources'], previous.sources()); superseded_absence()
    return raw


def plan(dependency):
    validate_name(JOB)
    # The PUT body contains no job name, so it remains byte-identical to the
    # reviewed body. Only the fixed ARM address and native name metadata change.
    return frozen.plan(dependency)


def execution_name(value):
    validate_name(JOB)
    require(type(value) is str and re.fullmatch(re.escape(JOB) + r'-[a-z0-9]{7,12}', value) is not None,
        'aca_named_exact_execution_required')
    return value


def new_execution_to_old(value):
    return contract.JOB + execution_name(value)[len(JOB):]


def validate_job_metadata(value, expected_runtime, principal_id, client_id):
    raw = native_copy(value)
    require(type(raw) is dict and raw.get('id') == JOB_ID and raw.get('name') == JOB,
        'aca_named_native_job_identity_invalid')
    copied = native_copy(raw); copied['id'], copied['name'] = contract.JOB_ID, contract.JOB
    properties = copied.get('properties')
    require(type(properties) is dict, 'aca_named_native_job_properties_invalid')
    if 'eventStreamEndpoint' in properties:
        require(properties['eventStreamEndpoint'] == EVENT_STREAM_ENDPOINT,
            'aca_named_registered_unsigned_event_endpoint_required')
        properties['eventStreamEndpoint'] = contract.EVENT_STREAM_ENDPOINT
    c.validate_job_metadata(copied, expected_runtime, principal_id, client_id)
    return raw


def validate_execution(value, pf, name, *, terminal):
    raw = native_copy(value); old_name = new_execution_to_old(name)
    require(type(raw) is dict and raw.get('id') == JOB_ID + '/executions/' + name and raw.get('name') == name,
        'aca_named_native_execution_identity_invalid')
    copied = native_copy(raw); copied['id'] = contract.JOB_ID + '/executions/' + old_name; copied['name'] = old_name
    frozen.validate_execution(copied, pf, old_name, terminal=terminal)
    return raw['properties']


def validate_replica(value, name):
    raw = native_copy(value); old_name = new_execution_to_old(name)
    require(type(raw) is dict and type(raw.get('value')) is list and len(raw['value']) == 1,
        'aca_named_single_native_replica_required')
    row = raw['value'][0]
    require(type(row) is dict and type(row.get('name')) is str
        and re.fullmatch(re.escape(name) + r'-[a-z0-9]{5,12}', row['name']) is not None
        and row.get('id') == JOB_ID + '/executions/' + name + '/replicas/' + row['name'],
        'aca_named_native_replica_identity_invalid')
    copied = native_copy(raw); shadow = copied['value'][0]
    shadow['name'] = old_name + row['name'][len(name):]
    shadow['id'] = contract.JOB_ID + '/executions/' + old_name + '/replicas/' + shadow['name']
    transport.validate_replica(copied, old_name)
    return native_copy(row)


def acknowledged_execution(value):
    ack = value.get('raw_ack')
    require(type(value.get('http_status')) is int and value['http_status'] == 200
        and type(ack) is dict and set(ack) == {'id', 'name'}, 'aca_named_start_ack_ambiguous_inspect_only')
    name = execution_name(ack['name'])
    require(ack['id'] == JOB_ID + '/executions/' + name, 'aca_named_exact_start_execution_required')
    return name


def parse_console(rows, replica, expected):
    rows, replica = native_copy(rows), native_copy(replica)
    require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict
        and rows[0].get('Group') == replica['name'], 'aca_named_exact_console_replica_required')
    require(type(replica.get('name')) is str and re.fullmatch(re.escape(JOB) + r'-[a-z0-9]{7,12}-[a-z0-9]{5,12}',
        replica['name']) is not None, 'aca_named_replica_prefix_invalid')
    shadow = native_copy(rows); shadow_replica = native_copy(replica)
    shadow_replica['name'] = contract.JOB + replica['name'][len(JOB):]
    shadow[0]['Group'] = shadow_replica['name']
    return frozen.parse_console(shadow, shadow_replica, expected)


class NativeHttpError(ValueError):
    """Public fixed code, bounded complete response body for private evidence."""
    def __init__(self, status, payload):
        super().__init__('aca_named_native_http_rejection_inspect_only')
        require(type(status) is int and type(payload) is bytes and len(payload) <= MAX_BYTES,
            'aca_named_error_response_invalid')
        retained = len(payload) <= MAX_PRIVATE_ERROR_BODY
        self.__diagnostic = dict(native_http_status=status,
            response_body_base64=base64.b64encode(payload).decode('ascii') if retained else None,
            complete_response_body_retained=retained, complete_response_body_unretained=not retained,
            response_body_bytes=len(payload), response_body_sha256=hashlib.sha256(payload).hexdigest(),
            request_headers_recorded=False, response_headers_recorded=False)
    def diagnostic(self):
        return native_copy(self.__diagnostic)


def response_bytes(response):
    pieces, length = [], 0
    for part in response.iter_content(chunk_size=16384):
        require(type(part) is bytes, 'aca_named_native_http_body_invalid')
        length += len(part); require(length <= MAX_BYTES, 'aca_named_native_http_body_oversized'); pieces.append(part)
    return b''.join(pieces)


def route(method, path, body, missing, expected):
    validate_name(JOB)
    require(type(method) is str and type(path) is str and type(missing) is bool and len(path) <= 16384,
        'aca_named_exact_request_required')
    parsed = urlsplit(path); pairs = parse_qsl(parsed.query, keep_blank_values=True); query = dict(pairs)
    require(not parsed.scheme and not parsed.netloc and not parsed.fragment and len(pairs) == len(query),
        'aca_named_exact_request_required')
    target = parsed.path
    if target == JOB_ID or target.startswith(JOB_ID + '/'):
        suffix = target[len(JOB_ID):]
        if method == 'GET':
            require(body is None and (not missing or suffix == ''), 'aca_named_route_denied')
            if suffix in ('', '/executions'): api = transport.JOB_API
            elif re.fullmatch(r'/executions/' + re.escape(JOB) + r'-[a-z0-9]{7,12}', suffix): api = transport.JOB_API
            elif re.fullmatch(r'/executions/' + re.escape(JOB) + r'-[a-z0-9]{7,12}/replicas', suffix): api = transport.REPLICA_API
            else: raise ValueError('aca_named_route_denied')
            require(query == {'api-version': api}, 'aca_named_route_denied')
        else:
            require(not missing and type(body) is dict and ((method, suffix) == ('PUT', '')
                or (method, suffix) == ('POST', '/start')), 'aca_named_route_denied')
            wanted = expected if method == 'PUT' else transport.start_template(expected)
            require(query == {'api-version': transport.JOB_API} and canonical(body) == canonical(wanted), 'aca_named_route_denied')
    else:
        # The old transport is used only as a PURE read-only route validator.
        require(method == 'GET', 'aca_named_identity_or_grant_mutation_forbidden')
        transport._route(method, path, body, missing, None)
    return target


class ArmTransport:
    def __init__(self, credential, session=None, *, runtime_source_bytes, expected_runtime):
        validate_name(JOB)
        self.__expected = canonical(contract.definition(runtime_source_bytes, expected_runtime))
        self.credential, self.token, self.__effects = credential, None, set()
        self.session = requests.Session() if session is None else session
        if session is None:
            self.session.mount('https://', HTTPAdapter(max_retries=0)); self.session.trust_env = False

    def call(self, method, path, *, body=None, missing=False):
        snapshot = None if body is None else native_copy(body)
        target = route(method, path, snapshot, missing, json.loads(self.__expected))
        if method != 'GET':
            effect = (method, target); require(effect not in self.__effects, 'aca_named_effect_consumed')
            self.__effects.add(effect)
        response = None
        try:
            if self.token is None or self.token.expires_on <= time.time() + 120:
                self.token = self.credential.get_token(transport.ARM_AUDIENCE)
            response = self.session.request(method, transport.ARM + path, json=snapshot,
                headers={'Authorization': 'Bearer ' + self.token.token}, allow_redirects=False, stream=True, timeout=(5, 20))
            status = response.status_code; require(type(status) is int, 'aca_named_native_http_status_invalid')
            payload = response_bytes(response)
            if status == 404 and method == 'GET' and missing:
                value = transport.strict_json(payload)
                codes = ('ResourceNotFound', 'RoleAssignmentNotFound') if target == transport.ASSIGNMENT_ID else ('ResourceNotFound',)
                require(type(value) is dict and set(value) == {'error'} and type(value['error']) is dict
                    and set(value['error']) == {'code', 'message'} and value['error']['code'] in codes
                    and type(value['error']['message']) is str and len(value['error']['message']) <= 4096,
                    'aca_named_exact_absence_unproven')
                return None, status
            if status not in ((200,) if method == 'GET' else (200, 201, 202)):
                raise NativeHttpError(status, payload)
            value = transport.strict_json(payload); require(type(value) is dict, 'aca_named_native_http_response_invalid')
            return value, status
        except NativeHttpError: raise
        except Exception: raise ValueError('aca_named_http_unknown_inspect_only') from None
        finally:
            if response is not None:
                try: response.close()
                except Exception: raise ValueError('aca_named_response_close_unknown_inspect_only') from None

    def request(self, path, api, *, method='GET', body=None, missing=False):
        require(type(path) is str and type(api) is str and '?' not in path, 'aca_named_exact_request_required')
        return self.call(method, path + '?api-version=' + api, body=body, missing=missing)

    def assignments(self, scope, principal_id, *, at_scope=False):
        return transport.role_assignments(self, scope, principal_id, at_scope=at_scope)


def arm(dependency):
    return ArmTransport(AzureCliCredential(), runtime_source_bytes=source_bytes(), expected_runtime=dependency['expected_runtime'])


def console_scope(execution, start_utc, end_utc):
    execution_name(execution); transport.ConsoleScope(new_execution_to_old(execution), start_utc, end_utc)
    return dict(execution=execution, start_utc=start_utc, end_utc=end_utc)


def checked_scope(scope):
    require(type(scope) is dict and set(scope) == {'execution', 'start_utc', 'end_utc'}, 'aca_named_console_scope_invalid')
    return console_scope(**scope)


def console_query(scope):
    scope = checked_scope(scope)
    query = transport.ConsoleQuery(transport.ConsoleScope(new_execution_to_old(scope['execution']), scope['start_utc'], scope['end_utc']))
    return query.text.replace("'" + query.scope.execution + "-'", "'" + scope['execution'] + "-'")


def validate_console_response(value, scope, *, http_status=200):
    scope = checked_scope(scope); raw = native_copy(value); shadow = native_copy(raw)
    require(type(shadow) is dict and type(shadow.get('tables')) is list and len(shadow['tables']) == 1
        and type(shadow['tables'][0]) is dict and type(shadow['tables'][0].get('rows')) is list,
        'aca_named_native_console_table_invalid')
    for row in shadow['tables'][0]['rows']:
        require(type(row) is list and len(row) == 4 and type(row[0]) is str
            and re.fullmatch(re.escape(scope['execution']) + r'-[A-Za-z0-9_-]{1,128}', row[0]) is not None,
            'aca_named_native_console_group_invalid')
        row[0] = new_execution_to_old(scope['execution']) + row[0][len(scope['execution']):]
    query = transport.ConsoleQuery(transport.ConsoleScope(new_execution_to_old(scope['execution']), scope['start_utc'], scope['end_utc']))
    transport.validate_console_response(shadow, query, http_status=http_status)
    return [dict(zip((name for name, _kind in transport.CONSOLE_COLUMNS), row)) for row in raw['tables'][0]['rows']]


class LogsTransport:
    def __init__(self, credential, session=None):
        self.credential, self.token = credential, None
        self.session = requests.Session() if session is None else session
        if session is None:
            self.session.mount('https://', HTTPAdapter(max_retries=0)); self.session.trust_env = False
    def call(self, scope):
        scope = checked_scope(scope); body = {'query': console_query(scope), 'timespan': scope['start_utc'] + '/' + scope['end_utc']}
        response = None
        try:
            if self.token is None or self.token.expires_on <= time.time() + 120:
                self.token = self.credential.get_token(transport.LOGS_AUDIENCE)
            response = self.session.request('POST', transport.LOGS_ENDPOINT, json=body,
                headers={'Authorization': 'Bearer ' + self.token.token}, allow_redirects=False, stream=True, timeout=(5, 20))
            require(type(response.status_code) is int and response.status_code == 200, 'aca_named_logs_http_invalid')
            value = transport.strict_json(response_bytes(response)); validate_console_response(value, scope)
            return value
        except Exception: raise ValueError('aca_named_logs_http_unknown') from None
        finally:
            if response is not None:
                try: response.close()
                except Exception: raise ValueError('aca_named_logs_close_unknown') from None


def collect_console(query, scope):
    scope = checked_scope(scope)
    first = validate_console_response(query(native_copy(scope)), scope)
    second = validate_console_response(query(native_copy(scope)), scope)
    require(Counter(canonical(row) for row in first) == Counter(canonical(row) for row in second), 'aca_named_logs_drift')
    rows = sorted(first, key=canonical)
    return dict(rows=rows, row_count=len(rows), inventory_sha256=digest(rows), observed_console_inventory_stable=True,
        late_delivery_excluded=False, zero_log_replica_absence_verified=False, platform_replacement_excluded=False)


def validate_diagnosis(value, pf, intent_raw, outcome_raw):
    value = native_copy(value)
    require(type(value) is dict and set(value) == {'status', 'operation_id', 'trial_operation_id', 'checked_at_utc',
        'intent_receipt', 'outcome_receipt', 'original_job', 'original_name_length', 'original_attempt_consumed',
        'original_automatic_retry', 'original_job_absent_before_and_after', 'no_runtime_execution_observed',
        'correlation_id', 'error_code', 'raw_activity_inventory', 'sources', 'sql_calls', 'model_calls'}
        and value.get('status') == 'explicit_invalid_job_name_rejection_observed'
        and value.get('operation_id') == c.OPERATION_ID and value.get('trial_operation_id') == TRIAL_OPERATION_ID
        and value.get('original_job') == contract.JOB and type(value.get('original_name_length')) is int
        and value['original_name_length'] == 35 and value.get('error_code') == 'ContainerAppInvalidName'
        and value.get('correlation_id') == CORRELATION_ID and value.get('original_attempt_consumed') is True
        and value.get('original_automatic_retry') is False and value.get('original_job_absent_before_and_after') is True
        and value.get('no_runtime_execution_observed') is True and value.get('intent_receipt') == intent_raw['protected_receipt']
        and value.get('outcome_receipt') == outcome_raw['protected_receipt'] and value.get('sources') == pf['sources']
        and type(value.get('sql_calls')) is int and value['sql_calls'] == 0
        and type(value.get('model_calls')) is int and value['model_calls'] == 0,
        'aca_named_exact_consumed_install_diagnosis_required')
    transport.utc(value['checked_at_utc'])
    inventory = value.get('raw_activity_inventory')
    require(type(inventory) is dict and set(inventory) == {'value'} and type(inventory['value']) is list
        and len(inventory['value']) == 2, 'aca_named_complete_activity_pair_required')
    statuses = {}
    for row in inventory['value']:
        require(type(row) is dict and row.get('resourceId') == contract.JOB_ID and row.get('correlationId') == CORRELATION_ID
            and row.get('operationId') == CORRELATION_ID and row.get('operationName', {}).get('value') == 'Microsoft.App/jobs/write'
            and row.get('httpRequest', {}).get('method') == 'PUT'
            and row['httpRequest'].get('uri') == transport.ARM + contract.JOB_ID + '?api-version=' + transport.JOB_API,
            'aca_named_exact_failed_activity_target_required')
        status = row.get('status', {}).get('value'); require(status in ('Started', 'Failed') and status not in statuses,
            'aca_named_exact_failed_activity_status_required'); statuses[status] = row
    require(statuses['Started'].get('eventTimestamp') == FAILURE_STARTED and statuses['Failed'].get('eventTimestamp') == FAILURE_ENDED
        and statuses['Failed'].get('subStatus', {}).get('value') == 'BadRequest'
        and statuses['Failed'].get('properties', {}).get('statusCode') == 'BadRequest', 'aca_named_exact_failure_interval_required')
    message = statuses['Failed']['properties'].get('statusMessage')
    require(type(message) is str and len(message) <= 8192, 'aca_named_exact_failure_code_required')
    failure = c.strict_json(message.encode('utf-8'))
    require(type(failure) is dict and set(failure) == {'error'} and type(failure['error']) is dict
        and set(failure['error']) == {'code', 'message'} and failure['error']['code'] == 'ContainerAppInvalidName'
        and type(failure['error']['message']) is str and 0 < len(failure['error']['message']) <= 8192
        and 'between 2 and 32 characters inclusive' in failure['error']['message'],
        'aca_named_exact_failure_code_required')


def validate_history(records):
    require(type(records) is dict and set(records) == set(HISTORY_PINS), 'aca_named_complete_history_required')
    values = {name: previous.q.readback.receipt_binding(raw, 'aca-runtime-continuation-' + name.removesuffix('.json'), HISTORY_PINS[name])
        for name, raw in records.items()}
    pf = values['preflight.json']; pf_raw = records['preflight.json']
    require(pf.get('status') == 'prepared_secretless_aca_runtime_continuation' and pf.get('operation_id') == c.OPERATION_ID
        and pf.get('trial_operation_id') == TRIAL_OPERATION_ID and all(pf.get(k) is False for k in FALSE_FLAGS),
        'aca_named_original_preflight_required')
    c.validate_sources(pf['history']['sources'], pf['sources']); previous.q.canary.validate_correction_checks(pf['local_checks'])
    require(canonical(pf['plan']) == canonical(plan(pf['prerequisite'])) and canonical(pf['identity']) == canonical(pf['history']['identity']),
        'aca_named_original_plan_or_identity_changed')
    grant = values['grant-acceptance.json']
    principal = pf['identity']['properties']['principalId']
    grant_expected = c.intent_value('grant', pf_raw, transport.ASSIGNMENT_ID, transport.ROLE_API,
        'PUT', transport.assignment_body(principal), {'identity': pf['history']['receipts']['identity-ack.json']})
    grant_intent, grant_ack = values['grant-intent.json'], values['grant-ack.json']
    require(set(grant_intent) == set(grant_expected) | {'status', 'reservation_receipt'}
        and canonical({k: grant_intent.get(k) for k in grant_expected}) == canonical(grant_expected)
        and grant_intent.get('status') == 'reserved_one_exact_cloud_effect'
        and set(grant_ack) == {'status', 'operation_id', 'trial_operation_id', 'stage', 'intent_receipt', 'http_status',
            'raw_ack', 'attempt_consumed', 'automatic_retry'}
        and grant_ack.get('status') == 'one_exact_cloud_effect_acknowledged' and grant_ack.get('stage') == 'grant'
        and grant_ack.get('operation_id') == c.OPERATION_ID and grant_ack.get('trial_operation_id') == TRIAL_OPERATION_ID
        and grant_ack.get('intent_receipt') == records['grant-intent.json']['protected_receipt']
        and type(grant_ack.get('http_status')) is int and grant_ack['http_status'] in (200, 201, 202)
        and grant_ack.get('attempt_consumed') is True and grant_ack.get('automatic_retry') is False,
        'aca_named_complete_accepted_grant_chain_required')
    transport.validate_assignment(grant_ack['raw_ack'], principal)
    expected = c.intent_value('install', pf_raw, contract.JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'],
        {'grant': records['grant-acceptance.json']['protected_receipt']})
    intent, outcome = values['install-intent.json'], values['install-outcome.json']
    require(set(intent) == set(expected) | {'status', 'reservation_receipt'}
        and canonical({k: intent.get(k) for k in expected}) == canonical(expected)
        and intent.get('status') == 'reserved_one_exact_cloud_effect'
        and outcome.get('status') == 'effect_unknown_ack_unverified_inspect_only' and outcome.get('stage') == 'install'
        and outcome.get('operation_id') == c.OPERATION_ID and outcome.get('trial_operation_id') == TRIAL_OPERATION_ID
        and outcome.get('intent_receipt') == records['install-intent.json']['protected_receipt']
        and outcome.get('reservation_receipt') == intent['reservation_receipt'] and outcome.get('attempt_consumed') is True
        and outcome.get('automatic_retry') is False and outcome.get('no_effects_verified') is False,
        'aca_named_original_install_remains_consumed')
    require(grant.get('status') == 'exact_registry_pull_role_observed' and grant.get('operation_id') == c.OPERATION_ID
        and grant.get('trial_operation_id') == TRIAL_OPERATION_ID
        and grant.get('ack_receipt') == records['grant-ack.json']['protected_receipt']
        and grant.get('identity_receipt') == pf['history']['receipts']['identity-ack.json']
        and all(grant.get(k) is False for k in FALSE_FLAGS), 'aca_named_accepted_grant_required')
    transport.validate_assignment(grant['assignment'], principal)
    validate_diagnosis(values['install-diagnosis.json'], pf, records['install-intent.json'], records['install-outcome.json'])
    return dict(continuation_preflight_record=pf_raw, continuation_preflight=pf,
        receipts={name: raw['protected_receipt'] for name, raw in records.items()}, sources=pf['sources'],
        identity=pf['identity'], resource_guards=pf['resource_guards'], prerequisite=pf['prerequisite'],
        grant=grant, raw_grant_ack=grant_ack['raw_ack'], grant_reservation=grant_intent['reservation_receipt'],
        failure_diagnosis=values['install-diagnosis.json'], install_reservation=intent['reservation_receipt'])


def old_start_absent():
    require(not any((c.FOLDER / name).exists() for name in ('install-ack.json', 'install-acceptance.json',
        'start-intent.json', 'start-ack.json', 'start-outcome.json', 'replica-binding.json', 'acceptance.json')),
        'aca_named_original_execution_or_install_ack_exists')
    try: c.reservation_blob('start').get_blob_properties(timeout=10, retry_total=0)
    except HttpResponseError as error:
        try: c.validate_original_reservation_absence(error.status_code, getattr(error, 'error_code', None))
        except ValueError: raise ValueError('aca_named_original_start_absence_unproven') from None
        previous.q.deploy.archive_protection(); return
    raise ValueError('aca_named_original_start_attempt_exists')


def history():
    records = {name: previous.q.readback.read_local(c.FOLDER, name) for name in HISTORY_PINS}
    result = validate_history(records)
    for name, raw in records.items(): previous.q.deploy.protected(raw, 'aca-runtime-continuation-' + name.removesuffix('.json'))
    require(canonical(c.history()) == canonical(result['continuation_preflight']['history']), 'aca_named_original_identity_history_drift')
    pf = result['continuation_preflight']; raw = result['continuation_preflight_record']
    c.reservation_readback('install', result['install_reservation'], c.intent_value('install', raw,
        contract.JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'], {'grant': result['receipts']['grant-acceptance.json']}))
    c.reservation_readback('grant', result['grant_reservation'], c.intent_value('grant', raw,
        transport.ASSIGNMENT_ID, transport.ROLE_API, 'PUT', transport.assignment_body(pf['identity']['properties']['principalId']),
        {'identity': pf['history']['receipts']['identity-ack.json']}))
    old_start_absent()
    grant_binding({'history': result}, c.arm(pf['prerequisite']))
    return result


def prerequisite(original):
    # Reconstruct protected ACR history with historical source maps, not by
    # rebinding the old validator to the current 729-source manifest.
    raws, values = {}, {}
    for name, pin in frozen.PINS.items():
        raw = previous.q.readback.read_local(previous.FOLDER, name)
        previous.q.readback.receipt_binding(raw, 'standard-pull-' + name.removesuffix('.json'), pin)
        raws[name] = raw; values[name] = previous.q.deploy.protected(raw, 'standard-pull-' + name.removesuffix('.json'))
    pf, accepted = values['preflight.json'], values['acceptance.json']
    frozen.validate_source_extension(pf['guards']['sources'], original['continuation_preflight']['history']['sources'])
    c.validate_sources(original['continuation_preflight']['history']['sources'], original['sources'])
    validate_sources(original['sources'], previous.sources()); previous.q.canary.validate_correction_checks(pf['local_checks'])
    require(pf.get('status') == 'prepared_standard_pull_runtime_observation' and pf.get('operation_id') == previous.OPERATION_ID
        and canonical(pf.get('plan')) == canonical(previous.probe_plan())
        and accepted.get('status') == 'secretless_default_user_runtime_observed' and accepted.get('operation_id') == previous.OPERATION_ID
        and accepted.get('preflight_receipt') == raws['preflight.json']['protected_receipt']
        and accepted.get('intent_receipt') == raws['dispatch-intent.json']['protected_receipt']
        and accepted.get('ack_receipt') == raws['dispatch-ack.json']['protected_receipt']
        and accepted.get('reservation_receipt') == values['dispatch-intent.json']['reservation_receipt']
        and accepted.get('acceptance_recorded') is True and accepted.get('engine_image_digest_observed') is True
        and accepted.get('candidate_runtime_metadata_verified') is True and all(accepted.get(k) is False for k in previous.FALSE_FLAGS),
        'aca_named_exact_acr_dependency_required')
    name = previous.chain(raws['preflight.json'], pf, raws['dispatch-intent.json'], values['dispatch-intent.json'], values['dispatch-ack.json'])
    require(name == 'ck84', 'aca_named_exact_acr_run_required')
    before = previous.show(name); logs = previous.call_cli('logs', name); after = previous.show(name)
    observed = previous.parse_logs(logs, pf['plan'])
    require(before == after == accepted['raw_run_before'] == accepted['raw_run_after'] and before['status'] == 'Succeeded'
        and logs.decode('utf-8') == accepted['full_logs'] and len(logs) == accepted['full_logs_bytes']
        and hashlib.sha256(logs).hexdigest() == accepted['full_logs_sha256'] and canonical(observed) == canonical(accepted['observed']),
        'aca_named_fresh_acr_evidence_changed')
    result = dict(receipts={n: raw['protected_receipt'] for n, raw in raws.items()}, frozen_sources=pf['guards']['sources'],
        frozen_resources={k: v for k, v in pf['guards'].items() if k != 'sources'}, expected_runtime=observed['runtime'], observed=observed)
    require(canonical(result) == canonical(original['prerequisite']), 'aca_named_original_acr_dependency_changed')
    return result


def resource_guards():
    previous.q.canary.host.checked_owner(); baseline = previous.q.deploy.baseline()
    for name in (previous.q.canary.JOB, contract.JOB, JOB): baseline['other_jobs'].pop(name, None)
    require(baseline.get('job_inventory_complete') is True and len(baseline['other_jobs']) == 34,
        'aca_named_complete_existing_job_inventory_required')
    old_job = previous.q.deploy.request('GET', previous.q.canary.PATH + '?api-version=2024-03-01')
    previous.q.canary.validate_job(old_job)
    return dict(baseline=baseline, canary_settings_sha256=previous.q.deploy.settings(old_job),
        registry=previous.q.registry_metadata(), isolated_host_receipt=previous.q.canary.historical_host())


def save(value, name):
    require(re.fullmatch(r'[a-z][a-z-]*\.json', name) is not None and 0 < len(canonical(value)) <= MAX_BYTES,
        'aca_named_evidence_name_or_size_invalid')
    kind = KIND_PREFIX + name.removesuffix('.json')
    with (FOLDER / name).open('x', encoding='utf-8') as output:
        previous.q.deploy.archive_protection()
        raw = dict(value, protected_receipt=previous.q.deploy.retain(value, 'owner-task-pg-' + kind))
        previous.q.deploy.archive_protection(); output.write(json.dumps(raw, sort_keys=True, indent=2))
    previous.q.deploy.protected(raw, kind); return raw


def load(name):
    raw = previous.q.readback.read_local(FOLDER, name)
    return raw, previous.q.deploy.protected(raw, KIND_PREFIX + name.removesuffix('.json'))


def reservation_blob(stage, version=None):
    require(stage in STAGES, 'aca_named_reservation_stage_invalid'); previous.q.deploy.archive_protection()
    service = BlobServiceClient('https://' + previous.q.deploy.ACCOUNT + '.blob.core.windows.net', credential=AzureCliCredential(),
        retry_total=0, retry_to_secondary=False, connection_timeout=5, read_timeout=10, permit_redirects=False, redirect_max=0)
    return service.get_blob_client(previous.q.deploy.CONTAINER, PREFIX + stage + '-reservation.json', version_id=version)


def reservation_readback(stage, receipt, value):
    require(stage in STAGES, 'aca_named_reservation_stage_invalid'); payload = canonical(value)
    require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'etag', 'protected_readback_verified'}
        and receipt.get('blob') == PREFIX + stage + '-reservation.json' and receipt.get('sha256') == hashlib.sha256(payload).hexdigest()
        and receipt.get('protected_readback_verified') is True and type(receipt.get('version_id')) is str
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id']) is not None
        and type(receipt.get('etag')) is str and re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag']) is not None
        and 0 < len(payload) <= MAX_BYTES, 'aca_named_exact_reservation_required')
    blob = reservation_blob(stage, receipt['version_id']); props = blob.get_blob_properties(timeout=10, retry_total=0)
    require(props.version_id == receipt['version_id'] and props.etag == receipt['etag'] and props.size == len(payload)
        and props.blob_type == 'BlockBlob' and blob.download_blob(offset=0, length=len(payload) + 1, etag=props.etag,
            match_condition=MatchConditions.IfNotModified, max_concurrency=1, timeout=10, retry_total=0).readall() == payload,
        'aca_named_reservation_readback_failed'); previous.q.deploy.archive_protection()


def reserve(stage, value):
    require(stage in STAGES, 'aca_named_reservation_stage_invalid'); payload = canonical(value)
    require(0 < len(payload) <= MAX_BYTES, 'aca_named_reservation_size_invalid')
    ack = reservation_blob(stage).upload_blob(payload, length=len(payload), blob_type='BlockBlob', overwrite=False,
        match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
    receipt = dict(blob=PREFIX + stage + '-reservation.json', sha256=hashlib.sha256(payload).hexdigest(),
        version_id=ack.get('version_id'), etag=ack.get('etag'), protected_readback_verified=True)
    reservation_readback(stage, receipt, value); return receipt


def grant_binding(pf, client):
    prior = pf['history']; identity = c.identity_observation(client)
    require(canonical(identity) == canonical(prior['identity']), 'aca_named_acknowledged_identity_changed')
    principal = identity['properties']['principalId']
    assignment = get(client, transport.ASSIGNMENT_ID, transport.ROLE_API); transport.validate_assignment(assignment, principal)
    require(canonical(assignment) == canonical(prior['grant']['assignment']), 'aca_named_accepted_grant_native_changed')
    permission = roles(client, principal, granted=True)
    require(canonical(permission) == canonical(prior['grant']['roles']), 'aca_named_accepted_grant_role_inventory_changed')
    require(get(client, contract.JOB_ID, transport.JOB_API, missing=True) is None, 'aca_named_old_invalid_job_appeared')
    return prior['receipts']['grant-acceptance.json'], identity, permission


def prepare():
    validate_name(JOB); require(not (FOLDER / 'preflight.json').exists(), 'aca_named_preflight_already_reserved')
    before = previous.sources(); old = history(); validate_sources(old['sources'], before)
    superseded = supersession(); dep = prerequisite(old)
    policy = validate_local_qa_policy(local_qa_policy()); checks = run_local_checks(); previous.q.canary.validate_correction_checks(checks)
    superseded_absence()
    current = resource_guards(); client = arm(dep); temp = {'history': old}
    receipt, identity, permission = grant_binding(temp, client)
    require(get(client, JOB_ID, transport.JOB_API, missing=True) is None, 'aca_named_new_job_must_be_absent')
    env = environment(client)
    require(canonical(current) == canonical(old['resource_guards']) == canonical(dep['frozen_resources']) and previous.sources() == before,
        'aca_named_source_or_resource_changed_during_qa')
    return save(dict(status='prepared_secretless_valid_name_aca_runtime', operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
        checked_at_utc=datetime.now(timezone.utc).isoformat(), job=JOB, job_id=JOB_ID, sources=before, history=old,
        prerequisite=dep, resource_guards=current, plan=plan(dep), environment=env, identity=identity,
        accepted_grant_receipt=receipt, initial_roles=permission, local_checks=checks, local_qa_policy=policy,
        supersession=superseded, sql_calls=0, model_calls=0,
        daily_schedule_hkt='18:00', **{k: False for k in FALSE_FLAGS}), 'preflight.json')


def prepared(*, cloud=True):
    validate_name(JOB); require(type(cloud) is bool, 'aca_named_cloud_flag_invalid'); raw, pf = load('preflight.json')
    require(pf.get('status') == 'prepared_secretless_valid_name_aca_runtime' and pf.get('operation_id') == OPERATION_ID
        and pf.get('trial_operation_id') == TRIAL_OPERATION_ID and pf.get('job') == JOB and pf.get('job_id') == JOB_ID
        and all(pf.get(k) is False for k in FALSE_FLAGS), 'aca_named_exact_preflight_required')
    previous.q.canary.validate_correction_checks(pf['local_checks']); validate_local_qa_policy(pf.get('local_qa_policy'))
    superseded = validate_supersession(pf.get('supersession'))
    validate_sources(pf['history']['sources'], previous.sources()); validate_supersession_sources(superseded['sources'], previous.sources())
    require(previous.sources() == pf['sources'] and canonical(pf['plan']) == canonical(plan(pf['prerequisite']))
        and canonical(pf['identity']) == canonical(pf['history']['identity'])
        and pf['accepted_grant_receipt'] == pf['history']['receipts']['grant-acceptance.json'], 'aca_named_source_or_plan_drift')
    if cloud:
        require(canonical(supersession()) == canonical(pf['supersession']), 'aca_named_supersession_history_changed')
        require(canonical(history()) == canonical(pf['history']) and canonical(prerequisite(pf['history'])) == canonical(pf['prerequisite'])
            and canonical(resource_guards()) == canonical(pf['resource_guards']), 'aca_named_cloud_dependency_drift')
        grant_binding(pf, arm(pf['prerequisite']))
    return raw, pf


def start_body(pf):
    return frozen.start_body(pf)


def validate_prior(stage, prior):
    require(stage in STAGES and type(prior) is dict, 'aca_named_mutation_stage_invalid')
    key, kind = ('grant', 'aca-runtime-continuation-grant-acceptance') if stage == 'install' else ('install', KIND_PREFIX + 'install-acceptance')
    require(set(prior) == {key}, 'aca_named_exact_prior_required'); receipt = prior[key]
    require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'protected_readback_verified'}
        and receipt.get('protected_readback_verified') is True and type(receipt.get('sha256')) is str
        and re.fullmatch('[0-9a-f]{64}', receipt['sha256']) is not None
        and receipt.get('blob') == 'acceptance/gateway-read-owner-task-pg-' + kind + '-' + receipt['sha256'] + '.json'
        and type(receipt.get('version_id')) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id']),
        'aca_named_exact_prior_receipt_required'); transport.utc(receipt['version_id'])


def validate_effect_route(stage, path, api, method):
    validate_name(JOB); require(stage in STAGES, 'aca_named_mutation_stage_invalid')
    wanted = (JOB_ID, transport.JOB_API, 'PUT') if stage == 'install' else (JOB_ID + '/start', transport.JOB_API, 'POST')
    require(all(type(v) is str for v in (path, api, method)) and (path, api, method) == wanted, 'aca_named_exact_effect_route_required')


def validate_effect_request(stage, path, api, method, body, pf, prior):
    validate_effect_route(stage, path, api, method); validate_prior(stage, prior)
    wanted = pf['plan']['definition'] if stage == 'install' else start_body(pf)
    require(type(body) is dict and canonical(body) == canonical(wanted), 'aca_named_exact_effect_body_required')
    if stage == 'install': require(prior['grant'] == pf['accepted_grant_receipt'], 'aca_named_accepted_grant_prior_required')


def intent_value(stage, pf_raw, path, api, method, body, prior):
    validate_effect_route(stage, path, api, method); validate_prior(stage, prior)
    return dict(operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID, stage=stage, job=JOB, job_id=JOB_ID,
        preflight_receipt=pf_raw['protected_receipt'], path=path, api=api, method=method, body=body, body_sha256=digest(body), prior_receipts=prior)


def inventory(client):
    value = get(client, JOB_ID + '/executions', transport.JOB_API)
    require(type(value) is dict and set(value) <= {'value', 'nextLink'} and type(value.get('value')) is list
        and value.get('nextLink') in (None, '') and len(value['value']) <= 1, 'aca_named_complete_single_execution_inventory_required')
    return value


def job_observation(client, pf, identity):
    return validate_job_metadata(get(client, JOB_ID, transport.JOB_API), pf['prerequisite']['expected_runtime'],
        identity['properties']['principalId'], identity['properties']['clientId'])


def pre_effect_checks(stage, client, pf, body):
    require(stage in STAGES, 'aca_named_mutation_stage_invalid'); superseded_absence(); _, identity, _ = grant_binding(pf, client)
    if stage == 'install': require(get(client, JOB_ID, transport.JOB_API, missing=True) is None, 'aca_named_job_appeared_after_reservation')
    else:
        job_observation(client, pf, identity); require(not inventory(client)['value'], 'aca_named_execution_appeared_after_reservation')


def mutate(stage, pf_raw, pf, path, api, method, body, prior):
    pf_raw, pf, body, prior = (native_copy(v) for v in (pf_raw, pf, body, prior))
    validate_effect_request(stage, path, api, method, body, pf, prior)
    require(not any((FOLDER / (stage + '-' + suffix + '.json')).exists() for suffix in ('intent', 'ack', 'outcome')),
        'aca_named_effect_consumed_inspect_only')
    superseded_absence()
    value = intent_value(stage, pf_raw, path, api, method, body, prior); receipt = reserve(stage, native_copy(value))
    intent = save(dict(native_copy(value), status='reserved_one_exact_cloud_effect', reservation_receipt=receipt), stage + '-intent.json')
    require(previous.sources() == pf['sources'], 'aca_named_source_changed_after_reservation'); reservation_readback(stage, receipt, native_copy(value))
    pre_effect_checks(stage, arm(pf['prerequisite']), native_copy(pf), native_copy(body))
    try: ack, status = arm(pf['prerequisite']).request(path, api, method=method, body=native_copy(body))
    except Exception as error:
        try:
            diagnostic_unretained = False
            try: private = error.diagnostic() if type(error) is NativeHttpError else None
            except Exception: private = None; diagnostic_unretained = True
            save(dict(status='effect_unknown_ack_unverified_inspect_only', operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
                stage=stage, intent_receipt=intent['protected_receipt'], reservation_receipt=receipt, attempt_consumed=True,
                automatic_retry=False, no_effects_verified=False, private_native_http_diagnostic=private,
                native_http_diagnostic_unretained=diagnostic_unretained, sql_calls=0, model_calls=0), stage + '-outcome.json')
        except Exception: raise ValueError('aca_named_effect_outcome_unretained_attempt_consumed_inspect_only') from None
        raise ValueError('aca_named_effect_unknown_attempt_consumed_inspect_only') from None
    return save(dict(status='one_exact_cloud_effect_acknowledged', operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
        stage=stage, intent_receipt=intent['protected_receipt'], http_status=status, raw_ack=ack,
        attempt_consumed=True, automatic_retry=False), stage + '-ack.json')


def chain(stage, pf_raw, path, api, method, body, prior):
    intent_raw, intent = load(stage + '-intent.json'); ack_raw, ack = load(stage + '-ack.json')
    expected = intent_value(stage, pf_raw, path, api, method, body, prior)
    require(set(intent) == set(expected) | {'status', 'reservation_receipt'}
        and canonical({k: intent.get(k) for k in expected}) == canonical(expected) and intent.get('status') == 'reserved_one_exact_cloud_effect'
        and ack.get('status') == 'one_exact_cloud_effect_acknowledged' and ack.get('operation_id') == OPERATION_ID
        and ack.get('trial_operation_id') == TRIAL_OPERATION_ID and ack.get('stage') == stage
        and ack.get('intent_receipt') == intent_raw['protected_receipt'] and ack.get('attempt_consumed') is True
        and ack.get('automatic_retry') is False and type(ack.get('http_status')) is int and ack['http_status'] in (200, 201, 202),
        'aca_named_effect_chain_invalid'); reservation_readback(stage, intent['reservation_receipt'], expected)
    return ack_raw, ack


def install():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); receipt, _, _ = grant_binding(pf, client)
    require(get(client, JOB_ID, transport.JOB_API, missing=True) is None, 'aca_named_job_create_only_absence_required')
    return mutate('install', pf_raw, pf, JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'], {'grant': receipt})


def install_chain(pf_raw, pf, client):
    receipt, identity, permission = grant_binding(pf, client)
    ack_raw, ack = chain('install', pf_raw, JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'], {'grant': receipt})
    require(type(ack.get('raw_ack')) is dict and ack['raw_ack'].get('id') == JOB_ID and ack['raw_ack'].get('name') == JOB,
        'aca_named_job_ack_identity_invalid')
    job = job_observation(client, pf, identity); raw, value = load('install-acceptance.json')
    require(value.get('status') == 'secretless_valid_name_job_installed' and value.get('operation_id') == OPERATION_ID
        and value.get('trial_operation_id') == TRIAL_OPERATION_ID and value.get('ack_receipt') == ack_raw['protected_receipt']
        and value.get('grant_receipt') == receipt and canonical(value.get('job')) == canonical(job)
        and value.get('environment') == environment(client), 'aca_named_installed_job_drift')
    return raw, job, identity, permission


def install_verify():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); receipt, identity, _ = grant_binding(pf, client)
    ack_raw, ack = chain('install', pf_raw, JOB_ID, transport.JOB_API, 'PUT', pf['plan']['definition'], {'grant': receipt})
    require(type(ack.get('raw_ack')) is dict and ack['raw_ack'].get('id') == JOB_ID and ack['raw_ack'].get('name') == JOB,
        'aca_named_job_ack_identity_invalid')
    job = job_observation(client, pf, identity); observed = inventory(client); require(not observed['value'], 'aca_named_job_not_unexecuted')
    return save(dict(status='secretless_valid_name_job_installed', operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
        ack_receipt=ack_raw['protected_receipt'], grant_receipt=receipt, job=job, environment=environment(client),
        empty_execution_inventory=observed, sql_calls=0, model_calls=0, **{k: False for k in FALSE_FLAGS}), 'install-acceptance.json')


def start():
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); raw, _, _, _ = install_chain(pf_raw, pf, client)
    capture_permitted()
    require(not inventory(client)['value'], 'aca_named_start_requires_empty_execution_inventory')
    ack_raw = mutate('start', pf_raw, pf, JOB_ID + '/start', transport.JOB_API, 'POST', start_body(pf), {'install': raw['protected_receipt']})
    try: capture_bound(pf, ack_raw)
    except CapturePending:
        print(json.dumps(dict(status='native_replica_capture_pending_read_only_inspection_required', start_attempt_consumed=True,
            automatic_start_retry=False)), flush=True)
    return ack_raw


def start_chain(pf_raw, pf):
    install_raw, _ = load('install-acceptance.json')
    raw, value = chain('start', pf_raw, JOB_ID + '/start', transport.JOB_API, 'POST', start_body(pf), {'install': install_raw['protected_receipt']})
    return raw, acknowledged_execution(value)


class CapturePending(ValueError):
    """Only explicitly validated readiness, never malformed metadata."""
    def __init__(self):
        super().__init__('aca_named_running_capture_pending_read_only_inspection_required')


def capture_not_found(error):
    if type(error) is not NativeHttpError: return False
    evidence = error.diagnostic()
    if evidence['native_http_status'] != 404 or evidence['complete_response_body_retained'] is not True: return False
    try:
        value = transport.strict_json(base64.b64decode(evidence['response_body_base64'], validate=True))
        return (type(value) is dict and set(value) == {'error'} and type(value['error']) is dict
            and set(value['error']) == {'code', 'message'} and value['error']['code'] == 'ResourceNotFound'
            and type(value['error']['message']) is str and len(value['error']['message']) <= 4096)
    except ValueError: return False


def capture_control_blob(version=None):
    previous.q.deploy.archive_protection()
    service = BlobServiceClient('https://' + previous.q.deploy.ACCOUNT + '.blob.core.windows.net', credential=AzureCliCredential(),
        retry_total=0, retry_to_secondary=False, connection_timeout=5, read_timeout=10, permit_redirects=False, redirect_max=0)
    return service.get_blob_client(previous.q.deploy.CONTAINER, PREFIX + 'capture-stop.json', version_id=version)


def capture_permitted():
    require(not any((FOLDER / name).exists() for name in ('capture-stop-intent.json', 'capture-diagnostic.json')),
        'aca_named_capture_previously_unqualified_inspect_only')
    try: capture_control_blob().get_blob_properties(timeout=10, retry_total=0)
    except HttpResponseError as error:
        try: c.validate_original_reservation_absence(error.status_code, getattr(error, 'error_code', None))
        except ValueError: raise ValueError('aca_named_capture_stop_absence_unproven') from None
        previous.q.deploy.archive_protection(); return
    raise ValueError('aca_named_capture_previously_unqualified_inspect_only')


def capture_stop(ack_raw):
    # A durable fixed marker closes both same-call polling and later commands.
    # Exception text/type/credentials are not incorporated into this evidence.
    value = dict(status='capture_validation_unqualified_inspect_only', operation_id=OPERATION_ID,
        trial_operation_id=TRIAL_OPERATION_ID, start_ack_receipt=ack_raw['protected_receipt'],
        reason='native_capture_validation_failed', start_attempt_consumed=True, automatic_start_retry=False,
        capture_or_collection_authorized=False, sql_calls=0, model_calls=0, **{k: False for k in FALSE_FLAGS})
    payload = canonical(value)
    try:
        # Local exclusive intent also consumes validation before any uncertain
        # remote outcome; a failed upload cannot silently re-open this process.
        with (FOLDER / 'capture-stop-intent.json').open('x', encoding='utf-8') as output:
            output.write(json.dumps(value, sort_keys=True, indent=2))
        ack = capture_control_blob().upload_blob(payload, length=len(payload), blob_type='BlockBlob', overwrite=False,
            match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
        receipt = dict(blob=PREFIX + 'capture-stop.json', sha256=hashlib.sha256(payload).hexdigest(),
            version_id=ack.get('version_id'), etag=ack.get('etag'), protected_readback_verified=True)
        require(type(receipt['version_id']) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id'])
            and type(receipt['etag']) is str and re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag']), 'aca_named_capture_stop_ack_invalid')
        blob = capture_control_blob(receipt['version_id']); props = blob.get_blob_properties(timeout=10, retry_total=0)
        require(props.version_id == receipt['version_id'] and props.etag == receipt['etag'] and props.size == len(payload)
            and props.blob_type == 'BlockBlob' and blob.download_blob(offset=0, length=len(payload) + 1, etag=props.etag,
                match_condition=MatchConditions.IfNotModified, max_concurrency=1, timeout=10, retry_total=0).readall() == payload,
            'aca_named_capture_stop_readback_failed')
        previous.q.deploy.archive_protection()
        return save(dict(value, capture_stop_receipt=receipt), 'capture-diagnostic.json')
    except Exception: raise ValueError('aca_named_capture_stop_outcome_unverified_inspect_only') from None


def capture_bound(pf, ack_raw):
    capture_permitted()
    try: return capture_readiness_bound(pf, ack_raw)
    except CapturePending: raise
    except Exception:
        capture_stop(ack_raw)
        raise ValueError('aca_named_capture_validation_unqualified_inspect_only') from None


def capture_readiness_bound(pf, ack_raw):
    require(previous.sources() == pf['sources'], 'aca_named_source_drift_before_capture')
    name = acknowledged_execution(ack_raw); client = arm(pf['prerequisite'])
    # Only bounded read-only GETs may be repeated while Azure exposes the
    # already acknowledged execution. No START or full protected-chain reload.
    deadline = time.monotonic() + 90
    def capture_get(path, api):
        if time.monotonic() >= deadline: raise CapturePending()
        try: result = get(client, path, api)
        except NativeHttpError as error:
            if capture_not_found(error): raise CapturePending() from None
            raise
        if time.monotonic() >= deadline: raise CapturePending()
        return result
    for attempt in range(45):
        try:
            before = capture_get(JOB_ID + '/executions/' + name, transport.JOB_API)
            if type(before) is dict and type(before.get('properties')) is dict:
                require(before['properties'].get('status') not in ('Succeeded', 'Failed', 'Stopped'),
                    'aca_named_running_capture_window_missed_inspect_only')
            validate_execution(before, pf, name, terminal=False)
            if before['properties']['status'] == 'Processing': raise CapturePending()
            replicas = capture_get(JOB_ID + '/executions/' + name + '/replicas', transport.REPLICA_API)
            require(type(replicas) is dict and set(replicas) <= {'value', 'nextLink'}
                and replicas.get('nextLink') in (None, '') and type(replicas.get('value')) is list,
                'aca_named_complete_native_replica_inventory_required')
            if not replicas['value']: raise CapturePending()
            binding = validate_replica(replicas, name)
            after = capture_get(JOB_ID + '/executions/' + name, transport.JOB_API)
            require(canonical(before) == canonical(after), 'aca_named_execution_drift_during_capture')
            break
        except CapturePending:
            if attempt == 44 or time.monotonic() >= deadline:
                raise CapturePending() from None
            time.sleep(1)
    require(previous.sources() == pf['sources'], 'aca_named_source_drift_after_capture')
    return save(dict(status='native_running_replica_binding_observed', operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID,
        start_ack_receipt=ack_raw['protected_receipt'], raw_execution=before, raw_replicas=replicas, binding=binding,
        checked_at_utc=datetime.now(timezone.utc).isoformat(), sql_calls=0, model_calls=0, **{k: False for k in FALSE_FLAGS}), 'replica-binding.json')


def capture():
    capture_permitted()
    pf_raw, pf = prepared(cloud=False); ack_raw, _ = start_chain(pf_raw, pf); raw, ack = load('start-ack.json')
    require(raw['protected_receipt'] == ack_raw['protected_receipt'], 'aca_named_capture_ack_changed')
    return capture_bound(pf, dict(ack, protected_receipt=ack_raw['protected_receipt']))


def collect(verify_only=False):
    require(type(verify_only) is bool, 'aca_named_verify_flag_invalid')
    capture_permitted()
    pf_raw, pf = prepared(); client = arm(pf['prerequisite']); install_raw, job, identity, permission = install_chain(pf_raw, pf, client)
    ack_raw, name = start_chain(pf_raw, pf); capture_raw, captured = load('replica-binding.json')
    require(captured.get('status') == 'native_running_replica_binding_observed' and captured.get('operation_id') == OPERATION_ID
        and captured.get('trial_operation_id') == TRIAL_OPERATION_ID and captured.get('start_ack_receipt') == ack_raw['protected_receipt'],
        'aca_named_capture_chain_invalid')
    validate_execution(captured['raw_execution'], pf, name, terminal=False); replica = validate_replica(captured['raw_replicas'], name)
    require(canonical(replica) == canonical(captured['binding']), 'aca_named_native_replica_mapping_changed')
    before = get(client, JOB_ID + '/executions/' + name, transport.JOB_API); props = validate_execution(before, pf, name, terminal=True)
    require(props['startTime'] == captured['raw_execution']['properties']['startTime'], 'aca_named_execution_start_changed')
    scope = console_scope(name, (transport.utc(props['startTime']) - timedelta(minutes=1)).isoformat(),
        (transport.utc(props['endTime']) + timedelta(minutes=10)).isoformat())
    console = collect_console(LogsTransport(AzureCliCredential()).call, scope)
    after = get(client, JOB_ID + '/executions/' + name, transport.JOB_API); inv = inventory(client)
    require(canonical(before) == canonical(after) and len(inv['value']) == 1 and inv['value'][0].get('name') == name
        and inv['value'][0].get('id') == JOB_ID + '/executions/' + name, 'aca_named_execution_family_drift')
    require(canonical(job_observation(client, pf, identity)) == canonical(job)
        and canonical(roles(client, identity['properties']['principalId'], granted=True)) == canonical(permission)
        and canonical(resource_guards()) == canonical(pf['resource_guards']) and previous.sources() == pf['sources'],
        'aca_named_terminal_guards_changed')
    rows, observed = console['rows'], None
    if props['status'] == 'Succeeded':
        require(rows, 'aca_named_logs_pending_read_only_recollect_no_start')
        try: observed = parse_console(rows, replica, pf['prerequisite']['observed'])
        except ValueError: pass
    qualified = observed is not None
    value = dict(status='secretless_valid_name_aca_runtime_observed' if qualified else 'terminal_valid_name_aca_runtime_unqualified',
        operation_id=OPERATION_ID, trial_operation_id=TRIAL_OPERATION_ID, preflight_receipt=pf_raw['protected_receipt'],
        install_receipt=install_raw['protected_receipt'], start_ack_receipt=ack_raw['protected_receipt'], capture_receipt=capture_raw['protected_receipt'],
        raw_execution_before=before, raw_execution_after=after, complete_execution_inventory=inv, complete_console_events=rows,
        complete_console_observation=console, complete_console_sha256=digest(rows), observed=observed, current_job=job, current_roles=permission,
        requested_image_digest_verified=True, image_runtime_metadata_verified=qualified, registry_pull_success_observed=qualified,
        configured_container_identity_lifecycle_none_verified=True, configured_database_model_keyvault_secrets_absent=True,
        attempt_consumed=True, automatic_retry=False, acceptance_recorded=qualified, sql_calls=0, model_calls=0,
        daily_schedule_hkt='18:00', **{k: False for k in FALSE_FLAGS})
    filename = 'acceptance.json' if qualified else 'terminal-diagnostic.json'
    if verify_only:
        raw, retained = load(filename); require(canonical(retained) == canonical(value), 'aca_named_live_and_retained_evidence_differs')
        return dict(status='independent_secretless_valid_name_aca_runtime_verified' if qualified else 'independent_valid_name_aca_diagnostic_verified',
            protected_receipt=raw['protected_receipt'])
    return save(value, filename)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage', choices=COMMANDS); stage = parser.parse_args().stage
    validate_name(JOB); previous.q.canary.host.checked_owner()
    if stage != 'verify': FOLDER.mkdir(parents=True, exist_ok=True)
    actions = {'prepare': prepare, 'install': install, 'install-verify': install_verify, 'start': start,
        'capture': capture, 'collect': collect, 'verify': lambda: collect(True)}
    result = actions[stage](); print(json.dumps({k: result[k] for k in ('status', 'protected_receipt')}, sort_keys=True), flush=True)


if __name__ == '__main__': main()
