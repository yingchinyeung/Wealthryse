"""Prepare/build separately scoped app, risk and benchmark candidates; no deployment."""
import json,os,re,shutil,subprocess,sys
from datetime import datetime,timezone
import release_platform_access_correction_20261009 as release
from deploy_metadata_monitor import request,BASE
from deploy_ms_presentation_worker_release import settings_invariant

ROOT=release.ROOT
STAGE=ROOT/'.work/platform-upgrade-20261009-stage7'
PRIOR=ROOT/'.work/platform-upgrade-20261009-stage5b'
WORKER=ROOT/'.work/platform-upgrade-20261009-stage5/risk-worker-release-v1'
BENCH=ROOT/'.work/alpha-t-operating-release-20261009-v4/benchmark'
SETTINGS={
 'app':{'base':PRIOR/'runtime','candidate':STAGE/'runtime','digest':'519f58ef20839b1f53d57086e367df7a3e7bcc4d691a94955a62d07ea25d9b4e',
        'files':('crisis_identity_policy.py','alpha_world_ms_crisis.py','alpha_world_ms_runtime.py','risk_snapshots.py','agent_tools.py'),
        'record':PRIOR/'application-release-v1/context/ALPHA_T_RUNTIME_RELEASE.json','resource':'containerApps/ibkrv2stg-web'},
 'risk':{'base':WORKER/'runtime','candidate':None,'digest':'cc904a6375e97b9c5827491a37bc21265055b8c452540a86c90b461170a1b94d',
         'files':('crisis_identity_policy.py','alpha_world_ms_crisis.py','alpha_world_ms_runtime.py','risk_snapshots.py'),
         'record':WORKER/'context/ALPHA_T_OPERATING_RELEASE.json','resource':'jobs/ibkrv2stg-risk-snapshots'},
 'benchmark':{'base':BENCH/'local-runtime','candidate':STAGE/'benchmark/runtime','digest':'21fdf485312816b2ce2c3858ce2a690ba9057c6895d4da984f389fc397c3117f',
              'files':('crisis_identity_policy.py','crisis_replay.py'),'record':BENCH/'context/ALPHA_T_OPERATING_RELEASE.json',
              'resource':'jobs/ibkrv2stg-benchmark-daily'}
}

def configure(kind):
    spec=SETTINGS[kind]
    release.WEB=STAGE/(kind+'-release-v1')
    release.IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:'+spec['digest']
    release.MARKER='PASS: approved scoped crisis identity proxy '+kind+'; no NAV or report mutation'
    return spec

def prepare(kind,spec):
    folder=release.WEB;context=folder/'context'
    if folder.exists():raise ValueError('Preserve existing candidate')
    release.read(STAGE/'local-qualification.json')
    runtime=spec['candidate']
    if runtime is None:
        runtime=folder/'runtime';shutil.copytree(spec['base'],runtime,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for name in spec['files']:shutil.copyfile(STAGE/'runtime/src/ibkr_etl'/name,runtime/'src/ibkr_etl'/name)
    before,after=release.manifest(spec['base']),release.manifest(runtime)
    files=['src/ibkr_etl/'+n for n in spec['files']]
    if {p for p in set(before)|set(after) if before.get(p)!=after.get(p)}!=set(files):raise ValueError('Unexpected candidate changes')
    for name in files:
        (context/name).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(runtime/name,context/name)
    tests=STAGE/'tests' if kind=='app' else context/'tests'
    if kind=='app':shutil.copytree(tests,context/'tests')
    else:
        shutil.copytree((WORKER if kind=='risk' else BENCH)/'context/tests',tests,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        if kind=='risk':shutil.copyfile(STAGE/'tests/test_extended_crisis_identity_policy.py',tests/'test_extended_crisis_identity_policy.py')
        else:shutil.copyfile(STAGE/'benchmark/test_identity_producer.py',tests/'test_identity_producer.py')
    record=release.read(spec['record'])
    for name in files:record['installed_files_sha256'][name]=release.sha(context/name)
    record['scope']='Owner-approved SPY identity proxy for U16088571/MS hypothetical crisis only; no actual NAV or report changes'
    record['base_image']=release.IMAGE
    release.save(context/spec['record'].name,record)
    expected={name:before[name] for name in files if name in before}
    absent=[name for name in files if name not in before]
    check="from pathlib import Path;import hashlib;e="+repr(expected)+";a="+repr(absent)+";assert all(hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest()==v for n,v in e.items());assert all(not (Path('/app')/n).exists() for n in a)"
    docker='FROM '+release.IMAGE+'\nRUN '+json.dumps(['python','-c',check])+'\n'
    for name in files:docker+='COPY --chown=10001:10001 '+name+' /app/'+name+'\n'
    docker+='COPY --chown=10001:10001 '+spec['record'].name+' /app/\nCOPY --chown=10001:10001 tests/ /app/identity-tests/\n'
    if kind=='app':docker+='RUN ["python","scripts/check_report_runtime.py"]\n'
    native="import os,subprocess,sys;os.environ['PORTAL_TEST_RUNTIME']='/app';os.environ['PYTHONPATH']='/app/src:/app/scripts';os.environ['RISK_DISPLAY_SOURCE']='/app/src/ibkr_etl/risk_display.py';sys.exit(subprocess.call([sys.executable,'-m','unittest','discover','-s','/app/identity-tests','-v']))"
    docker+='RUN '+json.dumps(['python','-c',native])+'\nRUN '+json.dumps(['python','-c','print('+repr(release.MARKER)+')'])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    release.save(folder/'source-intent.json',{'tag':'ibkr-platform:crisis-identity-'+kind+'-20261009-v1','base_image':release.IMAGE,
        'files_sha256':release.manifest(context),'changed_files':files,'runtime':str(runtime),'test_directory':str(tests),
        'actual_nav_changes':False,'report_renderer_changes':False,'policy_approval':'Owner explicitly approved extension to U16088571 and MS on 2026-10-09'})
    print('Prepared '+kind+' candidate')

def qualify(kind):
    folder=release.WEB;intent=release.read(folder/'source-intent.json');runtime=intent['runtime']
    env={**os.environ,'PYTHONPATH':os.pathsep.join((runtime+'/src',runtime+'/scripts')),'PORTAL_TEST_RUNTIME':runtime,
         'RISK_DISPLAY_SOURCE':runtime+'/src/ibkr_etl/risk_display.py','PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1',
         'TMP':str(ROOT/'.work/t-test-temp'),'TEMP':str(ROOT/'.work/t-test-temp')}
    with (folder/'local-tests.log').open('x',encoding='utf-8') as log:
        result=subprocess.run([sys.executable,'-m','unittest','discover','-s',intent['test_directory'],'-v'],
                              cwd=runtime,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
    text=(folder/'local-tests.log').read_text(encoding='utf-8')
    print(text[-500:])
    if result.returncode:raise ValueError('Candidate failed local regression')
    release.save(folder/'local-qualification.json',{'status':'passed','tests_run':int(re.findall(r'Ran (\d+) tests',text)[-1]),
        'source_intent_sha256':release.sha(folder/'source-intent.json'),'log_sha256':release.sha(folder/'local-tests.log')})

def build(kind,spec):
    folder=release.WEB;intent=release.read(folder/'source-intent.json');proof=release.read(folder/'local-qualification.json')
    if proof['source_intent_sha256']!=release.sha(folder/'source-intent.json') or release.manifest(folder/'context')!=intent['files_sha256']:
        raise ValueError('Frozen candidate changed')
    live=request('GET',BASE+'Microsoft.App/'+spec['resource']+'?api-version=2024-03-01')
    if live['properties']['template']['containers'][0]['image']!=release.IMAGE:raise ValueError('Installed runtime drift')
    if release.build.az('account','show','--query','id')!=release.build.SUB:raise ValueError('Wrong subscription')
    release.save(folder/'build-intent.json',{'tag':intent['tag'],'source_intent_sha256':release.sha(folder/'source-intent.json'),
        'before_jobs':{} if kind=='app' else {spec['resource'].split('/')[1]:{'image':release.IMAGE,'settings_invariant_sha256':settings_invariant(live)}},
        'before_website_image':release.IMAGE if kind=='app' else None,'prepared_at':datetime.now(timezone.utc).isoformat(),'database_writes':0})
    ack=release.build.az('acr','build','-r',release.build.REGISTRY,'-t',intent['tag'],'-f',str(folder/'context/Dockerfile'),'--no-logs','--no-wait',str(folder/'context'))
    if not ack or not ack.get('runId'):print('Build acknowledgement absent; reconcile exact run, do not resubmit');return
    release.save(folder/'build-ack.json',{'run':ack['runId'],'tag':intent['tag']})

if __name__=='__main__':
    kind,action=sys.argv[1:3];spec=configure(kind);sys.argv=[sys.argv[0],action,*sys.argv[3:]]
    if action=='prepare':prepare(kind,spec)
    elif action=='qualify':qualify(kind)
    elif action=='build':build(kind,spec)
    else:{'reconcile':release.reconcile,'logs':release.logs}[action]()
