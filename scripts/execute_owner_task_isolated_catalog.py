"""One reserved SELECT-only catalog execution on the registered private host.

Trusted owner controller, not an agent tool. No arbitrary routes/templates,
secret-value retrieval, SQL setup, production mutation or automatic redispatch.
Observed catalog/TLS evidence never constitutes restricted-login acceptance.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from azure.core import MatchConditions

import prepare_owner_task_isolated_catalog as preparation
import owner_task_catalog_complete_logs as logs
import inspect_owner_task_isolated_provider as collector

host, launch = preparation.host, preparation.launch
ROOT = preparation.ROOT
FOLDER = ROOT / '.work/owner-task-isolated-catalog-execution-20261005'
PREFIX = 'probes/owner-codex-isolated-catalog-execution-20261005/'
KIND = 'isolated-catalog-execution-'
OPERATION = 'e299c43c-2602-483c-a283-247234261ea6'
JOB, JOB_ID = launch.registry.JOB, launch.registry.JOB_ID
ADDITIONS = frozenset(('scripts/execute_owner_task_isolated_catalog.py',
    'tests/test_owner_task_isolated_catalog_execution.py'))
canonical, digest = launch.canonical, launch.digest
MAX_BYTES = 2 * 1024 * 1024


def require(condition, code):
    if not condition: raise ValueError(code)


def now(): return datetime.now(timezone.utc).isoformat()


def utc(value):
    require(type(value) is str and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|\+00:00)', value), 'catalog_time_invalid')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def name(value):
    require(type(value) is str and re.fullmatch(re.escape(JOB) + r'-[a-z0-9]{7,12}', value),
        'catalog_execution_name_invalid')
    return value


def validate_sources(previous, current):
    require(type(previous) is dict and len(previous) == 748 and type(current) is dict
        and set(current) == set(previous) | ADDITIONS and not set(previous) & ADDITIONS
        and all(current[k] == v for k, v in previous.items())
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v)
            for k, v in current.items()), 'catalog_sources_not_exactly_additive')


def validate_execution(value, plan, execution, *, terminal=False):
    execution = name(execution)
    require(type(terminal) is bool and type(value) is dict and set(value) == {'id','name','type','properties'}
        and value['id'] == JOB_ID + '/executions/' + execution and value['name'] == execution
        and value['type'] == 'Microsoft.App/jobs/executions', 'catalog_execution_invalid')
    props = value['properties']
    require(type(props) is dict and {'startTime','status','template'} <= set(props)
        <= {'startTime','endTime','status','template'}, 'catalog_execution_invalid')
    start = utc(props['startTime'])
    require(props['status'] in (('Succeeded','Failed','Stopped') if terminal else ('Running','Processing')),
        'catalog_execution_state_invalid')
    if terminal: require(utc(props['endTime']) >= start, 'catalog_execution_invalid')
    else: require(props.get('endTime') is None, 'catalog_execution_invalid')
    source = {k: (ROOT/k).read_bytes() for k in launch.prior.SOURCE_PATHS}
    launch.validate_template(props['template'], source, plan['source_hashes'], plan['expected_runtime'], execution=True)
    return props


def validate_replica(value, execution):
    execution = name(execution)
    require(type(value) is dict and set(value) <= {'value','nextLink'} and value.get('nextLink') in (None,'')
        and type(value.get('value')) is list and len(value['value']) <= 1, 'catalog_replica_invalid')
    if not value['value']: return None
    row = value['value'][0]
    require(type(row) is dict and set(row) == {'id','name','type','properties'}
        and type(row['name']) is str and re.fullmatch(re.escape(execution)+r'-[a-z0-9]{5,12}',row['name'])
        and row['id'] == JOB_ID+'/executions/'+execution+'/replicas/'+row['name']
        and row['type'] == 'Microsoft.App/containerapps/revisions/replicas', 'catalog_replica_invalid')
    props = row['properties']; needed = {'createdTime','runningState','containers'}
    require(type(props) is dict and needed <= set(props) <= needed | {'runningStateDetails','initContainers'}
        and props.get('initContainers',[]) == [] and props['runningState'] in ('Running','NotRunning','Unknown')
        and type(props['containers']) is list and len(props['containers']) <= 1, 'catalog_replica_invalid')
    utc(props['createdTime'])
    for item in (props.get('runningStateDetails',''),):
        require(type(item) is str and len(item) <= 4096 and all(c.isprintable() for c in item), 'catalog_replica_invalid')
    if not props['containers']: return None
    c = props['containers'][0]; needed = {'name','ready','started','restartCount','runningState'}
    require(type(c) is dict and needed <= set(c) <= needed | {'runningStateDetails','containerId','execEndpoint','logStreamEndpoint'}
        and c['name'] == launch.prior.CONTAINER and type(c['ready']) is bool and type(c['started']) is bool
        and type(c['restartCount']) is int and c['restartCount'] == 0
        and c['runningState'] in ('Running','Waiting','Terminated'), 'catalog_replica_invalid')
    details=c.get('runningStateDetails','')
    require(type(details) is str and len(details)<=4096 and all(x.isprintable() for x in details),'catalog_replica_invalid')
    if 'containerId' in c:
        require(type(c['containerId']) is str and re.fullmatch(r'containerd://(?:[0-9a-f]{32}|[0-9a-f]{64})',c['containerId']),
            'catalog_replica_invalid')
    base='eastasia.azurecontainerapps.dev/subscriptions/'+launch.registry.SUBSCRIPTION+'/resourceGroups/'+launch.registry.RESOURCE_GROUP
    tail='/replicas/'+row['name']+'/containers/'+launch.prior.CONTAINER
    endpoints=dict(execEndpoint='wss://'+base+'/containerApps/'+JOB+'/revisions/'+execution+tail+'/exec',
        logStreamEndpoint='https://'+base+'/jobs/'+JOB+'/executions/'+execution+tail+'/logstream')
    for key,wanted in endpoints.items():
        require(key not in c or type(c[key]) is str and c[key]==wanted,'catalog_replica_invalid')
    require(not c['ready'] or c['started'] and c['runningState']=='Running' and props['runningState']=='Running',
        'catalog_replica_invalid')
    if c['runningState']=='Waiting':
        require(not c['ready'] and not c['started'],'catalog_replica_invalid');return None
    require(c['runningState']!='Terminated','catalog_replica_terminated')
    return row if c['ready'] and c['started'] and props['runningState']=='Running' else None


def validate_inventory(value, execution=None):
    require(type(value) is dict and set(value)<= {'value','nextLink'} and value.get('nextLink') in (None,'')
        and type(value.get('value')) is list, 'catalog_inventory_invalid')
    rows=value['value'];require(len(rows)==(0 if execution is None else 1),'catalog_foreign_execution')
    if execution is not None:
        require(type(rows[0]) is dict and rows[0].get('name')==name(execution)
            and rows[0].get('id')==JOB_ID+'/executions/'+execution,'catalog_foreign_execution')


class Transport(preparation.Transport):
    """Only registered catalog GETs and one exact template START are added."""
    def call(self, method, path, api, *, body=None, plan=None, **kwargs):
        if method=='GET' and path.startswith(JOB_ID+'/executions'):
            require(body is None and not kwargs, 'catalog_route_invalid')
            tail=path[len(JOB_ID+'/executions'):]
            if tail:
                pieces=tail.split('/');require(len(pieces) in (2,3) and pieces[0]=='','catalog_route_invalid')
                name(pieces[1]);require(len(pieces)==2 or pieces[2]=='replicas','catalog_route_invalid')
            require(api==('2023-11-02-preview' if tail.endswith('/replicas') else '2025-07-01'),'catalog_route_invalid')
        elif method=='POST':
            require(not kwargs and path==JOB_ID+'/start' and api=='2025-07-01' and type(plan) is dict
                and canonical(body)==canonical(plan['definition']['properties']['template']),'catalog_route_invalid')
        else:
            require(method=='GET','catalog_route_invalid')
            return super().call(method,path,api,body=body,plan=plan,**kwargs)
        response=None
        try:
            if self.token is None or self.token.expires_on<=time.time()+120:
                self.token=self.credential.get_token('https://management.azure.com/.default')
            response=self.session.request(method,'https://management.azure.com'+path+'?api-version='+api,
                json=body,headers={'Authorization':'Bearer '+self.token.token},allow_redirects=False,stream=True,timeout=(5,20))
            require(response.status_code in ((200,) if method=='GET' else (200,201,202)),'catalog_http_unaccepted')
            chunks=[];size=0
            for chunk in response.iter_content(chunk_size=16384):
                require(type(chunk) is bytes,'catalog_http_unaccepted');size+=len(chunk)
                require(size<=MAX_BYTES,'catalog_http_oversized');chunks.append(chunk)
            value=logs.reader._json(b''.join(chunks),'logs_response_invalid')
            require(type(value) is dict,'catalog_http_unaccepted');return value,response.status_code
        except Exception: raise ValueError('catalog_http_outcome_requires_inspection') from None
        finally:
            if response is not None: response.close()


def save(value, filename):
    require(re.fullmatch(r'[a-z][a-z0-9-]*\.json',filename) and len(canonical(value))<=MAX_BYTES,'catalog_evidence_invalid')
    with (FOLDER/filename).open('x',encoding='utf-8') as output:
        host.deploy.archive_protection()
        raw=dict(value,protected_receipt=host.deploy.retain(value,'owner-task-pg-'+KIND+filename[:-5]))
        output.write(json.dumps(raw,sort_keys=True,indent=2))
    host.deploy.protected(raw,KIND+filename[:-5]);return raw


def load(filename):
    raw=json.loads((FOLDER/filename).read_text(encoding='utf-8'))
    return raw,host.deploy.protected(raw,KIND+filename[:-5])


def setup():
    pf_raw,pf=preparation.load('preflight.json');job_raw,job=preparation.load('job-accepted.json')
    require(pf_raw['protected_receipt']['sha256']=='c6ec528d6833bfdd7d1fd8caef99f0e7c66885f639f3706a604c72862b40adff'
        and job_raw['protected_receipt']['sha256']=='ac35116ef5db927d98eb99f5948a22595011b468592ac4120a1adb236dd83cd0'
        and job['status']=='idle_catalog_preparation_effect_verified' and job['job_started'] is False,
        'catalog_preparation_required')
    validate_sources(pf['sources'],host.sources())
    for stage in ('identity','acr','secret'):
        preparation.load(stage+'-accepted.json')
    return pf_raw,pf,job_raw,job


def guards(client,pf,job,execution=None):
    host.checked_owner()
    mi,_=client.call('GET',preparation.MI,'2023-01-31');identity=preparation.identity(mi)
    roles=preparation.role_inventory(client,identity['principalId'],['acr','secret'])
    actual,_=client.call('GET',JOB_ID,'2025-07-01');require(actual==job['observation'],'catalog_job_changed')
    plan=pf['foundation']['plan'];source={k:(ROOT/k).read_bytes() for k in launch.prior.SOURCE_PATHS}
    launch.validate_job(actual,source,plan['source_hashes'],plan['expected_runtime'],
        principal_id=identity['principalId'],client_id=identity['clientId'])
    current=host.observe(host.Transport())
    require(current==pf['foundation']['host_observed'],'catalog_private_host_changed')
    inventory,_=client.call('GET',JOB_ID+'/executions','2025-07-01');validate_inventory(inventory,execution)
    require(preparation.baseline()==pf['baseline'],'catalog_production_baseline_changed')
    return dict(identity=mi,roles=roles,job_sha256=digest(actual),host=current,inventory=inventory)


def prepare(client):
    require(not (FOLDER/'preflight.json').exists(),'catalog_preflight_consumed')
    pf_raw,pf,job_raw,job=setup();before=host.sources();live=guards(client,pf,job)
    checks=preparation.runtime.old.n.run_local_checks()
    preparation.runtime.old.previous.q.canary.validate_correction_checks(checks)
    require(host.sources()==before,'catalog_sources_changed')
    return save(dict(status='prepared_single_catalog_execution',operation_id=OPERATION,sources=before,
        preparation_receipt=pf_raw['protected_receipt'],job_receipt=job_raw['protected_receipt'],live=live,
        local_checks=checks,checked_at_utc=now(),production_changed=False,source_rows_written=0,
        fresh_restricted_login_verified=False,model_credential_isolation_verified=False),'preflight.json')


def reservation_blob(version=None):
    return BlobServiceClient('https://'+host.deploy.ACCOUNT+'.blob.core.windows.net',credential=AzureCliCredential(),
        retry_total=0).get_blob_client(host.deploy.CONTAINER,PREFIX+'start-reservation.json',version_id=version)


def reserve(value):
    payload=canonical(value);host.deploy.archive_protection()
    ack=reservation_blob().upload_blob(payload,length=len(payload),overwrite=False,
        match_condition=MatchConditions.IfMissing,max_concurrency=1,timeout=10,retry_total=0)
    exact=reservation_blob(ack['version_id']);props=exact.get_blob_properties()
    require(props.size==len(payload) and props.version_id==ack['version_id'] and props.etag==ack['etag']
        and exact.download_blob(etag=props.etag,match_condition=MatchConditions.IfNotModified).readall()==payload,
        'catalog_reservation_invalid')
    host.deploy.archive_protection()
    return dict(blob=PREFIX+'start-reservation.json',sha256=hashlib.sha256(payload).hexdigest(),
        version_id=ack['version_id'],etag=ack['etag'])


def start(client):
    require(not any((FOLDER/k).exists() for k in ('start-intent.json','start-ack.json','native-observations.jsonl')),
        'catalog_start_consumed_inspect_only')
    raw,prepared=load('preflight.json');_,pf,_,job=setup();require(prepared['sources']==host.sources(), 'catalog_sources_changed')
    require(prepared['operation_id']==OPERATION and guards(client,pf,job)==prepared['live'],'catalog_preflight_drift')
    plan=pf['foundation']['plan'];body=plan['definition']['properties']['template']
    value=dict(operation_id=OPERATION,preflight_receipt=raw['protected_receipt'],method='POST',path=JOB_ID+'/start',
        api='2025-07-01',body=body,automatic_retry=False)
    intent=save(dict(value,reservation=reserve(value)),'start-intent.json')
    require(guards(client,pf,job)==prepared['live'] and host.sources()==prepared['sources'],'catalog_preflight_drift')
    observations=[];capture=None;failure=None
    with (FOLDER/'native-observations.jsonl').open('x',encoding='utf-8') as output:
        def record(kind,value):
            item=dict(kind=kind,received_at_utc=now(),raw=value)
            output.write(json.dumps(item,sort_keys=True)+'\n');output.flush()
            require(sum(len(canonical(x)) for x in observations)+len(canonical(item))<=1024*1024,'catalog_capture_budget')
            observations.append(item)
        try:
            ack,status=client.call('POST',JOB_ID+'/start','2025-07-01',body=body,plan=plan)
            record('start_ack',dict(http_status=status,raw_ack=ack))
            execution=name(ack.get('name'));require(ack.get('id')==JOB_ID+'/executions/'+execution,'catalog_ack_invalid')
            deadline=time.monotonic()+120
            for _ in range(60):
                require(time.monotonic()<deadline,'catalog_capture_deadline')
                before,_=client.call('GET',JOB_ID+'/executions/'+execution,'2025-07-01');record('execution_before',before)
                validate_execution(before,plan,execution)
                replicas,_=client.call('GET',JOB_ID+'/executions/'+execution+'/replicas','2023-11-02-preview');record('replicas',replicas)
                replica=validate_replica(replicas,execution)
                if replica is None: time.sleep(1);continue
                after,_=client.call('GET',JOB_ID+'/executions/'+execution,'2025-07-01');record('execution_after',after)
                validate_execution(after,plan,execution);require(before==after,'catalog_capture_changed')
                capture=dict(execution=execution,raw_execution=before,raw_replicas=replicas,replica=replica);break
            require(capture is not None,'catalog_capture_deadline')
        except Exception as error:
            failure=str(error) if re.fullmatch(r'catalog_[a-z_]+',str(error)) else 'catalog_capture_unqualified'
    result=save(dict(status='catalog_running_replica_captured' if capture else 'catalog_execution_unqualified_inspect_only',
        operation_id=OPERATION,intent_receipt=intent['protected_receipt'],observations=observations,capture=capture,
        failure_code=failure,attempt_consumed=True,automatic_retry=False),'start-ack.json')
    require(capture is not None,'catalog_execution_unqualified_inspect_only')
    return result


def collect(client,*,verify=False):
    pf_raw,pf,_,job=setup();raw,prepared=load('preflight.json');ack_raw,ack=load('start-ack.json')
    intent_raw,intent=load('start-intent.json')
    require(intent['preflight_receipt']==raw['protected_receipt'] and ack['intent_receipt']==intent_raw['protected_receipt']
        and ack['status']=='catalog_running_replica_captured' and ack['automatic_retry'] is False
        and prepared['sources']==host.sources(),'catalog_chain_invalid')
    captured=ack['capture'];execution=name(captured['execution']);plan=pf['foundation']['plan']
    validate_execution(captured['raw_execution'],plan,execution)
    require(validate_replica(captured['raw_replicas'],execution)==captured['replica'],'catalog_capture_changed')
    before,_=client.call('GET',JOB_ID+'/executions/'+execution,'2025-07-01')
    require(before['properties']['startTime']==captured['raw_execution']['properties']['startTime'],'catalog_start_time_changed')
    if before['properties']['status'] in ('Running','Processing'):
        validate_execution(before,plan,execution);return dict(status='catalog_running_read_only_wait',execution=execution)
    props=validate_execution(before,plan,execution,terminal=True)
    require(props['status']=='Succeeded','catalog_execution_failed_inspect_only')
    binding=logs.reader.codec.Binding(**plan['binding'])
    scope=logs.family.CatalogFamilyScope(logs.reader.CatalogLogScope(binding,captured['replica']['name'],launch.prior.CONTAINER,
        (utc(props['startTime'])-timedelta(minutes=1)).isoformat().replace('+00:00','Z'),
        (utc(props['endTime'])+timedelta(minutes=10)).isoformat().replace('+00:00','Z')),execution)
    payload=logs.reader.LogsTransport(AzureCliCredential());payload.session.trust_env=False
    evidence=logs.collect_evidence(logs.CompleteLogsTransport(AzureCliCredential()),payload,scope)
    record=evidence['captured']['record'];collector.validate_record(record)
    after,_=client.call('GET',JOB_ID+'/executions/'+execution,'2025-07-01')
    require(after==before and host.sources()==prepared['sources'],'catalog_terminal_drift')
    live=guards(client,pf,job,execution)
    parts=logs.wire_parts(evidence['captured']['lines'],binding,before['id'],ack_raw['protected_receipt'])
    if verify:
        accepted_raw,accepted=load('acceptance.json');remote=[]
        headers_raw,headers=load('headers.json')
        require(accepted['status']=='protected_catalog_observation_review_required'
            and accepted['operation_id']==OPERATION and accepted['execution']==execution
            and accepted['preparation_receipt']==pf_raw['protected_receipt']
            and accepted['preflight_receipt']==raw['protected_receipt']
            and accepted['start_ack_receipt']==ack_raw['protected_receipt']
            and accepted['headers_receipt']==headers_raw['protected_receipt']
            and headers['inventory_sha256']==evidence['inventory_sha256']
            and headers['inventory_before']==evidence['inventory_before']
            and headers['inventory_after']==evidence['inventory_after'],'catalog_acceptance_changed')
        for item in accepted['parts']:
            part_raw,part=load(item['filename']);require(part_raw['protected_receipt']==item['receipt'],'catalog_part_changed');remote.append(part)
        require(logs.reconstruct(remote,binding,before['id'],ack_raw['protected_receipt'])==record
            and accepted['catalog_sha256']==record['catalog_sha256'] and accepted['native_terminal']==before
            and accepted['inventory_sha256']==evidence['inventory_sha256'],'catalog_acceptance_changed')
        return dict(status='fresh_catalog_observation_verified',execution=execution,catalog_counts=record['catalog_counts'],
            protected_receipt=accepted_raw['protected_receipt'],fresh_restricted_login_verified=False,
            model_credential_isolation_verified=False)
    receipts=[]
    for index,part in enumerate(parts):
        filename='wire-part-'+str(index)+'.json';saved=save(part,filename)
        receipts.append(dict(filename=filename,receipt=saved['protected_receipt']))
    headers=save(dict(inventory_before=evidence['inventory_before'],inventory_after=evidence['inventory_after'],
        inventory_sha256=evidence['inventory_sha256']),'headers.json')
    require(logs.reconstruct(parts,binding,before['id'],ack_raw['protected_receipt'])==record,'catalog_reconstruction_invalid')
    return save(dict(status='protected_catalog_observation_review_required',operation_id=OPERATION,execution=execution,
        preparation_receipt=pf_raw['protected_receipt'],preflight_receipt=raw['protected_receipt'],start_ack_receipt=ack_raw['protected_receipt'],
        parts=receipts,headers_receipt=headers['protected_receipt'],inventory_sha256=evidence['inventory_sha256'],
        catalog_sha256=record['catalog_sha256'],catalog_counts=record['catalog_counts'],native_terminal=before,live=live,
        checked_at_utc=now(),tls_catalog_connection_verified=True,fresh_restricted_login_verified=False,
        model_credential_isolation_verified=False,provider_semantics_verified=False,sql_setup_authorized=False,
        production_changed=False,source_rows_written=0),'acceptance.json')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','start','collect','verify'));args=parser.parse_args()
    FOLDER.mkdir(parents=True,exist_ok=True)
    try:
        client=Transport()
        result=prepare(client) if args.stage=='prepare' else start(client) if args.stage=='start' else collect(client,verify=args.stage=='verify')
        print(json.dumps({k:result[k] for k in ('status','execution','catalog_counts','protected_receipt') if k in result},sort_keys=True))
    except Exception as error:
        code=str(error);code=code if re.fullmatch(r'(catalog|logs)_[a-z_]+',code) else 'catalog_unaccepted_inspect_only'
        print(json.dumps(dict(status='unaccepted',code=code,automatic_retry=False)),flush=True);raise SystemExit(1) from None


if __name__=='__main__': main()
