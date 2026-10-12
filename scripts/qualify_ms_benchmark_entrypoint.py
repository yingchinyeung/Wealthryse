"""Credential-free native entrypoint qualification on the existing scratch job."""
import argparse
import copy
from pathlib import Path

import continue_ms_website_release_rollback as continuation

r=continuation.original
p=r.p
STAGE=r.FOLDER/'benchmark-entrypoint-v1'
CODE=r.CHECK+'''
import runpy
from unittest.mock import patch
from ibkr_etl import db
with patch.object(db,'connect',side_effect=AssertionError('Persistent database prohibited')):
 namespace=runpy.run_path('/app/scripts/refresh_provisional_benchmark.py',run_name='private_entrypoint_check')
if namespace['refresh_and_materialize'] is not provisional_benchmark.refresh_and_materialize:
 raise ValueError('Normal entrypoint does not use qualified producer')
events=[]
class Rows:
 def __init__(self,rows):self.rows=rows
 def fetchall(self):return self.rows
 def fetchone(self):return self.rows[0] if self.rows else None
class Connection:
 def execute(self,sql,parameters=None):
  if 'subject_operating_registrations' in sql:
   for policy in ("r.capability = 'benchmark_refresh'","r.operating_state = 'active'","r.effective_to IS NULL","s.lifecycle_status = 'active'"):
    if policy not in sql:raise ValueError('Active registration gating differs')
   return Rows([{'subject_id':'alpha-world-ms'},{'subject_id':'U12345678'}])
  if 'SELECT subject_type FROM platform_subjects' in sql:
   return Rows([{'subject_type':'composite_portfolio' if parameters[0]=='alpha-world-ms' else 'physical_account'}])
  if 'account_onboarding_approvals' in sql:return Rows([])
  raise ValueError('Unexpected entrypoint query')
 def close(self):events.append('closed')
def refresh(connection,account_id,start_date):
 events.append(account_id)
 return {'account_id':account_id,'benchmark_observations':2,'benchmarks':[]}
globals_=namespace['main'].__globals__
with patch.dict(os.environ,{'DEPLOYMENT_ENVIRONMENT':'staging','ALLOW_UNLICENSED_BENCHMARK':'true',
 'DATABASE_URL':'postgresql://private-fixture','BENCHMARK_ACCOUNT_IDS':'','BENCHMARK_START_DATE':'2025-01-20'}),patch.dict(globals_,{
 'connect':lambda *a:Connection(),'refresh_and_materialize':refresh,'start_run':lambda *a:'private-run',
 'finish_run_success':lambda *a,**k:events.append('success'),
 'finish_run_failure':lambda *a,**k:events.append('failure')}):
 if namespace['main']()!=0:raise ValueError('Entrypoint failed')
if events!=['alpha-world-ms','U12345678','success','closed']:
 raise ValueError('Entrypoint processing/close differs')
print('MS_BENCHMARK_NATIVE_ENTRYPOINT_PASSED',flush=True)
'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','dispatch','inspect'));args=parser.parse_args()
    base=p.release.read(r.FOLDER,'baseline');p.verify_protected(base);r.live_unchanged(base)
    _,image=r.qualified.image_gate()
    job=r.live('benchmark');r.scope(job,'benchmark')
    if job['properties']['template']['containers'][0]['image']!=image:raise ValueError('Candidate scratch image required')
    if continuation.non_image(job)!=r.read('benchmark-baseline')['non_image_sha256']:
        raise ValueError('Scratch settings drift')
    template=copy.deepcopy(job['properties']['template']);template['containers'][0]['args']=['-c',CODE]
    if args.stage=='prepare':
        if STAGE.exists():raise ValueError('Preserve prior qualification')
        STAGE.mkdir()
        intent={'template':template,'image':image,'controller_sha256':p.sha(Path(__file__)),
                'persistent_database_connections':0,'outbound_market_requests':0,'live_mutations':0}
        p.release.build.save(STAGE,'intent',{**intent,'protected_receipt':p.retain(intent,'ms-benchmark-entrypoint-v1-intent')})
        print('ENTRYPOINT_PREPARED',flush=True);return
    intent=p.release.read(STAGE,'intent');p.verify_protected(intent)
    if intent['template']!=template or intent['controller_sha256']!=p.sha(Path(__file__)):
        raise ValueError('Frozen entrypoint check changed')
    if args.stage=='dispatch':
        if (STAGE/'dispatch-intent.json').exists():raise ValueError('Inspect rather than redispatch')
        runs=p.request('GET',r.path('benchmark')+'/executions'+p.release.API).get('value',[])
        if any(x['properties']['status'] in r.old.ACTIVE for x in runs):raise ValueError('Scratch busy')
        p.release.build.save(STAGE,'dispatch-intent',{'image':image})
        ack=p.request('POST',r.path('benchmark')+'/start'+p.release.API,data=template)
        if not ack.get('name'):raise ValueError('Unknown dispatch; do not repeat')
        p.release.build.save(STAGE,'dispatch',{'execution':ack['name']})
        print(ack['name'],flush=True);return
    name=p.release.read(STAGE,'dispatch')['execution']
    run=p.request('GET',r.path('benchmark')+'/executions/'+name+p.release.API)
    print(run['properties']['status'],flush=True)
    if run['properties']['status']!='Succeeded':return
    c=run['properties']['template']['containers']
    if len(c)!=1 or any(c[0].get(k)!=template['containers'][0].get(k) for k in ('image','command','args')):
        raise ValueError('Executed entrypoint check differs')
    if any(e.get('secretRef') or e['name'] not in r.AUTH_ENV|{'MS_ROLLBACK_KIND'} for e in c[0]['env']):
        raise ValueError('Unexpected execution credentials')
    proof={'status':'native_benchmark_entrypoint_passed','execution':name,'image':image,
           'intent_sha256':p.sha(STAGE/'intent.json'),'persistent_database_connections':0,
           'outbound_market_requests':0,'live_mutations':0,'registration_selector_preserved':True}
    p.release.build.save(STAGE,'qualified',{**proof,'protected_receipt':p.retain(proof,'ms-benchmark-entrypoint-v1-passed')})


if __name__=='__main__':main()
