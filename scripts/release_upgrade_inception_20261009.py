"""Build a one-module risk-worker correction; never deploy implicitly."""
import json,sys,shutil,os,subprocess,re
from datetime import datetime,timezone
import release_platform_access_correction_20261009 as r
from deploy_viewer_private_admin_20261008 import request
from deploy_metadata_monitor import BASE
from deploy_ms_presentation_worker_release import settings_invariant

ROOT=r.ROOT; FOLDER=ROOT/'.work/platform-upgrade-20261009-stage8/risk-inception-v1'
PRIOR=ROOT/'.work/platform-upgrade-20261009-stage7/risk-release-v1'
FILE='src/ibkr_etl/risk_refresh.py'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:b95e81e28dcb8429563143cf5a7001cd1af4c2acad5a660cbf807ff85c18815f'
JOB='ibkrv2stg-risk-snapshots'; PATH=BASE+'Microsoft.App/jobs/'+JOB+'?api-version=2024-03-01'
r.WEB=FOLDER;r.IMAGE=IMAGE;r.MARKER='PASS: bounded MS insufficient-inception history; later failures preserved'

def prepare():
    context=FOLDER/'context'
    if context.exists():raise ValueError('Preserve frozen candidate')
    before,after=r.manifest(PRIOR/'runtime'),r.manifest(FOLDER/'runtime')
    if {n for n in set(before)|set(after) if before.get(n)!=after.get(n)}!={FILE}:raise ValueError('Only risk_refresh may change')
    shutil.copytree(PRIOR/'context/tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(FOLDER/'test_inception_boundary.py',context/'tests/test_inception_boundary.py')
    shutil.copyfile(ROOT/'tests/test_risk_refresh.py',context/'tests/test_risk_refresh.py')
    (context/FILE).parent.mkdir(parents=True)
    shutil.copyfile(FOLDER/'runtime'/FILE,context/FILE)
    record=r.read(PRIOR/'context/ALPHA_T_OPERATING_RELEASE.json')
    record['installed_files_sha256'][FILE]=r.sha(context/FILE)
    record['base_image']=IMAGE;record['scope']='Risk-worker only: expected unavailable MS inception history; no financial calculations or reports changed'
    r.save(context/'ALPHA_T_OPERATING_RELEASE.json',record)
    check="from pathlib import Path;import hashlib;assert hashlib.sha256(Path('/app/"+FILE+"').read_bytes()).hexdigest()=="+repr(before[FILE])
    docker='FROM '+IMAGE+'\nRUN '+json.dumps(['python','-c',check])+'\nCOPY --chown=10001:10001 '+FILE+' /app/'+FILE+'\n'
    docker+='COPY --chown=10001:10001 ALPHA_T_OPERATING_RELEASE.json /app/\nCOPY --chown=10001:10001 tests/ /app/inception-tests/\n'
    native="import os,subprocess,sys;os.environ['PORTAL_TEST_RUNTIME']='/app';os.environ['PYTHONPATH']='/app/src:/app/scripts';sys.exit(subprocess.call([sys.executable,'-m','unittest','discover','-s','/app/inception-tests','-v']))"
    docker+='RUN '+json.dumps(['python','-c',native])+'\nRUN '+json.dumps(['python','-c','print('+repr(r.MARKER)+')'])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    r.save(FOLDER/'source-intent.json',{'tag':'ibkr-platform:risk-inception-20261009-v1','base_image':IMAGE,'changed_files':[FILE],'files_sha256':r.manifest(context),'runtime':str(FOLDER/'runtime'),'report_calculation_and_renderer_changes':False})
    print('Frozen one-module candidate; production unchanged')

def qualify():
    runtime=FOLDER/'runtime'
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts'))),'PORTAL_TEST_RUNTIME':str(runtime),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1'}
    with (FOLDER/'local-tests.log').open('x',encoding='utf-8') as log:
        run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(FOLDER/'context/tests'),'-v'],env=env,cwd=runtime,stdout=log,stderr=subprocess.STDOUT,timeout=300)
    output=(FOLDER/'local-tests.log').read_text(encoding='utf-8');print(output[-1500:])
    if run.returncode:raise ValueError('Candidate failed regression')
    count=int(re.findall(r'Ran (\d+) tests',output)[-1])
    r.save(FOLDER/'local-qualification.json',{'status':'passed','tests_run':count,'source_intent_sha256':r.sha(FOLDER/'source-intent.json'),'log_sha256':r.sha(FOLDER/'local-tests.log')})

def build():
    source=r.read(FOLDER/'source-intent.json');proof=r.read(FOLDER/'local-qualification.json')
    if proof['source_intent_sha256']!=r.sha(FOLDER/'source-intent.json') or r.manifest(FOLDER/'context')!=source['files_sha256']:raise ValueError('Source qualification drift')
    live=request('GET',PATH)
    if live['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Worker image drift')
    if r.build.az('account','show','--query','id')!=r.build.SUB:raise ValueError('Wrong subscription')
    r.save(FOLDER/'build-intent.json',{'tag':source['tag'],'source_intent_sha256':r.sha(FOLDER/'source-intent.json'),'before_jobs':{JOB:{'image':IMAGE,'settings_invariant_sha256':settings_invariant(live)}},'prepared_at':datetime.now(timezone.utc).isoformat(),'database_writes':0,'running_service_update':False})
    ack=r.build.az('acr','build','-r',r.build.REGISTRY,'-t',source['tag'],'-f',str(FOLDER/'context/Dockerfile'),'--no-logs','--no-wait',str(FOLDER/'context'))
    if not ack or not ack.get('runId'):raise ValueError('Missing build acknowledgement; reconcile, do not retry')
    r.save(FOLDER/'build-ack.json',{'run':ack['runId'],'tag':source['tag']})
    print(json.dumps({'run':ack['runId'],'live_changes':False}))

if __name__=='__main__':
    {'prepare':prepare,'qualify':qualify,'build':build,'inspect':r.inspect,'logs':r.logs,'reconcile':r.reconcile}[sys.argv[1]]()
