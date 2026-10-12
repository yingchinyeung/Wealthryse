"""Read installed daily-job sources without running their normal entrypoints."""
import base64,copy,hashlib,json,sys,zlib
from pathlib import Path
import run_alpha_ms_recovery_operation as c

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-daily-baselines-20261009-v1'
TARGETS={
 'analytics':('ibkrv2stg-onboard-analytics','46288cdcfaa511e9a84a1403d2d65f9dd5484317e5aaa3ac15d755087ed7e62c'),
 'snapshot':('ibkrv2stg-report-snapshot','43e4f93e80e5521de275bd2877fb103e4157c46719a26958edb9ddd8a6709987'),
}
PROBE='''import base64,hashlib,json,os
from pathlib import Path
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
files={}
for parent in (Path('/app/src/ibkr_etl'),Path('/app/scripts')):
 for p in (parent.rglob('*') if parent.name=='ibkr_etl' else parent.glob('*')):
  if p.is_file() and not p.is_symlink() and p.suffix in ('.py','.mjs') and '__pycache__' not in p.parts:
   if p.stat().st_size>4000000:raise ValueError('Oversized source')
   files[p.relative_to('/app').as_posix()]=base64.b64encode(p.read_bytes()).decode()
if not files:raise ValueError('No app sources')
binding=json.loads(os.environ['T_BASELINE_PROBE'])
raw=json.dumps({'image':binding['image'],'files':files},sort_keys=True).encode()
service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
client=service.get_blob_client('internal-reports',binding['blob'])
client.upload_blob(raw,overwrite=False)
if client.download_blob().readall()!=raw:raise ValueError('Readback differs')
print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'passed','image':binding['image'],'files':len(files),'blob':binding['blob'],'sha256':hashlib.sha256(raw).hexdigest(),'database_connected':False}),flush=True)
'''

def start(kind):
 FOLDER.mkdir(exist_ok=True);c.FOLDER=FOLDER
 if (FOLDER/(kind+'-intent.json')).exists():raise ValueError('Reconcile existing dispatch')
 source_job,digest=TARGETS[kind]
 source=c.request('GET',c.BASE+'Microsoft.App/jobs/'+source_job+'?api-version=2024-03-01')
 image=source['properties']['template']['containers'][0]['image']
 if not image.endswith('@sha256:'+digest):raise ValueError('Source baseline drift')
 # The older analytics job has no managed storage identity. Reuse the
 # existing report runner for a one-shot read of that exact source image.
 # No identity, permission, or persistent image setting is changed.
 job_name='ibkrv2stg-onboard-render' if kind=='analytics' else source_job
 path=c.BASE+'Microsoft.App/jobs/'+job_name
 job=c.request('GET',path+'?api-version=2024-03-01')
 runs=c.request('GET',path+'/executions?api-version=2024-03-01')['value']
 if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Job busy')
 template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
 container=template['containers'][0]
 container['image']=image
 container['command']=['python'];container['args']=['-c',"import base64,zlib;exec(zlib.decompress(base64.b64decode('"+base64.b64encode(zlib.compress(PROBE.encode())).decode()+"')))"]
 binding={'image':image,'blob':'review/alpha-world-t/'+FOLDER.name.removeprefix('alpha-t-')+'/'+kind+'.json'}
 for key,value in {'IBKR_RUN_MIGRATIONS':'false','T_BASELINE_PROBE':json.dumps(binding)}.items():
  container['env']=[e for e in container.get('env',[]) if e['name']!=key]+[{'name':key,'value':value}]
 c.save(kind+'-intent',{'job':job_name,'source_job':source_job,'image':image,'job_invariant':c.invariant(job),'source_sha256':hashlib.sha256(PROBE.encode()).hexdigest(),'binding':binding})
 ack=c.request('POST',path+'/start?api-version=2024-03-01',data=template)
 c.save(kind+'-dispatch',{'job':job_name,'execution':ack['name']})
 print(json.dumps({'kind':kind,'execution':ack['name'],'saved_job_changed':False}))

if __name__=='__main__':
 action,kind=sys.argv[1:]
 if kind=='analytics':FOLDER=ROOT/'.work/alpha-t-daily-baselines-20261009-v4'
 if action=='start':start(kind)
 elif action=='read':c.FOLDER=FOLDER;c.read(kind)
 elif action=='diagnose':
  ack=json.loads((FOLDER/(kind+'-dispatch.json')).read_bytes())
  w=c.request('GET',c.BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs?api-version=2023-09-01')
  query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+ack['execution']+"' | where Log_s startswith 'KeyError:' or Log_s startswith 'ModuleNotFoundError:' or Log_s startswith 'ValueError:' or Log_s startswith 'azure.core.exceptions.' | project Log_s"
  print(json.dumps(c.az('monitor','log-analytics','query','-w',w['properties']['customerId'],'--analytics-query',query)))
 elif action=='download':
  result=json.loads((FOLDER/(kind+'-result.json')).read_bytes())
  if result['status']!='Succeeded' or not result['job_settings_unchanged']:raise ValueError('Probe did not succeed')
  record=result['records'][0];destination=FOLDER/(kind+'-source.json')
  c.az('storage','blob','download','--account-name','ibkrv2stgauhecwc4itaoo','--container-name','internal-reports','--name',record['blob'],'--file',str(destination),'--auth-mode','login','--overwrite','false','--only-show-errors')
  raw=destination.read_bytes()
  if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('Source receipt differs')
  bundle=json.loads(raw)
  if bundle['image']!=record['image']:raise ValueError('Image binding differs')
  runtime=FOLDER/kind/'local-runtime'
  for name,value in bundle['files'].items():
   p=Path(name)
   if p.is_absolute() or '..' in p.parts or p.parts[0] not in ('src','scripts') or p.suffix not in ('.py','.mjs'):raise ValueError('Unexpected source path')
   output=runtime/p;output.parent.mkdir(exist_ok=True,parents=True)
   with output.open('xb') as f:f.write(base64.b64decode(value,validate=True))
  print(json.dumps({'kind':kind,'verified_files':len(bundle['files']),'image':bundle['image']}))
 else:raise ValueError('Expected start/read')
