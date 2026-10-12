"""One protected, secretless ACR image observation; no ACA/SQL/model activation.

Only a fresh fixed reservation authorizes one registry quick-run request. Lost
acknowledgements consume the attempt. Collection/verification never dispatch.
No source context, build, push, env values, custom identity or KV references are
supplied. ACR platform-injected authority is NOT independently excluded here.
The candidate's descriptor must later match inside the credentialed ACA worker.
"""
import argparse
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import json
import re
import subprocess
import requests
from requests.adapters import HTTPAdapter

from azure.core import MatchConditions
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from audit_azure_runtime import AZ, RG

import deploy_owner_task_postgres_trial as deploy
import qualify_owner_task_catalog_canary as canary
import verify_owner_task_catalog_canary_readback as readback
import verify_owner_task_source_checkpoint as qa
import owner_task_provider_runtime as runtime

ROOT = deploy.ROOT
FOLDER = ROOT / '.work/owner-task-provider-runtime-20261004'
REGISTRY = 'ibkrv2stgauhecwc4itaoo'
IMAGE = REGISTRY + '.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c'
OPERATION_ID = '3d3a5fca-b9d6-4323-b81b-64eaa6c4f23d'
PREFIX = 'OWNER_TASK_PROVIDER_RUNTIME '
RESERVATION = 'probes/owner-codex-provider-runtime-20261004/run-reservation.json'
MAX_LOG_BYTES = 65536
RUN_API = '2019-04-01'
SCHEDULE_URL = 'https://management.azure.com' + canary.TARGET.registry_resource_id + '/scheduleRun?api-version=' + RUN_API
RUNTIME_SOURCE = 'scripts/owner_task_provider_runtime.py'
ADDITIONS = frozenset((RUNTIME_SOURCE, 'tests/test_owner_task_provider_runtime.py',
    'scripts/owner_task_provider_launch_contract.py', 'tests/test_owner_task_provider_launch_contract.py',
    'scripts/qualify_owner_task_provider_runtime.py', 'tests/test_owner_task_provider_runtime_controller.py'))
PINS = {
    'preflight.json': ('779fbec4ebc57033825b3a8d0e9ee0c1ff4c4894874dde74e992629b17c32dcf', '2026-10-04T15:57:29.6210213Z'),
    'inventory-before.json': ('a4e17a29b4d4714270e19d7eb93dc4512618668be658ed17c2a4b3c13d4c2fcd', '2026-10-04T16:06:57.2363278Z'),
    'inventory-after.json': ('c37337eccb6dea1c5bd391b032decaeea08f3b7860299fb0a9b45e8eea4c6a1b', '2026-10-04T16:07:38.5903279Z'),
    'acceptance.json': ('ba676b557fb0879b8982f84847548d653fe34bb0c164e04478a37035742bcd0f', '2026-10-04T16:09:23.8232896Z')}
FALSE_FLAGS = ('ibkr_source_writes', 'credentials_supplied', 'provider_semantics_verified',
    'fresh_restricted_login_verified', 'model_credential_containment_verified', 'owner_runtime_active',
    'six_specialist_runtimes_active', 'production_migration_059_installed',
    'platform_injected_credentials_excluded', 'platform_replacement_excluded', 'tls_connection_verified')


def require(condition, code):
    if not condition:
        raise ValueError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_LOG_BYTES, 'runtime_native_json_invalid')
    def pairs(items):
        result = {}
        for key, item in items:
            require(key not in result, 'runtime_duplicate_json_field'); result[key] = item
        return result
    try:
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('runtime_non_native_json')))
        canary.codec.canonical(value)  # Exact native nested types/depth, no NaN aliases.
        return value
    except Exception:
        raise ValueError('runtime_native_json_invalid') from None


def sources():
    return {name: sha for name, sha in qa.source_manifest().items() if name != qa.DOCUMENT}


def validate_source_extension(old, current):
    require(type(old) is dict and len(old) == 711 and type(current) is dict
        and set(current) == set(old) | ADDITIONS and not set(old) & ADDITIONS
        and all(type(name) is str and type(sha) is str and re.fullmatch('[0-9a-f]{64}', sha)
            for name, sha in current.items()) and all(current[name] == sha for name, sha in old.items()),
        'runtime_only_six_additive_sources_allowed')


def task_spec(source_bytes, expected_sha):
    require(type(source_bytes) is bytes and 0 < len(source_bytes) <= 32768
        and type(expected_sha) is str and re.fullmatch('[0-9a-f]{64}', expected_sha)
        and hashlib.sha256(source_bytes).hexdigest() == expected_sha, 'runtime_source_binding_invalid')
    try:
        source_bytes.decode('utf-8', errors='strict')
        compile(source_bytes, RUNTIME_SOURCE, 'exec')
    except Exception:
        raise ValueError('runtime_source_binding_invalid') from None
    encoded = base64.b64encode(source_bytes).decode('ascii')
    source = ('import base64,hashlib,json,types\n'
        + 'b=base64.b64decode(' + repr(encoded) + ',validate=True)\n'
        + 'assert hashlib.sha256(b).hexdigest()==' + repr(expected_sha) + '\n'
        + 'm=types.ModuleType("owner_task_provider_runtime");m.__file__=' + repr(RUNTIME_SOURCE) + '\n'
        + 'exec(compile(b,m.__file__,"exec"),m.__dict__)\n'
        + 'try:\n r=m.check_runtime()\n'
        + 'except Exception:\n print("OWNER_TASK_PROVIDER_RUNTIME_FAILURE runtime_check_failed",flush=True);raise SystemExit(1)\n'
        + 'v={"operation_id":' + repr(OPERATION_ID) + ',"image":' + repr(IMAGE)
        + ',"runtime_source_sha256":' + repr(expected_sha) + ',"runtime":r}\n'
        + 'print(' + repr(PREFIX) + '+json.dumps(v,sort_keys=True,separators=(",",":")),flush=True)\n')
    packed = base64.b64encode(gzip.compress(source.encode('utf-8'), mtime=0)).decode('ascii')
    runner = "import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('" + packed + "')),'fixed-runtime-probe','exec'))"
    task = dict(version='v1.1.0', stepTimeout=60, steps=[dict(id='runtimeprobe',
        cmd=IMAGE + ' -I -B -c "' + runner + '"', entryPoint='python',
        disableWorkingDirectoryOverride=True, env=[], timeout=60, retries=0, repeat=0,
        privileged=False, pull=True, user='10001:10001')])
    return dict(task=task, task_sha256=digest(task), runtime_source_sha256=expected_sha,
        probe_payload_sha256=hashlib.sha256(source.encode('utf-8')).hexdigest(), image=IMAGE,
        operation_id=OPERATION_ID, credentials_supplied=False, sql_calls=0, model_calls=0)


def probe_plan():
    first = (ROOT / RUNTIME_SOURCE).read_bytes()
    plan = task_spec(first, hashlib.sha256(first).hexdigest())
    require((ROOT / RUNTIME_SOURCE).read_bytes() == first, 'runtime_source_changed_while_bundling')
    return plan


def parse_logs(raw, plan):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_LOG_BYTES, 'runtime_complete_log_size_invalid')
    try:
        text = raw.decode('utf-8', errors='strict')
        require('\x00' not in text, 'runtime_complete_log_invalid')
        require(not any(word in text for word in ('?sv=', 'Authorization: Bearer', 'postgresql://',
            'AccountKey=', 'OWNERTRIAL_ADMIN_URL=')), 'runtime_unexpected_sensitive_log')
        # ACR may prefix engine timestamps. No sampling or deduplication.
        lines = [line for line in text.splitlines() if 'OWNER_TASK_PROVIDER_RUNTIME' in line]
        require(len(lines) == 1 and lines[0].count(PREFIX) == 1, 'runtime_exactly_one_record_required')
        prefix, encoded = lines[0].split(PREFIX)
        require(prefix == '' or re.fullmatch(r'\[[^\]\r\n]{1,100}\]\s*', prefix), 'runtime_log_prefix_invalid')
        value = strict_json(encoded.encode('utf-8'))
        require(type(value) is dict and set(value) == {'operation_id', 'image', 'runtime_source_sha256', 'runtime'}
            and type(value['operation_id']) is str and value['operation_id'] == OPERATION_ID
            and type(value['image']) is str and value['image'] == IMAGE
            and type(value['runtime_source_sha256']) is str and value['runtime_source_sha256'] == plan['runtime_source_sha256'],
            'runtime_record_binding_invalid')
        runtime.validate_descriptor(value['runtime'])
        wanted_digest = IMAGE.split('@', 1)[1]
        require(text.count('Digest: ' + wanted_digest) == 1, 'runtime_engine_digest_unverified')
        return value
    except ValueError:
        raise
    except Exception:
        raise ValueError('runtime_complete_log_invalid') from None


def run_id(value):
    require(type(value) is str and re.fullmatch('[a-z0-9]{2,24}', value), 'runtime_run_id_invalid')
    return value


def cli_args(stage, name=None):
    common = ['--registry', REGISTRY, '--resource-group', RG, '--subscription', deploy.SUB,
        '--only-show-errors']
    require(stage in ('show', 'logs'), 'runtime_cli_stage_invalid')
    name = run_id(name)
    return ['acr', 'task', 'show-run' if stage == 'show' else 'logs', *common, '--run-id', name,
        *(['-o', 'json'] if stage == 'show' else ['--no-format'])]


def call_cli(stage, name=None, *, task=None):
    require(stage in ('show', 'logs') and task is None, 'runtime_readonly_cli_required')
    args = cli_args(stage, name)
    try:
        result = subprocess.run([AZ, '-m', 'azure.cli', *args],
            capture_output=True,
            timeout=90, cwd=ROOT)
        require(result.returncode == 0 and len(result.stdout) <= MAX_LOG_BYTES
            and len(result.stderr) <= MAX_LOG_BYTES, 'runtime_cli_failed_or_oversize')
        return result.stdout if stage == 'logs' else strict_json(result.stdout)
    except Exception:
        raise ValueError('runtime_cli_result_unverified_attempt_not_reissued') from None


def schedule_body(task):
    require(type(task) is dict and canonical(task) == canonical(probe_plan()['task']), 'runtime_exact_schedule_task_required')
    return dict(type='EncodedTaskRunRequest', encodedTaskContent=base64.b64encode(canonical(task)).decode('ascii'),
        platform=dict(os='Linux', architecture='amd64'), timeout=300, agentConfiguration=dict(cpu=2),
        credentials=dict(sourceRegistry=dict(loginMode='Default')), isArchiveEnabled=False)


def dispatch_http(task, *, credential=None, session=None):
    """One exact POST, no redirect/retry/poller. Unknown ACK consumes the attempt.

    Caller retains protected intent/reservation FIRST. This is trusted rollout
    code, not an exposed model tool. Registry login is host-managed pull auth;
    no database/model credential, MI or Key Vault reference is sent to the task.
    """
    body = schedule_body(task)
    owned = session is None
    response = None
    try:
        credential = credential or AzureCliCredential()
        session = session or requests.Session()
        if owned:
            session.mount('https://', HTTPAdapter(max_retries=0))
            session.trust_env = False
        token = credential.get_token('https://management.azure.com/.default').token
        response = session.post(SCHEDULE_URL, headers={'Authorization': 'Bearer ' + token},
            json=body, allow_redirects=False, timeout=(5, 30), stream=True)
        require(type(response.status_code) is int and response.status_code == 200, 'runtime_schedule_ack_unknown_inspect_only')
        chunks = []; length = 0
        for chunk in response.iter_content(chunk_size=4096):
            require(type(chunk) is bytes, 'runtime_schedule_ack_unknown_inspect_only')
            length += len(chunk)
            require(length <= MAX_LOG_BYTES, 'runtime_schedule_ack_unknown_inspect_only'); chunks.append(chunk)
        ack = strict_json(b''.join(chunks))
        require(type(ack) is dict and type(ack.get('properties')) is dict, 'runtime_schedule_ack_unknown_inspect_only')
        name = run_id(ack['properties'].get('runId'))
        require(ack.get('id') == canary.TARGET.registry_resource_id + '/runs/' + name and ack.get('name') == name,
            'runtime_schedule_ack_unknown_inspect_only')
        return ack
    except Exception:
        raise ValueError('runtime_schedule_ack_unknown_attempt_consumed_inspect_only') from None
    finally:
        try:
            if response is not None: response.close()
            if owned and session is not None: session.close()
        except Exception:
            raise ValueError('runtime_schedule_close_unverified_attempt_consumed_inspect_only') from None


def history():
    values = {}
    for name, pin in PINS.items():
        raw = readback.read_local(ROOT / '.work/owner-task-catalog-family-20261004', name)
        readback.receipt_binding(raw, 'catalog-family-' + name.removesuffix('.json'), pin)
        values[name] = deploy.protected(raw, 'catalog-family-' + name.removesuffix('.json'))
    old = values['preflight.json']['sources']
    validate_source_extension(old, sources())
    accepted = values['acceptance.json']
    require(accepted.get('status') == 'observed_execution_family_transport_rehearsal_retained'
        and accepted.get('observed_execution_family_protocol_complete') is True
        and accepted.get('protected_complete_record_readback') is True and accepted.get('record_bytes') == 4194304
        and accepted.get('record_sha256') == readback.RECORD_SHA
        and accepted.get('inventory_sha256') == '295a3d63ed751887fa2a43d194341b6686dc657b58df0d2c570cf8e2b107b254',
        'runtime_completed_family_prerequisite_invalid')
    original_raw = readback.read_local(canary.FOLDER, 'preflight.json')
    readback.receipt_binding(original_raw, 'catalog-canary-preflight', readback.PINNED['preflight.json'])
    original = deploy.protected(original_raw, 'catalog-canary-preflight')
    require(original['baseline'] == canary.baseline(), 'runtime_existing_baseline_changed_since_canary')
    return dict(receipts={name: dict(sha256=pin[0], version_id=pin[1]) for name, pin in PINS.items()},
        historical_source_count=len(old), historical_acceptance_only=True, baseline=original['baseline'])


def registry_metadata():
    value = deploy.az('acr', 'show', '-n', REGISTRY, '-g', RG, '--subscription', deploy.SUB,
        '--query', '{id:id,loginServer:loginServer,adminUserEnabled:adminUserEnabled,roleAssignmentMode:roleAssignmentMode,sku:sku.name}')
    expected = dict(id=canary.TARGET.registry_resource_id, loginServer=REGISTRY + '.azurecr.io',
        adminUserEnabled=False, roleAssignmentMode='LegacyRegistryPermissions', sku='Basic')
    require(value == expected and value.get('adminUserEnabled') is False, 'runtime_registry_definition_changed')
    return value


def guards():
    canary.host.checked_owner()
    job = deploy.request('GET', canary.PATH + '?api-version=2024-03-01')
    canary.validate_job(job)
    return dict(sources=sources(), baseline=canary.baseline(),
        canary_settings_sha256=deploy.settings(job),
        registry=registry_metadata(), isolated_host_receipt=canary.historical_host())


def save(value, name):
    require(0 < len(canonical(value)) <= canary.MAX_BYTES, 'runtime_evidence_size_invalid')
    deploy.FOLDER = FOLDER
    raw = deploy.save(value, name, 'provider-runtime-' + name.removesuffix('.json'))
    deploy.protected(raw, 'provider-runtime-' + name.removesuffix('.json'))
    return raw


def load(name):
    raw = readback.read_local(FOLDER, name)
    value = deploy.protected(raw, 'provider-runtime-' + name.removesuffix('.json'))
    return raw, value


def reservation_blob(version=None):
    deploy.archive_protection()
    client = BlobServiceClient('https://' + deploy.ACCOUNT + '.blob.core.windows.net', credential=AzureCliCredential(),
        retry_total=0, retry_to_secondary=False, connection_timeout=5, read_timeout=10,
        permit_redirects=False, redirect_max=0)
    return client.get_blob_client(deploy.CONTAINER, RESERVATION, version_id=version)


def verify_reservation(receipt, value):
    payload = canonical(value)
    require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'etag', 'protected_readback_verified'}
        and receipt['blob'] == RESERVATION and receipt['sha256'] == hashlib.sha256(payload).hexdigest()
        and receipt['protected_readback_verified'] is True and type(receipt['version_id']) is str
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id'])
        and type(receipt['etag']) is str and re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag']),
        'runtime_reservation_binding_invalid')
    blob = reservation_blob(receipt['version_id']); props = blob.get_blob_properties(timeout=10, retry_total=0)
    require(props.version_id == receipt['version_id'] and props.etag == receipt['etag']
        and props.size == len(payload) and props.blob_type == 'BlockBlob'
        and blob.download_blob(offset=0, length=len(payload) + 1, etag=props.etag,
            match_condition=MatchConditions.IfNotModified, max_concurrency=1, timeout=10, retry_total=0).readall() == payload,
        'runtime_reservation_readback_failed')
    deploy.archive_protection()


def reserve(value):
    payload = canonical(value)
    require(0 < len(payload) <= canary.MAX_BYTES, 'runtime_reservation_size_invalid')
    ack = reservation_blob().upload_blob(payload, length=len(payload), blob_type='BlockBlob', overwrite=False,
        match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
    receipt = dict(blob=RESERVATION, sha256=hashlib.sha256(payload).hexdigest(), version_id=ack.get('version_id'),
        etag=ack.get('etag'), protected_readback_verified=True)
    verify_reservation(receipt, value)
    return receipt


def prepare():
    require(not (FOLDER / 'preflight.json').exists(), 'runtime_preflight_exists_no_overwrite')
    before = sources(); historical = history(); checks = qa.run_local_checks()
    current = guards()
    require(current['sources'] == before and current['baseline'] == historical['baseline'], 'runtime_sources_or_baseline_changed_during_qa')
    value = dict(status='prepared_secretless_runtime_observation', checked_at_utc=datetime.now(timezone.utc).isoformat(),
        operation_id=OPERATION_ID, plan=probe_plan(), guards=current, historical=historical, local_checks=checks,
        sql_calls=0, model_calls=0, daily_schedule_hkt='18:00', **{key: False for key in FALSE_FLAGS})
    require(sources() == before, 'runtime_sources_changed_during_preflight')
    return save(value, 'preflight.json')


def frozen(prepared):
    require(prepared.get('status') == 'prepared_secretless_runtime_observation'
        and prepared.get('operation_id') == OPERATION_ID and prepared.get('plan') == probe_plan()
        and all(prepared.get(key) is False for key in FALSE_FLAGS), 'runtime_preflight_binding_invalid')
    canary.validate_correction_checks(prepared['local_checks'])
    require(guards() == prepared['guards'], 'runtime_current_source_or_resource_drift')


def dispatch():
    require(not (FOLDER / 'dispatch-intent.json').exists() and not (FOLDER / 'dispatch-ack.json').exists(),
        'runtime_dispatch_attempt_consumed_inspect_only')
    raw, prepared = load('preflight.json'); frozen(prepared)
    value = dict(operation_id=OPERATION_ID, preflight_receipt=raw['protected_receipt'],
        plan=prepared['plan'], registry_id=canary.TARGET.registry_resource_id)
    reservation = reserve(value)  # Conditional create + exact version-pinned readback first.
    intent = save(dict(value, reservation_receipt=reservation, status='reserved_single_secretless_quickrun'), 'dispatch-intent.json')
    frozen(prepared); verify_reservation(reservation, value)
    try:
        ack = dispatch_http(prepared['plan']['task'])  # One exact zero-retry POST, no SDK poller.
        require(type(ack) is dict, 'runtime_dispatch_ack_invalid_inspect_only')
        name = run_id(ack.get('properties', {}).get('runId'))
    except Exception:
        # An uncertain response is not evidence of no effect. Keep the attempt
        # consumed and retain only fixed, non-sensitive outcome metadata.
        try:
            save(dict(status='effect_unknown_ack_unverified_inspect_only', operation_id=OPERATION_ID,
                intent_receipt=intent['protected_receipt'], reservation_receipt=reservation,
                attempt_consumed=True, automatic_retry=False, no_effects_verified=False,
                credentials_supplied=False, sql_calls=0, model_calls=0), 'dispatch-outcome.json')
        except Exception:
            raise ValueError('runtime_dispatch_outcome_unretained_attempt_consumed_inspect_only') from None
        raise ValueError('runtime_dispatch_effect_unknown_attempt_consumed_inspect_only') from None
    return save(dict(status='secretless_quickrun_acknowledged', operation_id=OPERATION_ID, run_id=name,
        intent_receipt=intent['protected_receipt'], raw_ack=ack), 'dispatch-ack.json')


def show(name):
    value = call_cli('show', name)
    require(type(value) is dict and value.get('runId') == name and value.get('runType') == 'QuickRun'
        and value.get('status') == 'Succeeded' and type(value.get('platform')) is dict
        and value['platform'].get('os') == 'Linux' and value['platform'].get('architecture') == 'amd64',
        'runtime_terminal_quickrun_metadata_invalid')
    return value


def collect(verify_only=False):
    require(type(verify_only) is bool and (verify_only or not (FOLDER / 'acceptance.json').exists()),
        'runtime_acceptance_exists_use_verify')
    prepared_raw, prepared = load('preflight.json'); frozen(prepared)
    intent_raw, intent = load('dispatch-intent.json'); ack_raw, ack = load('dispatch-ack.json')
    reserved = {key: intent[key] for key in ('operation_id', 'preflight_receipt', 'plan', 'registry_id')}
    require(intent.get('status') == 'reserved_single_secretless_quickrun'
        and ack.get('status') == 'secretless_quickrun_acknowledged'
        and type(ack.get('raw_ack')) is dict and type(ack['raw_ack'].get('properties')) is dict
        and ack['raw_ack']['properties'].get('runId') == ack.get('run_id')
        and reserved == dict(operation_id=OPERATION_ID, preflight_receipt=prepared_raw['protected_receipt'],
        plan=prepared['plan'], registry_id=canary.TARGET.registry_resource_id)
        and ack.get('intent_receipt') == intent_raw['protected_receipt'] and ack.get('operation_id') == OPERATION_ID,
        'runtime_dispatch_chain_invalid')
    verify_reservation(intent['reservation_receipt'], reserved)
    name = run_id(ack['run_id']); first = show(name)
    logs = call_cli('logs', name); observed = parse_logs(logs, prepared['plan']); second = show(name)
    require(first == second, 'runtime_execution_drift_during_collection')
    frozen(prepared)
    value = dict(status='secretless_candidate_runtime_observed', operation_id=OPERATION_ID, run_id=name,
        preflight_receipt=prepared_raw['protected_receipt'], intent_receipt=intent_raw['protected_receipt'],
        ack_receipt=ack_raw['protected_receipt'], raw_run_before=first, raw_run_after=second,
        full_logs=logs.decode('utf-8'), full_logs_bytes=len(logs), full_logs_sha256=hashlib.sha256(logs).hexdigest(),
        observed=observed, requested_task=prepared['plan']['task'], engine_image_digest_observed=True,
        candidate_runtime_metadata_verified=True, current_source_and_resource_guards_verified=True,
        sql_calls=0, model_calls=0, daily_schedule_hkt='18:00', **{key: False for key in FALSE_FLAGS})
    if verify_only:
        raw, retained = load('acceptance.json')
        require(retained == value, 'runtime_fresh_live_and_retained_evidence_differs')
        return dict(status='independent_secretless_candidate_runtime_verified', protected_receipt=raw['protected_receipt'])
    return save(value, 'acceptance.json')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage', choices=('prepare', 'dispatch', 'collect', 'verify'))
    stage = parser.parse_args().stage
    canary.host.checked_owner()
    if stage != 'verify': FOLDER.mkdir(parents=True, exist_ok=True)
    raw = prepare() if stage == 'prepare' else dispatch() if stage == 'dispatch' else collect(stage == 'verify')
    print(json.dumps({key: raw[key] for key in ('status', 'protected_receipt')}, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
