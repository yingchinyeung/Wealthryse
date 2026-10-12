"""Read-only continuation for one completed synthetic Azure canary.

No job PUT/START, SQL, secret-value or model path. The replica provenance is a
protected owner observation, NOT retroactive automated capture or a proof that
Azure did not replace a replica. New current QA does not rewrite historical QA.
Only fixed create-only audit-evidence records may be added by prepare/collect.
The verify stage only rereads remote evidence and the complete live log stream.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

import qualify_owner_task_catalog_canary as prior

ROOT = prior.deploy.ROOT
HISTORY_FOLDER = prior.FOLDER
FOLDER = ROOT / '.work/owner-task-catalog-readback-20261004'
ADDITIONS = ('scripts/verify_owner_task_catalog_canary_readback.py',
    'tests/test_owner_task_catalog_canary_readback.py')
EXECUTION = 'ibkrv2stg-ownertrial-log-canary-9hh541u'
EXECUTION_ID = prior.PATH + '/executions/' + EXECUTION
REPLICA = EXECUTION + '-87mk2'
REPLICA_TYPE = 'Microsoft.App/containerapps/revisions/replicas'
RECORD_SHA = '5c7101833c90c9539fd9fa1a158053dfe04bee71a4746ceb42eecd10981a35b5'
PINNED = {
    'preflight.json': ('ae7e1836cc94f7e5995bf2ec0524e2bca7e5b0abcd59a8ed29eb9c33aa42a34c', '2026-10-04T12:38:53.2891355Z'),
    'install-ack.json': ('94f94a4e0a42cab5ddb381e38490024ce8e6490c093930fc7b4fab290b43e9c8', '2026-10-04T12:44:28.0266417Z'),
    'readback-correction.json': ('38f731712ac04556ab62655edff490f74695f86738303a38b1340b2c699e9d87', '2026-10-04T13:22:16.8421209Z'),
    'install-acceptance.json': ('9b3224795c395ea57b4bdca3c8fdbbf059364a12be9769fa827e5e52fa9a83e2', '2026-10-04T13:28:01.1233365Z'),
    'start-ack.json': ('6c66a35159f71646f88505a308eb1292b328517634e5747867d33d00741c2ae3', '2026-10-04T13:31:42.5808438Z'),
    'running-metadata-observed.json': ('0255cb4a2c9b05c6dfea127a44309fe45c95497ea171e0c39dec68b825345106', '2026-10-04T13:35:47.7516538Z'),
    'log-diagnostic.json': ('f55690b650554ea502829b6d579bb5b38c0eaf2b850b5b1fc51d3280ef8710a2', '2026-10-04T13:50:32.1977619Z')}
HISTORY_NAMES = ('preflight.json', 'install-intent.json', 'install-ack.json',
    'readback-correction.json', 'install-acceptance.json', 'start-intent.json',
    'start-ack.json', 'running-metadata-observed.json', 'log-diagnostic.json')
FROZEN_CONTROLLER = {
    'scripts/qualify_owner_task_catalog_canary.py': 'bdbac18bfa59a3f01bef8d4ce6e5c824ccb42b433e0420d2453ea90cc2f82eb7',
    'tests/test_owner_task_catalog_canary_controller.py': 'd114f8fe06fcb714d72e1cb501e89f7a270b974bbd30847eda6e07ad206c386e'}
MAX_BYTES = prior.MAX_BYTES
FALSE_FLAGS = ('platform_replacement_excluded', 'provider_sql_authority',
    'provider_semantics_verified', 'fresh_restricted_login_verified',
    'model_credential_containment_verified', 'owner_runtime_active',
    'six_specialist_runtimes_active', 'production_migration_059_installed',
    'ibkr_source_writes', 'credentials_supplied')


def require(condition, code):
    if not condition:
        raise ValueError(code)


def canonical(value):
    return prior.codec.canonical(value)


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def utc(value):
    require(type(value) is str and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|\+00:00)', value) is not None,
        'readback_exact_utc_required')
    result = datetime.fromisoformat(value.removesuffix('Z') + ('+00:00' if value.endswith('Z') else ''))
    require(result.tzinfo == timezone.utc, 'readback_exact_utc_required')
    return result


def binding():
    return asdict(prior.binding())


def sources():
    return prior.sources()


def validate_sources(old, current):
    require(type(old) is dict and type(current) is dict and len(old) == 705
        and set(current) == set(old) | set(ADDITIONS) and not set(old) & set(ADDITIONS)
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for k,v in current.items())
        and all(current[name] == value for name,value in old.items())
        and all(old.get(name) == value for name,value in FROZEN_CONTROLLER.items()),
        'readback_only_two_new_sources_allowed')


def body(raw):
    require(type(raw) is dict and 'protected_receipt' in raw, 'readback_protected_record_required')
    return {k:v for k,v in raw.items() if k != 'protected_receipt'}


def receipt_binding(raw, kind, pin=None):
    value = body(raw)
    payload = canonical(value)
    receipt = raw['protected_receipt']
    sha = hashlib.sha256(payload).hexdigest()
    require(type(receipt) is dict and set(receipt) == {'blob','sha256','version_id','protected_readback_verified'}
        and receipt['protected_readback_verified'] is True
        and receipt['sha256'] == sha and receipt['blob'] ==
            f'acceptance/gateway-read-owner-task-pg-{kind}-{sha}.json'
        and type(receipt['version_id']) is str and re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id']) is not None
        and 0 < len(payload) <= MAX_BYTES, 'readback_exact_receipt_required')
    utc(receipt['version_id'])
    require(pin is None or (sha,receipt['version_id']) == pin, 'readback_pinned_history_required')
    return value


def read_local(folder, name):
    path = folder / name
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_BYTES,
        'readback_complete_local_record_required')
    return prior.strict_json(path.read_bytes())


def history():
    records = {}
    for name in HISTORY_NAMES:
        raw = read_local(HISTORY_FOLDER, name)
        kind = 'catalog-canary-' + name.removesuffix('.json')
        value = receipt_binding(raw,kind,PINNED.get(name))
        require(canonical(prior.deploy.protected(raw,kind)) == canonical(value),
            'readback_remote_history_differs')
        records[name] = raw
    validate_history(records)
    for stage in ('install','start'):
        prior.reservation_readback(stage,body(records[stage+'-ack.json'])['reservation'],
            canonical(body(records[stage+'-intent.json'])))
    return records


def validate_history(records):
    require(type(records) is dict and set(records) == set(HISTORY_NAMES), 'readback_complete_history_required')
    for name,raw in records.items():
        receipt_binding(raw,'catalog-canary-'+name.removesuffix('.json'),PINNED.get(name))
    p, correction = body(records['preflight.json']), body(records['readback-correction.json'])
    require(p.get('status') == 'prepared_no_secret_catalog_canary'
        and canonical(p.get('definition')) == canonical(prior.definition())
        and p.get('binding') == binding() and p.get('registration_sha256') == prior.TARGET.registration_sha256,
        'readback_original_worker_differs')
    prior.correction_sources(p,correction.get('sources'))
    prior.validate_correction_checks(correction.get('local_checks'))
    require(correction.get('status') == 'reviewed_canary_readback_only_correction'
        and correction.get('preflight_receipt') == records['preflight.json']['protected_receipt']
        and correction.get('install_ack_receipt') == records['install-ack.json']['protected_receipt']
        and correction.get('definition_sha256') == digest(prior.definition())
        and correction.get('binding') == binding()
        and correction.get('runtime') == p.get('runtime')
        and correction.get('allowed_predecessors') == prior.CORRECTION_PREDECESSORS
        and correction.get('requested_location') == 'eastasia' and correction.get('observed_location') == 'East Asia'
        and canonical(correction.get('observed_resource_defaults')) == canonical({'ephemeralStorage':''})
        and correction.get('creation_redispatch_authorized') is False and correction.get('start_was_absent') is True
        and correction.get('credentials_supplied') is False and correction.get('ibkr_source_writes') is False
        and type(correction.get('sql_calls')) is int and correction['sql_calls'] == 0,
        'readback_historical_correction_differs')
    for stage in ('install','start'):
        intent,ack = body(records[stage+'-intent.json']),body(records[stage+'-ack.json'])
        require(intent.get('stage') == stage and ack.get('stage') == stage
            and intent.get('preflight_receipt') == records['preflight.json']['protected_receipt']
            and intent.get('definition_sha256') == digest(p['definition']) and intent.get('binding') == binding()
            and intent.get('automatic_retry') is False and intent.get('credentials_supplied') is False
            and type(intent.get('sql_calls')) is int and intent['sql_calls'] == 0
            and ack.get('intent_receipt') == records[stage+'-intent.json']['protected_receipt']
            and type(ack.get('http_status')) is int and ack['http_status'] in (200,201,202),
            'readback_consumed_intent_chain_differs')
    installed = body(records['install-acceptance.json'])
    require(installed.get('status') == 'installed_no_secret_manual_canary'
        and installed.get('install_ack_receipt') == records['install-ack.json']['protected_receipt']
        and installed.get('preflight_receipt') == records['preflight.json']['protected_receipt']
        and body(records['install-ack.json']).get('readiness_verified') is False
        and body(records['install-intent.json']).get('unrelated_administrator_race_excluded') is False
        and body(records['start-intent.json']).get('install_acceptance_receipt') == records['install-acceptance.json']['protected_receipt']
        and body(records['start-ack.json']).get('execution') == EXECUTION
        and body(records['start-ack.json']).get('execution_id') == EXECUTION_ID,
        'readback_exact_consumed_execution_required')
    observation = body(records['running-metadata-observed.json'])
    require(observation.get('status') == 'running_metadata_observed_not_accepted'
        and observation.get('start_ack_receipt') == records['start-ack.json']['protected_receipt']
        and observation.get('binding') == binding() and observation.get('source_manifest') == correction['sources']
        and observation.get('response_root_keys') == ['value']
        and type(observation.get('raw_replica_response_sha256')) is str
        and re.fullmatch('[0-9a-f]{64}',observation['raw_replica_response_sha256']) is not None
        and all(observation.get(k) is False for k in ('transport_acceptance','provider_sql_authority','ibkr_source_writes','credentials_supplied'))
        and all(type(observation.get(k)) is int and observation[k] == 0 for k in ('sql_calls','model_calls')),
        'readback_owner_observation_differs')
    diagnostic = body(records['log-diagnostic.json'])
    require(diagnostic.get('status') == 'diagnostic_complete_live_transport_not_controller_acceptance'
        and diagnostic.get('observed_replica_receipt') == records['running-metadata-observed.json']['protected_receipt']
        and diagnostic.get('sources') == correction['sources'] and diagnostic.get('binding') == binding()
        and diagnostic.get('mapping_provenance') == 'protected_owner_observed_replica'
        and diagnostic.get('controller_acceptance') is False and diagnostic.get('complete_parts_retained') is False
        and diagnostic.get('diagnostic_record_sha256') == RECORD_SHA,
        'readback_historical_diagnostic_differs')


class ReadOnlyArm:
    def __init__(self, transport=None):
        self.__transport = transport or prior.ArmTransport()

    def get(self, path):
        require(type(path) is str and path in (prior.PATH+'?api-version='+prior.API,
            EXECUTION_ID+'?api-version='+prior.API,
            EXECUTION_ID+'/replicas?api-version='+prior.REPLICA_API,
            prior.TARGET.environment_resource_id+'?api-version='+prior.API), 'readback_registered_get_required')
        return self.__transport.call('GET',path)[0]


def execution(value, status):
    require(type(status) is str and status in ('Running','Succeeded'), 'readback_registered_status_required')
    require(type(value) is dict and value.get('id') == EXECUTION_ID and value.get('name') == EXECUTION,
        'readback_exact_execution_required')
    p = value.get('properties',{})
    require(type(p) is dict and p.get('status') == status, 'readback_execution_status_differs')
    prior.validate_template(p.get('template'),prior.definition()['properties']['template'])
    start = utc(p.get('startTime'))
    end = p.get('endTime')
    require((end is None if status == 'Running' else type(end) is str and start < utc(end) <= start+timedelta(minutes=20)),
        'readback_execution_window_differs')
    return {'execution':EXECUTION,'execution_id':EXECUTION_ID,'status':status,
        'start_time':p['startTime'],'end_time':end,'image':prior.IMAGE,
        'validated_requested_template_sha256':digest(prior.definition()['properties']['template']),
        'actual_template_readback_sha256':digest(p['template'])}


def owner_mapping(value, running, terminal):
    require(type(value) is dict and set(value) == {'id','name','type','createdTime','runningState','containers'}
        and value.get('id') == EXECUTION_ID+'/replicas/'+REPLICA and value.get('name') == REPLICA
        and value.get('type') == REPLICA_TYPE and value.get('runningState') == 'Running',
        'readback_exact_owner_replica_required')
    require(utc(running['start_time']) <= utc(value.get('createdTime')) <= utc(terminal['end_time']),
        'readback_replica_window_differs')
    require(canonical(value.get('containers')) == canonical([{'name':prior.CONTAINER,'ready':True,
        'started':True,'restartCount':0,'runningState':'Running'}]), 'readback_owner_container_differs')
    return {'log_group':REPLICA,'container_name':prior.CONTAINER,
        'provenance':'protected_owner_observed_replica','original_automated_capture_passed':False}


def observed_execution(records, actual):
    observation = body(records['running-metadata-observed.json'])
    running = execution(observation['execution_response'],'Running')
    terminal = execution(actual,'Succeeded')
    require(all(running[k] == terminal[k] for k in ('execution_id','start_time','image',
        'validated_requested_template_sha256','actual_template_readback_sha256'))
        and utc(running['start_time']) <= utc(observation.get('observed_at_utc')) <= utc(terminal['end_time'])
        and canonical(body(records['log-diagnostic.json'])['execution_response']) == canonical(actual),
        'readback_protected_terminal_or_observation_drift')
    mapping = owner_mapping(observation.get('replica_identity'),running,terminal)
    fmt = lambda dt:dt.isoformat(timespec='seconds').replace('+00:00','Z')
    start = utc(terminal['start_time'])
    return {'execution':terminal,'mapping':mapping,'scope_start':fmt(start-timedelta(minutes=2)),
        'scope_end':fmt(start+timedelta(minutes=20))}


def guards(records, arm):
    old = body(records['readback-correction.json'])['sources']
    current = sources()
    validate_sources(old,current)
    p = body(records['preflight.json'])
    require(canonical(prior.host.runtime_descriptor()) == canonical(p['runtime'])
        and canonical(prior.baseline()) == canonical(p['baseline'])
        and canonical(prior.historical_host()) == canonical(p['historical_host_receipt']),
        'readback_current_runtime_or_baseline_differs')
    prior.validate_job(arm.get(prior.PATH+'?api-version='+prior.API))
    env = arm.get(prior.TARGET.environment_resource_id+'?api-version='+prior.API)
    config=env.get('properties',{}).get('appLogsConfiguration',{})
    require(env.get('id') == prior.TARGET.environment_resource_id and config.get('destination') == 'log-analytics'
        and config.get('logAnalyticsConfiguration',{}).get('customerId') == prior.logs.WORKSPACE,
        'readback_log_environment_differs')
    observed = observed_execution(records,arm.get(EXECUTION_ID+'?api-version='+prior.API))
    require(sources() == current, 'readback_source_changed_during_guards')
    return current,observed


def record_kind(name):
    require(name in ('preflight.json','acceptance.json') or re.fullmatch(r'part-00[0-8]\.json',name) is not None,
        'readback_record_name_unregistered')
    return 'catalog-readback-'+name.removesuffix('.json')


def load(name):
    kind=record_kind(name)
    raw=read_local(FOLDER,name)
    value=receipt_binding(raw,kind)
    require(canonical(prior.deploy.protected(raw,kind)) == canonical(value), 'readback_remote_record_differs')
    return raw,value


def save(value,name):
    kind=record_kind(name)
    require(0 < len(canonical(value)) <= MAX_BYTES, 'readback_record_oversized')
    require(not (FOLDER/name).exists(), 'readback_record_consumed_inspect_only')
    prior.deploy.FOLDER=FOLDER
    raw=prior.deploy.save(value,name,kind)
    receipt_binding(raw,kind)
    require(canonical(prior.deploy.protected(raw,kind)) == canonical(value), 'readback_saved_record_differs')
    return raw


def history_receipts(records):
    return {name:raw['protected_receipt'] for name,raw in records.items()}


def prepare(arm):
    require(not (FOLDER/'preflight.json').exists(), 'readback_preflight_already_consumed')
    before,runtime=sources(),prior.host.runtime_descriptor()
    records=history()
    current,observed=guards(records,arm)
    require(before == current, 'readback_source_changed_before_qa')
    checks=prior.qa.run_local_checks()
    prior.validate_correction_checks(checks)
    require(type(checks.get('test_count')) is int and checks['test_count'] >= 1978
        and sources() == before and prior.host.runtime_descriptor() == runtime,
        'readback_current_qa_or_sources_differs')
    after_sources,after_observed=guards(records,arm)
    require(after_sources == before and canonical(after_observed) == canonical(observed),
        'readback_live_guard_changed_during_qa')
    return save({'status':'prepared_readonly_owner_observed_canary_readback','sources':before,
        'runtime':runtime,'local_checks':checks,'history_receipts':history_receipts(records),
        'binding':binding(),'definition_sha256':digest(prior.definition()),'observed':observed,
        'allowed_additions':list(ADDITIONS),'sql_calls':0,'model_calls':0,
        **{k:False for k in FALSE_FLAGS}},'preflight.json')


def frozen(preflight,records,arm):
    prior.validate_correction_checks(preflight.get('local_checks'))
    require(preflight['local_checks']['test_count'] >= 1978, 'readback_current_qa_or_sources_differs')
    current,observed=guards(records,arm)
    require(preflight.get('status') == 'prepared_readonly_owner_observed_canary_readback'
        and canonical(preflight.get('sources')) == canonical(current)
        and canonical(preflight.get('runtime')) == canonical(prior.host.runtime_descriptor())
        and preflight.get('history_receipts') == history_receipts(records)
        and preflight.get('binding') == binding() and preflight.get('definition_sha256') == digest(prior.definition())
        and canonical(preflight.get('observed')) == canonical(observed)
        and preflight.get('allowed_additions') == list(ADDITIONS)
        and all(preflight.get(k) is False for k in FALSE_FLAGS)
        and all(type(preflight.get(k)) is int and preflight[k] == 0 for k in ('sql_calls','model_calls')),
        'readback_frozen_preflight_differs')
    return observed


def complete_evidence(query,observed):
    scope=prior.logs.CatalogLogScope(prior.binding(),observed['mapping']['log_group'],prior.CONTAINER,
        observed['scope_start'],observed['scope_end'])
    evidence=prior.logs.collect_evidence(query,scope)
    require(prior.complete_evidence(evidence,scope) == RECORD_SHA, 'readback_exact_expected_record_required')
    return evidence


def part_value(lines,index,preflight_raw,records,observed):
    require(type(index) is int and 0 <= index < 9 and type(lines) is list and len(lines) == 1025,
        'readback_exact_part_input_required')
    return {'kind':'complete_owner_observed_canary_wire_part','index':index,'offset':index*128,
        'lines':lines[index*128:(index+1)*128],'binding':binding(),'execution_id':EXECUTION_ID,
        'preflight_receipt':preflight_raw['protected_receipt'],
        'owner_observation_receipt':records['running-metadata-observed.json']['protected_receipt'],
        'mapping':observed['mapping']}


def retain_parts(lines,preflight_raw,records,observed):
    # Prepare/cap ALL parts before the first write. A partial local receipt or
    # lost ACK is inspection-only, never absent and never silently overwritten.
    values=[part_value(lines,i,preflight_raw,records,observed) for i in range(9)]
    require(all(0 < len(canonical(v)) <= MAX_BYTES for v in values), 'readback_part_oversized')
    entries=[]
    for index,value in enumerate(values):
        name=f'part-{index:03d}.json'
        if (FOLDER/name).exists():
            raw,old=load(name)
            require(canonical(old) == canonical(value), 'readback_existing_part_differs')
        else:
            raw=save(value,name)
        entries.append({'name':name,'receipt':raw['protected_receipt'],'index':index,
            'offset':index*128,'count':128 if index < 8 else 1})
    return entries


def verify_parts(entries,preflight_raw,records,observed):
    require(type(entries) is list and len(entries) == 9, 'readback_complete_part_manifest_required')
    lines=[]
    for index,entry in enumerate(entries):
        name=f'part-{index:03d}.json'
        raw,value=load(name)
        require(canonical(entry) == canonical({'name':name,'receipt':raw['protected_receipt'],'index':index,
            'offset':index*128,'count':128 if index < 8 else 1}), 'readback_part_receipt_chain_differs')
        require(type(value.get('lines')) is list and len(value['lines']) == (128 if index < 8 else 1),
            'readback_part_count_differs')
        wanted=part_value(['unused']*1025,index,preflight_raw,records,observed)
        wanted['lines']=value['lines']
        require(canonical(value) == canonical(wanted), 'readback_part_binding_differs')
        lines.extend(value['lines'])
    decoded=prior.codec.decode_record(lines,prior.binding())
    require(canonical(decoded) == canonical(prior.worker.synthetic_record(prior.binding()))
        and digest(decoded) == RECORD_SHA, 'readback_complete_protected_record_differs')
    return lines


def collect(arm,query,verify_only=False):
    require(type(verify_only) is bool, 'readback_exact_verify_mode_required')
    preflight_raw,preflight=load('preflight.json')
    records=history()
    observed=frozen(preflight,records,arm)
    evidence=complete_evidence(query,observed)
    if verify_only:
        raw,accepted=load('acceptance.json')
        entries=accepted.get('parts')
    else:
        require(not (FOLDER/'acceptance.json').exists(), 'readback_acceptance_exists_use_verify')
        entries=retain_parts(evidence['lines'],preflight_raw,records,observed)
    protected_lines=verify_parts(entries,preflight_raw,records,observed)
    require(protected_lines == evidence['lines'], 'readback_live_and_protected_wire_differs')
    require(canonical(frozen(preflight,records,arm)) == canonical(observed), 'readback_final_guard_drift')
    value={'status':'complete_owner_observed_canary_transport_retained',
        'preflight_receipt':preflight_raw['protected_receipt'],'history_receipts':history_receipts(records),
        'binding':binding(),'observed':observed,'parts':entries,'record_bytes':4194304,'chunk_count':1024,
        'frame_count':1025,'record_sha256':RECORD_SHA,
        'header_inventory_sha256':evidence['header_inventory_sha256'],
        'transport_scope':'protected_owner_observed_replica_only',
        'original_automated_capture_passed':False,'protected_complete_record_readback':True,
        'existing_jobs_unchanged':34,'daily_schedule_hkt':'18:00','sql_calls':0,'model_calls':0,
        **{k:False for k in FALSE_FLAGS}}
    if verify_only:
        require(canonical(accepted) == canonical(value), 'readback_fresh_acceptance_or_live_stream_differs')
        return {'status':'independent_owner_observed_transport_readback_verified','protected_receipt':raw['protected_receipt']}
    raw=save(value,'acceptance.json')
    return {'status':value['status'],'protected_receipt':raw['protected_receipt']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=('prepare','collect','verify'))
    args=parser.parse_args()
    prior.host.checked_owner()
    arm=ReadOnlyArm()
    FOLDER.mkdir(parents=True,exist_ok=True)
    if args.stage == 'prepare':
        raw=prepare(arm)
        result={'status':raw['status'],'tests':raw['local_checks']['test_count'],'protected_receipt':raw['protected_receipt']}
    else:
        result=collect(arm,prior.logs.LogsTransport(prior.AzureCliCredential()),verify_only=args.stage == 'verify')
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__ == '__main__':
    main()
