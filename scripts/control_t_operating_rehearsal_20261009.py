"""Exact installed operating candidate, session-temporary DB writes only."""
import base64,copy,hashlib,json,sys,zlib
from pathlib import Path
import run_alpha_ms_recovery_operation as controller
from scan_ms_presentation_worker_candidate import verify
from prepare_t_operating_release_20261009 import ROOT,FOLDER as RELEASE
from ibkr_etl.alpha_world_t_presentation import build_model

def main(stage,kind):
 if kind not in ('daily','benchmark'):raise ValueError('Unknown kind')
 folder=RELEASE/kind/'postgres-v1';folder.mkdir(exist_ok=True);controller.FOLDER=folder
 if stage=='read':controller.read('runtime');return
 if stage!='start' or (folder/'runtime-intent.json').exists():raise ValueError('Use start once, then read')
 source,_,output=verify(RELEASE/kind)
 image=output['registry']+'/'+output['repository']+'@'+output['digest']
 scan=json.loads((RELEASE/kind/'scan-completed.json').read_bytes())
 if scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Exact scanned image required')
 release=json.loads((RELEASE/kind/'context/ALPHA_T_OPERATING_RELEASE.json').read_bytes())
 months=[]
 for path in sorted((RELEASE/kind/'context/tests/inputs').glob('*.json')):
  snapshot=json.loads(path.read_bytes());months.append({'period_end':snapshot['period_end'],'metrics':build_model(snapshot)['risk']})
 bundle={'sources':{},'hashes':{},'installed_candidate':True,'operating_candidate':kind,
  'installed_files_sha256':release['installed_files_sha256'],'months':months,'nav':snapshot['performance']['series'],
  'expected_subject_state':[{'subject_id':'alpha-world-t','lifecycle_status':'onboarding','base_currency':'HKD','reporting_currency':'HKD'}]}
 encoded=base64.b64encode(zlib.compress(json.dumps(bundle).encode())).decode()
 parts=[encoded[i:i+24000] for i in range(0,len(encoded),24000)]
 env={f'T_QUALIFICATION_{i}':v for i,v in enumerate(parts)}
 env.update(IBKR_RUN_MIGRATIONS='false',T_QUALIFICATION_PARTS=str(len(parts)))
 job_name='ibkrv2stg-derived-daily' if kind=='daily' else 'ibkrv2stg-benchmark-daily'
 path=controller.BASE+'Microsoft.App/jobs/'+job_name
 job=controller.request('GET',path+'?api-version=2024-03-01')
 runs=controller.request('GET',path+'/executions?api-version=2024-03-01')['value']
 if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Job busy')
 if job['properties']['template']['containers'][0]['image']!=source['base_image']:raise ValueError('Baseline drift')
 template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
 script=(ROOT/'scripts/qualify_t_postgres_20261009.py').read_bytes();container=template['containers'][0]
 container['image']=image;container['command']=['python'];container['args']=['-c',
  "import base64,zlib;exec(compile(zlib.decompress(base64.b64decode('"+base64.b64encode(zlib.compress(script)).decode()+"')),'t-operating-rehearsal','exec'))"]
 for key,value in env.items():container['env']=[e for e in container.get('env',[]) if e['name']!=key]+[{'name':key,'value':value}]
 controller.save('runtime-intent',{'job':job_name,'image':image,'job_invariant':controller.invariant(job),
  'source_sha256':hashlib.sha256(script).hexdigest(),'bundle_sha256':hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest(),
  'persistent_database_writes':0,'persistent_job_update':False})
 ack=controller.request('POST',path+'/start?api-version=2024-03-01',data=template)
 controller.save('runtime-dispatch',{'job':job_name,'execution':ack['name']})
 print(json.dumps({'execution':ack['name'],'saved_job_unchanged':True}))
if __name__=='__main__':main(sys.argv[1],sys.argv[2])
