"""Additive readback of Azure's observed null-for-empty model-job secrets.

The 798-source preflight/worker, existing identity, grants and job are unchanged.
Only a detached verification copy maps configuration.secrets None to []. No
nonempty binding is accepted. The original unconsumed START scope is retained;
this is not permission to restart a consumed attempt or install another job.
"""
import argparse,copy,json,re,time
import deploy_owner_task_model_isolation_trial as c

ADDITIONS=frozenset(('scripts/owner_task_model_empty_secret_readback.py','tests/test_owner_task_model_empty_secret_readback.py'))
PREFLIGHT_SHA='6811945645cfdc326facff307e2e8ff14e329c170e322430a71c7186333d30e0'

def source_valid(before,after):
 c.require(type(before)is dict and len(before)==798 and type(after)is dict and set(after)==set(before)|ADDITIONS
  and not set(before)&ADDITIONS and all(after[k]==v for k,v in before.items())
  and all(type(v)is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()),'model_isolation_empty_secret_source_drift')

def validate_job(native,plan,registered):
 shadow=copy.deepcopy(native);cfg=shadow.get('properties',{}).get('configuration',{})
 c.require('secrets' in cfg and (cfg['secrets']is None or cfg['secrets']==[]),'model_isolation_job_secret_invalid')
 changed=cfg['secrets']is None;cfg['secrets']=[]
 accepted=c.validate_job(shadow,plan,registered)
 return dict(accepted,native_job_sha256=c.digest(native),detached_shadow_sha256=c.digest(shadow),
  only_null_for_empty_secrets_descriptor_mapped=changed)

def live(pf,review):
 source_valid(pf['sources'],review['sources'])
 c.require(review['preflight_receipt']['sha256']==PREFLIGHT_SHA and review['operation_id']==c.worker.OPERATION
  and review['status']=='model_job_empty_secret_descriptor_reviewed','model_isolation_empty_secret_review_invalid')
 # Caller-supplied detached context is explicitly bound to the protected new
 # review. No frozen helper globals, source files or original receipt change.
 c.live(dict(pf,sources=review['sources']))

def checked():
 rr,pf=c.load('preflight.json');vr,review=c.load('empty-secret-review.json')
 c.require(rr['protected_receipt']['sha256']==PREFLIGHT_SHA and review['preflight_receipt']==rr['protected_receipt'],'model_isolation_chain_invalid')
 live(pf,review);return rr,pf,vr,review

def prepare(client):
 c.require(not (c.FOLDER/'empty-secret-review.json').exists() and not (c.FOLDER/'job-accepted.json').exists()
  and not any((c.FOLDER/('start-'+s+'.json')).exists() for s in ('intent','ack','accepted')),'model_isolation_stage_consumed')
 rr,pf=c.load('preflight.json');ir,intent=c.load('job-intent.json');ar,ack=c.load('job-ack.json')
 c.require(rr['protected_receipt']['sha256']==PREFLIGHT_SHA and ack['intent_receipt']==ir['protected_receipt']
  and intent['preflight_receipt']==rr['protected_receipt'],'model_isolation_chain_invalid')
 before=c.prior.host.sources();source_valid(pf['sources'],before)
 review=dict(status='model_job_empty_secret_descriptor_reviewed',operation_id=c.worker.OPERATION,sources=before,
  preflight_receipt=rr['protected_receipt'],job_intent_receipt=ir['protected_receipt'],job_ack_receipt=ar['protected_receipt'])
 live(pf,review)
 registered=c.identity(client.call('GET',c.MI,'2023-01-31'));c.require(registered==intent['registered_identity'],'model_isolation_identity_invalid')
 roles=c.direct_roles(client,registered['principalId'],['acr','ai'])
 native=client.call('GET',c.JOB,'2025-07-01');verified=validate_job(native,intent['plan'],registered)
 inv=client.call('GET',c.JOB+'/executions','2025-07-01');c.require(inv.get('value')==[] and inv.get('nextLink') in (None,''),'model_isolation_execution_exists')
 checks=c.prior.parent.reviewed.run_local_checks();live(pf,review)
 c.require(client.call('GET',c.JOB,'2025-07-01')==native,'model_isolation_job_drift')
 return c.save(dict(review,checked_at_utc=c.now(),local_checks=checks,native_job=native,readback_verification=verified,
  direct_role_inventory=roles,worker_changed=False,job_changed=False,new_grants=0,model_calls=0,
  step_2_complete=False,step_3_started=False),'empty-secret-review.json')

def accept_job(client):
 rr,pf,vr,review=checked()
 ir,intent=c.load('job-intent.json');ar,ack=c.load('job-ack.json')
 native=client.call('GET',c.JOB,'2025-07-01')
 c.require(native==review['native_job'] and ack['intent_receipt']==ir['protected_receipt'],'model_isolation_chain_invalid')
 verified=validate_job(native,intent['plan'],intent['registered_identity'])
 return c.save(dict(status='model_isolation_stage_verified',operation_id=c.worker.OPERATION,stage='job',
  preflight_receipt=rr['protected_receipt'],intent_receipt=ir['protected_receipt'],ack_receipt=ar['protected_receipt'],
  empty_secret_review_receipt=vr['protected_receipt'],native_verification=verified,checked_at_utc=c.now(),step_2_complete=False),'job-accepted.json')

def start(client):
 c.require(not any((c.FOLDER/('start-'+s+'.json')).exists() for s in ('intent','ack','accepted')),'model_isolation_stage_consumed')
 rr,pf,vr,review=checked();registered,plan=c.prerequisite(client,'start')
 _,job=c.load('job-accepted.json');c.require(job['empty_secret_review_receipt']==vr['protected_receipt'],'model_isolation_chain_invalid')
 method,path,api,body=c.effect('start',plan,registered['principalId'])
 def guard():
  validate_job(client.call('GET',c.JOB,'2025-07-01'),plan,registered)
  inv=client.call('GET',c.JOB+'/executions','2025-07-01')
  c.require(inv.get('value')==[] and inv.get('nextLink') in (None,''),'model_isolation_execution_exists')
 guard()
 value=dict(operation_id=c.worker.OPERATION,stage='start',preflight_receipt=rr['protected_receipt'],
  empty_secret_review_receipt=vr['protected_receipt'],method=method,path=path,api=api,body=body,plan=plan,
  registered_identity=registered,automatic_retry=False)
 intent=c.save(dict(value,reservation=c.reserve('start',value)),'start-intent.json')
 live(pf,review);guard()
 ack=client.call(method,path,api,body=body,stage='start',plan=plan,principal=registered['principalId'])
 return c.save(dict(status='effect_acknowledged_inspect_required',operation_id=c.worker.OPERATION,stage='start',
  intent_receipt=intent['protected_receipt'],empty_secret_review_receipt=vr['protected_receipt'],raw_ack=ack,
  attempt_consumed=True,automatic_retry=False,checked_at_utc=c.now(),step_2_complete=False),'start-ack.json')

def collect(client):
 rr,pf,vr,review=checked();_,_,ir,intent,ar,ack,jr=c.start_chain()
 c.require(intent['empty_secret_review_receipt']==ack['empty_secret_review_receipt']==vr['protected_receipt'],'model_isolation_chain_invalid')
 fr,first=c.load('execution-first-observation.json')
 c.require(first['start_ack_receipt']==ar['protected_receipt'] and first['preflight_receipt']==rr['protected_receipt'],'model_isolation_chain_invalid')
 registered=c.identity(client.call('GET',c.MI,'2023-01-31'));c.require(registered==intent['registered_identity'],'model_isolation_identity_invalid')
 roles=c.direct_roles(client,registered['principalId'],['acr','ai']);validate_job(client.call('GET',c.JOB,'2025-07-01'),intent['plan'],registered)
 run,inv=c.execution(client,intent,ack,'Succeeded');query=c.family_query(run)
 token=client.credential.get_token('https://api.loganalytics.io/.default')
 with client.session.post('https://api.loganalytics.io/v1/workspaces/5264a124-10cd-45f6-93a5-6d65c37e9bfb/query',
  json={'query':query},headers={'Authorization':'Bearer '+token.token},allow_redirects=False,timeout=(5,30),stream=True) as response:
  c.require(response.status_code==200,'model_isolation_logs_invalid');parts=[];size=0
  for part in response.iter_content(65536):
   size+=len(part);c.require(size<=2*1024*1024,'model_isolation_logs_invalid');parts.append(part)
  family=json.loads(b''.join(parts))
 record=c.validate_result(c.logs_response(family,run['name']))
 after,_=c.execution(client,intent,ack,'Succeeded');c.require(after==run,'model_isolation_execution_drift');live(pf,review)
 return c.save(dict(status='live_model_credential_isolation_verified',operation_id=c.worker.OPERATION,checked_at_utc=c.now(),
  preflight_receipt=rr['protected_receipt'],empty_secret_review_receipt=vr['protected_receipt'],job_accepted_receipt=jr['protected_receipt'],
  start_intent_receipt=ir['protected_receipt'],start_ack_receipt=ar['protected_receipt'],first_native_observation_receipt=fr['protected_receipt'],
  native_execution_before=run,native_execution_after=after,execution_inventory=inv,full_bounded_observed_family=family,
  query=query,worker_result=record,exact_direct_role_inventory=roles,model_credential_isolation_verified=True,
  late_log_delivery_excluded=False,zero_log_replicas_excluded=False,step_2_complete=False,step_3_started=False),'model-isolation-observation.json')

def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=('prepare','accept-job','start','collect'));a=p.parse_args()
 try:
  client=c.Transport();value={'prepare':prepare,'accept-job':accept_job,'start':start,'collect':collect}[a.command](client)
  print(json.dumps(dict(status=value['status'],protected_receipt=value['protected_receipt'])),flush=True)
 except Exception as e:
  print(json.dumps(dict(status='model_readback_unaccepted_inspect_only',reason=getattr(e,'code',type(e).__name__),automatic_retry=False)),flush=True);raise SystemExit(1) from None

if __name__=='__main__':main()
