"""Separate once-only step-2 model identity/job. No SQL credentials or old restart.

Source/protected intent/reservation precede every new effect. Uncertain requests
consume their stages and must be inspected, never automatically redispatched.
Only two direct grants on this new identity: registry pull and the approved AI
account's OpenAI User role. Actual credential denial remains a runtime gate.
"""
import argparse
import base64
from datetime import datetime,timezone,timedelta
import hashlib
import json
import lzma
import re

import requests
from requests.adapters import HTTPAdapter
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from azure.core import MatchConditions
import deploy_owner_task_provider_object_runtime_review as prior
import deploy_codex_runtime_probe as model_inventory
import owner_task_model_isolation_trial as worker

ROOT=prior.ROOT
FOLDER=ROOT/'.work/owner-task-model-isolation-trial-20261005'
KIND='model-isolation-trial-'
PREFIX='probes/owner-codex-model-isolation-trial-20261005/'
MI,JOB=worker.MI,worker.JOB_ID
AI=worker.BASE+'Microsoft.CognitiveServices/accounts/ibkrv2stg-ai-auhecwc4itaoo'
SUB='/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb'
REGISTRY=worker.BASE+'Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/codex-synthetic-runtime@sha256:6511542c49001ba34c2639d4a90d6d3ea28ab1c50cee5be05dc09bd414d34748'
STAGES=('identity','acr','ai','job','start')
FROZEN_JOBS=('ibkrv2stg-ownsql-root-20261005','ibkrv2stg-ownscope-20261005','ibkrv2stg-owntrial-20261005',
 'ibkrv2stg-ownsafe-20261005','ibkrv2stg-owntls-20261005','ibkrv2stg-owntlsv-20261005','ibkrv2stg-ownobj-20261005','ibkrv2stg-ownobjv-20261005')
ROLES={
 'acr':(REGISTRY,'7f951dda-4ed3-4680-a7ca-43fe172d538d','1bb464d3-0c3d-446b-b4db-4429fbb1789e'),
 'ai':(AI,'5e0bd9bd-7b93-4f28-af87-19fc36ad61bd','4e3476d9-5c13-4ef8-bc2e-c7422932a3cb'),
}
ADDITIONS=frozenset((worker.PATH,'scripts/deploy_owner_task_model_isolation_trial.py','tests/test_owner_task_model_isolation_trial.py'))
canonical,require=worker.canonical,worker.require
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def image_receipt():
 path=ROOT/'.work/codex-exec-runtime-20261003/7ba4f97fa7ef/build-acceptance.json'
 record=model_inventory.accepted(path);receipt=record['protected_receipt']
 require(receipt['sha256']=='231c42658a29543d0f80f63ee002b3bc729f551650c3ec0600c53ef9bea5de59'
  and receipt['blob']=='acceptance/gateway-read-codex-exec-build-completed-'+receipt['sha256']+'.json'
  and record['image']==IMAGE and record['offline_cli_gate_passed']is True,'model_isolation_image_receipt_invalid')
 prior.host.deploy.archive_protection()
 service=BlobServiceClient('https://'+prior.host.deploy.ACCOUNT+'.blob.core.windows.net',credential=AzureCliCredential())
 blob=service.get_blob_client(prior.host.deploy.CONTAINER,receipt['blob'],version_id=receipt['version_id'])
 raw=json.dumps({k:v for k,v in record.items() if k!='protected_receipt'},sort_keys=True,separators=(',',':')).encode()
 props=blob.get_blob_properties()
 require(props.size==len(raw) and props.version_id==receipt['version_id']
  and blob.download_blob(offset=0,length=len(raw)+1,etag=props.etag,match_condition=MatchConditions.IfNotModified).readall()==raw,'model_isolation_image_receipt_invalid')
 prior.host.deploy.archive_protection();return receipt


def sources_valid(before,after):
 require(type(before)is dict and len(before)==795 and type(after)is dict and set(after)==set(before)|ADDITIONS
  and not set(before)&ADDITIONS and all(after[k]==v for k,v in before.items())
  and all(type(v)is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()),'model_isolation_source_drift')


def save(value,name):
 require(re.fullmatch('[a-z][a-z0-9-]*\\.json',name) and len(canonical(value))<=2*1024*1024,'model_isolation_evidence_invalid')
 FOLDER.mkdir(parents=True,exist_ok=True)
 with (FOLDER/name).open('x',encoding='utf-8') as stream:
  prior.host.deploy.archive_protection()
  raw=dict(value,protected_receipt=prior.host.deploy.retain(value,'owner-task-pg-'+KIND+name[:-5]))
  stream.write(json.dumps(raw,sort_keys=True,indent=2))
 prior.host.deploy.protected(raw,KIND+name[:-5]);return raw


def load(name):
 require(re.fullmatch('[a-z][a-z0-9-]*\\.json',name),'model_isolation_evidence_invalid')
 raw=json.loads((FOLDER/name).read_text());return raw,prior.host.deploy.protected(raw,KIND+name[:-5])


def assignment(stage):
 require(stage in ROLES,'model_isolation_stage_invalid')
 scope,_,name=ROLES[stage];return scope+'/providers/Microsoft.Authorization/roleAssignments/'+name


def identity_body():
 return dict(location='eastasia',tags=dict(purpose='owner-model-isolation-trial',operation=worker.OPERATION))


def identity(value):
 require(type(value)is dict and value.get('id','').casefold()==MI.casefold() and value.get('name')==worker.IDENTITY_NAME
  and value.get('type')=='Microsoft.ManagedIdentity/userAssignedIdentities' and value.get('location')=='eastasia'
  and value.get('tags')==identity_body()['tags'],'model_isolation_identity_invalid')
 p=value.get('properties',{})
 require(type(p)is dict and set(p)=={'tenantId','principalId','clientId'} and p['tenantId']==worker.TENANT,'model_isolation_identity_invalid')
 worker.uuid_value(p['principalId']);worker.uuid_value(p['clientId']);return p


def execution_name(name):
 require(type(name)is str and re.fullmatch(re.escape(worker.JOB)+'-[a-z0-9]{7,12}',name),'model_isolation_execution_invalid');return name


class Transport:
 def __init__(self):
  self.credential=AzureCliCredential();self.session=requests.Session();self.session.trust_env=False
  self.session.mount('https://',HTTPAdapter(max_retries=0));self.token=None
 def call(self,method,path,api,*,body=None,stage=None,plan=None,principal=None,missing=False):
  get={MI,JOB,JOB+'/executions',SUB+'/providers/Microsoft.Authorization/roleAssignments',*(assignment(s) for s in ROLES),*(worker.BASE+'Microsoft.App/jobs/'+name for name in FROZEN_JOBS)}
  if path.startswith(JOB+'/executions/'):
   tail=path.removeprefix(JOB+'/executions/').split('/')
   execution_name(tail[0]);require(len(tail)==1 or tail[1:]==['replicas'],'model_isolation_route_invalid');get.add(path)
  if method=='GET':
   expected_api='2023-01-31' if path==MI else '2022-04-01' if path==SUB+'/providers/Microsoft.Authorization/roleAssignments' or path in {assignment(s) for s in ROLES} else '2023-11-02-preview' if path.endswith('/replicas') else '2025-07-01'
   require(path in get and body is None and api==expected_api,'model_isolation_route_invalid')
  else:
   expected=effect(stage,plan,principal)
   require((method,path,api,body)==expected,'model_isolation_route_invalid')
  params={'api-version':api}
  if path==SUB+'/providers/Microsoft.Authorization/roleAssignments':
   worker.uuid_value(principal);params['$filter']="principalId eq '"+principal+"'"
  else:require(method!='GET' or principal is None,'model_isolation_route_invalid')
  if self.token is None or self.token.expires_on<__import__('time').time()+120:self.token=self.credential.get_token('https://management.azure.com/.default')
  with self.session.request(method,'https://management.azure.com'+path,params=params,json=body,
    headers={'Authorization':'Bearer '+self.token.token},allow_redirects=False,timeout=(5,30),stream=True) as response:
   if missing and response.status_code==404:return None
   require(200<=response.status_code<300,'model_isolation_arm_request_unaccepted')
   parts=[];size=0
   for part in response.iter_content(65536):
    size+=len(part);require(size<=2*1024*1024,'model_isolation_arm_response_limit');parts.append(part)
   return json.loads(b''.join(parts))


def direct_roles(client,principal,stages):
 value=client.call('GET',SUB+'/providers/Microsoft.Authorization/roleAssignments','2022-04-01',principal=principal)
 require(type(value)is dict and set(value)<={'value','nextLink'} and value.get('nextLink') in (None,'')
  and type(value.get('value'))is list and len(value['value'])==len(stages),'model_isolation_role_inventory_invalid')
 wanted={assignment(s).casefold():s for s in stages}
 require({r.get('id','').casefold() for r in value['value']}==set(wanted),'model_isolation_role_inventory_invalid')
 for row in value['value']:
  stage=wanted[row['id'].casefold()];p=row.get('properties',{})
  require(p.get('principalId')==principal and p.get('principalType')=='ServicePrincipal'
   and p.get('scope','').casefold()==ROLES[stage][0].casefold()
   and p.get('roleDefinitionId','').casefold()==(SUB+'/providers/Microsoft.Authorization/roleDefinitions/'+ROLES[stage][1]).casefold()
   and p.get('condition') in (None,'') and p.get('conditionVersion') in (None,''),'model_isolation_role_inventory_invalid')
 return value


def build(source,source_sha,registered):
 require(type(source)is bytes and hashlib.sha256(source).hexdigest()==source_sha,'model_isolation_source_drift')
 worker.uuid_value(registered['clientId']);worker.uuid_value(registered['principalId'])
 compile(source,worker.PATH,'exec')
 script=('import hashlib\n_source='+repr(source)+'\nassert hashlib.sha256(_source).hexdigest()=='+repr(source_sha)
  +'\n_namespace={"__name__":"owner_task_model_isolation_trial","__file__":"'+worker.PATH+'"}\n'
  +'exec(compile(_source,"model-isolation-worker","exec"),_namespace)\n'
  +'_result=_namespace["main"]('+repr(registered['clientId'])+','+repr(registered['principalId'])+')\n'
  +'raise SystemExit(0 if _result["status"]=="model_isolation_trial_passed" else 1)\n')
 packed=base64.b64encode(lzma.compress(script.encode(),preset=9)).decode()
 args=['-I','-B','-c',"import base64,lzma;exec(compile(lzma.decompress(base64.b64decode('"+packed+"')),'model-isolation-launch','exec'))"]
 require(len(canonical(args))<=65536 and b'$' not in canonical(args),'model_isolation_argument_invalid')
 value=dict(location='eastasia',identity=dict(type='UserAssigned',userAssignedIdentities={MI:{}}),
  tags=dict(purpose='owner-model-isolation-trial',operation=worker.OPERATION,sourceSha256=source_sha,
   fullSourceSha256=hashlib.sha256(script.encode()).hexdigest()),
  properties=dict(environmentId=prior.target.ENVIRONMENT,configuration=dict(triggerType='Manual',replicaTimeout=240,
   replicaRetryLimit=0,identitySettings=[dict(identity=MI,lifecycle='All')],
   manualTriggerConfig=dict(parallelism=1,replicaCompletionCount=1),secrets=[],
   registries=[dict(server='ibkrv2stgauhecwc4itaoo.azurecr.io',identity=MI)]),
   template=dict(containers=[dict(name='model-isolation-trial',image=IMAGE,command=['python'],args=args,
    env=[dict(name='AZURE_CLIENT_ID',value=registered['clientId'])],resources=dict(cpu=1,memory='2Gi'))])))
 return dict(definition=value,definition_sha256=digest(value),source_sha256=source_sha,step_2_complete=False)


def effect(stage,plan,principal):
 require(stage in STAGES,'model_isolation_stage_invalid')
 if stage=='identity':return 'PUT',MI,'2023-01-31',identity_body()
 if stage in ROLES:
  worker.uuid_value(principal)
  return 'PUT',assignment(stage),'2022-04-01',dict(properties=dict(principalId=principal,principalType='ServicePrincipal',
   roleDefinitionId=SUB+'/providers/Microsoft.Authorization/roleDefinitions/'+ROLES[stage][1]))
 require(type(plan)is dict and plan['definition_sha256']==digest(plan['definition']),'model_isolation_plan_invalid')
 return ('PUT',JOB,'2025-07-01',plan['definition']) if stage=='job' else ('POST',JOB+'/start','2025-07-01',plan['definition']['properties']['template'])


def template_checked(value,wanted,*,execution=False):
 v=json.loads(canonical(value))
 for key in ('initContainers','volumes'):
  if key in v:
   require(v[key] is None or execution and v[key]==[],'model_isolation_job_invalid');del v[key]
 require(type(v.get('containers'))is list and len(v['containers'])==1,'model_isolation_job_invalid')
 container=v['containers'][0]
 if container.get('resources',{}).get('ephemeralStorage')=='':del container['resources']['ephemeralStorage']
 if 'imageType' in container:
  require(container['imageType']=='ContainerImage','model_isolation_job_invalid');del container['imageType']
 require(v==wanted,'model_isolation_job_invalid');return digest(value)


def validate_job(value,plan,registered):
 expected=plan['definition'];p=value.get('properties',{})
 require(value.get('id')==JOB and value.get('name')==worker.JOB and value.get('type')=='Microsoft.App/jobs'
  and value.get('location') in ('eastasia','East Asia') and value.get('tags')==expected['tags'],'model_isolation_job_invalid')
 i=value.get('identity',{});assigned=i.get('userAssignedIdentities',{})
 require(i.get('type')=='UserAssigned' and i.get('principalId')is None and i.get('tenantId')is None
  and set(i)<={'type','userAssignedIdentities','principalId','tenantId'} and len(assigned)==1
  and next(iter(assigned)).casefold()==MI.casefold()
  and next(iter(assigned.values()))=={k:registered[k] for k in ('principalId','clientId')},'model_isolation_job_invalid')
 require(p.get('environmentId')==prior.target.ENVIRONMENT and p.get('provisioningState')=='Succeeded'
  and p.get('workloadProfileName') in (None,'Consumption'),'model_isolation_job_invalid')
 config=json.loads(canonical(p.get('configuration')))
 wanted=expected['properties']['configuration']
 for key in ('scheduleTriggerConfig','eventTriggerConfig','dapr'):
  if key in config:require(config[key]is None,'model_isolation_job_invalid');del config[key]
 require(config.get('secrets')==[] and len(config.get('identitySettings',[]))==len(config.get('registries',[]))==1,'model_isolation_job_invalid')
 for field in ('identitySettings','registries'):
  require(config[field][0].get('identity','').casefold()==MI.casefold(),'model_isolation_job_invalid');config[field][0]['identity']=MI
 for field in ('username','passwordSecretRef'):
  if field in config['registries'][0]:require(config['registries'][0][field]=='','model_isolation_job_invalid');del config['registries'][0][field]
 require(config==wanted,'model_isolation_job_invalid')
 template_checked(p.get('template'),expected['properties']['template'])
 return dict(native_job_sha256=digest(value),configured_sql_secrets=0,single_separate_model_identity_verified=True)


def baseline():
 value=prior.baseline();value['other_jobs'].pop(worker.JOB,None)
 return value


def frozen_native_jobs(client):
 p=client or Transport()
 return {name:digest(p.call('GET',worker.BASE+'Microsoft.App/jobs/'+name,'2025-07-01')) for name in FROZEN_JOBS}


def live(pf):
 prior.host.checked_owner()
 require(prior.host.sources()==pf['sources'] and baseline()==pf['baseline']
  and prior.host.observe(prior.host.Transport())==pf['host'] and frozen_native_jobs(None)==pf['frozen_native_jobs'],'model_isolation_live_drift')
 # Preserve the old SQL identity and its two exact direct grants. No old code,
 # bindings, grants, SQL fixtures or consumed executions are retargeted.
 p=prior.parent.Transport();registered=prior.parent.live_identity(p.call('GET',prior.MI,'2023-01-31'))
 require(registered==pf['sql_identity'],'model_isolation_live_drift');prior.parent.roles(p,registered['principalId'],['acr','secret'])
 value=prior.Transport().call('GET',prior.JOB+'/executions/ibkrv2stg-ownobjv-20261005-n9y23ym')
 require(value==pf['provider_native_execution'],'model_isolation_live_drift')


def prepare(client):
 require(not (FOLDER/'preflight.json').exists(),'model_isolation_stage_consumed')
 rr,old=prior.load('preflight.json');ar,authority=prior.load('owner-standing-authority-confirmation.json');vr,observation=prior.load('provider-object-observation.json')
 require(rr['protected_receipt']['sha256']=='ac1aa81bc185c85e405702c6b214f7099e8bb9ed988da974bffd1732cb364a21'
  and ar['protected_receipt']['sha256']=='e264c8a291a9a046ad986939aa48ab8fb5470cb8509e003604f981c04b61f90c'
  and vr['protected_receipt']['sha256']=='b3660df5e7ce215b5d1a71692dcd09e0d732307b14df31a94526f792bd0d1e87'
  and authority['broader_system_and_resource_group_operational_authority_confirmed']is True
  and authority['ibkr_received_data_modification_authorized']is False
  and observation['complete_metadata_reconstruction_verified']is True and observation['step_3_started']is False,'model_isolation_predecessor_invalid')
 sources=prior.host.sources();sources_valid(old['sources'],sources)
 p=prior.parent.Transport();registered=prior.parent.live_identity(p.call('GET',prior.MI,'2023-01-31'))
 value=dict(sources=sources,baseline=baseline(),host=old['host'],frozen_native_jobs=frozen_native_jobs(client),
  provider_native_execution=observation['native_execution_after'],sql_identity=registered)
 require(value['baseline']==old['baseline'],'model_isolation_live_drift')
 live(value)
 require(client.call('GET',MI,'2023-01-31',missing=True)is None and client.call('GET',JOB,'2025-07-01',missing=True)is None,'model_isolation_target_exists')
 for stage in ROLES:require(client.call('GET',assignment(stage),'2022-04-01',missing=True)is None,'model_isolation_target_exists')
 checks=prior.parent.reviewed.run_local_checks();live(value)
 require(prior.host.sources()==sources,'model_isolation_source_drift')
 return save(dict(value,status='prepared_model_isolation_trial',operation_id=worker.OPERATION,checked_at_utc=now(),
  authority_receipt=ar['protected_receipt'],provider_observation_receipt=vr['protected_receipt'],
  restricted_sql_subgate_receipt=old['restricted_sql_subgate_receipt'],tls_verified_receipt=old['tls_verified_receipt'],
  local_checks=checks,approved_model=model_inventory.validate_azure_model(),image=IMAGE,image_receipt=image_receipt(),
  configured_sql_credentials=0,model_calls=0,step_2_complete=False,step_3_started=False),'preflight.json')


def reserve(stage,value):
 prior.host.deploy.archive_protection()
 service=BlobServiceClient('https://'+prior.host.deploy.ACCOUNT+'.blob.core.windows.net',credential=AzureCliCredential(),
  retry_total=0,connection_timeout=5,read_timeout=10)
 raw=canonical(value);name=PREFIX+stage+'-reservation.json';blob=service.get_blob_client(prior.host.deploy.CONTAINER,name)
 ack=blob.upload_blob(raw,length=len(raw),overwrite=False,match_condition=MatchConditions.IfMissing,max_concurrency=1,retry_total=0,timeout=10)
 exact=service.get_blob_client(prior.host.deploy.CONTAINER,name,version_id=ack['version_id']);props=exact.get_blob_properties()
 require(props.size==len(raw) and props.version_id==ack['version_id'] and props.etag==ack['etag']
  and exact.download_blob(etag=props.etag,match_condition=MatchConditions.IfNotModified).readall()==raw,'model_isolation_reservation_invalid')
 prior.host.deploy.archive_protection();return dict(blob=name,sha256=hashlib.sha256(raw).hexdigest(),version_id=ack['version_id'],etag=ack['etag'])


def prerequisite(client,stage):
 index=STAGES.index(stage)
 rr,pf=load('preflight.json')
 if index:
  _,previous=load(STAGES[index-1]+'-accepted.json')
  require(previous['status']=='model_isolation_stage_verified' and previous['operation_id']==worker.OPERATION
   and previous['stage']==STAGES[index-1] and previous['preflight_receipt']==rr['protected_receipt'],'model_isolation_chain_invalid')
 if stage=='identity':return None,None
 _,ir=load('identity-accepted.json');registered=identity(client.call('GET',MI,'2023-01-31'))
 require(registered==ir['registered_identity'] and ir['preflight_receipt']==rr['protected_receipt']
  and ir['operation_id']==worker.OPERATION and ir['stage']=='identity','model_isolation_identity_invalid')
 direct_roles(client,registered['principalId'],['acr','ai'][:max(0,min(index-1,2))])
 plan=build((ROOT/worker.PATH).read_bytes(),prior.host.sources()[worker.PATH],registered) if stage in ('job','start') else None
 return registered,plan


def dispatch(client,stage):
 require(stage in STAGES and not any((FOLDER/(stage+'-'+s+'.json')).exists() for s in ('intent','ack','accepted')),'model_isolation_stage_consumed')
 rr,pf=load('preflight.json');live(pf);registered,plan=prerequisite(client,stage)
 principal=registered['principalId'] if registered else None
 method,path,api,body=effect(stage,plan,principal)
 def guard():
  if stage!='start':require(client.call('GET',path,api,missing=True)is None,'model_isolation_target_exists')
  else:
   validate_job(client.call('GET',JOB,'2025-07-01'),plan,registered)
   inv=client.call('GET',JOB+'/executions','2025-07-01')
   require(inv.get('value')==[] and inv.get('nextLink') in (None,''),'model_isolation_execution_exists')
 guard()
 value=dict(operation_id=worker.OPERATION,stage=stage,preflight_receipt=rr['protected_receipt'],method=method,path=path,api=api,
  body=body,plan=plan,registered_identity=registered,automatic_retry=False)
 intent=save(dict(value,reservation=reserve(stage,value)),stage+'-intent.json')
 live(pf);guard()
 ack=client.call(method,path,api,body=body,stage=stage,plan=plan,principal=principal)
 return save(dict(status='effect_acknowledged_inspect_required',operation_id=worker.OPERATION,stage=stage,intent_receipt=intent['protected_receipt'],
  raw_ack=ack,attempt_consumed=True,automatic_retry=False,checked_at_utc=now(),step_2_complete=False),stage+'-ack.json')


def inspect(client,stage):
 rr,pf=load('preflight.json');ir,intent=load(stage+'-intent.json');ar,ack=load(stage+'-ack.json')
 require(ack['intent_receipt']==ir['protected_receipt'] and intent['preflight_receipt']==rr['protected_receipt']
  and intent['operation_id']==worker.OPERATION and intent['stage']==stage,'model_isolation_chain_invalid');live(pf)
 result=dict(status='model_isolation_stage_verified',operation_id=worker.OPERATION,stage=stage,
  preflight_receipt=rr['protected_receipt'],intent_receipt=ir['protected_receipt'],ack_receipt=ar['protected_receipt'],checked_at_utc=now(),step_2_complete=False)
 if stage=='identity':
  registered=identity(client.call('GET',MI,'2023-01-31'));direct_roles(client,registered['principalId'],[])
  result['registered_identity']=registered
 elif stage in ROLES:
  _,record=load('identity-accepted.json');registered=identity(client.call('GET',MI,'2023-01-31'))
  require(registered==record['registered_identity'],'model_isolation_identity_invalid')
  result['direct_role_inventory']=direct_roles(client,registered['principalId'],['acr','ai'][:STAGES.index(stage)])
 elif stage=='job':result['native_verification']=validate_job(client.call('GET',JOB,'2025-07-01'),intent['plan'],intent['registered_identity'])
 else:
  name=execution_name(ack['raw_ack']['name']);require(ack['raw_ack']['id']==JOB+'/executions/'+name,'model_isolation_execution_invalid')
  result.update(status='model_isolation_execution_observed',execution=name,native_status=client.call('GET',JOB+'/executions/'+name,'2025-07-01')['properties']['status'])
  return result
 return save(result,stage+'-accepted.json')


def execution(client,intent,ack,status):
 name=execution_name(ack['raw_ack']['name'])
 require(ack['raw_ack']['id']==JOB+'/executions/'+name,'model_isolation_execution_invalid')
 value=client.call('GET',JOB+'/executions/'+name,'2025-07-01')
 require(value.get('name')==name and value.get('id')==JOB+'/executions/'+name
  and value.get('properties',{}).get('status')==status,'model_isolation_execution_unaccepted')
 template_checked(value['properties']['template'],intent['plan']['definition']['properties']['template'],execution=True)
 inv=client.call('GET',JOB+'/executions','2025-07-01')
 require(inv.get('nextLink') in (None,'') and type(inv.get('value'))is list and len(inv['value'])==1
  and inv['value'][0].get('name')==name and inv['value'][0].get('id')==value['id'],'model_isolation_execution_invalid')
 return value,inv


def start_chain():
 rr,pf=load('preflight.json');ir,intent=load('start-intent.json');ar,ack=load('start-ack.json')
 jr,job=load('job-accepted.json')
 require(ack['intent_receipt']==ir['protected_receipt'] and intent['preflight_receipt']==rr['protected_receipt']
  and job['preflight_receipt']==rr['protected_receipt'] and intent['operation_id']==ack['operation_id']==worker.OPERATION
  and intent['stage']==ack['stage']=='start','model_isolation_chain_invalid')
 return rr,pf,ir,intent,ar,ack,jr


def observe_ready(client):
 rr,pf,ir,intent,ar,ack,jr=start_chain();live(pf)
 run,inv=execution(client,intent,ack,'Running')
 replicas=client.call('GET',run['id']+'/replicas','2023-11-02-preview')
 require(replicas.get('nextLink') in (None,'') and type(replicas.get('value'))is list and len(replicas['value'])==1,'model_isolation_ready_invalid')
 replica=replicas['value'][0];containers=replica.get('properties',{}).get('containers')
 require(type(containers)is list and len(containers)==1 and containers[0].get('name')=='model-isolation-trial'
  and containers[0].get('ready')is True and containers[0].get('started')is True,'model_isolation_ready_invalid')
 return save(dict(status='model_isolation_native_ready_observed',operation_id=worker.OPERATION,checked_at_utc=now(),
  preflight_receipt=rr['protected_receipt'],start_ack_receipt=ar['protected_receipt'],native_execution=run,
  execution_inventory=inv,native_replicas=replicas,step_2_complete=False),'execution-first-observation.json')


def family_query(run):
 start=datetime.fromisoformat(run['properties']['startTime'].replace('Z','+00:00'))
 require(start.tzinfo is not None,'model_isolation_log_window_invalid')
 lower=(start-timedelta(seconds=45)).isoformat();upper=(start+timedelta(minutes=15)).isoformat()
 return "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution_name(run['name'])+"-' | where TimeGenerated between (datetime("+lower+") .. datetime("+upper+")) | project Group=ContainerGroupName_s,Container=ContainerName_s,LogTime=TimeGenerated,Log=Log_s | order by LogTime asc"


def logs_response(value,name):
 require(type(value)is dict and set(value)=={'tables'} and type(value['tables'])is list and len(value['tables'])==1,'model_isolation_logs_invalid')
 table=value['tables'][0]
 require(type(table)is dict and set(table)=={'name','columns','rows'} and table['name']=='PrimaryResult'
  and table['columns']==[dict(name=n,type=t) for n,t in (('Group','string'),('Container','string'),('LogTime','datetime'),('Log','string'))]
  and type(table['rows'])is list and 0<len(table['rows'])<=2048 and len(canonical(value))<=2*1024*1024,'model_isolation_logs_invalid')
 records=[]
 for row in table['rows']:
  require(type(row)is list and len(row)==4 and all(type(v)is str for v in row) and row[0].startswith(name+'-'),'model_isolation_logs_invalid')
  if row[3].startswith(worker.PREFIX):
   require(row[1]=='model-isolation-trial','model_isolation_logs_invalid')
   payload=row[3].removeprefix(worker.PREFIX);record=json.loads(payload)
   require(canonical(record).decode()==payload,'model_isolation_logs_invalid');records.append(record)
 require(len(records)==1,'model_isolation_worker_record_unaccepted');return records[0]


def validate_result(record):
 expected=dict(status='model_isolation_trial_passed',operation_id=worker.OPERATION,job_resource_id=JOB,identity_resource_id=MI,
  model_credential_isolation_verified=True,credential_access_denial_verified=True,configured_sql_credentials=0,sql_connections=0,
  source_rows_read=0,source_rows_written=0,provider_routines_invoked=0,model_calls=1,owner_runtime_active=False,
  six_specialist_runtimes_active=False,production_changed=False,step_2_complete=False,automatic_retry=False,
  accepted_scope='fixed_tool_free_turn_on_separate_compute',arbitrary_tool_execution_accepted=False)
 require(type(record)is dict and all(type(record.get(k))is type(v) and record[k]==v for k,v in expected.items()),'model_isolation_worker_record_unaccepted')
 require(record['parent_protection']==dict(uid=10001,gid=10001,effective_capabilities_zero=True,dumpable=0,no_new_privileges=1,core_dump_limit=0),'model_isolation_worker_record_unaccepted')
 for key in ('process_denials','post_model_process_denials'):
  result=record[key];require(result.get('same_uid_adversarial_checks_verified')is True and result.get('actual_credential_bytes_probed')is False,'model_isolation_worker_record_unaccepted')
  worker.validate_companion({k:v for k,v in result.items() if k not in ('same_uid_adversarial_checks_verified','actual_credential_bytes_probed')})
 for kind,code,inner in (('secret','Forbidden','ForbiddenByRbac'),('server','AuthorizationFailed',None)):
  require(record[kind+'_denial']==dict(target=kind,status=403,error_code=code,inner_error_code=inner,effective_service_denial_verified=True,unexpected_success_body_read=False),'model_isolation_worker_record_unaccepted')
 require(record['relay']['request_count']==record['relay']['accepted_count']==1 and record['relay']['failure']is None
  and record['relay']['upstream_tools_enabled']is False and re.fullmatch('[0-9a-f]{64}',record['relay']['response_sha256']),'model_isolation_worker_record_unaccepted')
 for name in ('thread_started_verified','turn_started_verified','turn_completed','event_stream_observed','runtime_output_verified','process_exit_verified'):
  require(record['model_result'].get(name)is True,'model_isolation_worker_record_unaccepted')
 require(record['restriction']==dict(discarded_declaration_count=1,discarded_declaration_sha256='449a364fe16bf52da8798ab2f76ab5e5618319de87447719d753d66140ff074d',declaration_tools_forwarded=False,upstream_tool_contract_verified=True),'model_isolation_worker_record_unaccepted')
 return record


def collect(client):
 rr,pf,ir,intent,ar,ack,jr=start_chain();live(pf)
 fr,first=load('execution-first-observation.json')
 require(first['start_ack_receipt']==ar['protected_receipt'] and first['preflight_receipt']==rr['protected_receipt'],'model_isolation_chain_invalid')
 registered=identity(client.call('GET',MI,'2023-01-31'));require(registered==intent['registered_identity'],'model_isolation_identity_invalid')
 roles=direct_roles(client,registered['principalId'],['acr','ai']);validate_job(client.call('GET',JOB,'2025-07-01'),intent['plan'],registered)
 run,inv=execution(client,intent,ack,'Succeeded');query=family_query(run)
 token=client.credential.get_token('https://api.loganalytics.io/.default')
 with client.session.post('https://api.loganalytics.io/v1/workspaces/5264a124-10cd-45f6-93a5-6d65c37e9bfb/query',
   json={'query':query},headers={'Authorization':'Bearer '+token.token},allow_redirects=False,timeout=(5,30),stream=True) as response:
  require(response.status_code==200,'model_isolation_logs_invalid');parts=[];size=0
  for part in response.iter_content(65536):
   size+=len(part);require(size<=2*1024*1024,'model_isolation_logs_invalid');parts.append(part)
  family=json.loads(b''.join(parts))
 record=validate_result(logs_response(family,run['name']))
 after,_=execution(client,intent,ack,'Succeeded');require(after==run,'model_isolation_execution_drift');live(pf)
 return save(dict(status='live_model_credential_isolation_verified',operation_id=worker.OPERATION,checked_at_utc=now(),
  preflight_receipt=rr['protected_receipt'],job_accepted_receipt=jr['protected_receipt'],start_intent_receipt=ir['protected_receipt'],
  start_ack_receipt=ar['protected_receipt'],first_native_observation_receipt=fr['protected_receipt'],native_execution_before=run,
  native_execution_after=after,execution_inventory=inv,full_bounded_observed_family=family,query=query,worker_result=record,
  exact_direct_role_inventory=roles,model_credential_isolation_verified=True,late_log_delivery_excluded=False,
  zero_log_replicas_excluded=False,step_2_complete=False,step_3_started=False),'model-isolation-observation.json')


def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=('prepare','dispatch','inspect','ready','collect'));p.add_argument('--stage',choices=STAGES);a=p.parse_args()
 try:
  client=Transport();value=prepare(client) if a.command=='prepare' else dispatch(client,a.stage) if a.command=='dispatch' else inspect(client,a.stage) if a.command=='inspect' else observe_ready(client) if a.command=='ready' else collect(client)
  print(json.dumps({k:value[k] for k in ('status','stage','execution','native_status','protected_receipt') if k in value}),flush=True)
 except Exception:
  print(json.dumps(dict(status='model_isolation_unaccepted_inspect_only',automatic_retry=False)),flush=True);raise SystemExit(1) from None


if __name__=='__main__':main()
