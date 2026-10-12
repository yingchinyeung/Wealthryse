"""Separate TLS/provider observation after protected restricted-SQL subgate.

Only the disposable LOGIN credential may change; synthetic rows stay immutable.
No setup, grants, production/source data, old-job restart or cleanup.
"""
import argparse
import hashlib
import json
import re
import time
import requests
from requests.adapters import HTTPAdapter
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from azure.core import MatchConditions
import deploy_owner_task_isolated_sql_root as parent
import owner_task_isolated_tls_observation as worker
import deploy_owner_task_isolated_sql_safe_encoding as safe_control
import owner_task_isolated_scope_diagnostic as diagnostic_worker
import deploy_owner_task_scope_diagnostic as diagnostic_control
import deploy_owner_task_isolated_sql_resume as resume_control
import owner_task_isolated_sql_root_launch as bundle

host,target,compatibility=parent.host,parent.target,parent.compatibility
ROOT=parent.ROOT
FOLDER=ROOT/'.work/owner-task-isolated-tls-observation-20261005'
KIND='isolated-tls-observation-'
PREFIX='probes/owner-codex-isolated-tls-observation-20261005/'
JOB,MI=worker.JOB_ID,parent.MI
canonical,digest,require=parent.canonical,parent.digest,parent.require
ADDITIONS=frozenset((worker.PATH,'scripts/deploy_owner_task_isolated_tls_observation.py','tests/test_owner_task_isolated_tls_observation.py'))


def sources_valid(before,after):
    require(type(before)is dict and len(before)==783 and type(after)is dict
        and set(after)==set(before)|ADDITIONS and not set(before)&ADDITIONS
        and all(after[k]==v for k,v in before.items())
        and all(type(v)is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()),
        'scope_source_manifest_invalid')


def save(value,name):
    require(re.fullmatch('[a-z][a-z0-9-]*\\.json',name) and len(canonical(value))<=parent.MAX_BYTES,
        'scope_evidence_invalid')
    FOLDER.mkdir(parents=True,exist_ok=True)
    with (FOLDER/name).open('x',encoding='utf-8') as out:
        host.deploy.archive_protection()
        raw=dict(value,protected_receipt=host.deploy.retain(value,'owner-task-pg-'+KIND+name[:-5]))
        out.write(json.dumps(raw,sort_keys=True,indent=2))
    host.deploy.protected(raw,KIND+name[:-5])
    return raw


def load(name):
    require(re.fullmatch('[a-z][a-z0-9-]*\\.json',name),'scope_evidence_invalid')
    raw=json.loads((FOLDER/name).read_text(encoding='utf-8'))
    return raw,host.deploy.protected(raw,KIND+name[:-5])


def baseline():
    value=parent.baseline(); value['other_jobs'].pop(worker.JOB,None); value['other_jobs'].pop(diagnostic_worker.JOB,None); value['other_jobs'].pop(resume_control.worker.JOB,None); value['other_jobs'].pop(safe_control.worker.JOB,None); return value


def effect(stage,plan):
    require(stage in ('job','start') and plan['definition_sha256']==digest(plan['definition']),
        'scope_effect_invalid')
    return ('PUT',JOB,plan['definition']) if stage=='job' else (
        'POST',JOB+'/start',plan['definition']['properties']['template'])


def execution_name(value):
    require(type(value)is str and re.fullmatch(re.escape(worker.JOB)+'-[a-z0-9]{7,12}',value),
        'scope_execution_invalid')
    return value


class Transport:
    def __init__(self):
        self.credential=AzureCliCredential(); self.session=requests.Session(); self.session.trust_env=False
        self.session.mount('https://',HTTPAdapter(max_retries=0)); self.token=None
    def call(self,method,path,*,body=None,plan=None,stage=None,missing=False):
        if method=='GET':
            allowed={JOB,JOB+'/executions'}
            if path.startswith(JOB+'/executions/'):
                execution_name(path.removeprefix(JOB+'/executions/')); allowed.add(path)
            require(path in allowed and body is None,'scope_route_invalid')
        else:
            require(plan is not None and (method,path,canonical(body))==
                (effect(stage,plan)[0],effect(stage,plan)[1],canonical(effect(stage,plan)[2])),
                'scope_route_invalid')
        if self.token is None or self.token.expires_on<time.time()+120:
            self.token=self.credential.get_token('https://management.azure.com/.default')
        r=self.session.request(method,'https://management.azure.com'+path+'?api-version=2025-07-01',
            json=body,headers={'Authorization':'Bearer '+self.token.token},allow_redirects=False,
            stream=True,timeout=(5,20))
        try:
            if missing and method=='GET' and r.status_code==404:return None
            require(r.status_code in ((200,) if method=='GET' else (200,201,202)),'scope_http_unaccepted')
            data=b''
            for part in r.iter_content(16384):
                data+=part; require(len(data)<=parent.MAX_BYTES,'scope_http_oversized')
            value=json.loads(data); require(type(value)is dict,'scope_http_invalid'); return value
        finally:r.close()


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
    native_template = compatibility.copy(props.get('template'))
    require(type(native_template) is dict, 'isolated_sql_job_invalid')
    for field in ('initContainers', 'volumes'):
        if field in native_template:
            require(native_template[field] is None, 'isolated_sql_job_invalid')
            del native_template[field]
    template = compatibility.template_shadow(native_template)
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



def live(pf):
    host.checked_owner()
    require(host.sources()==pf['sources'] and baseline()==pf['baseline']
        and host.observe(host.Transport())==pf['host'],'scope_live_drift')
    p=parent.Transport(); identity=parent.live_identity(p.call('GET',MI,'2023-01-31'))
    require(identity==pf['registered_identity'],'resume_registered_identity_drift')
    parent.roles(p,identity['principalId'],['acr','secret'])
    parent.validate_job(p.call('GET',parent.JOB,'2025-07-01'),pf['parent_plan'],identity)
    n='ibkrv2stg-ownsql-root-20261005-vfq8yud'
    x=p.call('GET',parent.JOB+'/executions/'+n,'2025-07-01')
    inv=p.call('GET',parent.JOB+'/executions','2025-07-01')
    require(x['properties']['status']=='Failed' and inv.get('nextLink') in (None,'')
        and len(inv['value'])==1 and inv['value'][0]['name']==n,'scope_failed_attempt_drift')
    d=diagnostic_control.Transport()
    diagnostic_control.validate_job(d.call('GET',diagnostic_control.JOB),pf['diagnostic_plan'],identity)
    dn='ibkrv2stg-ownscope-20261005-p2x478n'
    dx=d.call('GET',diagnostic_control.JOB+'/executions/'+dn)
    di=d.call('GET',diagnostic_control.JOB+'/executions')
    require(dx['properties']['status']=='Succeeded' and di.get('nextLink') in (None,'')
        and len(di['value'])==1 and di['value'][0]['name']==dn,'resume_diagnostic_drift')
    r=resume_control.Transport()
    resume_control.validate_job(r.call('GET',resume_control.JOB),pf['resume_plan'],identity)
    rn='ibkrv2stg-owntrial-20261005-ti8rq5q'
    rx=r.call('GET',resume_control.JOB+'/executions/'+rn)
    ri=r.call('GET',resume_control.JOB+'/executions')
    require(rx['properties']['status']=='Failed' and ri.get('nextLink') in (None,'')
        and len(ri['value'])==1 and ri['value'][0]['name']==rn,'safe_encoding_prior_execution_drift')
    sc=safe_control.Transport()
    safe_control.validate_job(sc.call('GET',safe_control.JOB),pf['safe_plan'],identity)
    sn='ibkrv2stg-ownsafe-20261005-mk8nf8t'
    sx=sc.call('GET',safe_control.JOB+'/executions/'+sn)
    si=sc.call('GET',safe_control.JOB+'/executions')
    require(sx['properties']['status']=='Failed' and si.get('nextLink') in (None,'')
        and len(si['value'])==1 and si['value'][0]['name']==sn,'tls_prior_execution_drift')
    return identity


def prepare(client):
    require(not (FOLDER/'preflight.json').exists(),'resume_preflight_consumed')
    rr,rp=safe_control.load('preflight.json')
    sr,subgate=safe_control.load('restricted-sql-verified.json')
    require(rr['protected_receipt']['sha256']=='5168ff99b3c4fc712346e78c8e2b1169d3e10281558b7e4ad98fa003e58c9a80'
        and sr['protected_receipt']['sha256']=='0066eb52dedc8a27333765089b1e2ecb1bb5a13436d657ba1588788a64285b22'
        and subgate['preflight_receipt']==rr['protected_receipt']
        and subgate['status']=='restricted_sql_subgate_verified_remaining_acceptance_pending'
        and subgate['fresh_restricted_sql_verified'] is True
        and subgate['positive_tls_accepted'] is False
        and subgate['restricted_trial']['status']=='passed'
        and subgate['restricted_trial']['fresh_database_authority_revocation_verified'] is True,
        'tls_observation_origin_invalid')
    before=host.sources();sources_valid(rp['sources'],before)
    view=dict(sources=before,baseline=rp['baseline'],host=rp['host'],
        parent_plan=rp['parent_plan'],diagnostic_plan=rp['diagnostic_plan'],
        resume_plan=rp['resume_plan'],safe_plan=rp['plan'],registered_identity=rp['registered_identity'])
    live(view);require(client.call('GET',JOB,missing=True)is None,'resume_job_exists')
    paths=(*bundle.SOURCE_PATHS,worker.PATH)
    source={k:(ROOT/k).read_bytes() for k in paths}
    plan=worker.build(source,{k:hashlib.sha256(v).hexdigest() for k,v in source.items()},rp['plan']['expected_runtime'])
    checks=parent.reviewed.run_local_checks()
    live(view);require(client.call('GET',JOB,missing=True)is None,'resume_job_exists')
    return save(dict(view,status='prepared_registered_tls_observation',operation_id=worker.OPERATION,
        plan=plan,local_checks=checks,safe_preflight_receipt=rr['protected_receipt'],
        restricted_sql_subgate_receipt=sr['protected_receipt'],
        encoded_arguments_have_no_dollar_signs=True,
        existing_fixture_reused=True,restricted_crud_repeated=False,setup_repeated=False,grants_changed=False,
        credential_rotation_scope=target.LOGIN,checked_at_utc=parent.now(),step_2_complete=False),'preflight.json')


def reserve(stage,value):
    host.deploy.archive_protection()
    service=BlobServiceClient('https://'+host.deploy.ACCOUNT+'.blob.core.windows.net',
        credential=AzureCliCredential(),retry_total=0,connection_timeout=5,read_timeout=10)
    data=canonical(value); name=PREFIX+stage+'-reservation.json'
    b=service.get_blob_client(host.deploy.CONTAINER,name)
    ack=b.upload_blob(data,length=len(data),overwrite=False,match_condition=MatchConditions.IfMissing,
        max_concurrency=1,timeout=10,retry_total=0)
    exact=service.get_blob_client(host.deploy.CONTAINER,name,version_id=ack['version_id']); props=exact.get_blob_properties()
    require(props.size==len(data) and props.etag==ack['etag'] and props.version_id==ack['version_id']
        and exact.download_blob(etag=props.etag,match_condition=MatchConditions.IfNotModified).readall()==data,
        'scope_reservation_invalid')
    host.deploy.archive_protection()
    return dict(blob=name,sha256=hashlib.sha256(data).hexdigest(),version_id=ack['version_id'],etag=ack['etag'])


def dispatch(client,stage):
    require(stage in ('job','start') and not any((FOLDER/(stage+'-'+x+'.json')).exists()
        for x in ('intent','ack','accepted')),'scope_stage_consumed_inspect_only')
    raw,pf=load('preflight.json'); identity=live(pf)
    require(pf['operation_id']==worker.OPERATION and pf['status']=='prepared_registered_tls_observation','scope_preflight_invalid')
    method,path,body=effect(stage,pf['plan'])
    def guard():
        if stage=='job':require(client.call('GET',JOB,missing=True)is None,'scope_job_exists')
        else:
            validate_job(client.call('GET',JOB),pf['plan'],identity)
            inv=client.call('GET',JOB+'/executions')
            require(inv.get('value')==[] and inv.get('nextLink') in (None,''),'scope_execution_exists')
    if stage=='start':
        ar,accepted=load('job-accepted.json')
        require(accepted['preflight_receipt']==raw['protected_receipt'] and accepted['status']=='tls_observation_job_verified',
            'scope_job_not_verified')
    guard()
    value=dict(operation_id=worker.OPERATION,stage=stage,preflight_receipt=raw['protected_receipt'],
        method=method,path=path,body=body,automatic_retry=False)
    intent=save(dict(value,reservation=reserve(stage,value)),stage+'-intent.json')
    live(pf);guard()
    ack=client.call(method,path,body=body,stage=stage,plan=pf['plan'])
    return save(dict(status='resume_effect_acknowledged_inspect_required',operation_id=worker.OPERATION,
        stage=stage,intent_receipt=intent['protected_receipt'],raw_ack=ack,automatic_retry=False,
        attempt_consumed=True,checked_at_utc=parent.now(),step_2_complete=False),stage+'-ack.json')


def inspect(client,stage):
    raw,pf=load('preflight.json'); ar,ack=load(stage+'-ack.json'); ir,intent=load(stage+'-intent.json')
    require(ack['intent_receipt']==ir['protected_receipt'] and intent['preflight_receipt']==raw['protected_receipt']
        and intent['operation_id']==worker.OPERATION and intent['stage']==stage,'scope_chain_invalid')
    identity=live(pf)
    if stage=='job':
        checked=validate_job(client.call('GET',JOB),pf['plan'],identity)
        return save(dict(status='tls_observation_job_verified',stage=stage,operation_id=worker.OPERATION,
            preflight_receipt=raw['protected_receipt'],ack_receipt=ar['protected_receipt'],
            native_verification=checked,step_2_complete=False),'job-accepted.json')
    n=execution_name(ack['raw_ack']['name'])
    require(ack['raw_ack']['id']==JOB+'/executions/'+n,'scope_execution_invalid')
    native=client.call('GET',JOB+'/executions/'+n)
    return dict(status='resume_execution_observed',execution=n,native_status=native['properties']['status'],step_2_complete=False)


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('prepare','dispatch','inspect'))
    p.add_argument('--stage',choices=('job','start'));a=p.parse_args()
    try:
        client=Transport()
        result=prepare(client) if a.command=='prepare' else dispatch(client,a.stage) if a.command=='dispatch' else inspect(client,a.stage)
        print(json.dumps({k:result[k] for k in ('status','stage','execution','native_status','protected_receipt') if k in result},sort_keys=True))
    except Exception:
        print(json.dumps(dict(status='unaccepted_inspect_only',automatic_retry=False)),flush=True)
        raise SystemExit(1) from None


if __name__=='__main__':main()



