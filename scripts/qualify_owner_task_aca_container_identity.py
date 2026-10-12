"""Fresh qualification with the documented native containerId field.

The prior acknowledged execution and its capture stop stay immutable. This
operation permits one new execution, with its own protected intent, and no
installation, permission, database, secret or model operation. Raw observations
are retained on failure so a parser error cannot discard the diagnosis.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time

import qualify_owner_task_aca_runtime_metadata as old
import owner_task_aca_container_identity as native
import qualify_owner_task_aca_readiness as stopped

ROOT, JOB, JOB_ID = old.ROOT, old.JOB, old.JOB_ID
FOLDER = ROOT / '.work/owner-task-aca-container-identity-20261005'
PREFIX = 'probes/owner-codex-aca-container-identity-20261005/'
KIND = 'aca-container-identity-'
OPERATION_ID = '9c2c8c7f-ad84-45d1-8e36-a6519b3a4e82'
ADDITIONS = frozenset(('scripts/owner_task_aca_container_identity.py', 'tests/test_owner_task_aca_container_identity.py',
    'scripts/qualify_owner_task_aca_container_identity.py', 'tests/test_owner_task_aca_container_identity_controller.py'))
PINS = {
    'preflight.json': ('3a1ec764fd53b1e72b05939ce0175ff6673f66086e78485ca9cc39d9b7662ef1', '2026-10-04T23:46:36.2272145Z'),
    'start-ack.json': ('2200da188abcaf302c3ff6f367fc5adcf64b3733856e858693a291e1c6eb7851', '2026-10-04T23:51:01.7978904Z'),
    'capture-diagnostic.json': ('40fb1dfaf54be23de9e6b3f585797c3b376b007d830889fd5373ac06caefe56a', '2026-10-04T23:51:25.1378118Z')}
require, canonical, digest = old.require, old.canonical, old.digest
FALSE_FLAGS = old.FALSE_FLAGS


def validate_sources(before, after):
    require(type(before) is dict and len(before) == 739 and type(after) is dict
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v)
            for values in (before, after) for k, v in values.items())
        and set(after) == set(before) | ADDITIONS and not set(before) & ADDITIONS
        and all(after[k] == v for k, v in before.items()), 'native_source_extension_invalid')


def save(value, name):
    require(type(name) is str and re.fullmatch('[a-z][a-z-]*\\.json', name)
        and 0 < len(canonical(value)) <= old.MAX_BYTES, 'native_evidence_invalid')
    with (FOLDER / name).open('x', encoding='utf-8') as output:
        old.previous.q.deploy.archive_protection()
        raw = dict(value, protected_receipt=old.previous.q.deploy.retain(value,
            'owner-task-pg-' + KIND + name.removesuffix('.json')))
        output.write(json.dumps(raw, sort_keys=True, indent=2))
    old.previous.q.deploy.protected(raw, KIND + name.removesuffix('.json'))
    return raw


def load(name):
    raw = old.previous.q.readback.read_local(FOLDER, name)
    return raw, old.previous.q.deploy.protected(raw, KIND + name.removesuffix('.json'))


def history():
    records, values = {}, {}
    for name, pin in PINS.items():
        raw, value = stopped.load(name)
        old.validate_receipt(raw['protected_receipt'], stopped.KIND + name.removesuffix('.json'), pin)
        records[name], values[name] = raw, value
    pf, ack, diagnosis = (values[k] for k in PINS)
    validate_sources(pf['sources'], old.previous.sources())
    stopped.validate_sources(pf['prior']['sources'], pf['sources'])
    require(pf['operation_id'] == stopped.OPERATION_ID
        and pf['status'] == 'prepared_native_response_qualification'
        and all(pf[k] is False for k in FALSE_FLAGS), 'native_prior_preflight_invalid')
    old.previous.q.canary.validate_correction_checks(pf['local_checks'])
    old.n.validate_local_qa_policy(pf['local_qa_policy'])
    ack_raw, execution = stopped.chain(records['preflight.json'], pf)
    require(ack_raw == records['start-ack.json'] and ack['attempt_consumed'] is True
        and diagnosis['operation_id'] == stopped.OPERATION_ID
        and diagnosis['status'] == 'native_capture_unqualified_inspect_only'
        and diagnosis['failure_code'] == 'native_replica_invalid'
        and diagnosis['start_ack_receipt'] == ack_raw['protected_receipt']
        and diagnosis['attempt_consumed'] is True and diagnosis['acceptance_recorded'] is False
        and diagnosis['automatic_retry'] is False
        and all(diagnosis[k] is False for k in FALSE_FLAGS)
        and type(diagnosis['sql_calls']) is int and diagnosis['sql_calls'] == 0
        and type(diagnosis['model_calls']) is int and diagnosis['model_calls'] == 0,
        'native_prior_stop_chain_invalid')
    observations = diagnosis['observations']
    require(type(observations) is list and len(observations) == 27
        and all(type(row) is dict and set(row) == {'kind','received_at_utc','raw'} for row in observations)
        and observations[0]['kind'] == 'start_ack'
        and observations[0]['raw'] == dict(http_status=ack['http_status'],raw_ack=ack['raw_ack']),
        'container_identity_exact_diagnosis_required')
    base = pf['prior']['pf']
    times = [old.transport.utc(row['received_at_utc']) for row in observations]
    require(times == sorted(times) and (times[-1]-times[0]).total_seconds() <= 90,
        'container_identity_observation_times_invalid')
    for index, row in enumerate(observations[1:],1):
        require(row['kind'] == ('execution_before' if index % 2 else 'replicas'),
            'container_identity_observation_order_invalid')
        if index % 2:
            native.validate_execution(row['raw'],base,execution,terminal=False)
        else:
            state = native.classify_replica(row['raw'],execution)
            require(state == ('ready' if index == 26 else 'pending'),
                'container_identity_observed_readiness_invalid')
    ready = native.validate_replica(observations[-1]['raw'],execution)
    require(ready['properties']['containers'][0].get('containerId') ==
        'containerd://70f8b8632b314686bb149f3bae439345',
        'container_identity_exact_observed_field_required')
    names = pf['prior']['prior_executions'] + [execution]
    require(len(names) == 3 and len(set(names)) == 3,'native_execution_replayed')
    current = old.get(old.arm(base['prerequisite']),JOB_ID+'/executions/'+execution,old.transport.JOB_API)
    native.validate_execution(current,base,execution,terminal=True)
    startup = observations[-2]['raw']
    require(current['properties']['status'] == 'Succeeded'
        and current['properties']['startTime'] == startup['properties']['startTime']
        and current['properties']['template'] == startup['properties']['template'],
        'container_identity_prior_terminal_changed')
    prior_raw = old.native_copy(pf['prior']['prior_raw_executions'])
    for name in names[:-1]:
        native.validate_execution(prior_raw[name],base,name,terminal=True)
        require(prior_raw[name]['properties']['status'] == 'Succeeded','native_prior_execution_changed')
    prior_raw[execution] = current
    return dict(pf=base,sources=pf['sources'],prior_executions=names,
        prior_raw_executions=prior_raw,receipts={k:v['protected_receipt'] for k,v in records.items()})


def inventory(client):
    value = old.get(client, JOB_ID + '/executions', old.transport.JOB_API)
    require(type(value) is dict and set(value) <= {'value', 'nextLink'}
        and value.get('nextLink') in (None, '') and type(value.get('value')) is list
        and len(value['value']) <= 4, 'native_complete_execution_inventory_required')
    return value


def check_inventory(value, prior, current=None):
    require(type(prior) is list and len(prior) == 3 and all(type(n) is str for n in prior)
        and len(set(prior)) == 3, 'container_identity_exact_three_prior_executions_required')
    for name in prior: old.n.execution_name(name)
    if current is not None:
        old.n.execution_name(current); require(current not in prior, 'native_execution_replayed')
    require(type(value) is dict and set(value) <= {'value', 'nextLink'}
        and value.get('nextLink') in (None, '') and type(value.get('value')) is list,
        'native_inventory_invalid')
    rows = value['value']; expected = set(prior) | ({current} if current else set())
    require(all(type(r) is dict and type(r.get('name')) is str for r in rows)
        and len(rows) == len(expected) and {r['name'] for r in rows} == expected
        and all(type(r) is dict and r.get('id') == JOB_ID + '/executions/' + r['name'] for r in rows),
        'native_unregistered_execution_present')


def guards(pf, prior, current=None):
    client = old.arm(pf['prerequisite'])
    identity = old.c.identity_observation(client)
    require(canonical(identity) == canonical(pf['identity']), 'native_identity_changed')
    require(digest(old.environment(client)) == pf['history_binding']['installed_environment_sha256'],
        'native_environment_changed')
    job = old.get(client, JOB_ID, old.transport.JOB_API)
    native.validate_job_metadata(job, pf['prerequisite']['expected_runtime'],
        pf['identity']['properties']['principalId'], pf['identity']['properties']['clientId'])
    require(digest(job) == pf['history_binding']['installed_native_job_sha256'], 'native_job_changed')
    roles = old.roles(client, pf['identity']['properties']['principalId'], granted=True)
    require(canonical(roles) == canonical(pf['initial_roles']), 'native_roles_changed')
    assignment = old.get(client, old.transport.ASSIGNMENT_ID, old.transport.ROLE_API)
    old.transport.validate_assignment(assignment, pf['identity']['properties']['principalId'])
    current_inventory = inventory(client); check_inventory(current_inventory, prior, current)
    prior_readbacks = {}
    for name in prior:
        value = old.get(client, JOB_ID+'/executions/'+name, old.transport.JOB_API)
        native.validate_execution(value, pf, name, terminal=True)
        require(value['properties']['status'] == 'Succeeded', 'native_prior_execution_changed')
        prior_readbacks[name] = value
    return dict(job=job, roles=roles, assignment=assignment, executions=current_inventory,
        prior_execution_readbacks=prior_readbacks)


def reservation_blob(version=None):
    return old.storage().get_blob_client(old.previous.q.deploy.CONTAINER,
        PREFIX + 'start-reservation.json', version_id=version)


def absent():
    require(not any((FOLDER / n).exists() for n in ('start-intent.json', 'start-ack.json',
        'start-outcome.json', 'replica-binding.json', 'capture-diagnostic.json', 'acceptance.json',
        'native-observations.jsonl')), 'native_attempt_already_consumed')
    old.blob_absent(reservation_blob())


def prepare():
    require(not (FOLDER / 'preflight.json').exists(), 'native_preflight_exists')
    absent(); before = old.previous.sources(); prior = history()
    checks = old.n.run_local_checks(); old.previous.q.canary.validate_correction_checks(checks)
    live = guards(prior['pf'], prior['prior_executions'])
    require(live['prior_execution_readbacks'] == prior['prior_raw_executions']
        and canonical(old.n.resource_guards()) == canonical(prior['pf']['resource_guards'])
        and old.previous.sources() == before, 'native_preflight_drift')
    return save(dict(status='prepared_native_response_qualification', operation_id=OPERATION_ID,
        sources=before, prior=prior, live=live, local_checks=checks,
        local_qa_policy=old.n.local_qa_policy(), checked_at_utc=datetime.now(timezone.utc).isoformat(),
        sql_calls=0, model_calls=0, **{k:False for k in FALSE_FLAGS}), 'preflight.json')


def prepared():
    raw, pf = load('preflight.json')
    require(pf.get('status') == 'prepared_native_response_qualification' and pf.get('operation_id') == OPERATION_ID
        and pf['sources'] == old.previous.sources() and all(pf[k] is False for k in FALSE_FLAGS), 'native_preflight_changed')
    validate_sources(pf['prior']['sources'], pf['sources'])
    old.previous.q.canary.validate_correction_checks(pf['local_checks'])
    old.n.validate_local_qa_policy(pf['local_qa_policy'])
    return raw, pf


def intent_value(raw, pf):
    return old.native_copy(dict(operation_id=OPERATION_ID, preflight_receipt=raw['protected_receipt'],
        prior_executions=pf['prior']['prior_executions'], prior_receipts=pf['prior']['receipts'],
        path=JOB_ID + '/start', api=old.transport.JOB_API, method='POST',
        body=old.start_body(pf['prior']['pf']), automatic_retry=False))


def reserve(value):
    payload = canonical(value)
    ack = reservation_blob().upload_blob(payload, length=len(payload), blob_type='BlockBlob', overwrite=False,
        match_condition=old.MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
    receipt = dict(blob=PREFIX+'start-reservation.json', sha256=hashlib.sha256(payload).hexdigest(),
        version_id=ack.get('version_id'), etag=ack.get('etag'), protected_readback_verified=True)
    reservation_readback(receipt, value)
    return receipt


def reservation_readback(receipt, value):
    payload = canonical(value)
    require(type(receipt) is dict and set(receipt) == {'blob','sha256','version_id','etag','protected_readback_verified'}
        and receipt['blob'] == PREFIX+'start-reservation.json'
        and receipt['sha256'] == hashlib.sha256(payload).hexdigest() and receipt['protected_readback_verified'] is True
        and type(receipt['version_id']) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id'])
        and type(receipt['etag']) is str and re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag']), 'native_reservation_invalid')
    blob = reservation_blob(receipt['version_id']); props = blob.get_blob_properties(timeout=10,retry_total=0)
    require(props.version_id == receipt['version_id'] and props.etag == receipt['etag'] and props.size == len(payload)
        and props.blob_type == 'BlockBlob'
        and blob.download_blob(offset=0,length=len(payload)+1,etag=props.etag,
            match_condition=old.MatchConditions.IfNotModified,max_concurrency=1,timeout=10,retry_total=0).readall() == payload,
        'native_reservation_changed')


def capture(client, pf, name, record, *, monotonic=time.monotonic, sleep=time.sleep):
    deadline = monotonic() + 90
    def read(path, api):
        try: return old.get(client, path, api)
        except old.NativeHttpError as error:
            if not old.n.capture_not_found(error): raise
            record('replica_visibility_pending', error.diagnostic())
            raise old.CapturePending() from None
    for _ in range(45):
        require(monotonic() < deadline, 'native_capture_deadline')
        try:
            before = read(JOB_ID+'/executions/'+name, old.transport.JOB_API)
            record('execution_before', before)
            native.validate_execution(before, pf, name, terminal=False)
            if before['properties']['status'] == 'Processing': raise old.CapturePending()
            replicas = read(JOB_ID+'/executions/'+name+'/replicas', old.transport.REPLICA_API)
            record('replicas', replicas)
            state = native.classify_replica(replicas, name)
            if state != 'ready':
                require(state == 'pending', 'native_replica_state_invalid'); raise old.CapturePending()
            replica = native.validate_replica(replicas, name)
            after = read(JOB_ID+'/executions/'+name, old.transport.JOB_API)
            record('execution_after', after)
            native.validate_execution(after, pf, name, terminal=False)
            require(before == after and monotonic() < deadline, 'native_capture_changed')
            return dict(raw_execution=before, raw_replicas=replicas, replica=replica,
                observed_at_utc=datetime.now(timezone.utc).isoformat())
        except old.CapturePending: sleep(1)
    raise ValueError('native_capture_deadline')


def start():
    raw, pf = prepared(); absent()
    require(canonical(history()) == canonical(pf['prior']), 'native_history_changed')
    current = guards(pf['prior']['pf'], pf['prior']['prior_executions'])
    require(current == pf['live'] and old.previous.sources() == pf['sources'], 'native_start_guards_changed')
    value = intent_value(raw, pf); receipt = reserve(value)
    intent = save(dict(value, reservation_receipt=receipt, status='reserved_single_qualification'), 'start-intent.json')
    # Recheck mutable authority and job immediately before dispatch, after protected intent.
    require(guards(pf['prior']['pf'], pf['prior']['prior_executions']) == pf['live']
        and canonical(old.n.resource_guards()) == canonical(pf['prior']['pf']['resource_guards'])
        and old.previous.sources() == pf['sources'], 'native_post_reservation_drift')
    client = old.arm(pf['prior']['pf']['prerequisite'])
    with (FOLDER/'native-observations.jsonl').open('x', encoding='utf-8') as journal:
        observations = []
        observed_bytes = 0
        def record(kind, response):
            nonlocal observed_bytes
            item = dict(kind=kind, received_at_utc=datetime.now(timezone.utc).isoformat(), raw=response)
            payload = canonical(item)
            require(len(payload) <= old.MAX_BYTES, 'native_observation_oversized')
            journal.write(json.dumps(item, sort_keys=True)+'\n'); journal.flush()
            if observed_bytes + len(payload) > 768*1024:
                observations.append(dict(kind='observation_limit', received_at_utc=item['received_at_utc'],
                    complete_raw_retained_in_local_journal=True, complete_raw_retained_in_protected_record=False,
                    bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
                raise ValueError('native_observation_budget_exceeded')
            observed_bytes += len(payload); observations.append(item)
        try:
            ack, status = client.request(value['path'], value['api'], method='POST', body=value['body'])
            record('start_ack', dict(http_status=status, raw_ack=ack))
            name = old.acknowledged_execution(dict(http_status=status, raw_ack=ack))
            require(name not in pf['prior']['prior_executions'], 'native_execution_replayed')
        except Exception:
            try:
                save(dict(status='start_outcome_unqualified_attempt_consumed', operation_id=OPERATION_ID,
                    intent_receipt=intent['protected_receipt'], observations=observations, attempt_consumed=True,
                    automatic_retry=False, sql_calls=0, model_calls=0,
                    **{k:False for k in FALSE_FLAGS}), 'start-outcome.json')
            except Exception: raise ValueError('native_outcome_unretained_attempt_consumed') from None
            raise ValueError('native_start_outcome_unqualified_inspect_only') from None
        # Capture the short-lived native Running row before slow archive round trips.
        binding, failure = None, None
        try: binding = capture(client, pf['prior']['pf'], name, record)
        except Exception as error:
            text = str(error)
            allowed = {'native_capture_deadline', 'native_capture_changed', 'native_replica_state_invalid',
                'native_execution_invalid', 'native_replica_invalid', 'native_replica_pending',
                'native_observation_budget_exceeded', 'native_observation_oversized'}
            failure = text if text in allowed else 'native_capture_validation_failed'
        ack_record = save(dict(status='new_qualification_execution_acknowledged', operation_id=OPERATION_ID,
            intent_receipt=intent['protected_receipt'], http_status=status, raw_ack=ack,
            attempt_consumed=True, automatic_retry=False), 'start-ack.json')
        if failure:
            save(dict(status='native_capture_unqualified_inspect_only', operation_id=OPERATION_ID,
                start_ack_receipt=ack_record['protected_receipt'], observations=observations,
                failure_code=failure, attempt_consumed=True, acceptance_recorded=False,
                automatic_retry=False, sql_calls=0, model_calls=0,
                **{k:False for k in FALSE_FLAGS}), 'capture-diagnostic.json')
            raise ValueError('native_capture_unqualified_inspect_only') from None
        require(old.previous.sources() == pf['sources'], 'native_sources_changed_after_capture')
        return save(dict(status='native_running_replica_observed', operation_id=OPERATION_ID,
            start_ack_receipt=ack_record['protected_receipt'], observations=observations, binding=binding,
            sql_calls=0, model_calls=0, **{k:False for k in FALSE_FLAGS}), 'replica-binding.json')


def chain(raw, pf):
    intent_raw, intent = load('start-intent.json'); ack_raw, ack = load('start-ack.json')
    expected = intent_value(raw, pf)
    require(set(intent) == set(expected) | {'status','reservation_receipt'}
        and canonical({k:intent[k] for k in expected}) == canonical(expected)
        and intent['status'] == 'reserved_single_qualification'
        and set(ack) == {'status','operation_id','intent_receipt','http_status','raw_ack','attempt_consumed','automatic_retry'}
        and ack['status'] == 'new_qualification_execution_acknowledged'
        and ack['operation_id'] == OPERATION_ID and ack['intent_receipt'] == intent_raw['protected_receipt']
        and ack['attempt_consumed'] is True and ack['automatic_retry'] is False, 'native_start_chain_invalid')
    reservation_readback(intent['reservation_receipt'], expected)
    name = old.acknowledged_execution(ack)
    require(name not in pf['prior']['prior_executions'], 'native_prior_execution_replayed')
    return ack_raw, name


class CollectionPending(ValueError):
    pass


def collect_impl(verify, evidence):
    raw, pf = prepared(); ack_raw, name = chain(raw, pf)
    binding_raw, captured = load('replica-binding.json')
    require(captured['status'] == 'native_running_replica_observed' and captured['operation_id'] == OPERATION_ID
        and captured['start_ack_receipt'] == ack_raw['protected_receipt'], 'native_capture_chain_invalid')
    bind = captured['binding']; prior = pf['prior']['pf']
    native.validate_execution(bind['raw_execution'], prior, name, terminal=False)
    replica = native.validate_replica(bind['raw_replicas'], name)
    require(replica == bind['replica'], 'native_replica_changed')
    client = old.arm(prior['prerequisite'])
    before = old.get(client, JOB_ID+'/executions/'+name, old.transport.JOB_API)
    evidence['raw_execution_before'] = before
    require(before.get('properties', {}).get('startTime') == bind['raw_execution']['properties']['startTime'],
        'native_execution_start_changed')
    if before.get('properties', {}).get('status') in ('Running', 'Processing'):
        native.validate_execution(before, prior, name, terminal=False)
        raise CollectionPending('native_execution_still_running_read_only_wait')
    native.validate_execution(before, prior, name, terminal=True)
    props = before['properties']; require(props['status'] == 'Succeeded', 'native_execution_failed')
    require(props['startTime'] == bind['raw_execution']['properties']['startTime'], 'native_execution_start_changed')
    scope = old.console_scope(name, (old.transport.utc(props['startTime'])-timedelta(minutes=1)).isoformat(),
        (old.transport.utc(props['endTime'])+timedelta(minutes=10)).isoformat())
    responses = []; logs = old.LogsTransport(old.AzureCliCredential())
    evidence['raw_console_responses'] = responses
    def query(scope):
        value = logs.call(scope); responses.append(value); return value
    console = old.collect_console(query, scope)
    if not console['rows']: raise CollectionPending('native_logs_pending_read_only_wait')
    observed = native.validate_log_binding(console['rows'], replica, prior)
    after = old.get(client, JOB_ID+'/executions/'+name, old.transport.JOB_API)
    evidence['raw_execution_after'] = after
    live = guards(prior, pf['prior']['prior_executions'], name)
    evidence['live_guards'] = live
    prior_now = live['prior_execution_readbacks']
    require(before == after and prior_now == pf['prior']['prior_raw_executions']
        and canonical(old.n.resource_guards()) == canonical(prior['resource_guards'])
        and old.previous.sources() == pf['sources'], 'native_final_drift')
    value = dict(status='secretless_aca_container_identity_runtime_verified', operation_id=OPERATION_ID,
        preflight_receipt=raw['protected_receipt'], start_ack_receipt=ack_raw['protected_receipt'],
        replica_binding_receipt=binding_raw['protected_receipt'], raw_execution_before=before,
        raw_execution_after=after, current_job=live['job'], current_roles=live['roles'],
        complete_execution_inventory=live['executions'], raw_prior_executions=prior_now,
        console_scope=scope, raw_console_responses=responses, complete_console_observation=console,
        observed=observed, acceptance_recorded=True, original_stopped_attempt_requalified=False,
        configured_no_database_model_secrets=True, runtime_metadata_verified=True,
        sql_calls=0, model_calls=0, **{k:False for k in FALSE_FLAGS})
    if verify:
        accepted_raw, accepted = load('acceptance.json')
        require(canonical(accepted) == canonical(value), 'native_fresh_evidence_differs')
        return dict(status='independent_native_runtime_readback_verified', protected_receipt=accepted_raw['protected_receipt'])
    return save(value, 'acceptance.json')


def collect(verify=False):
    require(type(verify) is bool, 'native_verify_flag_invalid')
    require(not any((FOLDER/name).exists() for name in ('capture-diagnostic.json', 'start-outcome.json',
        'collection-stop.json', 'collection-diagnostic.json')), 'native_attempt_unqualified_inspect_only')
    stop_blob = old.storage().get_blob_client(old.previous.q.deploy.CONTAINER, PREFIX+'collection-stop.json')
    old.blob_absent(stop_blob)
    evidence = {}
    try: return collect_impl(verify, evidence)
    except CollectionPending: raise
    except Exception:
        # Fresh verification is strictly read-only. A failed first collection
        # consumes its acceptance attempt and preserves a bounded diagnosis.
        if verify: raise ValueError('native_verification_unqualified_read_only') from None
        if not evidence: raise ValueError('native_collection_prerequisite_unqualified') from None
        record = dict(status='native_collection_unqualified_inspect_only', operation_id=OPERATION_ID,
            acceptance_recorded=False, automatic_retry=False, sql_calls=0, model_calls=0,
            **{k:False for k in FALSE_FLAGS})
        payload = canonical(evidence)
        record['observations'] = evidence if len(payload) <= 768*1024 else dict(
            complete_raw_retained=False, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        with (FOLDER/'collection-stop.json').open('x', encoding='utf-8') as output:
            output.write(json.dumps(record, sort_keys=True, indent=2))
        body = canonical(record)
        try:
            stop_blob.upload_blob(body, length=len(body), blob_type='BlockBlob', overwrite=False,
                match_condition=old.MatchConditions.IfMissing, max_concurrency=1,timeout=10,retry_total=0)
            save(record, 'collection-diagnostic.json')
        except Exception: raise ValueError('native_collection_stop_unretained_inspect_only') from None
        raise ValueError('native_collection_unqualified_inspect_only') from None


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('stage', choices=('prepare','start','collect','verify'))
    stage = parser.parse_args().stage; old.previous.q.canary.host.checked_owner()
    if stage != 'verify': FOLDER.mkdir(parents=True, exist_ok=True)
    result = {'prepare':prepare, 'start':start, 'collect':collect, 'verify':lambda:collect(True)}[stage]()
    print(json.dumps({k:result[k] for k in ('status','protected_receipt')}, sort_keys=True), flush=True)


if __name__ == '__main__': main()
