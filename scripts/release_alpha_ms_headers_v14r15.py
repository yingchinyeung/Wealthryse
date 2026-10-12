"""Scoped, immutable MS layout candidate preparation and native build control.

No service updates, database writes, report registration or approval changes.
"""
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import build_ms_presentation_worker_candidate as build
from deploy_alpha_ms_allocation_geometry import node_source, replace_symbol

ROOT=build.ROOT
FOLDER=ROOT/'.work/alpha-ms-headers-v14r15-20261008-r2'
BASE=ROOT/'.work/alpha-ms-distribution-v14r13-20261007/context'
WORKER=ROOT/'.work/alpha-ms-format-worker-candidate-20261008-r4/context'
WEB=ROOT/'.work/viewer-report-investigation-20261008/candidate-v2/context'
IMAGES={
    'worker':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:af5d255d4f71f762db763fa7bb8ab542e9c6f52a211c48b51cb1f44deb488fe9',
    'web':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:961bc36d5ddff5eff4d00476ef6e48ed9709ef8ba301e2cb1143db95e8c3502d'}
LAYOUT='subject-completed-factsheet-model-v14r15-alpha-world-ms-right-aligned-dates'
MARKER='PASS: scoped MS subtitle and date headers v14r15; calculations and T unchanged'


def unchanged(text,allowed):
    result=[]
    for node in ast.parse(text).body:
        name=getattr(node,'name',None)
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            name=node.targets[0].id
        if name not in allowed:result.append(ast.dump(node,include_attributes=False))
    return result


def renderer_patch():
    name='src/ibkr_etl/monthly_artifacts.py'
    original=(BASE/name).read_text(encoding='utf-8')
    current=(ROOT/name).read_text(encoding='utf-8')
    allowed={'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION','_draw_alpha_world_page_one',
             '_draw_alpha_world_page_two','render_factsheet','_draw_alpha_world_ms_date_header',
             '_remove_alpha_world_ms_source_date_text'}
    result=original
    for symbol in ('ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION','_draw_alpha_world_page_one',
                   '_draw_alpha_world_page_two','render_factsheet'):
        source=node_source(current,symbol)[1]
        if symbol=='render_factsheet':
            excluded="    if snapshot.get('subject_id') == 'alpha-world-t':\n        from .alpha_world_t_factsheet import render\n        return render(snapshot, template_path=template_path, output_path=output_path,\n                      design_model_path=design_model_path)\n"
            if source.count(excluded)!=1:raise ValueError('Exact unqualified T dispatch exclusion required')
            source=source.replace(excluded,'')
        result=replace_symbol(result,symbol,source)
    for symbol in ('_draw_alpha_world_ms_date_header','_remove_alpha_world_ms_source_date_text'):
        result+='\n'+node_source(current,symbol)[1]+'\n'
    if unchanged(original,allowed)!=unchanged(result,allowed):raise ValueError('Out-of-scope renderer change')
    return result.encode()


def copy_file(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as stream:stream.write(source.read_bytes())


def prepare():
    if FOLDER.exists():raise ValueError('Frozen attempt exists; inspect, never replace')
    prior=json.loads((BASE.parent/'source-intent.json').read_bytes())['record']
    for name,digest in prior['files_sha256'].items():
        if build.sha(BASE/name)!=digest:raise ValueError('Frozen baseline changed: '+name)
    pdf_name='src/ibkr_etl/alpha_world_ms_pdf_validation.py'
    old=(BASE/pdf_name).read_text(encoding='utf-8');new=(ROOT/pdf_name).read_text(encoding='utf-8')
    if unchanged(old,{'validate','validate_date_headers'})!=unchanged(new,{'validate','validate_date_headers'}):
        raise ValueError('Unrelated PDF validation changed')
    patch={'src/ibkr_etl/monthly_artifacts.py':renderer_patch(),pdf_name:new.encode(),
           'src/ibkr_etl/alpha_world_ms_subtitle.py':(ROOT/'src/ibkr_etl/alpha_world_ms_subtitle.py').read_bytes(),
           'src/ibkr_etl/assets/alpha_world_subtitle_font.pdf':(ROOT/'src/ibkr_etl/assets/alpha_world_subtitle_font.pdf').read_bytes()}
    FOLDER.mkdir()
    runtime=FOLDER/'local-runtime'
    baseline_runtime=ROOT/'.work/alpha-ms-presentation-worker-candidate-20261007-r5/local-runtime'
    shutil.copytree(baseline_runtime/'src',runtime/'src',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(baseline_runtime/'scripts',runtime/'scripts',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    # Apply the latest installed worker overlays without changing their source.
    for section in ('src','scripts'):
        for path in (WORKER/section).rglob('*'):
            if path.is_file() and path.suffix!='.pyc':
                target=runtime/path.relative_to(WORKER);target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,target)
    for kind,image in IMAGES.items():
        folder=FOLDER/kind;context=folder/'context';context.mkdir(parents=True)
        for name,data in patch.items():
            target=context/name;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:stream.write(data)
        names=('test_monthly_artifacts.py','test_alpha_world_ms_pdf_accuracy.py',
               'test_alpha_world_ms_copy.py','test_alpha_world_ms_subtitle.py',
               'test_alpha_world_ms_date_headers.py','test_alpha_world_ms_chart_style.py',
               'test_alpha_world_ms_allocation_layout.py','test_alpha_world_ms_analytics.py',
               'test_alpha_world_ms_hkd.py','test_alpha_ms_hkd_runtime.py')
        for name in names:
            source=BASE/'tests'/name
            if name in ('test_alpha_world_ms_subtitle.py','test_alpha_world_ms_date_headers.py'):
                source=ROOT/'tests'/name
            copy_file(source,context/'tests'/name)
        manifest={'scope':'MS subtitle and two right-aligned regular-font date headers only',
            'base_image':image,'layout_version':LAYOUT,
            'baseline_files_sha256':{name:build.sha(BASE/name) for name in (pdf_name,'src/ibkr_etl/monthly_artifacts.py')},
            'installed_files_sha256':{name:__import__('hashlib').sha256(data).hexdigest() for name,data in patch.items()},
            'financial_calculation_code_unchanged':True,'alpha_world_t_unchanged':True,
            'source_data_changed':False,'report_approvals_changed':False}
        build.save(context,'MS_LAYOUT_RELEASE',manifest)
        before="import hashlib,pathlib; expected="+repr(manifest['baseline_files_sha256'])+"; assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==h for n,h in expected.items()), 'Installed baseline source drift'"
        after="import json,pathlib,hashlib;from ibkr_etl.monthly_artifacts import factsheet_layout_version;from ibkr_etl.alpha_world_ms_subtitle import _font; m=json.loads(pathlib.Path('/app/MS_LAYOUT_RELEASE.json').read_bytes()); assert factsheet_layout_version('alpha-world-ms')==m['layout_version']; assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==h for n,h in m['installed_files_sha256'].items()); print("+repr(MARKER)+")"
        # Font loading is exercised by the native PDF tests, not by host fonts.
        after=after.replace(';from ibkr_etl.alpha_world_ms_subtitle import _font','')
        docker=f'FROM {image}\nENV PYTHONPATH=/app/src:/app/scripts:/app PYTHONDONTWRITEBYTECODE=1\n'
        docker+='RUN '+json.dumps(['python','-c',before])+'\n'
        docker+='COPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/\nCOPY --chown=10001:10001 tests/ /app/ms-layout-v14r15-tests/\nCOPY --chown=10001:10001 MS_LAYOUT_RELEASE.json /app/\n'
        docker+='RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/ms-layout-v14r15-tests","-v"]\n'
        docker+='RUN '+json.dumps(['python','-c',after])+'\n'
        with (context/'Dockerfile').open('x',encoding='utf-8') as stream:stream.write(docker)
        build.save(folder,'source-intent',{'tag':f'ibkr-platform:alpha-ms-headers-v14r15-{kind}-20261008',
            'base_image':image,'layout_version':LAYOUT,
            'files_sha256':{p.relative_to(context).as_posix():build.sha(p) for p in context.rglob('*') if p.is_file()},
            'scope':manifest['scope'],'production_promotion':False})
    for name,data in patch.items():
        target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    build.save(FOLDER,'prepared',{'complete':True,'candidates':2,'financial_calculations_unchanged':True})
    print(json.dumps({'prepared':True,'candidates':2,'scope':'MS header layout only'}))


def qualify():
    if not json.loads((FOLDER/'prepared.json').read_bytes())['complete']:raise ValueError('Preparation incomplete')
    env={**os.environ,'PYTHONPATH':str(FOLDER/'local-runtime/src')+';'+str(FOLDER/'local-runtime/scripts')+';'+str(FOLDER/'worker/context/tests')+';'+str(ROOT/'scripts'),
         'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1','TEMP':str(ROOT/'.work'),'TMP':str(ROOT/'.work')}
    result=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(FOLDER/'worker/context/tests'),'-v'],env=env,capture_output=True,text=True,encoding='utf-8')
    with (FOLDER/'local-tests.log').open('x',encoding='utf-8') as stream:stream.write(result.stdout+result.stderr)
    if result.returncode:raise ValueError('Scoped isolated tests failed; inspect local-tests.log')
    code="import qualify_alpha_ms_subtitle as q; q.OUTPUT="+repr(str(FOLDER/'replay-pdfs'))+"; q.OUTPUT=__import__('pathlib').Path(q.OUTPUT); q.QA=q.ROOT/'tmp/pdfs/alpha-ms-headers-combined-v14r15-20261008-r2'; from ibkr_etl.alpha_world_ms_pdf_validation import validate_date_headers; q.main(changed_regions={1:(21,764,590,819),2:(480,798,590,838)},extra_validation=validate_date_headers)"
    subprocess.run([sys.executable,'-c',code],env=env,check=True)
    replay=json.loads((FOLDER/'replay-pdfs/manifest.json').read_bytes())
    if replay['count']!=21 or not all(r['financial_metadata_unchanged'] and r['outside_requested_regions_pixel_identical'] for r in replay['checks']):raise ValueError('All-month qualification failed')
    for kind in IMAGES:
        folder=FOLDER/kind
        build.save(folder,'local-qualification',{'status':'isolated_local_candidate_passed',
            'source_intent_sha256':build.sha(folder/'source-intent.json'),'replay':{'months_verified':21,
            'manifest_sha256':build.sha(FOLDER/'replay-pdfs/manifest.json')},'tests_log_sha256':build.sha(FOLDER/'local-tests.log')})
    print(json.dumps({'status':'isolated_local_candidate_passed','months':21,'scope_unchanged':True}))


def native(stage,kind):
    folder=FOLDER/kind
    if stage!='build':
        if stage=='log':
            ack=json.loads((folder/'build-ack.json').read_bytes())
            import re
            response=subprocess.run([build.AZ,'-m','azure.cli','acr','task','logs',
                '--subscription',build.SUB,'-r',build.REGISTRY,'--run-id',ack['run']],
                capture_output=True,text=True,encoding='utf-8',timeout=120,
                env={**os.environ,'PYTHONUTF8':'1'})
            if response.returncode:raise ValueError('Native build log retrieval failed')
            log=response.stdout+response.stderr
            tests=re.findall(r'Ran (\d+) tests',log)
            if MARKER not in log or not tests or int(tests[-1])<40 or '\nFAILED (' in log:raise ValueError('Native qualification failed')
            with (folder/'native-build.log').open('x',encoding='utf-8') as stream:stream.write(log)
            build.save(folder,'native-build-qualified',{'qualified':True,'run':ack['run'],'linux_tests':int(tests[-1]),'native_log_sha256':build.sha(folder/'native-build.log')})
            print(json.dumps({'native_tests':int(tests[-1]),'kind':kind}));return
        old=sys.argv;sys.argv=[old[0],stage,'--folder',str(folder)]
        try:build.main()
        finally:sys.argv=old
        return
    source=json.loads((folder/'source-intent.json').read_bytes())
    if build.sha(folder/'source-intent.json')!=json.loads((folder/'local-qualification.json').read_bytes())['source_intent_sha256']:raise ValueError('Unqualified candidate')
    actual={p.relative_to(folder/'context').as_posix():build.sha(p) for p in (folder/'context').rglob('*') if p.is_file()}
    if actual!=source['files_sha256']:raise ValueError('Frozen context drift')
    jobs={}
    for name in build.JOBS:
        job=build.az('containerapp','job','show','--subscription',build.SUB,'-g',build.GROUP,'-n',name)
        if job['properties']['template']['containers'][0]['image']!=IMAGES['worker']:raise ValueError('Worker baseline drift')
        jobs[name]={'image':IMAGES['worker'],'settings_invariant_sha256':build.invariant(job)}
    web=build.az('containerapp','show','--subscription',build.SUB,'-g',build.GROUP,'-n','ibkrv2stg-web')
    if web['properties']['template']['containers'][0]['image']!=IMAGES['web']:raise ValueError('Website baseline drift')
    build.save(folder,'build-intent',{'tag':source['tag'],'source_intent_sha256':build.sha(folder/'source-intent.json'),
        'local_qualification_sha256':build.sha(folder/'local-qualification.json'),'before_jobs':jobs,
        'before_website_image':IMAGES['web'],'prepared_at':datetime.now(timezone.utc).isoformat(),
        'running_service_update':False,'database_writes':0,'reports_released':False})
    result=build.az('acr','build','--subscription',build.SUB,'-r',build.REGISTRY,'-t',source['tag'],
        '-f',str(folder/'context/Dockerfile'),'--no-logs','--no-wait',str(folder/'context'))
    if not result or not result.get('runId'):raise ValueError('Unknown outcome; reconcile, never redispatch')
    build.save(folder,'build-ack',{'run':result['runId'],'tag':source['tag']})
    print(json.dumps({'kind':kind,'run':result['runId'],'running_services_changed':False}))


if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    elif sys.argv[1]=='qualify':qualify()
    elif sys.argv[1] in ('build','inspect','log'):native(sys.argv[1],sys.argv[2])
    else:raise ValueError('Unknown stage; deployment is not implemented in this build controller')
