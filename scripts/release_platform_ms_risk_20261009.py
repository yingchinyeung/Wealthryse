"""Qualify a versioned MS analytics extension; no report renderer/template changes."""
import json,os,re,shutil,subprocess,sys
from pathlib import Path
import release_platform_access_correction_20261009 as release
from check_platform_upgrade_20261009 import check

ROOT=release.ROOT
BUNDLE=ROOT/'.work/platform-upgrade-20261009-stage5'
BASE=ROOT/'.work/platform-upgrade-20261009-stage4'
release.WEB=BUNDLE/'application-release-v1'
release.IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:76233dbaf92962f0254031edd2dd5057482692fd804b1581b9fdd9f003df848a'
release.MARKER='PASS: MS versioned risk extension; original core metrics and report renderers preserved'
FILES=tuple('src/ibkr_etl/'+n for n in ('web.py','agent_tools.py','qualified_agent_risk.py','risk_snapshots.py','alpha_world_ms_runtime.py'))

def prepare():
    if not check()['integrity_passed']:raise ValueError('Verified base drifted')
    folder=release.WEB;context=folder/'context';runtime=BUNDLE/'runtime'
    if folder.exists() or runtime.exists():raise ValueError('Preserve prepared candidate')
    shutil.copytree(BASE/'runtime',runtime,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(BASE/'tests',BUNDLE/'tests',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(BASE/'worker',BUNDLE/'worker',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (BUNDLE/'scripts').mkdir()
    for name in ('release_internal_reports.py','alpha_t_presentation_runtime.mjs'):
        shutil.copyfile(BASE/'runtime/scripts'/name,BUNDLE/'scripts'/name)
    for name in FILES:
        (context/name).parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(BUNDLE/'changed'/name,runtime/name)
        shutil.copyfile(BUNDLE/'changed'/name,context/name)
    before,after=release.manifest(BASE/'runtime'),release.manifest(runtime)
    changed={p for p in set(before)|set(after) if before.get(p)!=after.get(p)}
    if changed!=set(FILES):raise ValueError('Scope exceeds versioned MS extension')
    shutil.copytree(BUNDLE/'tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    record=release.read(BASE/'application-release-v1/context/ALPHA_T_RUNTIME_RELEASE.json')
    for name in FILES:record['installed_files_sha256'][name]=release.sha(context/name)
    record['scope']='Versioned MS extended risk; original core metrics, report renderers, approvals and access preserved'
    release.save(context/'ALPHA_T_RUNTIME_RELEASE.json',record)
    expected={n:release.sha(BASE/'runtime'/n) for n in FILES if (BASE/'runtime'/n).exists()}
    assertion="from pathlib import Path;import hashlib;expected="+repr(expected)+";assert all(hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest()==v for n,v in expected.items())"
    docker='FROM '+release.IMAGE+'\nRUN '+json.dumps(['python','-c',assertion])+'\n'
    for name in FILES:docker+='COPY --chown=10001:10001 '+name+' /app/'+name+'\n'
    docker+='COPY --chown=10001:10001 ALPHA_T_RUNTIME_RELEASE.json /app/\nCOPY --chown=10001:10001 tests/ /app/upgrade-tests/\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\n'
    native="import os,subprocess,sys;os.environ['PORTAL_TEST_RUNTIME']='/app';os.environ['PYTHONPATH']='/app/src:/app/scripts';os.environ['RISK_DISPLAY_SOURCE']='/app/src/ibkr_etl/risk_display.py';sys.exit(subprocess.call([sys.executable,'-m','unittest','discover','-s','/app/upgrade-tests','-v']))"
    docker+='RUN '+json.dumps(['python','-c',native])+'\nRUN '+json.dumps(['python','-c','print('+repr(release.MARKER)+')'])+'\n'
    with (context/'Dockerfile').open('x',encoding='utf-8') as f:f.write(docker)
    release.save(folder/'source-intent.json',{'tag':'ibkr-platform:platform-ms-risk-20261009-v1',
        'base_image':release.IMAGE,'files_sha256':release.manifest(context),
        'financial_renderers_and_templates_unchanged':True,'authorization_changes':False,
        'changed_files':sorted(changed),'scope':record['scope']})
    print(json.dumps({'prepared':True,'source_files':sorted(changed),'live_changes':False}))

def qualify():
    folder=release.WEB;runtime=BUNDLE/'runtime'
    env={**os.environ,'PORTAL_TEST_RUNTIME':str(runtime),'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts'))),
        'RISK_DISPLAY_SOURCE':str(runtime/'src/ibkr_etl/risk_display.py'),'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1',
        'TMP':str(ROOT/'.work/t-test-temp'),'TEMP':str(ROOT/'.work/t-test-temp')}
    for name in ('release_internal_reports.py','alpha_t_presentation_runtime.mjs'):
        if release.sha(BUNDLE/'scripts'/name)!=release.sha(BASE/'runtime/scripts'/name):
            raise ValueError('Local script fixture differs from unchanged baseline')
    log_path=folder/'local-tests-elevated-v2.log'
    with log_path.open('x',encoding='utf-8') as log:
        run=subprocess.run([sys.executable,'-u','-m','unittest','discover','-s',str(BUNDLE/'tests'),'-v'],
            env=env,cwd=runtime,stdout=log,stderr=subprocess.STDOUT,timeout=300)
    output=log_path.read_text(encoding='utf-8')
    if run.returncode:print(output[-6000:]);raise ValueError('Regression failed')
    count=int(re.findall(r'Ran (\d+) tests',output)[-1])
    release.save(folder/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':count,
        'source_intent_sha256':release.sha(folder/'source-intent.json'),'log_sha256':release.sha(log_path),
        'log_file':log_path.name,'local_loopback_required':True})
    print(output[-200:])

if __name__=='__main__':
    {'prepare':prepare,'qualify':qualify,'build':release.dispatch,'reconcile':release.reconcile,'logs':release.logs}[sys.argv[1]]()
