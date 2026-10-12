"""New once-only model trial after a retained audience-binding failure.

Old sources/identities/grants/jobs/executions stay unchanged. This uses the same
two-grant model identity, never the SQL identity. Audience/signature validation
belongs to each Azure destination API; identity selection and denied-service
checks remain mandatory. No arbitrary input token, tool or SQL is accepted.
"""
import argparse,copy,json,re,time
from datetime import datetime,timedelta
import deploy_owner_task_model_isolation_trial as old
import owner_task_model_empty_secret_readback as alias
import owner_task_model_service_audience_trial as w

JOB=w.JOB_ID
DIAG=w.BASE+'Microsoft.App/jobs/ibkrv2stg-ownmid-20261005'
NAMES=frozenset((w.PATH,'scripts/deploy_owner_task_model_service_audience_trial.py','tests/test_owner_task_model_service_audience_trial.py'))

def sources_valid(before,after):
 w.require(type(before)is dict and len(before)==800 and type(after)is dict and set(after)==set(before)|NAMES
  and not set(before)&NAMES and all(after[k]==v for k,v in before.items())
  and all(type(v)is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()),'model_isolation_service_source_drift')

class Transport(old.Transport):
 def call(self,method,path,api='2025-07-01',*,body=None,plan=None,stage=None,missing=False):
  paths={JOB,JOB+'/executions',DIAG}
  if path.startswith(JOB+'/executions/'):
   parts=path.removeprefix(JOB+'/executions/').split('/')
   w.require(re.fullmatch(re.escape(w.JOB)+'-[a-z0-9]{7,12}',parts[0]) and (len(parts)==1 or parts[1:]==['replicas']),'model_isolation_service_route_invalid');paths.add(path)
  if method=='GET':w.require(path in paths and body is None and api==('2023-11-02-preview' if path.endswith('/replicas') else '2025-07-01'),'model_isolation_service_route_invalid')
  else:
   w.require(plan is not None and plan['definition_sha256']==old.digest(plan['definition']),'model_isolation_plan_invalid')
   expected=('PUT',JOB,plan['definition']) if stage=='job' else ('POST',JOB+'/start',plan['definition']['properties']['template']) if stage=='start' else None
   w.require(expected is not None and (method,path,body)==expected and api=='2025-07-01','model_isolation_service_route_invalid')
  if self.token is None or self.token.expires_on<time.time()+120:self.token=self.credential.get_token('https://management.azure.com/.default')
  with self.session.request(method,'https://management.azure.com'+path,params={'api-version':api},json=body,
   headers={'Authorization':'Bearer '+self.token.token},allow_redirects=False,timeout=(5,30),stream=True) as response:
   if missing and method=='GET' and response.status_code==404:return None
   w.require(response.status_code in (200,201,202),'model_isolation_service_http_unaccepted');parts=[];size=0
   for part in response.iter_content(65536):
    size+=len(part);w.require(size<=2*1024*1024,'model_isolation_service_http_oversize');parts.append(part)
   return json.loads(b''.join(parts))

def build(sources,registered):
 raw=(old.ROOT/w.PATH).read_bytes();plan=old.build(raw,sources[w.PATH],registered)
 plan['definition']['tags']['operation']=w.OPERATION
 plan['definition_sha256']=old.digest(plan['definition']);return plan

def native(value,plan,registered):
 w.require(value.get('id')==JOB and value.get('name')==w.JOB and value.get('tags',{}).get('operation')==w.OPERATION,'model_isolation_service_native_invalid')
 shadow=copy.deepcopy(value);shadow.update(id=old.JOB,name=old.worker.JOB)
 result=alias.validate_job(shadow,plan,registered)
 return dict(result,native_job_sha256=old.digest(value),actual_job_resource_id=JOB)

def live(pf,client):
 old.prior.host.checked_owner()
 w.require(old.prior.host.sources()==pf['sources'],'model_isolation_service_source_drift')
 baseline=old.baseline();baseline['other_jobs'].pop(w.JOB,None);baseline['other_jobs'].pop('ibkrv2stg-ownmid-20261005',None)
 w.require(baseline==pf['baseline'] and old.prior.host.observe(old.prior.host.Transport())==pf['host']
  and old.frozen_native_jobs(None)==pf['frozen_native_jobs'],'model_isolation_service_live_drift')
 w.require(old.digest(client.call('GET',DIAG))==pf['diagnostic_native_job_sha256'],'model_isolation_service_diagnostic_drift')
 control=old.Transport();registered=old.identity(control.call('GET',old.MI,'2023-01-31'))
 w.require(registered==pf['registered_identity'],'model_isolation_identity_invalid');old.direct_roles(control,registered['principalId'],['acr','ai'])
 # Old SQL identity/grants and completed provider execution are independently
 # preserved; the old model/diagnostic exclusions are explicitly registered.
 p=old.prior.parent.Transport();sql=old.prior.parent.live_identity(p.call('GET',old.prior.MI,'2023-01-31'))
 w.require(sql==pf['sql_identity'],'model_isolation_service_live_drift');old.prior.parent.roles(p,sql['principalId'],['acr','secret'])
 w.require(old.prior.Transport().call('GET',old.prior.JOB+'/executions/ibkrv2stg-ownobjv-20261005-n9y23ym')==pf['provider_native_execution'],'model_isolation_service_live_drift')

def load():
 rr,pf=old.load('serviceaud-preflight.json');w.require(pf['operation_id']==w.OPERATION and pf['status']=='service_owned_audience_trial_prepared','model_isolation_service_preflight_invalid');return rr,pf

def prepare(client):
 w.require(not (old.FOLDER/'serviceaud-preflight.json').exists(),'model_isolation_stage_consumed')
 rr,pf0=old.load('preflight.json');vr,review=old.load('empty-secret-review.json');fr,failure=old.load('failed-model-observation.json');dr,diagnostic=old.load('identity-metadata-observation.json');_,start=old.load('identity-metadata-start-intent.json')
 sources=old.prior.host.sources();sources_valid(review['sources'],sources)
 w.require(fr['protected_receipt']['sha256']=='db54fd982da2594b191a3dcf218e18e86df565c41e96a5011ddd4f54c64f958c'
  and dr['protected_receipt']['sha256']=='02fd5151c19157af14ee119a79266d27adfd0ad3ca7ca5e94301685afd23c54d'
  and failure['worker_result']['failure_phase']=='effective_secret_denial'
  and diagnostic['diagnostic_result']['claims']==dict(appid_exact=True,audience_exact=False,azp_exact=False,expiry_gt_90s=True,expiry_integer=True,principal_exact=True,tenant_exact=True),'model_isolation_service_predecessor_invalid')
 control=old.Transport();registered=old.identity(control.call('GET',old.MI,'2023-01-31'))
 pf=dict(pf0,sources=sources,operation_id=w.OPERATION,status='service_owned_audience_trial_prepared',
  previous_preflight_receipt=rr['protected_receipt'],empty_secret_review_receipt=vr['protected_receipt'],failed_model_receipt=fr['protected_receipt'],diagnostic_receipt=dr['protected_receipt'],
  diagnostic_native_job_sha256=start['native_verification']['actual_native_sha256'],registered_identity=registered,plan=build(sources,registered),
  resource_api_audience_and_signature_validation_required=True,caller_supplied_tokens_accepted=False,step_2_complete=False,step_3_started=False)
 live(pf,client);w.require(client.call('GET',JOB,missing=True)is None,'model_isolation_target_exists')
 checks=old.prior.parent.reviewed.run_local_checks();live(pf,client)
 return old.save(dict(pf,local_checks=checks,checked_at_utc=old.now()),'serviceaud-preflight.json')

def dispatch(client,stage):
 w.require(stage in ('job','start') and not any((old.FOLDER/('serviceaud-'+stage+'-'+s+'.json')).exists() for s in ('intent','ack','accepted')),'model_isolation_stage_consumed')
 rr,pf=load();live(pf,client);plan=pf['plan']
 def guard():
  if stage=='job':w.require(client.call('GET',JOB,missing=True)is None,'model_isolation_target_exists')
  else:
   ar,accepted=old.load('serviceaud-job-accepted.json');w.require(accepted['preflight_receipt']==rr['protected_receipt'],'model_isolation_chain_invalid')
   native(client.call('GET',JOB),plan,pf['registered_identity']);inv=client.call('GET',JOB+'/executions')
   w.require(inv.get('value')==[] and inv.get('nextLink') in (None,''),'model_isolation_execution_exists')
 guard();method,path,body=('PUT',JOB,plan['definition']) if stage=='job' else ('POST',JOB+'/start',plan['definition']['properties']['template'])
 value=dict(operation_id=w.OPERATION,stage=stage,preflight_receipt=rr['protected_receipt'],method=method,path=path,body=body,automatic_retry=False)
 intent=old.save(dict(value,reservation=old.reserve('serviceaud-'+stage,value)),'serviceaud-'+stage+'-intent.json')
 live(pf,client);guard();ack=client.call(method,path,body=body,plan=plan,stage=stage)
 return old.save(dict(status='service_audience_effect_acknowledged',operation_id=w.OPERATION,stage=stage,preflight_receipt=rr['protected_receipt'],intent_receipt=intent['protected_receipt'],raw_ack=ack,checked_at_utc=old.now(),automatic_retry=False,step_2_complete=False),'serviceaud-'+stage+'-ack.json')

def accept_job(client):
 rr,pf=load();live(pf,client);ir,intent=old.load('serviceaud-job-intent.json');ar,ack=old.load('serviceaud-job-ack.json')
 w.require(ack['intent_receipt']==ir['protected_receipt'] and intent['preflight_receipt']==rr['protected_receipt'],'model_isolation_chain_invalid')
 return old.save(dict(status='service_audience_job_verified',operation_id=w.OPERATION,preflight_receipt=rr['protected_receipt'],ack_receipt=ar['protected_receipt'],native_verification=native(client.call('GET',JOB),pf['plan'],pf['registered_identity']),step_2_complete=False),'serviceaud-job-accepted.json')

def collect(client):
 rr,pf=load();live(pf,client);ar,ack=old.load('serviceaud-start-ack.json');fr,first=old.load('serviceaud-first-observation.json')
 name=ack['raw_ack']['name'];w.require(re.fullmatch(re.escape(w.JOB)+'-[a-z0-9]{7,12}',name),'model_isolation_execution_invalid');path=JOB+'/executions/'+name
 run=client.call('GET',path);w.require(run['id']==path and run['properties']['status']=='Succeeded','model_isolation_execution_unaccepted')
 old.template_checked(run['properties']['template'],pf['plan']['definition']['properties']['template'],execution=True)
 w.require(first['native_execution']['id']==path and first['preflight_receipt']==rr['protected_receipt'],'model_isolation_chain_invalid')
 start=datetime.fromisoformat(run['properties']['startTime'].replace('Z','+00:00'))
 query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where TimeGenerated between (datetime("+(start-timedelta(seconds=45)).isoformat()+") .. datetime("+(start+timedelta(minutes=15)).isoformat()+")) | project Group=ContainerGroupName_s,Container=ContainerName_s,LogTime=TimeGenerated,Log=Log_s | order by LogTime asc"
 token=client.credential.get_token('https://api.loganalytics.io/.default')
 with client.session.post('https://api.loganalytics.io/v1/workspaces/5264a124-10cd-45f6-93a5-6d65c37e9bfb/query',json={'query':query},headers={'Authorization':'Bearer '+token.token},allow_redirects=False,timeout=(5,30)) as response:
  w.require(response.status_code==200 and len(response.content)<=2*1024*1024,'model_isolation_logs_invalid');family=response.json()
 shadow=copy.deepcopy(family)
 # Reviewed exact new wire prefix is normalized only in a detached parser copy.
 # Full native family is retained unchanged; no line is filtered or deduplicated.
 for row in shadow['tables'][0]['rows']:
  if row[3].startswith(w.PREFIX):row[3]=old.worker.PREFIX+row[3].removeprefix(w.PREFIX)
 record=old.logs_response(shadow,name)
 w.require(record['operation_id']==w.OPERATION and record['job_resource_id']==JOB,'model_isolation_service_worker_invalid')
 old.validate_result(dict(record,operation_id=old.worker.OPERATION,job_resource_id=old.JOB))
 after=client.call('GET',path);w.require(after==run,'model_isolation_execution_drift');live(pf,client)
 return old.save(dict(status='service_owned_audience_live_isolation_verified',operation_id=w.OPERATION,checked_at_utc=old.now(),preflight_receipt=rr['protected_receipt'],start_ack_receipt=ar['protected_receipt'],first_native_observation_receipt=fr['protected_receipt'],native_execution_before=run,native_execution_after=after,full_bounded_observed_family=family,query=query,worker_result=record,model_credential_isolation_verified=True,step_2_complete=False,step_3_started=False),'serviceaud-model-observation.json')

def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=('prepare','job','accept-job','start','collect'));a=p.parse_args()
 try:
  client=Transport();value=prepare(client) if a.command=='prepare' else accept_job(client) if a.command=='accept-job' else collect(client) if a.command=='collect' else dispatch(client,a.command)
  print(json.dumps({k:value[k] for k in ('status','protected_receipt','raw_ack') if k in value and k!='raw_ack'}),flush=True)
 except Exception as e:print(json.dumps(dict(status='service_audience_trial_unaccepted_inspect_only',reason=getattr(e,'code',type(e).__name__),automatic_retry=False)),flush=True);raise SystemExit(1) from None

if __name__=='__main__':main()
