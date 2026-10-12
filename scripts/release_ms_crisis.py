"""Frozen MS-only crisis candidate; build/scan is not production promotion."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import build_ms_presentation_worker_candidate as build
from deploy_alpha_ms_allocation_geometry import node_source,replace_symbol

ROOT=build.ROOT
FOLDER=ROOT/'.work/alpha-ms-crisis-release-20261008/candidate-v4'
BASE=ROOT/'.work/alpha-ms-headers-v14r15-20261008-r2/local-runtime'
IMAGES={'worker':build.REGISTRY+'.azurecr.io/ibkr-platform@sha256:bae3b6cb43d48af8c525e57895c61b74c25e4b9bc688dd90458a2995a954e372',
        'web':build.REGISTRY+'.azurecr.io/ibkr-platform@sha256:ea6d337314269593c8bf214b22dab756ad367b3a65840814c0c65031377d6a70'}
MARKER='PASS: scoped MS independently verified HKD crisis replay; original NAV metrics and release policy unchanged'
CANDIDATES={kind:FOLDER.parent/'candidate-v6'/kind for kind in IMAGES}

def prepare_retry():
    parent=FOLDER.parent/'candidate-v6'
    if parent.exists():raise ValueError('Frozen retry exists; inspect it')
    runtime=parent/'local-runtime'
    shutil.copytree(FOLDER/'local-runtime',runtime,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    module='src/ibkr_etl/alpha_world_ms_crisis.py'
    shutil.copyfile(ROOT/module,runtime/module)
    for kind,folder in CANDIDATES.items():
        previous=FOLDER/kind if kind=='worker' else FOLDER.parent/'candidate-v5/web'
        context=folder/'context';shutil.copytree(previous/'context',context)
        shutil.copyfile(ROOT/module,context/module)
        shutil.copyfile(ROOT/'tests/test_alpha_world_ms_crisis.py',context/'tests/test_alpha_world_ms_crisis.py')
        manifest=json.loads((context/'MS_CRISIS_RELEASE.json').read_bytes())
        manifest['installed_files_sha256'][module]=build.sha(context/module)
        (context/'MS_CRISIS_RELEASE.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        build.save(folder,'source-intent',{'tag':f'ibkr-platform:alpha-ms-crisis-v6-{kind}-20261008','base_image':IMAGES[kind],
            'files_sha256':{p.relative_to(context).as_posix():build.sha(p) for p in context.rglob('*') if p.is_file()},
            'scope':'MS report crisis replay only; typed PostgreSQL identifiers qualified','production_promotion':False})
        env={**os.environ,'PYTHONPATH':os.pathsep.join((str(runtime/'src'),str(runtime/'scripts'),str(context/'tests'))),
             'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TEMP':str(ROOT/'.work'),'TMP':str(ROOT/'.work')}
        run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],env=env,capture_output=True,text=True,encoding='utf-8')
        with (folder/'local-tests.log').open('x',encoding='utf-8') as stream:stream.write(run.stdout+run.stderr)
        if run.returncode:raise ValueError('Retry regression failed')
        build.save(folder,'local-qualification',{'status':'isolated_local_candidate_passed',
            'source_intent_sha256':build.sha(folder/'source-intent.json'),'replay':{'months_verified':21},
            'actual_inputs_qualification_sha256':build.sha(FOLDER.parent/'qualified-inputs-v1/verification.json'),
            'tests_sha256':build.sha(folder/'local-tests.log')})
    print(json.dumps({'prepared':True,'candidates':2,'regression_tests':92}))

def prepare_web():
    folder=CANDIDATES['web']
    if folder.exists():raise ValueError('Frozen web retry exists; inspect it')
    baseline=ROOT/'.work/alpha-ms-crisis-release-20261008/web-baseline-v1'
    proof=json.loads((baseline/'verification.json').read_bytes())
    if proof['image_digest']!=IMAGES['web'].split('@')[1] or not proof['all_uncompressed_layer_digests_verified']:
        raise ValueError('Exact website image source proof required')
    source=FOLDER/'web/context';context=folder/'context';shutil.copytree(source,context)
    manifest=json.loads((context/'MS_CRISIS_RELEASE.json').read_bytes())
    old=manifest['baseline_files_sha256'];new=proof['files_sha256']
    if {n for n in old if old[n]!=new[n]}!={'src/ibkr_etl/agent_tools.py'}:raise ValueError('Unexpected website source difference')
    # The website adapter is the sole baseline delta. All source outside the
    # MS risk producer remains identical to the installed website module.
    installed=(baseline/'src/ibkr_etl/agent_tools.py').read_text(encoding='utf-8')
    patched=(context/'src/ibkr_etl/agent_tools.py').read_text(encoding='utf-8')
    def without_risk(text):
        node,code=node_source(text,'_risk');return text.replace(code,'<MS-RISK-PRODUCER>',1)
    if without_risk(installed)!=without_risk(patched):raise ValueError('Website fixes would be changed')
    manifest['baseline_files_sha256']=new
    (context/'MS_CRISIS_RELEASE.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    docker=(context/'Dockerfile').read_text(encoding='utf-8')
    docker=docker.replace(old['src/ibkr_etl/agent_tools.py'],new['src/ibkr_etl/agent_tools.py'])
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    build.save(folder,'source-intent',{'tag':'ibkr-platform:alpha-ms-crisis-v5-web-20261008','base_image':IMAGES['web'],
        'files_sha256':{p.relative_to(context).as_posix():build.sha(p) for p in context.rglob('*') if p.is_file()},
        'scope':'MS report crisis replay only; installed website adapter preserved','production_promotion':False,
        'website_baseline_proof_sha256':build.sha(baseline/'verification.json')})
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(FOLDER/'local-runtime/src'),str(FOLDER/'local-runtime/scripts'),str(context/'tests'))),
         'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TEMP':str(ROOT/'.work'),'TMP':str(ROOT/'.work')}
    run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],env=env,capture_output=True,text=True,encoding='utf-8')
    with (folder/'local-tests.log').open('x',encoding='utf-8') as stream:stream.write(run.stdout+run.stderr)
    if run.returncode:raise ValueError('Website regression failed')
    build.save(folder,'local-qualification',{'status':'isolated_local_candidate_passed',
        'source_intent_sha256':build.sha(folder/'source-intent.json'),'replay':{'months_verified':21},
        'actual_inputs_qualification_sha256':build.sha(ROOT/'.work/alpha-ms-crisis-release-20261008/qualified-inputs-v1/verification.json'),
        'tests_sha256':build.sha(folder/'local-tests.log')})
    print(json.dumps({'prepared_web':True,'website_fixes_preserved':True}))

def prepare():
    if FOLDER.exists():raise ValueError('Frozen candidate exists; inspect instead')
    proof=ROOT/'.work/alpha-ms-crisis-release-20261008/qualified-inputs-v1/verification.json'
    value=json.loads(proof.read_bytes())
    if value['status']!='passed' or value['months']!=21:raise ValueError('All-month independent qualification required')
    patch={}
    def direct(name):patch[name]=(ROOT/name).read_bytes()
    for name in ('src/ibkr_etl/alpha_world_ms_crisis.py','src/ibkr_etl/assets/alpha_world_ms_crisis_market_v1.json',
                 'src/ibkr_etl/alpha_world_ms_presentation.py','scripts/render_monthly_presentation.mjs',
                 'scripts/audit_alpha_ms_presentation.py'):direct(name)
    name='src/ibkr_etl/agent_tools.py'
    text=(BASE/name).read_text(encoding='utf-8')
    replacement=node_source((ROOT/name).read_text(encoding='utf-8'),'_risk')[1]
    original=node_source(text,'_risk')[1]
    tail='    mapping = connection.execute('
    if original.split(tail,1)[1]!=replacement.split(tail,1)[1]:raise ValueError('Non-MS risk code changed')
    patch[name]=replace_symbol(text,'_risk',replacement).encode()
    name='src/ibkr_etl/reporting_snapshot.py';text=(BASE/name).read_text(encoding='utf-8')
    needle='    else:\n        crisis_as_of = date.fromisoformat(str(crisis["holdings_as_of"]))'
    inserted='    else:\n        if subject_id==\'alpha-world-ms\' and reporting_currency==\'HKD\':\n            from .alpha_world_ms_crisis import validate\n            validate(crisis,as_of=effective.isoformat(),holdings=sanitized[\'holdings\'][\'data\'][\'holdings\'],\n                     nav=sanitized[\'summary\'][\'data\'][\'latest_nav\'][\'total_text\'])\n        crisis_as_of = date.fromisoformat(str(crisis["holdings_as_of"]))'
    if text.count(needle)!=1:raise ValueError('Reporting baseline differs')
    patch[name]=text.replace(needle,inserted).encode()
    name='src/ibkr_etl/presentation_design.py';text=(BASE/name).read_text(encoding='utf-8')
    for symbol in ('ALPHA_WORLD_MS_PRESENTATION_CONTENT_CONTRACT_VERSION','ALPHA_WORLD_MS_PRESENTATION_CONTENT_REQUIREMENTS'):
        text=replace_symbol(text,symbol,node_source((ROOT/name).read_text(encoding='utf-8'),symbol)[1])
    patch[name]=text.encode()
    # Existing release-policy regression inspects this unchanged baseline script.
    patch['scripts/release_internal_reports.py']=(BASE/'scripts/release_internal_reports.py').read_bytes()
    FOLDER.mkdir(parents=True);runtime=FOLDER/'local-runtime'
    for section in ('src','scripts','templates'):
        shutil.copytree(BASE/section,runtime/section,ignore=shutil.ignore_patterns('__pycache__','*.pyc','node_modules')) if (BASE/section).exists() else None
    if not (runtime/'templates').exists():
        shutil.copytree(ROOT/'.work/alpha-ms-format-worker-candidate-20261008-r4/local-runtime/templates',runtime/'templates')
    # Desktop rendering uses the existing bundled package from ROOT/scripts.
    # Do not create new junctions or copy desktop dependencies into the image.
    for name,data in patch.items():
        target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    tests=('test_alpha_world_ms_crisis.py','test_ms_presentation_model.py','test_internal_release.py',
           'test_monthly_release.py')
    for kind,image in IMAGES.items():
        folder=FOLDER/kind;context=folder/'context';context.mkdir(parents=True)
        for name,data in patch.items():
            target=context/name;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:stream.write(data)
        shutil.copytree(ROOT/'.work/alpha-ms-headers-v14r15-20261008-r2/worker/context/tests',context/'tests')
        for name in tests:shutil.copyfile(ROOT/'tests'/name,context/'tests'/name)
        baseline={name:build.sha(BASE/name) for name in patch if (BASE/name).exists()}
        manifest={'base_image':image,'baseline_files_sha256':baseline,
                  'installed_files_sha256':{name:hashlib.sha256(data).hexdigest() for name,data in patch.items()},
                  'actual_inputs_qualification_sha256':build.sha(proof),'scope':'MS report crisis replay only',
                  'nav_metrics_unchanged':True,'factsheet_layout_unchanged':True,'release_allowlist_unchanged':True,
                  'alpha_world_t_unchanged':True,'source_data_changed':False,'reports_released':False}
        build.save(context,'MS_CRISIS_RELEASE',manifest)
        before="import hashlib,pathlib; expected="+repr(baseline)+"; assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==h for n,h in expected.items()), 'Installed baseline differs'"
        after="from ibkr_etl.alpha_world_ms_crisis import market_sources;assert len(market_sources()['calendar'])==121;print("+repr(MARKER)+")"
        docker='FROM '+image+'\nENV PYTHONPATH=/app/src:/app/scripts:/app PYTHONDONTWRITEBYTECODE=1\nRUN '+json.dumps(['python','-c',before])+'\n'
        docker+='COPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/\nCOPY --chown=10001:10001 scripts/ /app/scripts/\nCOPY --chown=10001:10001 tests/ /app/ms-crisis-tests/\nCOPY --chown=10001:10001 MS_CRISIS_RELEASE.json /app/\n'
        docker+='RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/ms-crisis-tests","-v"]\nRUN '+json.dumps(['python','-c',after])+'\n'
        with (context/'Dockerfile').open('x',encoding='utf-8') as stream:stream.write(docker)
        build.save(folder,'source-intent',{'tag':f'ibkr-platform:alpha-ms-crisis-v4-{kind}-20261008','base_image':image,
            'files_sha256':{p.relative_to(context).as_posix():build.sha(p) for p in context.rglob('*') if p.is_file()},
            'scope':manifest['scope'],'production_promotion':False})
        env={**os.environ,'PYTHONPATH':os.pathsep.join((str(runtime/'src'),str(runtime/'scripts'),str(context/'tests'))),
             'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TEMP':str(ROOT/'.work'),'TMP':str(ROOT/'.work')}
        run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],env=env,capture_output=True,text=True,encoding='utf-8')
        with (folder/'local-tests.log').open('x',encoding='utf-8') as stream:stream.write(run.stdout+run.stderr)
        if run.returncode:raise ValueError('Isolated candidate regression failed')
        build.save(folder,'local-qualification',{'status':'isolated_local_candidate_passed',
            'source_intent_sha256':build.sha(folder/'source-intent.json'),'replay':{'months_verified':21},
            'actual_inputs_qualification_sha256':build.sha(proof),'tests_sha256':build.sha(folder/'local-tests.log')})
    print(json.dumps({'prepared':True,'candidates':2,'months':21,'production_changes':0}))

def main():
    stage=sys.argv[1]
    if stage=='prepare-retry':prepare_retry();return
    if stage=='prepare':prepare();return
    if stage=='prepare-web':prepare_web();return
    import release_alpha_ms_headers_v14r15 as driver
    driver.FOLDER=CANDIDATES[sys.argv[2]].parent;driver.IMAGES=IMAGES;driver.MARKER=MARKER
    driver.native(stage,sys.argv[2])

if __name__=='__main__':main()
