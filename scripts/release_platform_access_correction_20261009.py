"""Scoped website correction. Frozen source, local/Linux tests and scan gates.

Build commands do not change running services. Deployment is a separate stage.
"""
import hashlib,json,os,re,shutil,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
import build_ms_presentation_worker_candidate as build
from deploy_metadata_monitor import request,BASE

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/platform-corrections-20261009-v1'
WEB=FOLDER/'web-v2'
PARENT=ROOT/'.work/alpha-t-download-names-20261009-v2/web'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:1226d2e0ef97739f2b9f380e8eb153848e98c7cf1b3a1ec81353c3e9fc25eb90'
MARKER='PASS: owner-only subject-scoped snapshot export; report bytes unchanged'
FILES=('src/ibkr_etl/web.py','src/ibkr_etl/metrics_guide.py')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_bytes())
def save(p,obj):
    with p.open('x',encoding='utf-8') as f:json.dump(obj,f,indent=2)
def manifest(folder):return {p.relative_to(folder).as_posix():sha(p) for p in folder.rglob('*') if p.is_file() and p.suffix!='.pyc'}
def prepare():
    if WEB.exists():raise ValueError('Preserve prepared candidate')
    runtime=WEB/'local-runtime';context=WEB/'context'
    shutil.copytree(PARENT/'local-runtime',runtime,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in FILES:shutil.copyfile(FOLDER/'runtime'/name,runtime/name)
    shutil.copytree(PARENT/'context/tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(FOLDER/'tests/test_snapshot_access_correction.py',context/'tests/test_snapshot_access_correction.py')
    for name in FILES:
        (context/name).parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(runtime/name,context/name)
    for name in ('scripts/release_internal_reports.py','scripts/alpha_t_presentation_runtime.mjs'):
        (context/name).parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(runtime/name,context/name)
    release=read(PARENT/'context/ALPHA_T_RUNTIME_RELEASE.json')
    expected={name:sha(PARENT/'local-runtime'/name) for name in FILES}
    for name in FILES:release['installed_files_sha256'][name]=sha(context/name)
    release['base_image']=IMAGE
    release['scope']='Owner-only subject-scoped frozen snapshot export and derived NAV guide wording'
    save(context/'ALPHA_T_RUNTIME_RELEASE.json',release)
    check="import pathlib,hashlib; expected="+repr(expected)+"; assert all(hashlib.sha256((pathlib.Path('/app')/p).read_bytes()).hexdigest()==h for p,h in expected.items())"
    docker='FROM '+IMAGE+'\nRUN '+json.dumps(['python','-c',check])+'\n'
    for name in FILES:docker+='COPY --chown=10001:10001 '+name+' /app/'+name+'\n'
    docker+='COPY --chown=10001:10001 ALPHA_T_RUNTIME_RELEASE.json /app/\nCOPY --chown=10001:10001 tests/ /app/platform-correction-tests/\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\n'
    tests="import os,subprocess,sys; os.environ['PORTAL_TEST_RUNTIME']='/app'; os.environ['PYTHONPATH']='/app/src:/app/scripts'; sys.exit(subprocess.call([sys.executable,'-m','unittest','discover','-s','/app/platform-correction-tests','-v']))"
    docker+='RUN '+json.dumps(['python','-c',tests])+'\n'
    docker+='RUN '+json.dumps(['python','-c','print('+repr(MARKER)+')'])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    save(WEB/'source-intent.json',{'tag':'ibkr-platform:platform-access-correction-20261009-v2','base_image':IMAGE,
      'scope':release['scope'],'files_sha256':manifest(context),'baseline_files_sha256':expected,
      'report_calculation_and_renderer_changes':False})
    print('Prepared isolated website candidate')
def qualify():
    runtime=WEB/'local-runtime'; context=WEB/'context'
    env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','PORTAL_TEST_RUNTIME':str(runtime),
      'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts',context/'tests'))),
      'TMP':str(ROOT/'.work/t-test-temp'),'TEMP':str(ROOT/'.work/t-test-temp')}
    run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],env=env,cwd=runtime,capture_output=True,text=True,encoding='utf-8',timeout=240)
    (WEB/'local-tests.log').write_text(run.stdout+run.stderr,encoding='utf-8')
    if run.returncode:print(run.stderr[-5000:]);raise ValueError('Local qualification failed')
    count=int(re.findall(r'Ran (\d+) tests',run.stderr)[-1])
    save(WEB/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':count,
      'source_intent_sha256':sha(WEB/'source-intent.json'),'log_sha256':sha(WEB/'local-tests.log')})
    print(run.stderr[-250:])
def dispatch():
    source=read(WEB/'source-intent.json');qualified=read(WEB/'local-qualification.json')
    if manifest(WEB/'context')!=source['files_sha256'] or qualified['source_intent_sha256']!=sha(WEB/'source-intent.json'):raise ValueError('Qualification drift')
    if build.az('account','show','--query','id')!=build.SUB:raise ValueError('Wrong subscription')
    live=request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web?api-version=2024-03-01')
    if live['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Website changed; rebase required')
    save(WEB/'build-intent.json',{'tag':source['tag'],'source_intent_sha256':sha(WEB/'source-intent.json'),
      'local_qualification_sha256':sha(WEB/'local-qualification.json'),'before_jobs':{},'before_website_image':IMAGE,
      'prepared_at':datetime.now(timezone.utc).isoformat(),'running_service_update':False,'database_writes':0})
    result=build.az('acr','build','--subscription',build.SUB,'-r',build.REGISTRY,'-t',source['tag'],'-f',str(WEB/'context/Dockerfile'),'--no-logs','--no-wait',str(WEB/'context'))
    if not result or not result.get('runId'):raise ValueError('Unknown build outcome; reconcile, never retry')
    save(WEB/'build-ack.json',{'run':result['runId'],'tag':source['tag']})
    print(json.dumps({'run':result['runId'],'production_changed':False}))
def inspect():
    ack=read(WEB/'build-ack.json');source=read(WEB/'source-intent.json')
    run=build.az('acr','task','show-run','-r',build.REGISTRY,'--run-id',ack['run'])
    print(json.dumps({'run':ack['run'],'status':run['status']}))
    if run['status']!='Succeeded':return
    outputs=run.get('outputImages') or []
    if len(outputs)!=1 or outputs[0]['registry']!=build.REGISTRY+'.azurecr.io' or outputs[0]['repository']!='ibkr-platform' or outputs[0]['tag']!=source['tag'].split(':')[1]:raise ValueError('Unexpected output image')
    save(WEB/'build-completed.json',{'run':ack['run'],'native_status':run['status'],'output_images':outputs,'source_intent_sha256':sha(WEB/'source-intent.json')})
def reconcile():
    intent=read(WEB/'build-intent.json');run_id=sys.argv[2]
    run=build.az('acr','task','show-run','-r',build.REGISTRY,'--run-id',run_id)
    created=datetime.fromisoformat(run['createTime'].replace('Z','+00:00'))
    elapsed=(created-datetime.fromisoformat(intent['prepared_at'])).total_seconds()
    outputs=run.get('outputImages') or []
    if run['status']!='Succeeded':
        print(json.dumps({'run':run_id,'status':run['status'],'reconciled':False}));return
    if not 0<=elapsed<=120 or len(outputs)!=1 or outputs[0]['tag']!=intent['tag'].split(':')[1] or outputs[0]['repository']!='ibkr-platform' or outputs[0]['registry']!=build.REGISTRY+'.azurecr.io':raise ValueError('Run identity does not match frozen intent')
    save(WEB/'build-ack.json',{'run':run_id,'tag':intent['tag'],'reconciled_after_missing_cli_ack':True})
    inspect()
def logs():
    ack=read(WEB/'build-ack.json');run=read(WEB/'build-completed.json')
    response=subprocess.run([build.AZ,'-m','azure.cli','acr','task','logs','-r',build.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120,env={**os.environ,'PYTHONUTF8':'1'})
    log=response.stdout+response.stderr
    count=read(WEB/'local-qualification.json')['tests_run']
    if response.returncode or MARKER not in log or not re.search(r'Ran '+str(count)+r' tests\b',log) or '\nFAILED (' in log:raise ValueError('Linux qualification failed')
    with (WEB/'native-build.log').open('x',encoding='utf-8') as f:f.write(log)
    save(WEB/'native-build-qualified.json',{'qualified':True,'run':ack['run'],'linux_tests':count,'native_log_sha256':sha(WEB/'native-build.log')})
    print(json.dumps({'native_tests':count,'qualified':True}))
if __name__=='__main__':
    {'prepare':prepare,'qualify':qualify,'build':dispatch,'inspect':inspect,'logs':logs,'reconcile':reconcile}[sys.argv[1]]()
