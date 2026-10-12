"""One-line verified-tenant propagation correction; no calculations changed."""
import json,os,shutil,subprocess,sys,re
from pathlib import Path
import release_platform_access_correction_20261009 as release
ROOT=release.ROOT
BASE=ROOT/'.work/platform-upgrade-20261009-stage5'
BUNDLE=ROOT/'.work/platform-upgrade-20261009-stage5b'
release.WEB=BUNDLE/'application-release-v1'
release.IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:6268c3c1efce22c45864be989e9d00258c57d8ac6a195d8810e9948327589106'
release.MARKER='PASS: verified MS tenant propagated; authorization and financial calculations unchanged'
FILE='src/ibkr_etl/agent_tools.py'
def prepare():
    folder=release.WEB;context=folder/'context';runtime=BUNDLE/'runtime'
    if folder.exists() or runtime.exists():raise ValueError('Preserve frozen candidate')
    for name in ('runtime','tests','scripts','worker'):
        shutil.copytree(BASE/name,BUNDLE/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(BUNDLE/'changed'/FILE,runtime/FILE)
    shutil.copyfile(ROOT/'tests/test_ms_agent_tenant_propagation.py',BUNDLE/'tests/test_ms_agent_tenant_propagation.py')
    before,after=release.manifest(BASE/'runtime'),release.manifest(runtime)
    if {p for p in set(before)|set(after) if before.get(p)!=after.get(p)}!={FILE}:raise ValueError('Unexpected scope')
    (context/FILE).parent.mkdir(parents=True)
    shutil.copyfile(runtime/FILE,context/FILE)
    shutil.copytree(BUNDLE/'tests',context/'tests')
    record=release.read(BASE/'application-release-v1/context/ALPHA_T_RUNTIME_RELEASE.json')
    record['installed_files_sha256'][FILE]=release.sha(context/FILE)
    record['scope']='Pass already-verified MS tenant to read-only tool executor; no financial/report changes'
    record['base_image']=release.IMAGE
    release.save(context/'ALPHA_T_RUNTIME_RELEASE.json',record)
    assertion="from pathlib import Path;import hashlib;assert hashlib.sha256(Path('/app/"+FILE+"').read_bytes()).hexdigest()=="+repr(release.sha(BASE/'runtime'/FILE))
    docker='FROM '+release.IMAGE+'\nRUN '+json.dumps(['python','-c',assertion])+'\n'
    docker+='COPY --chown=10001:10001 '+FILE+' /app/'+FILE+'\nCOPY --chown=10001:10001 ALPHA_T_RUNTIME_RELEASE.json /app/\nCOPY --chown=10001:10001 tests/ /app/upgrade-tests/\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\n'
    native="import os,subprocess,sys;os.environ['PORTAL_TEST_RUNTIME']='/app';os.environ['PYTHONPATH']='/app/src:/app/scripts';os.environ['RISK_DISPLAY_SOURCE']='/app/src/ibkr_etl/risk_display.py';sys.exit(subprocess.call([sys.executable,'-m','unittest','discover','-s','/app/upgrade-tests','-v']))"
    docker+='RUN '+json.dumps(['python','-c',native])+'\nRUN '+json.dumps(['python','-c','print('+repr(release.MARKER)+')'])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    release.save(folder/'source-intent.json',{'tag':'ibkr-platform:ms-agent-tenant-20261009-v1','base_image':release.IMAGE,'files_sha256':release.manifest(context),'changed_files':[FILE],'financial_renderers_and_templates_unchanged':True,'authorization_policy_changes':False})
    print('Frozen one-line candidate')
def qualify():
    folder=release.WEB;runtime=BUNDLE/'runtime'
    env={**os.environ,'PORTAL_TEST_RUNTIME':str(runtime),'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts'))),'RISK_DISPLAY_SOURCE':str(runtime/'src/ibkr_etl/risk_display.py'),'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TMP':str(ROOT/'.work/t-test-temp'),'TEMP':str(ROOT/'.work/t-test-temp')}
    log=folder/'local-tests.log'
    with log.open('x',encoding='utf-8') as stream:
        result=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(BUNDLE/'tests'),'-v'],env=env,cwd=runtime,stdout=stream,stderr=subprocess.STDOUT,timeout=300)
    output=log.read_text(encoding='utf-8')
    if result.returncode:print(output[-7000:]);raise ValueError('Regression failed')
    release.save(folder/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':int(re.findall(r'Ran (\d+) tests',output)[-1]),'source_intent_sha256':release.sha(folder/'source-intent.json'),'log_sha256':release.sha(log)})
    print(output[-250:])
if __name__=='__main__':{'prepare':prepare,'qualify':qualify,'build':release.dispatch,'reconcile':release.reconcile,'logs':release.logs}[sys.argv[1]]()
