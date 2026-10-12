"""Two-file MS risk extension on the risk worker's own frozen baseline."""
import ast,json,os,re,shutil,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
import release_platform_access_correction_20261009 as release
from deploy_metadata_monitor import request,BASE
from deploy_ms_presentation_worker_release import settings_invariant
ROOT=release.ROOT
BUNDLE=ROOT/'.work/platform-upgrade-20261009-stage5'
PRIOR=ROOT/'.work/alpha-t-operating-release-20261009-v4/daily'
WEB=BUNDLE/'application-release-v1'
release.WEB=BUNDLE/'risk-worker-release-v1'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:3e7caaed1be1d5caf64cd027547c0f79f983f29c0d9b7850f6ad472344b4725a'
release.MARKER='PASS: scoped MS extended risk worker; other runtime and entrypoint bytes preserved'
JOB='ibkrv2stg-risk-snapshots'
FILES=('src/ibkr_etl/risk_snapshots.py','src/ibkr_etl/alpha_world_ms_runtime.py')

def prepare():
    folder=release.WEB;context=folder/'context';runtime=folder/'runtime'
    if folder.exists():raise ValueError('Preserve existing worker candidate')
    prior=release.read(PRIOR/'build-completed.json')
    if IMAGE.rsplit('@',1)[1]!=prior['output_images'][0]['digest']:raise ValueError('Worker base differs')
    shutil.copytree(PRIOR/'local-runtime',runtime,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    before=release.manifest(runtime)
    for name in FILES:
        for parent in (runtime,context):
            (parent/name).parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(WEB/'context'/name,parent/name)
    after=release.manifest(runtime)
    if {p for p in set(before)|set(after) if before.get(p)!=after.get(p)}!=set(FILES):raise ValueError('Worker scope differs')
    tests=context/'tests';tests.mkdir()
    for name in ('test_alpha_world_ms_analytics.py','test_alpha_world_ms_crisis.py','test_alpha_world_ms_hkd.py',
                 'test_alpha_world_t_analytics.py','test_alpha_world_t_cache.py','test_alpha_world_t_crisis.py','test_alpha_world_t_crisis_policy.py'):
        shutil.copyfile(BUNDLE/'tests'/name,tests/name)
    shutil.copyfile(PRIOR/'context/tests/test_monthly_artifacts.py',tests/'test_monthly_artifacts.py')
    raw=(BUNDLE/'tests/test_ms_extended_runtime.py').read_text(encoding='utf-8')
    prefix=raw.split('class ExactCache(')[0].replace('from ibkr_etl import qualified_agent_risk as agent\n','')
    with (tests/'test_ms_extended_runtime.py').open('x',encoding='utf-8') as f:f.write(prefix)
    record=release.read(PRIOR/'context/ALPHA_T_OPERATING_RELEASE.json')
    for name in FILES:record['installed_files_sha256'][name]=release.sha(context/name)
    record['scope']='MS-only versioned risk extension; unchanged T logic and all reporting code'
    release.save(context/'ALPHA_T_OPERATING_RELEASE.json',record)
    baseline={FILES[0]:before[FILES[0]]}
    assertion="from pathlib import Path;import hashlib;e="+repr(baseline)+";assert all(hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest()==v for n,v in e.items())"
    docker='FROM '+IMAGE+'\nRUN '+json.dumps(['python','-c',assertion])+'\n'
    for name in FILES:docker+='COPY --chown=10001:10001 '+name+' /app/'+name+'\n'
    docker+='COPY --chown=10001:10001 ALPHA_T_OPERATING_RELEASE.json /app/\nCOPY --chown=10001:10001 tests/ /app/ms-risk-tests/\n'
    docker+='RUN ["python","-m","unittest","discover","-s","/app/ms-risk-tests","-v"]\n'
    docker+='RUN '+json.dumps(['python','-c','print('+repr(release.MARKER)+')'])+'\n'
    with (context/'Dockerfile').open('x',encoding='utf-8') as f:f.write(docker)
    release.save(folder/'source-intent.json',{'tag':'ibkr-platform:platform-ms-risk-worker-20261009-v1','base_image':IMAGE,
        'files_sha256':release.manifest(context),'changed_files':list(FILES),'source_data_changed':False,'report_renderers_unchanged':True})
    print('Prepared two-file worker candidate')

def qualify():
    folder=release.WEB
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(folder/'runtime/src',folder/'runtime/scripts'))),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1'}
    with (folder/'local-tests-v2.log').open('x',encoding='utf-8') as stream:
        run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(folder/'context/tests'),'-v'],
            cwd=folder/'runtime',env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=300)
    text=(folder/'local-tests-v2.log').read_text(encoding='utf-8')
    if run.returncode:print(text[-6000:]);raise ValueError('Worker tests failed')
    count=int(re.findall(r'Ran (\d+) tests',text)[-1])
    release.save(folder/'local-qualification.json',{'status':'passed','tests_run':count,'source_intent_sha256':release.sha(folder/'source-intent.json'),'log_sha256':release.sha(folder/'local-tests-v2.log'),'log_file':'local-tests-v2.log'})
    print(text[-180:])

def dispatch():
    source=release.read(release.WEB/'source-intent.json');proof=release.read(release.WEB/'local-qualification.json')
    if release.manifest(release.WEB/'context')!=source['files_sha256'] or proof['source_intent_sha256']!=release.sha(release.WEB/'source-intent.json'):raise ValueError('Frozen qualification drift')
    job=request('GET',BASE+'Microsoft.App/jobs/'+JOB+'?api-version=2024-03-01')
    if job['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Installed worker drift')
    if release.build.az('account','show','--query','id')!=release.build.SUB:raise ValueError('Wrong subscription')
    release.save(release.WEB/'build-intent.json',{'tag':source['tag'],'source_intent_sha256':release.sha(release.WEB/'source-intent.json'),
        'before_jobs':{JOB:{'image':IMAGE,'settings_invariant_sha256':settings_invariant(job)}},'prepared_at':datetime.now(timezone.utc).isoformat(),'database_writes':0})
    result=release.build.az('acr','build','-r',release.build.REGISTRY,'-t',source['tag'],'-f',str(release.WEB/'context/Dockerfile'),'--no-logs','--no-wait',str(release.WEB/'context'))
    if not result or not result.get('runId'):print('Build acknowledgement absent; reconcile existing run, do not resubmit');return
    release.save(release.WEB/'build-ack.json',{'run':result['runId'],'tag':source['tag']})

if __name__=='__main__':
    {'prepare':prepare,'qualify':qualify,'build':dispatch,'reconcile':release.reconcile,'logs':release.logs}[sys.argv[1]]()
