"""Separately reserved standard-pull observation, not a retry of the consumed run.

Preserves all 717 frozen sources. Only pull=False and omission of the forced
user differ from the previous task. The unchanged checker must observe UID/GID
10001 before qualification. No database/model credentials or RBAC changes.
The payload keeps its original probe-family ID; the dispatch has a distinct ID.
"""
import argparse
import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import requests
from requests.adapters import HTTPAdapter
from azure.core import MatchConditions
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient

import qualify_owner_task_provider_runtime as q

ROOT = q.ROOT
FOLDER = ROOT / '.work/owner-task-standard-pull-20261004'
OPERATION_ID = 'ee2817f9-4bc5-4740-88f7-3e2187994620'
RESERVATION = 'probes/owner-codex-standard-pull-20261004/run-reservation.json'
ADDITIONS = frozenset(('scripts/qualify_owner_task_standard_pull.py',
    'tests/test_owner_task_standard_pull.py'))
PINS = {
    'preflight.json': ('d54afd8d01a2821e462b41731c2e966ae0e1de9db2aa2cdb20622b3807c9a8b7', '2026-10-04T17:23:15.1924313Z'),
    'dispatch-intent.json': ('d140c9892df1edb39815ad9ca29c6027904b98814ed40d3eb25c661c84ef0a10', '2026-10-04T17:26:22.1383500Z'),
    'dispatch-ack.json': ('3bca25cdfbec4accdffc8d52f5ed4f771f8d0f694c6be3184c0fe515b49531e6', '2026-10-04T17:28:18.3648798Z'),
    'failure-diagnostic.json': ('63b8c19750b6dc77591d827d16bf86e501f5bc0afe9a6854e0d3f679256f7c9c', '2026-10-04T17:39:23.0924191Z')}
FALSE_FLAGS = q.FALSE_FLAGS
MAX_LOG_BYTES = q.MAX_LOG_BYTES
IMAGE = q.IMAGE
PREFIX = q.PREFIX
SCHEDULE_URL = q.SCHEDULE_URL
require = q.require
canonical = q.canonical
strict_json = q.strict_json
run_id = q.run_id
call_cli = q.call_cli
sources = q.sources


def validate_source_extension(old, current):
    require(type(old) is dict and len(old) == 717 and type(current) is dict
        and set(current) == set(old) | ADDITIONS and not set(old) & ADDITIONS
        and all(type(name) is str and type(sha) is str and re.fullmatch('[0-9a-f]{64}', sha)
            for name, sha in current.items()) and all(current[name] == sha for name, sha in old.items()),
        'standard_pull_only_two_additive_sources_allowed')


def probe_plan():
    previous = q.probe_plan()
    plan = copy.deepcopy(previous)
    step = plan['task']['steps'][0]
    require(step['pull'] is True and step['user'] == '10001:10001', 'standard_pull_original_task_invalid')
    step['pull'] = False
    del step['user']
    plan['task_sha256'] = q.digest(plan['task'])
    plan['dispatch_operation_id'] = OPERATION_ID
    plan['probe_family_operation_id'] = q.OPERATION_ID
    plan['task_changes'] = ['steps[0].pull: true to false', 'steps[0].user: omit override']
    return plan


def schedule_body(task):
    require(type(task) is dict and canonical(task) == canonical(probe_plan()['task']),
        'standard_pull_exact_schedule_task_required')
    return dict(type='EncodedTaskRunRequest', encodedTaskContent=base64.b64encode(canonical(task)).decode('ascii'),
        platform=dict(os='Linux', architecture='amd64'), timeout=300, agentConfiguration=dict(cpu=2),
        credentials=dict(sourceRegistry=dict(loginMode='Default')), isArchiveEnabled=False)


def dispatch_http(task, *, credential=None, session=None):
    body = schedule_body(task)  # Reject altered commands before obtaining authority.
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
        require(type(response.status_code) is int and response.status_code == 200, 'standard_pull_ack_unknown')
        chunks = []; length = 0
        for chunk in response.iter_content(chunk_size=4096):
            require(type(chunk) is bytes, 'standard_pull_ack_unknown')
            length += len(chunk)
            require(length <= MAX_LOG_BYTES, 'standard_pull_ack_unknown'); chunks.append(chunk)
        ack = strict_json(b''.join(chunks))
        validate_ack(ack)
        return ack
    except Exception:
        raise ValueError('standard_pull_schedule_ack_unknown_attempt_consumed_inspect_only') from None
    finally:
        try:
            if response is not None: response.close()
            if owned and session is not None: session.close()
        except Exception:
            raise ValueError('standard_pull_schedule_close_unverified_attempt_consumed_inspect_only') from None


def validate_ack(ack):
    require(type(ack) is dict and type(ack.get('properties')) is dict, 'standard_pull_ack_invalid')
    name = run_id(ack['properties'].get('runId'))
    require(ack.get('id') == q.canary.TARGET.registry_resource_id + '/runs/' + name
        and ack.get('name') == name, 'standard_pull_ack_invalid')
    return name


def chain(prepared_raw, prepared, intent_raw, intent, ack):
    reserved = {key: intent[key] for key in ('operation_id', 'preflight_receipt', 'plan', 'registry_id')}
    require(intent.get('status') == 'reserved_single_secretless_quickrun'
        and reserved == dict(operation_id=OPERATION_ID, preflight_receipt=prepared_raw['protected_receipt'],
            plan=prepared['plan'], registry_id=q.canary.TARGET.registry_resource_id)
        and ack.get('status') == 'secretless_quickrun_acknowledged'
        and ack.get('operation_id') == OPERATION_ID and ack.get('intent_receipt') == intent_raw['protected_receipt']
        and validate_ack(ack['raw_ack']) == ack.get('run_id'), 'standard_pull_dispatch_chain_invalid')
    verify_reservation(intent['reservation_receipt'], reserved)
    return run_id(ack['run_id'])


def history():
    values = {}; raws = {}
    for name, pin in PINS.items():
        raw = q.readback.read_local(q.FOLDER, name)
        kind = 'provider-runtime-' + name.removesuffix('.json')
        q.readback.receipt_binding(raw, kind, pin)
        raws[name] = raw; values[name] = q.deploy.protected(raw, kind)
    old = values['preflight.json']; diagnostic = values['failure-diagnostic.json']
    validate_source_extension(old['guards']['sources'], sources())
    q.canary.validate_correction_checks(old['local_checks'])
    require(old.get('status') == 'prepared_secretless_runtime_observation' and old.get('operation_id') == q.OPERATION_ID
        and old.get('plan') == q.probe_plan() and all(old.get(key) is False for key in FALSE_FLAGS),
        'standard_pull_historical_preflight_invalid')
    intent = values['dispatch-intent.json']; ack = values['dispatch-ack.json']
    reserved = {key: intent[key] for key in ('operation_id', 'preflight_receipt', 'plan', 'registry_id')}
    require(intent.get('status') == 'reserved_single_secretless_quickrun'
        and reserved == dict(operation_id=q.OPERATION_ID, preflight_receipt=raws['preflight.json']['protected_receipt'],
            plan=old['plan'], registry_id=q.canary.TARGET.registry_resource_id)
        and ack.get('status') == 'secretless_quickrun_acknowledged' and ack.get('operation_id') == q.OPERATION_ID
        and ack.get('intent_receipt') == raws['dispatch-intent.json']['protected_receipt']
        and validate_ack(ack['raw_ack']) == ack.get('run_id') == 'ck83', 'standard_pull_historical_chain_invalid')
    q.verify_reservation(intent['reservation_receipt'], reserved)
    require(diagnostic.get('status') == 'observed_failed_image_pull_runtime_not_qualified'
        and diagnostic.get('operation_id') == q.OPERATION_ID and diagnostic.get('run_id') == 'ck83'
        and diagnostic.get('preflight_receipt') == raws['preflight.json']['protected_receipt']
        and diagnostic.get('intent_receipt') == raws['dispatch-intent.json']['protected_receipt']
        and diagnostic.get('ack_receipt') == raws['dispatch-ack.json']['protected_receipt']
        and diagnostic.get('reservation_receipt') == intent['reservation_receipt']
        and diagnostic.get('attempt_consumed') is True and diagnostic.get('automatic_retry') is False
        and diagnostic.get('acceptance_recorded') is False and all(diagnostic.get(key) is False for key in FALSE_FLAGS)
        and not (q.FOLDER / 'acceptance.json').exists(), 'standard_pull_consumed_failure_required')
    first = show('ck83'); logs = call_cli('logs', 'ck83'); second = show('ck83')
    bounded_logs(logs)
    require(first == second == diagnostic['raw_run_before'] == diagnostic['raw_run_after']
        and first['status'] == 'Failed' and logs.decode('utf-8') == diagnostic['full_logs']
        and len(logs) == diagnostic['full_logs_bytes'] and hashlib.sha256(logs).hexdigest() == diagnostic['full_logs_sha256']
        and 'Step specified pull. Performing an explicit pull' in logs.decode('utf-8')
        and 'unauthorized: authentication required' in logs.decode('utf-8')
        and 'OWNER_TASK_PROVIDER_RUNTIME' not in logs.decode('utf-8'), 'standard_pull_failure_live_readback_invalid')
    return dict(receipts={name: raws[name]['protected_receipt'] for name in PINS},
        frozen_sources=old['guards']['sources'], frozen_resources={k: v for k, v in old['guards'].items() if k != 'sources'},
        previous_run_id='ck83', previous_attempt_consumed=True, previous_runtime_qualified=False)


def guards():
    return q.guards()  # Uses the full current manifest; never invokes old 711+6 validator.


def save(value, name):
    require(0 < len(canonical(value)) <= q.canary.MAX_BYTES, 'standard_pull_evidence_size_invalid')
    q.deploy.FOLDER = FOLDER
    raw = q.deploy.save(value, name, 'standard-pull-' + name.removesuffix('.json'))
    q.deploy.protected(raw, 'standard-pull-' + name.removesuffix('.json'))
    return raw


def load(name):
    raw = q.readback.read_local(FOLDER, name)
    return raw, q.deploy.protected(raw, 'standard-pull-' + name.removesuffix('.json'))


def reservation_blob(version=None):
    q.deploy.archive_protection()
    client = BlobServiceClient('https://' + q.deploy.ACCOUNT + '.blob.core.windows.net', credential=AzureCliCredential(),
        retry_total=0, retry_to_secondary=False, connection_timeout=5, read_timeout=10,
        permit_redirects=False, redirect_max=0)
    return client.get_blob_client(q.deploy.CONTAINER, RESERVATION, version_id=version)


def verify_reservation(receipt, value):
    payload = canonical(value)
    require(type(receipt) is dict and set(receipt) == {'blob', 'sha256', 'version_id', 'etag', 'protected_readback_verified'}
        and receipt['blob'] == RESERVATION and receipt['sha256'] == hashlib.sha256(payload).hexdigest()
        and receipt['protected_readback_verified'] is True and type(receipt['version_id']) is str
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id'])
        and type(receipt['etag']) is str and re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag']),
        'standard_pull_reservation_binding_invalid')
    blob = reservation_blob(receipt['version_id']); props = blob.get_blob_properties(timeout=10, retry_total=0)
    require(props.version_id == receipt['version_id'] and props.etag == receipt['etag']
        and props.size == len(payload) and props.blob_type == 'BlockBlob'
        and blob.download_blob(offset=0, length=len(payload) + 1, etag=props.etag,
            match_condition=MatchConditions.IfNotModified, max_concurrency=1, timeout=10, retry_total=0).readall() == payload,
        'standard_pull_reservation_readback_failed')
    q.deploy.archive_protection()


def reserve(value):
    payload = canonical(value)
    require(0 < len(payload) <= q.canary.MAX_BYTES, 'standard_pull_reservation_size_invalid')
    ack = reservation_blob().upload_blob(payload, length=len(payload), blob_type='BlockBlob', overwrite=False,
        match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
    receipt = dict(blob=RESERVATION, sha256=hashlib.sha256(payload).hexdigest(), version_id=ack.get('version_id'),
        etag=ack.get('etag'), protected_readback_verified=True)
    verify_reservation(receipt, value)
    return receipt


def prepare():
    require(not (FOLDER / 'preflight.json').exists(), 'standard_pull_preflight_exists_no_overwrite')
    before = sources(); historical = history(); checks = q.qa.run_local_checks(); current = guards()
    require(current['sources'] == before and {k: v for k, v in current.items() if k != 'sources'} == historical['frozen_resources'],
        'standard_pull_sources_or_resources_changed_during_qa')
    value = dict(status='prepared_standard_pull_runtime_observation', checked_at_utc=datetime.now(timezone.utc).isoformat(),
        operation_id=OPERATION_ID, plan=probe_plan(), guards=current, historical=historical, local_checks=checks,
        sql_calls=0, model_calls=0, daily_schedule_hkt='18:00', **{key: False for key in FALSE_FLAGS})
    require(sources() == before, 'standard_pull_sources_changed_during_preflight')
    return save(value, 'preflight.json')


def frozen(prepared):
    require(prepared.get('status') == 'prepared_standard_pull_runtime_observation'
        and prepared.get('operation_id') == OPERATION_ID and prepared.get('plan') == probe_plan()
        and all(prepared.get(key) is False for key in FALSE_FLAGS), 'standard_pull_preflight_binding_invalid')
    q.canary.validate_correction_checks(prepared['local_checks'])
    validate_source_extension(prepared['historical']['frozen_sources'], sources())
    require({k: v for k, v in prepared['guards'].items() if k != 'sources'} == prepared['historical']['frozen_resources']
        and guards() == prepared['guards'], 'standard_pull_current_source_or_resource_drift')


def dispatch():
    require(not any((FOLDER / name).exists() for name in ('dispatch-intent.json', 'dispatch-ack.json', 'dispatch-outcome.json')),
        'standard_pull_dispatch_attempt_consumed_inspect_only')
    raw, prepared = load('preflight.json'); frozen(prepared)
    value = dict(operation_id=OPERATION_ID, preflight_receipt=raw['protected_receipt'],
        plan=prepared['plan'], registry_id=q.canary.TARGET.registry_resource_id)
    reservation = reserve(value)
    intent = save(dict(value, reservation_receipt=reservation, status='reserved_single_secretless_quickrun'), 'dispatch-intent.json')
    frozen(prepared); verify_reservation(reservation, value)
    try:
        ack = dispatch_http(prepared['plan']['task']); name = validate_ack(ack)
    except Exception:
        try:
            save(dict(status='effect_unknown_ack_unverified_inspect_only', operation_id=OPERATION_ID,
                intent_receipt=intent['protected_receipt'], reservation_receipt=reservation,
                attempt_consumed=True, automatic_retry=False, no_effects_verified=False,
                credentials_supplied=False, sql_calls=0, model_calls=0), 'dispatch-outcome.json')
        except Exception:
            raise ValueError('standard_pull_dispatch_outcome_unretained_attempt_consumed_inspect_only') from None
        raise ValueError('standard_pull_dispatch_effect_unknown_attempt_consumed_inspect_only') from None
    return save(dict(status='secretless_quickrun_acknowledged', operation_id=OPERATION_ID, run_id=name,
        intent_receipt=intent['protected_receipt'], raw_ack=ack), 'dispatch-ack.json')


def show(name):
    value = call_cli('show', name)
    require(type(value) is dict and value.get('runId') == name and value.get('runType') == 'QuickRun'
        and value.get('id') == q.canary.TARGET.registry_resource_id + '/runs/' + name and value.get('name') == name
        and value.get('status') in ('Succeeded', 'Failed') and type(value.get('platform')) is dict
        and value['platform'].get('os') == 'Linux' and value['platform'].get('architecture') == 'amd64',
        'standard_pull_terminal_metadata_invalid')
    return value


def bounded_logs(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_LOG_BYTES, 'standard_pull_complete_log_size_invalid')
    try: text = raw.decode('utf-8', errors='strict')
    except Exception: raise ValueError('standard_pull_complete_log_invalid') from None
    require('\x00' not in text and not any(word in text for word in ('?sv=', 'Authorization: Bearer',
        'postgresql://', 'AccountKey=', 'OWNERTRIAL_ADMIN_URL=')), 'standard_pull_unexpected_sensitive_log')
    return text


def parse_logs(raw, plan):
    require(plan == probe_plan() and plan['task']['steps'][0]['pull'] is False
        and 'user' not in plan['task']['steps'][0], 'standard_pull_exact_observed_plan_required')
    text = bounded_logs(raw)
    digest_lines = [line for line in text.splitlines() if 'Digest:' in line]
    require(len(digest_lines) == 1, 'standard_pull_exactly_one_engine_digest_required')
    match = re.fullmatch(r'(?:\[[^\]\r\n]{1,100}\]\s*)?Digest: (sha256:[0-9a-f]{64})', digest_lines[0])
    require(match is not None and match.group(1) == IMAGE.split('@', 1)[1],
        'standard_pull_native_engine_digest_invalid')
    return q.parse_logs(raw, plan)  # Preserves family ID, immutable checker and strict engine-digest requirement.


def collect(verify_only=False):
    require(type(verify_only) is bool and (verify_only or not any((FOLDER / name).exists()
        for name in ('acceptance.json', 'terminal-diagnostic.json'))), 'standard_pull_terminal_record_exists_use_verify')
    prepared_raw, prepared = load('preflight.json'); frozen(prepared)
    intent_raw, intent = load('dispatch-intent.json'); ack_raw, ack = load('dispatch-ack.json')
    name = chain(prepared_raw, prepared, intent_raw, intent, ack)
    first = show(name); logs = call_cli('logs', name); text = bounded_logs(logs); second = show(name)
    require(first == second, 'standard_pull_execution_drift_during_collection')
    observed = None
    if first['status'] == 'Succeeded':
        try: observed = parse_logs(logs, prepared['plan'])
        except ValueError: pass  # Retain complete unaccepted output; never weaken the acceptance contract.
    frozen(prepared)
    qualified = observed is not None
    value = dict(status='secretless_default_user_runtime_observed' if qualified else 'terminal_standard_pull_runtime_unqualified',
        operation_id=OPERATION_ID, probe_family_operation_id=q.OPERATION_ID, run_id=name,
        preflight_receipt=prepared_raw['protected_receipt'], intent_receipt=intent_raw['protected_receipt'],
        ack_receipt=ack_raw['protected_receipt'], reservation_receipt=intent['reservation_receipt'],
        raw_run_before=first, raw_run_after=second, full_logs=text, full_logs_bytes=len(logs),
        full_logs_sha256=hashlib.sha256(logs).hexdigest(), observed=observed, requested_task=prepared['plan']['task'],
        engine_image_digest_observed=qualified, candidate_runtime_metadata_verified=qualified,
        default_user_runtime_verified=qualified, current_source_and_resource_guards_verified=True,
        attempt_consumed=True, automatic_retry=False, acceptance_recorded=qualified, actual_root_cause_verified=False,
        sql_calls=0, model_calls=0, daily_schedule_hkt='18:00', **{key: False for key in FALSE_FLAGS})
    filename = 'acceptance.json' if qualified else 'terminal-diagnostic.json'
    if verify_only:
        raw, retained = load(filename)
        require(retained == value, 'standard_pull_fresh_live_and_retained_evidence_differs')
        return dict(status='independent_standard_pull_runtime_verified' if qualified else 'independent_standard_pull_diagnostic_verified',
            protected_receipt=raw['protected_receipt'], runtime_qualified=qualified)
    return save(value, filename)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage', choices=('prepare', 'dispatch', 'collect', 'verify'))
    stage = parser.parse_args().stage
    q.canary.host.checked_owner()
    if stage != 'verify': FOLDER.mkdir(parents=True, exist_ok=True)
    raw = prepare() if stage == 'prepare' else dispatch() if stage == 'dispatch' else collect(stage == 'verify')
    print(json.dumps({key: raw[key] for key in ('status', 'protected_receipt')}, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
