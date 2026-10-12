"""One-shot private archive qualification; no SQL/model/runtime activation.

Only the registered new identities/grants/jobs can be created. Protected intent
and reservation consume each effect, including uncertainty. Old jobs, grants,
data and receipt formats are not edited, restarted, reset or retrospectively
accepted. Separate SQL-commit/worker-receipt and website gates remain required.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import re
import time

import requests
from requests.adapters import HTTPAdapter
from azure.identity import AzureCliCredential

import qualify_owner_task_worker_archive_source as previous
import owner_task_worker_archive_launch as launch

w=launch.worker
ROOT=previous.prior.ROOT
NAME_PREFIX='serviceaud-step3-worker-archive-live-'
SOURCE_SHA='11ab8298e8719879b5558ccb63af2748c80350fe33a7b866dc4c0818c8c16db3'
READBACK_SHA='3af542b3db1f0678de20930231182eef336261d183ea1fb4b1457ad1769bddb6'
STAGES=('identity-stack','writer-job','writer-start','verifier-job','verifier-start')
MAX_BYTES=2*1024*1024
require=w.require
digest=launch.digest


def projection(after):
    require(type(after) is dict and len(after)==847 and launch.ADDITIONS.issubset(after)
        and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()))
    return {k:v for k,v in after.items() if k not in launch.ADDITIONS}


def extension(before,after):
    require(type(before) is dict and len(before)==844 and not set(before)&launch.ADDITIONS
        and projection(after)==before)


def read_context():
    """Owned source/native namespace; unchanged frozen functions and globals.

    The only added native exclusions are these TWO registered jobs; every live
    new job/identity/assignment is independently qualified by this controller.
    No wildcard or native acceptance of predecessor failures is introduced.
    """
    p=previous.prior
    def pred_extension(before,after):p.extension(before,previous.project_sources(projection(after)))
    def pred_chain(before,after):p.chain(before,previous.project_sources(projection(after)))
    def archive_extension(before,after):return previous.source_extension(before,projection(after))
    text=Path(p.__file__).read_text()
    body=ast.get_source_segment(text,next(x for x in ast.parse(text).body
        if isinstance(x,ast.FunctionDef) and x.name=='predecessor_context'))
    tree=ast.parse(body);old='catalog_job, future_job):';changed=0
    for node in ast.walk(tree):
        if isinstance(node,ast.Constant) and type(node.value) is str and node.value.endswith(old):
            node.value=node.value.removesuffix(old)+'catalog_job, future_job, '+', '.join(
                repr(j.rsplit('/',1)[1]) for j in w.JOBS.values())+'):';changed+=1
    require(changed==1);body=ast.unparse(tree)
    ns=dict(vars(p),chain=pred_chain)
    exec(compile(body,__file__,'exec'),ns)
    predecessor=ns['predecessor_context']
    text=Path(previous.__file__).read_text()
    body=ast.get_source_segment(text,next(x for x in ast.parse(text).body
        if isinstance(x,ast.FunctionDef) and x.name=='read_context'))
    old='context=clone(prior.predecessor_context,chain=predecessor_chain)'
    require(body.count(old)==1);body=body.replace(old,'context=archive_predecessor_context')
    ns=dict(vars(previous),predecessor_extension=pred_extension,predecessor_chain=pred_chain,
        source_extension=archive_extension,archive_predecessor_context=predecessor)
    exec(compile(body,__file__,'exec'),ns)
    return ns['read_context']()


def save(n,value,name):return n['p'].old.save(value,NAME_PREFIX+name+'.json')
def load(n,name):return n['p'].old.load(NAME_PREFIX+name+'.json')


def source_gate(n):
    p=n['p'];sr,source=p.old.load(previous.NAME)
    rr,rb=p.old.load('serviceaud-step3-worker-archive-source-readback.json')
    require(sr['protected_receipt']['sha256']==SOURCE_SHA and rr['protected_receipt']['sha256']==READBACK_SHA
        and rb['source_receipt']==sr['protected_receipt'] and rb['sources']==source['sources']
        and rb['all_841_predecessor_sources_preserved'] is True
        and rb['step_3_complete'] is False and rb['step_4_started'] is False)
    sources=p.old.prior.host.sources();extension(rb['sources'],sources)
    _,pf=n['load']('live-preflight');_,ob=n['load']('live-observation')
    native=previous.native(n,pf,ob)
    require(native==rb['native']==source['native_before']==source['native_after'])
    return rr,rb,sources,native


def api(path):
    if path in (launch.DEPLOYMENT,launch.VALIDATION,launch.VALIDATION+'/validate'):return '2022-09-01'
    if path in w.IDENTITIES.values():return '2023-01-31'
    if path==w.ARM_SCOPE:return '2023-05-01'
    if path==launch.SUB+'/providers/Microsoft.Authorization/roleAssignments' or path in {
        launch.assignment(m,r) for m in w.IDENTITIES for r in ('acr','protection',m)} or path in {
        launch.SUB+'/providers/Microsoft.Authorization/roleDefinitions/'+r[1] for r in launch.ROLES.values()}:
        return '2022-04-01'
    if path in w.JOBS.values() or path in {j+'/executions' for j in w.JOBS.values()}:
        return '2025-07-01'
    for j in w.JOBS.values():
        if path.startswith(j+'/executions/'):
            require(re.fullmatch(re.escape(j.rsplit('/',1)[1])+'-[a-z0-9]{7,12}',path.removeprefix(j+'/executions/')))
            return '2025-07-01'
    raise w.TrialError('worker_archive_route_invalid')


def effect(stage,plan=None):
    if stage=='validate':return 'POST',launch.VALIDATION+'/validate',launch.identity_stack()
    require(stage in STAGES)
    if stage=='identity-stack':return 'PUT',launch.DEPLOYMENT,launch.identity_stack()
    mode,action=stage.split('-');require(mode in w.JOBS and action in ('job','start'))
    require(type(plan) is dict and plan['definition_sha256']==digest(plan['definition'])
        and plan['context']['mode']==mode and plan['definition']['identity']==dict(
            type='UserAssigned',userAssignedIdentities={w.IDENTITIES[mode]:{}}))
    sources={path:(ROOT/path).read_bytes() for path,_ in launch.MODULES}
    hashes={k:hashlib.sha256(v).hexdigest() for k,v in sources.items()}
    require(plan==launch.build(mode,sources,hashes,w.Context(**plan['context']),plan['references']))
    return ('PUT',w.JOBS[mode],plan['definition']) if action=='job' else (
        'POST',w.JOBS[mode]+'/start',plan['definition']['properties']['template'])


class Transport:
    def __init__(self):
        self.credential=AzureCliCredential();self.session=requests.Session();self.session.trust_env=False
        self.session.mount('https://',HTTPAdapter(max_retries=0));self.token=None
    def call(self,method,path,*,body=None,stage=None,plan=None,principal=None,missing=False):
        if method=='GET':require(body is None);version=api(path)
        else:
            require((method,path,body)==effect(stage,plan))
            version='2025-07-01' if path.endswith('/start') else api(path)
        params={'api-version':version}
        if path==launch.SUB+'/providers/Microsoft.Authorization/roleAssignments':
            w.guid(principal);params['$filter']="principalId eq '"+principal+"'"
        else:require(principal is None)
        if self.token is None or self.token.expires_on<time.time()+120:
            self.token=self.credential.get_token('https://management.azure.com/.default')
        with self.session.request(method,'https://management.azure.com'+path,params=params,json=body,
            headers={'Authorization':'Bearer '+self.token.token},allow_redirects=False,stream=True,timeout=(5,30)) as response:
            if method=='GET' and missing and response.status_code==404:return None
            require(response.status_code in ((200,) if method=='GET' else (200,201,202)),
                'worker_archive_arm_unaccepted')
            raw=bytearray()
            for chunk in response.iter_content(65536):raw.extend(chunk);require(len(raw)<=MAX_BYTES)
            value=json.loads(raw);require(type(value) is dict);return value


def protection(client):
    v=client.call('GET',w.ARM_SCOPE);p=v.get('properties',{})
    require(v.get('id','').casefold()==w.ARM_SCOPE.casefold() and p.get('publicAccess')=='None'
        and p.get('hasLegalHold') is True and p.get('deleted') in (None,False)
        and p.get('legalHold',{}).get('protectedAppendWritesHistory',{}).get('allowProtectedAppendWritesAll') in (None,False))
    return v


def roles(client,mode,principal):
    value=client.call('GET',launch.SUB+'/providers/Microsoft.Authorization/roleAssignments',principal=principal)
    require(set(value)<={'value','nextLink'} and value.get('nextLink') in (None,'') and type(value.get('value')) is list)
    wanted={launch.assignment(mode,r).casefold():r for r in ('acr','protection',mode)}
    require({r.get('id','').casefold() for r in value['value']}==set(wanted) and len(value['value'])==3)
    for row in value['value']:
        role=wanted[row['id'].casefold()];p=row.get('properties',{});scope,definition=launch.ROLES[role]
        require(p.get('principalId')==principal and p.get('principalType')=='ServicePrincipal'
            and p.get('scope','').casefold()==scope.casefold()
            and p.get('roleDefinitionId','').casefold()==(launch.SUB+
                '/providers/Microsoft.Authorization/roleDefinitions/'+definition).casefold()
            and p.get('condition') in (None,'') and p.get('conditionVersion') in (None,''))
    return value


def identity_stack_checked(client):
    stack=client.call('GET',launch.DEPLOYMENT)
    require(stack.get('id','').casefold()==launch.DEPLOYMENT.casefold()
        and stack.get('properties',{}).get('provisioningState')=='Succeeded')
    identities={m:client.call('GET',resource) for m,resource in w.IDENTITIES.items()}
    values={m:launch.registered(v,m) for m,v in identities.items()}
    require(values['writer']['principalId']!=values['verifier']['principalId']
        and values['writer']['clientId']!=values['verifier']['clientId'])
    grants={m:roles(client,m,p['principalId']) for m,p in values.items()}
    return dict(deployment=stack,identities=identities,roles=grants)


def prepare():
    n=read_context();p=n['p'];client=Transport()
    require(not (p.old.FOLDER/(NAME_PREFIX+'preflight.json')).exists())
    rr,rb,sources,before=source_gate(n)
    absent=[launch.DEPLOYMENT,*w.IDENTITIES.values(),*w.JOBS.values(),
        *(launch.assignment(m,r) for m in w.IDENTITIES for r in ('acr','protection',m))]
    require(all(client.call('GET',path,missing=True) is None for path in absent))
    hold=protection(client)
    definitions={r:client.call('GET',launch.SUB+'/providers/Microsoft.Authorization/roleDefinitions/'+v[1])
        for r,v in launch.ROLES.items()}
    names=dict(acr='AcrPull',protection='Reader',writer='Storage Blob Data Contributor',verifier='Storage Blob Data Reader')
    require(all(v['properties']['roleName']==names[r] and v['properties']['type']=='BuiltInRole'
        for r,v in definitions.items()))
    validation=client.call('POST',launch.VALIDATION+'/validate',body=launch.identity_stack(),stage='validate')
    require(validation.get('error') is None and validation.get('properties',{}).get('provisioningState')=='Succeeded'
        and validation.get('id','').casefold()==launch.VALIDATION.casefold())
    checks=p.old.prior.parent.reviewed.run_local_checks()
    _,_,after_sources,after=source_gate(n)
    require(after_sources==sources and before==after and all(client.call('GET',path,missing=True) is None for path in absent)
        and protection(client)==hold)
    return save(n,dict(status='step_3_archive_live_preflight_verified',operation_id=w.OPERATION,
        checked_at_utc=p.old.now(),sources=sources,predecessor_sources=rb['sources'],
        source_readback_receipt=rr['protected_receipt'],native_predecessor=before,
        identity_stack_plan=launch.identity_stack(),role_definitions=definitions,protection=hold,
        azure_template_validation=validation,local_checks=checks,
        source_rows_read=0,source_rows_written=0,sql_connections=0,model_calls=0,production_changed=False,
        archive_identities_live_qualified=False,protected_worker_receipts_verified=False,
        runtime_process_cessation_verified=False,website_owner_authenticated=False,
        owner_runtime_active=False,six_specialist_runtimes_active=False,step_3_complete=False,step_4_started=False),'preflight')


def guards(n,client,pf):
    _,rb,sources,native=source_gate(n)
    require(pf['operation_id']==w.OPERATION and pf['sources']==sources
        and pf['predecessor_sources']==rb['sources'] and pf['native_predecessor']==native
        and pf['identity_stack_plan']==launch.identity_stack() and protection(client)==pf['protection'])
    for role,value in pf['role_definitions'].items():
        require(client.call('GET',launch.SUB+'/providers/Microsoft.Authorization/roleDefinitions/'+launch.ROLES[role][1])==value)


def reserve(n,stage,value):
    generic=previous.prior.metadata.prior.base.generic
    fn=previous.clone(generic.reserve,p=n['p'],worker=w,
        PREFIX='probes/owner-worker-archive-20261005/')
    return fn(stage,value)


def planned_job(n,pf,client,mode):
    ar,accepted=load(n,'identity-stack-accepted');rr,_=load(n,'preflight')
    require(accepted['preflight_receipt']==rr['protected_receipt'])
    stack=identity_stack_checked(client);require(stack==accepted['native'])
    sources={path:(ROOT/path).read_bytes() for path,_ in launch.MODULES}
    hashes={k:hashlib.sha256(v).hexdigest() for k,v in sources.items()}
    require(all(pf['sources'][k]==v for k,v in hashes.items()))
    ctx=launch.context(mode,stack['identities'],digest(pf['sources']))
    references=None
    if mode=='verifier':
        wr,record=load(n,'writer-observation')
        require(record['status']=='step_3_archive_writer_live_qualified'
            and record['preflight_receipt']==rr['protected_receipt'])
        references={k:record['worker_result'][k] for k in ('normal','lost_ack_inspection')}
    return launch.build(mode,sources,hashes,ctx,references),stack


def dispatch(stage):
    require(stage in STAGES);n=read_context();p=n['p'];client=Transport()
    require(not any((p.old.FOLDER/(NAME_PREFIX+stage+'-'+suffix+'.json')).exists()
        for suffix in ('intent','ack','accepted')),'worker_archive_effect_consumed')
    rr,pf=load(n,'preflight');guards(n,client,pf);plan=None
    if stage=='identity-stack':
        require(all(client.call('GET',path,missing=True) is None for path in
            [launch.DEPLOYMENT,*w.IDENTITIES.values(),*(launch.assignment(m,r) for m in w.IDENTITIES for r in ('acr','protection',m))]))
    else:
        mode,action=stage.split('-');plan,stack=planned_job(n,pf,client,mode)
        if action=='job':require(client.call('GET',w.JOBS[mode],missing=True) is None)
        else:
            ar,accepted=load(n,mode+'-job-accepted');require(accepted['preflight_receipt']==rr['protected_receipt'])
            require(accepted['plan']==plan);launch.checked_job(client.call('GET',w.JOBS[mode]),plan,mode,
                launch.registered(stack['identities'][mode],mode))
            inv=client.call('GET',w.JOBS[mode]+'/executions')
            require(inv.get('value')==[] and inv.get('nextLink') in (None,''))
    method,path,body=effect(stage,plan)
    value=dict(operation_id=w.OPERATION,stage=stage,preflight_receipt=rr['protected_receipt'],
        method=method,path=path,body=body,plan=plan,automatic_retry=False)
    intent=save(n,dict(value,reservation=reserve(n,stage,value)),stage+'-intent')
    guards(n,client,pf)
    if stage=='identity-stack':
        require(all(client.call('GET',t,missing=True) is None for t in
            [launch.DEPLOYMENT,*w.IDENTITIES.values(),*(launch.assignment(m,r) for m in w.IDENTITIES for r in ('acr','protection',m))]))
    elif stage.endswith('-job'):require(client.call('GET',path,missing=True) is None)
    else:
        mode=stage.split('-')[0];fresh,stack=planned_job(n,pf,client,mode);require(fresh==plan)
        launch.checked_job(client.call('GET',w.JOBS[mode]),plan,mode,launch.registered(stack['identities'][mode],mode))
        require(client.call('GET',w.JOBS[mode]+'/executions').get('value')==[])
    ack=client.call(method,path,body=body,stage=stage,plan=plan)
    return save(n,dict(status='step_3_archive_effect_acknowledged',checked_at_utc=p.old.now(),
        stage=stage,intent_receipt=intent['protected_receipt'],raw_ack=ack,automatic_retry=False,
        step_3_complete=False,step_4_started=False),stage+'-ack')


def accept(stage):
    require(stage in ('identity-stack','writer-job','verifier-job'))
    n=read_context();client=Transport();rr,pf=load(n,'preflight');ir,intent=load(n,stage+'-intent');ar,ack=load(n,stage+'-ack')
    require(intent['preflight_receipt']==rr['protected_receipt'] and ack['intent_receipt']==ir['protected_receipt'])
    guards(n,client,pf);plan=None
    if stage=='identity-stack':native=identity_stack_checked(client)
    else:
        mode=stage.split('-')[0];plan,stack=planned_job(n,pf,client,mode)
        require(plan==intent['plan']);job=client.call('GET',w.JOBS[mode])
        launch.checked_job(job,plan,mode,launch.registered(stack['identities'][mode],mode))
        inv=client.call('GET',w.JOBS[mode]+'/executions');require(inv.get('value')==[] and inv.get('nextLink') in (None,''))
        native=dict(job=job,inventory=inv)
    return save(n,dict(status='step_3_archive_native_registration_verified',preflight_receipt=rr['protected_receipt'],
        ack_receipt=ar['protected_receipt'],native=native,plan=plan,step_3_complete=False,step_4_started=False),stage+'-accepted')


def execution(n,client,mode):
    ar,ack=load(n,mode+'-start-ack');ir,intent=load(n,mode+'-start-intent')
    rr,_=load(n,'preflight')
    require(ack['intent_receipt']==ir['protected_receipt'] and intent['preflight_receipt']==rr['protected_receipt'])
    name=ack['raw_ack'].get('name');require(type(name) is str)
    path=w.JOBS[mode]+'/executions/'+name;api(path)
    value=client.call('GET',path)
    require(value.get('id')==ack['raw_ack'].get('id')==path and value.get('name')==name
        and value.get('type')=='Microsoft.App/jobs/executions')
    return value,ar,ir


def qualified_result(result,ctx,plan):
    require(type(result) is dict and result.get('status')=='synthetic_archive_boundary_verified'
        and result.get('operation_id')==w.OPERATION and result.get('mode')==ctx.mode
        and result.get('job_resource_id')==w.JOBS[ctx.mode]
        and result.get('identity_resource_id')==w.IDENTITIES[ctx.mode]
        and result.get('principal_id')==ctx.principal_id
        and result.get('source_manifest_sha256')==ctx.source_manifest_sha256
        and result.get('image_sha256')==ctx.image_sha256)
    for key in ('database_committed','protected_worker_receipts_verified','production_evidence_registry_enrolled',
            'runtime_process_cessation_verified','website_owner_authenticated','owner_runtime_active',
            'six_specialist_runtimes_active','step_3_complete','step_4_started','production_changed','automatic_retry'):
        require(result.get(key) is False)
    for key in ('source_rows_read','source_rows_written','sql_connections','model_calls'):
        require(type(result.get(key)) is int and result[key]==0)
    require(result.get('lost_ack_receipt_accepted') is False and result.get('lost_ack_newly_created') is False)
    if ctx.mode=='writer':
        for kind,key in (('normal','normal'),('lost_ack','lost_ack_inspection')):
            w.reference(result.get(key),w.payload(ctx,kind))
        require(result.get('lost_ack_automatic_retry') is False)
        for key in ('duplicate_denial','lost_ack_duplicate_denial'):
            require(result.get(key) in (dict(status_code=409,error_code='BlobAlreadyExists'),
                dict(status_code=409,error_code='BlobImmutableDueToLegalHold')))
        require(result.get('overwrite_denial')==dict(status_code=409,error_code='BlobImmutableDueToLegalHold'))
    else:
        require(result.get('exact_versions_independently_read') is True
            and result.get('reader_creation_denial')==dict(status_code=403,error_code='AuthorizationPermissionMismatch'))


def collect(mode):
    require(mode in w.JOBS);n=read_context();client=Transport();rr,pf=load(n,'preflight')
    require(not (n['p'].old.FOLDER/(NAME_PREFIX+mode+'-observation.json')).exists())
    guards(n,client,pf);plan,stack=planned_job(n,pf,client,mode)
    ar,accepted=load(n,mode+'-job-accepted');require(accepted['preflight_receipt']==rr['protected_receipt'] and accepted['plan']==plan)
    launch.checked_job(client.call('GET',w.JOBS[mode]),plan,mode,launch.registered(stack['identities'][mode],mode))
    value,start,_=execution(n,client,mode)
    require(value['properties']['status'] in ('Succeeded','Failed'))
    launch.template(value['properties']['template'],plan['definition']['properties']['template'])
    inventory=client.call('GET',w.JOBS[mode]+'/executions')
    require(inventory.get('nextLink') in (None,'') and len(inventory.get('value',[]))==1
        and inventory['value'][0]['id']==value['id'])
    started=datetime.fromisoformat(value['properties']['startTime'].replace('Z','+00:00'));name=value['name']
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where TimeGenerated between (datetime("+(started-timedelta(seconds=45)).isoformat()+") .. datetime("+(started+timedelta(minutes=10)).isoformat()+")) | project Group=ContainerGroupName_s,Container=ContainerName_s,LogTime=TimeGenerated,Log=Log_s | order by LogTime asc"
    token=client.credential.get_token('https://api.loganalytics.io/.default')
    with client.session.post('https://api.loganalytics.io/v1/workspaces/5264a124-10cd-45f6-93a5-6d65c37e9bfb/query',
        json={'query':query},headers={'Authorization':'Bearer '+token.token},allow_redirects=False,timeout=(5,30)) as response:
        require(response.status_code==200 and len(response.content)<=MAX_BYTES);family=response.json()
    require(type(family) is dict and set(family)=={'tables'} and len(family['tables'])==1)
    table=family['tables'][0]
    require([c['name'] for c in table['columns']]==['Group','Container','LogTime','Log']
        and type(table['rows']) is list and 0<len(table['rows'])<=1000
        and all(type(r) is list and len(r)==4 and all(type(x) is str for x in r)
            and r[1]=='archive-'+mode and re.fullmatch(re.escape(name)+'-[a-z0-9-]{1,80}',r[0]) for r in table['rows']))
    records=[json.loads(r[3][len(w.PREFIX):]) for r in table['rows'] if r[3].startswith(w.PREFIX)]
    require(len(records)==1 and len(w.canonical(records[0]))<=16384)
    result=records[0];ctx=w.Context(**plan['context'])
    success=value['properties']['status']=='Succeeded' and result.get('status')=='synthetic_archive_boundary_verified'
    if success:qualified_result(result,ctx,plan)
    return save(n,dict(status=('step_3_archive_'+mode+'_live_qualified' if success
        else 'step_3_archive_failure_retained'),checked_at_utc=n['p'].old.now(),preflight_receipt=rr['protected_receipt'],
        start_receipt=start['protected_receipt'],job_receipt=ar['protected_receipt'],native_execution=value,
        native_inventory=inventory,native_identity=stack['identities'][mode],native_roles=stack['roles'][mode],
        plan=plan,worker_result=result,full_bounded_observed_family=family,
        archive_identities_live_qualified=success and mode=='verifier',protected_worker_receipts_verified=False,
        production_evidence_registry_enrolled=False,runtime_process_cessation_verified=False,
        website_owner_authenticated=False,owner_runtime_active=False,six_specialist_runtimes_active=False,
        production_changed=False,source_rows_read=0,source_rows_written=0,sql_connections=0,model_calls=0,
        step_3_complete=False,step_4_started=False),mode+'-observation')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('prepare',*STAGES,
        'accept-identities','accept-writer-job','accept-verifier-job','observe-writer','observe-verifier',
        'collect-writer','collect-verifier'))
    command=parser.parse_args().command
    try:
        if command=='prepare':out=prepare()
        elif command.startswith('accept-'):out=accept('identity-stack' if command=='accept-identities' else command.removeprefix('accept-'))
        elif command.startswith('collect-'):out=collect(command.removeprefix('collect-'))
        elif command.startswith('observe-'):
            n=read_context();value,_,_=execution(n,Transport(),command.removeprefix('observe-'))
            out=dict(status=value['properties']['status'])
        else:out=dispatch(command)
        print(json.dumps({k:out[k] for k in ('status','protected_receipt') if k in out}),flush=True)
    except Exception as error:
        print(json.dumps(dict(status='step_3_archive_live_unaccepted',code=type(error).__name__,automatic_retry=False)),flush=True)
        raise SystemExit(1) from None
