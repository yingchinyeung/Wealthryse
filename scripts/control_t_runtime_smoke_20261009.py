"""Read-only startup and fallback qualification; no saved Azure job changes."""
import base64,copy,hashlib,json,sys,zlib
from pathlib import Path
import run_alpha_ms_recovery_operation as controller
from scan_ms_presentation_worker_candidate import verify

ROOT=Path(__file__).resolve().parents[1]
TARGETS={
 'web':('.work/alpha-t-runtime-release-20261009-v7/web','ibkrv2stg-onboard-render','ALPHA_T_RUNTIME_RELEASE.json'),
 'worker':('.work/alpha-t-runtime-release-20261009-v7/worker','ibkrv2stg-month-end-factsheet','ALPHA_T_RUNTIME_RELEASE.json'),
 'daily':('.work/alpha-t-operating-release-20261009-v4/daily','ibkrv2stg-derived-daily','ALPHA_T_OPERATING_RELEASE.json'),
 'benchmark':('.work/alpha-t-operating-release-20261009-v4/benchmark','ibkrv2stg-benchmark-daily','ALPHA_T_OPERATING_RELEASE.json'),
}
CHECK='''import hashlib,importlib,json,os,sys
from pathlib import Path
sys.path[:0]=['/app/src','/app/scripts']
for name,digest in BINDING['files_sha256'].items():
 if hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()!=digest:raise ValueError('Runtime source binding differs')
for name in BINDING['modules']:importlib.import_module(name)
from ibkr_etl.subject_onboarding import approved_reference_definitions,configuration_sha256
definitions=approved_reference_definitions()
if BINDING['mode']=='candidate' and configuration_sha256(definitions['alpha-world-t'])!='99b9d84e8118632b59dfd73fa64c08c05af409bb05a3daa34dd2fe78173f0209':raise ValueError('T HKD contract drift')
import psycopg
from psycopg.rows import dict_row
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=25,options='-c default_transaction_read_only=on -c statement_timeout=30000') as c:
 c.execute("SELECT set_config('app.tenant_id',%s,true)",('00000000-0000-0000-0000-000000000001',))
 subject=c.execute("SELECT lifecycle_status,base_currency,reporting_currency FROM platform_subjects WHERE subject_id='alpha-world-t'").fetchall()
 approved=c.execute("SELECT COUNT(*) AS n FROM subject_calculation_definitions WHERE subject_id='alpha-world-t' AND approval_status='approved' AND effective_to IS NULL").fetchone()['n']
 if subject!=[{'lifecycle_status':'onboarding','base_currency':'HKD','reporting_currency':'HKD'}] or approved:raise ValueError('T private pre-activation rollout condition no longer holds')
 if c.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only check failed')
 c.rollback()
print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'passed','kind':BINDING['kind'],'mode':BINDING['mode'],'image':BINDING['image'],'verified_files':len(BINDING['files_sha256']),'modules':BINDING['modules'],'persistent_database_writes':0,'normal_jobs_invoked':False,'T_remains_inactive_and_unapproved':True}),flush=True)
'''

def main(stage,kind,mode):
 if kind not in TARGETS or mode not in ('candidate','baseline'):raise ValueError('Unknown smoke scope')
 relative,job_name,manifest_name=TARGETS[kind];candidate=ROOT/relative
 folder=candidate/('smoke-'+mode+'-v1');folder.mkdir(exist_ok=True);controller.FOLDER=folder
 if stage=='read':controller.read('runtime');return
 if stage!='start' or (folder/'runtime-intent.json').exists():raise ValueError('Existing dispatch; use read')
 source,_,output=verify(candidate);image=output['registry']+'/'+output['repository']+'@'+output['digest']
 scan=json.loads((candidate/'scan-completed.json').read_bytes())
 if scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Exact candidate security gate failed')
 release=json.loads((candidate/'context'/manifest_name).read_bytes())
 modules={'web':['ibkr_etl.web'],
  'worker':['generate_month_end_package','render_onboarding_historical_reports','run_self_service_onboarding_reports','run_registry_month_end_packages'],
  'daily':['run_self_service_derived_refresh','build_risk_snapshots'],
  'benchmark':['refresh_provisional_benchmark']}[kind]
 binding={'kind':kind,'mode':mode,'image':image if mode=='candidate' else source['base_image'],
  'files_sha256':release['installed_files_sha256' if mode=='candidate' else 'baseline_files_sha256'],'modules':modules}
 path=controller.BASE+'Microsoft.App/jobs/'+job_name
 job=controller.request('GET',path+'?api-version=2024-03-01')
 runs=controller.request('GET',path+'/executions?api-version=2024-03-01')['value']
 if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Job busy; no overlapping probe')
 expected=json.loads((ROOT/TARGETS['worker'][0]/'source-intent.json').read_bytes())['base_image'] if kind=='web' else source['base_image']
 if job['properties']['template']['containers'][0]['image']!=expected:raise ValueError('Saved baseline drift')
 template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
 code=('BINDING='+repr(binding)+'\n'+CHECK).encode();container=template['containers'][0]
 container['image']=binding['image'];container['command']=['python'];container['args']=['-c',
  "import base64,zlib;exec(compile(zlib.decompress(base64.b64decode('"+base64.b64encode(zlib.compress(code)).decode()+"')),'t-read-only-startup','exec'))"]
 container['env']=[e for e in container.get('env',[]) if e['name']!='IBKR_RUN_MIGRATIONS']+[{'name':'IBKR_RUN_MIGRATIONS','value':'false'}]
 controller.save('runtime-intent',{'job':job_name,'image':binding['image'],'binding':binding,
  'job_invariant':controller.invariant(job),'source_sha256':hashlib.sha256(code).hexdigest(),'persistent_database_writes':0,'persistent_job_update':False})
 ack=controller.request('POST',path+'/start?api-version=2024-03-01',data=template)
 controller.save('runtime-dispatch',{'job':job_name,'execution':ack['name']})
 print(json.dumps({'execution':ack['name'],'mode':mode,'saved_job_unchanged':True}))
if __name__=='__main__':main(*sys.argv[1:])
