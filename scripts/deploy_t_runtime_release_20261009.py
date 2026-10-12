"""Scoped image-only T runtime cutover while T remains inactive.

No financial-source, definition approval, report registration or viewer writes.
Each apply is separately journaled. An unknown outcome must be verified, never
blindly repeated. The original full definitions are retained privately.
"""
import copy,json,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import build_ms_presentation_worker_candidate as build
from deploy_metadata_monitor import BASE,request
from deploy_ms_headers_v14r15 import web_hash,clean
from deploy_ms_presentation_worker_release import settings_invariant
from scan_ms_presentation_worker_candidate import verify
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected
from control_t_runtime_smoke_20261009 import TARGETS

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-runtime-cutover-20261009-v1'
API='?api-version=2024-03-01'
TARGET_KINDS={'ibkrv2stg-web':'web','ibkrv2stg-onboard-render':'worker',
 'ibkrv2stg-month-end-factsheet':'worker','ibkrv2stg-derived-daily':'daily',
 'ibkrv2stg-risk-snapshots':'daily','ibkrv2stg-benchmark-daily':'benchmark'}
WEB=BASE+'Microsoft.App/containerApps/ibkrv2stg-web'
REVISION='alpha-t-hkd-1009-v7'

def read(path):return json.loads(path.read_bytes())
def save(name,record):
 build.save(FOLDER,name,{**record,'protected_receipt':retain(record,'alpha-t-cutover-'+name)})
def protected(record):
 archived,_=restore_protected(record['protected_receipt'])
 if archived!={k:v for k,v in record.items() if k!='protected_receipt'}:raise ValueError('Protected record differs')
def passed(path):
 r=read(path)
 if r['status']!='Succeeded' or not r['job_settings_unchanged'] or len(r['records'])!=1 or r['records'][0]['status']!='passed':raise ValueError('Incomplete execution: '+str(path))
 return r['records'][0]
def safe_window():
 now=datetime.now(timezone.utc);minute=now.hour*60+now.minute
 if 8*60<=minute<9*60+30:raise ValueError('Do not cut over near scheduled reporting/calculation jobs')
def idle(name):
 if name=='ibkrv2stg-web':return
 target=BASE+'Microsoft.App/jobs/'+name+'/executions';url=target+API
 for _ in range(20):
  data=request('GET',url)
  if any(r['properties']['status'] not in ('Succeeded','Failed','Stopped','Canceled','Cancelled') for r in data.get('value',[])):raise ValueError('Active/unknown execution: '+name)
  link=data.get('nextLink')
  if not link:return
  parsed=urlsplit(link)
  if parsed.scheme!='https' or parsed.netloc!='management.azure.com' or parsed.path.casefold()!=target.casefold():raise ValueError('Unexpected pagination')
  url=parsed.path+'?'+parsed.query
 raise ValueError('Execution history exceeds bound')
def gates():
 result={}
 for kind,(relative,_,_) in TARGETS.items():
  folder=ROOT/relative;source,_,output=verify(folder)
  image=output['registry']+'/'+output['repository']+'@'+output['digest']
  scan=read(folder/'scan-completed.json');archived,_=restore_protected(read(folder/'scan-protected-receipt.json'))
  if scan!=archived or scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Exact security gate failed')
  fallback=passed(folder/'smoke-baseline-v1/runtime-result.json')
  if fallback['image']!=source['base_image'] or not fallback['T_remains_inactive_and_unapproved']:raise ValueError('Fallback/state gate failed')
  if kind in ('web','worker'):
   candidate=passed(folder/'smoke-candidate-v1/runtime-result.json')
   if candidate['image']!=image:raise ValueError('Candidate startup differs')
  else:
   database=passed(folder/'postgres-v1/runtime-result.json')
   if database['months']!=21 or not database['persistent_state_unchanged'] or database['database_persistent_writes']:raise ValueError('Operating DB rehearsal failed')
   if kind=='benchmark' and database['operating_benchmark']['secondary_failure_rollback']!='passed':raise ValueError('Atomic pair rollback failed')
  result[kind]={'image':image,'base_image':source['base_image'],'source_intent_sha256':build.sha(folder/'source-intent.json'),'scan_sha256':build.sha(folder/'scan-completed.json')}
 database=passed(ROOT/'.work/alpha-t-candidate-rehearsal-20261009-v3/runtime-result.json')
 if not database['onboarding_acceptance']['rollback_verified'] or not database['persistent_state_unchanged']:raise ValueError('Report/acceptance DB rollback failed')
 reports=passed(ROOT/'.work/alpha-t-candidate-reports-20261009-v4/reports-result.json')
 download=read(ROOT/'.work/alpha-t-candidate-reports-20261009-v4/download-verified.json')
 if reports['months']!=21 or download['count']!=42 or download['image']!=result['worker']['image'] or download['proof_sha256']!=reports['proof']['sha256']:raise ValueError('Private all-month report acceptance failed')
 return result

def main(stage,name=None):
 if build.az('account','show','--query','id')!=build.SUB:raise ValueError('Wrong subscription')
 if stage=='prepare':
  if FOLDER.exists():raise ValueError('Plan exists; do not replace')
  safe_window();images=gates();targets={}
  for target,kind in TARGET_KINDS.items():
   idle(target);path=WEB if kind=='web' else BASE+'Microsoft.App/jobs/'+target
   before=request('GET',path+API);props=before['properties'];containers=props['template']['containers']
   if len(containers)!=1 or containers[0]['image']!=images[kind]['base_image']:raise ValueError('Live baseline drift: '+target)
   if any(s.get('value') for s in props['configuration'].get('secrets',[])):raise ValueError('Do not archive inline secret values')
   if kind=='web' and props['configuration']['activeRevisionsMode']!='Single':raise ValueError('Expected preserved Single revision policy')
   template=clean(copy.deepcopy(props['template']))
   for item in template['containers']:
    for env in item.get('env',[]):
     if env.get('secretRef') and env.get('value')=='':env.pop('value')
   rollback=copy.deepcopy(template);template['containers'][0]['image']=images[kind]['image']
   if kind=='web':template['revisionSuffix']=REVISION;rollback['revisionSuffix']='alpha-t-hkd-rollback-1009'
   targets[target]={'path':path,'kind':kind,'before':before,'base_image':images[kind]['base_image'],
    'image':images[kind]['image'],'invariant':web_hash(before) if kind=='web' else settings_invariant(before),
    'apply_body':{'properties':{'template':template}},'rollback_body':{'properties':{'template':rollback}}}
  auth=request('GET',WEB+'/authConfigs/current'+API)
  FOLDER.mkdir();save('plan',{'targets':targets,'images':images,'auth_before':auth,
   'authorization':'User requested completion of Alpha World T onboarding on their behalf using the approved HKD setup.',
   'database_writes':0,'report_approvals_changed':False,'viewer_access_changed':False,
   'scope':'Image-only infrastructure readiness; T remains inactive and its calculation definition unapproved.'})
  print(json.dumps({'prepared':True,'targets':len(targets),'deployed':False}));return
 plan=read(FOLDER/'plan.json');protected(plan)
 if stage=='seal':
  for target,data in plan['targets'].items():
   proof=read(FOLDER/(target+'-verified.json'));protected(proof)
   if proof['image']!=data['image'] or proof['status']!='passed':raise ValueError('Target acceptance incomplete')
  save('completion',{'status':'runtime_images_deployed_T_inactive','targets':{n:r['image'] for n,r in plan['targets'].items()},
   'database_writes':0,'report_registration_changed':False,'report_approvals_changed':False,'viewer_access_activated':False})
  print(json.dumps({'status':'runtime_images_deployed_T_inactive','targets':6}));return
 if name not in plan['targets'] or stage not in ('apply','verify','rollback'):raise ValueError('Unknown target/stage')
 data=plan['targets'][name];is_web=data['kind']=='web';current=request('GET',data['path']+API)
 invariant=web_hash(current) if is_web else settings_invariant(current)
 if invariant!=data['invariant']:raise ValueError('Non-image settings drifted; stop')
 if request('GET',WEB+'/authConfigs/current'+API)['properties']!=plan['auth_before']['properties']:raise ValueError('Authentication drifted; stop')
 actual=current['properties']['template']['containers'][0]['image']
 if stage in ('apply','rollback'):
  safe_window();idle(name)
  if stage=='apply':
   if gates()!=plan['images']:raise ValueError('Bound release gates changed')
  expected=data['base_image'] if stage=='apply' else data['image']
  if actual!=expected:raise ValueError('Expected pre-mutation image differs; reconcile')
  body=data['apply_body'] if stage=='apply' else data['rollback_body']
  save(name+'-'+stage+'-intent',{'plan_sha256':build.sha(FOLDER/'plan.json'),'from_image':actual,'to_image':body['properties']['template']['containers'][0]['image'],'body':body})
  ack=request('PATCH',data['path']+API,data=body)
  save(name+'-'+stage+'-ack',{'id':ack.get('id'),'provisioning_state':ack.get('properties',{}).get('provisioningState')})
  print(json.dumps({'submitted':name,'operation':stage,'image_only':True}));return
 if actual!=data['image']:raise ValueError('Installed image differs')
 props=current['properties']
 if props['provisioningState']!='Succeeded':print(json.dumps({'waiting':name,'state':props['provisioningState']}));return
 if is_web:
  revision=props['latestReadyRevisionName']
  if revision!='ibkrv2stg-web--'+REVISION:print(json.dumps({'waiting':name,'ready_revision':revision}));return
  healthy=request('GET',WEB+'/revisions/'+revision+API)['properties']
  if healthy['healthState']!='Healthy' or not healthy['active'] or healthy['runningState'] not in ('Running','RunningAtMaxScale'):raise ValueError('New website revision unhealthy')
  traffic=props['configuration']['ingress']['traffic']
  if len(traffic)!=1 or traffic[0]['weight']!=100 or traffic[0].get('revisionName')!=revision:raise ValueError('Traffic differs')
 save(name+'-verified',{'status':'passed','image':actual,'settings_preserved':True,'authentication_preserved':True})
 print(json.dumps({'verified':name,'settings_preserved':True}))

if __name__=='__main__':main(*sys.argv[1:])
