"""Build scoped operating candidates; no saved job or account updates."""
from datetime import datetime,timezone
import json,os,re,subprocess,sys
import build_ms_presentation_worker_candidate as build
from prepare_t_operating_release_20261009 import FOLDER,IMAGES

JOBS={'daily':('ibkrv2stg-derived-daily','ibkrv2stg-risk-snapshots'),
      'benchmark':('ibkrv2stg-benchmark-daily',)}

def main(stage,kind):
 if kind not in IMAGES:raise ValueError('Unknown job kind')
 folder=FOLDER/kind
 source=json.loads((folder/'source-intent.json').read_bytes())
 proof=json.loads((folder/'local-qualification.json').read_bytes())
 if proof['source_intent_sha256']!=build.sha(folder/'source-intent.json'):raise ValueError('Qualification source drift')
 actual={p.relative_to(folder/'context').as_posix():build.sha(p) for p in (folder/'context').rglob('*') if p.is_file()}
 if actual!=source['files_sha256']:raise ValueError('Context drift')
 if build.az('account','show','--query','id')!=build.SUB:raise ValueError('Wrong subscription')
 if stage in ('inspect','reconcile'):
  old=sys.argv;sys.argv=[old[0],stage,'--folder',str(folder)]+(['--run-id',old[3]] if stage=='reconcile' else [])
  try:build.main()
  finally:sys.argv=old
  return
 if stage=='log':
  native=json.loads((folder/'build-completed.json').read_bytes())
  response=subprocess.run([build.AZ,'-m','azure.cli','acr','task','logs','--subscription',build.SUB,'-r',build.REGISTRY,'--run-id',native['run']],capture_output=True,text=True,encoding='utf-8',timeout=120,env={**os.environ,'PYTHONUTF8':'1'})
  log=response.stdout+response.stderr
  if response.returncode or native['native_status']!='Succeeded' or 'PASS: scoped T operating candidate' not in log or '\nFAILED (' in log or re.findall(r'Ran (\d+) tests',log)!=[str(proof['tests_passed'])]:raise ValueError('Native qualification did not match local acceptance')
  with (folder/'native-build.log').open('x',encoding='utf-8') as stream:stream.write(log)
  build.save(folder,'native-build-qualified',{'qualified':True,'run':native['run'],'tests_passed':proof['tests_passed'],'native_log_sha256':build.sha(folder/'native-build.log')})
  print(json.dumps({'kind':kind,'native_tests_passed':proof['tests_passed']}));return
 if stage!='build':raise ValueError('Expected build/reconcile/inspect/log')
 if (folder/'build-intent.json').exists():raise ValueError('Existing dispatch; reconcile instead')
 jobs={}
 for name in JOBS[kind]:
  job=build.az('containerapp','job','show','--subscription',build.SUB,'-g',build.GROUP,'-n',name)
  if job['properties']['template']['containers'][0]['image']!=source['base_image']:raise ValueError('Installed baseline differs: '+name)
  jobs[name]={'image':source['base_image'],'settings_invariant_sha256':build.invariant(job)}
 build.save(folder,'build-intent',{'tag':source['tag'],'source_intent_sha256':build.sha(folder/'source-intent.json'),
  'local_qualification_sha256':build.sha(folder/'local-qualification.json'),'before_jobs':jobs,
  'prepared_at':datetime.now(timezone.utc).isoformat(),'running_service_update':False,'database_writes':0,'reports_released':False})
 ack=build.az('acr','build','--subscription',build.SUB,'-r',build.REGISTRY,'-t',source['tag'],'-f',str(folder/'context/Dockerfile'),'--no-logs','--no-wait',str(folder/'context'))
 if not ack or not ack.get('runId'):
  print(json.dumps({'kind':kind,'dispatch_acknowledgement_missing':True,'action':'reconcile existing native run; never redispatch'}));return
 build.save(folder,'build-ack',{'run':ack['runId'],'tag':source['tag']})
 print(json.dumps({'kind':kind,'run':ack['runId'],'saved_jobs_unchanged':True}))

if __name__=='__main__':main(sys.argv[1],sys.argv[2])
