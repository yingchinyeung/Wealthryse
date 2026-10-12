"""MS response filenames only; exact installed parent, immutable evidence, web-only cutover."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

import build_ms_presentation_worker_candidate as b
import release_alpha_ms_headers_v14r15 as native
import deploy_ms_headers_v14r15 as deploy
import deploy_ms_presentation_worker_release as jobs
from deploy_metadata_monitor import request
from run_specialist_gateway_read_verification import retain

ROOT=b.ROOT
FOLDER=ROOT/'.work/ms-download-names-20261008-r2'
PRIOR=ROOT/'.work/alpha-ms-crisis-release-20261008/candidate-v6'
IMAGES={'web':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:d857da01fa5d03dcd8d5e1214bd3e4696a88bf30bd1d25fd495d6abe6d2aada6',
        'worker':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:1b1583be5be5296e9362220193152426aa341d3db78c26b8ec1eaf69d079eebe'}
MARKER='PASS: MS download names only; immutable archive, authorization and report bytes preserved'
REVISION='ms-download-names-v1'
NAME='src/ibkr_etl/web.py'
AUTHORIZATION='Owner accepted cleaner MS download names; website image-only update'
BASELINE_COMPLETION_PATH=PRIOR/'end-to-end-completion.json'

def read(folder,name):return json.loads((folder/(name+'.json')).read_bytes())
def protected(folder,name,record):
    b.save(folder,name,{**record,'protected_receipt':retain(record,'ms-download-names-v1-'+name)})

def prepare():
    if FOLDER.exists():raise ValueError('Frozen attempt exists; never overwrite')
    scan=PRIOR/'web/scan-v1';digest=IMAGES['web'].split(':')[-1]
    raw=(scan/digest).read_bytes();assert hashlib.sha256(raw).hexdigest()==digest
    manifest=json.loads(raw);config_digest=manifest['config']['digest'].split(':')[-1]
    config_raw=(scan/config_digest).read_bytes();assert hashlib.sha256(config_raw).hexdigest()==config_digest
    config=json.loads(config_raw);captured=None
    with tarfile.open(scan/'image.docker.tar') as archive:
        docker=json.load(archive.extractfile('manifest.json'))[0]
        assert hashlib.sha256(archive.extractfile(docker['Config']).read()).hexdigest()==config_digest
        assert len(docker['Layers'])==len(config['rootfs']['diff_ids'])
        for layer,diff_id in zip(docker['Layers'],config['rootfs']['diff_ids']):
            h=hashlib.sha256()
            with archive.extractfile(layer) as stream:
                while chunk:=stream.read(1024*1024):h.update(chunk)
            assert 'sha256:'+h.hexdigest()==diff_id
            with tarfile.open(fileobj=archive.extractfile(layer),mode='r|') as contents:
                for entry in contents:
                    if entry.name.removeprefix('./').removeprefix('/')=='app/'+NAME:
                        assert entry.isfile() and 0<entry.size<2*1024*1024
                        captured=contents.extractfile(entry).read()
    assert captured is not None
    original=captured.decode('utf-8');current=(ROOT/NAME).read_text(encoding='utf-8')
    helper=native.node_source(current,'_monthly_download_filename')[1]
    anchor='def _download_monthly_report('
    assert original.count(anchor)==1
    patched=original.replace(anchor,helper+'\n\n\n'+anchor)
    functions=('_download_monthly_report','_download_monthly_review_report','download_onboarding_historical_report')
    for function in functions:
        node,source=native.node_source(patched,function)
        old='f\'attachment; filename="{PurePosixPath(artifact["filename"]).name}"\''
        subject='subject' if function==functions[-1] else 'subject_id'
        new='f\'attachment; filename="{_monthly_download_filename(artifact, subject_id='+subject+', period_end=period_end)}"\''
        assert source.count(old)==1
        patched=native.replace_symbol(patched,function,source.replace(old,new))
    allowed=set(functions)|{'_monthly_download_filename'}
    assert native.unchanged(original,allowed)==native.unchanged(patched,allowed)
    # Verify each original function is byte-identical after undoing just its header edit.
    for function in functions:
        old_source=native.node_source(original,function)[1]
        new_source=native.node_source(patched,function)[1]
        subject='subject' if function==functions[-1] else 'subject_id'
        new='f\'attachment; filename="{_monthly_download_filename(artifact, subject_id='+subject+', period_end=period_end)}"\''
        assert new_source.replace(new,old)==old_source
    FOLDER.mkdir();baseline=FOLDER/'baseline';baseline.mkdir()
    (baseline/'web.py').write_bytes(captured)
    b.save(baseline,'verification',{'image':IMAGES['web'],'all_layer_digests_verified':True,
        'web_sha256':hashlib.sha256(captured).hexdigest()})
    runtime=FOLDER/'local-runtime'
    for section in ('src','scripts'):
        shutil.copytree(PRIOR/'local-runtime'/section,runtime/section,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (runtime/NAME).write_text(patched,encoding='utf-8',newline='\n')
    context=FOLDER/'web/context';context.mkdir(parents=True)
    target=context/NAME;target.parent.mkdir(parents=True);target.write_bytes(patched.encode())
    shutil.copytree(PRIOR/'web/context/tests',context/'tests')
    for name in ('test_alpha_ms_download_names.py','test_monthly_reports.py'):
        shutil.copyfile(ROOT/'tests'/name,context/'tests'/name)
    # A baseline policy test reads this installed script as a fixture. It is not overlaid.
    fixture=context/'scripts/release_internal_reports.py';fixture.parent.mkdir()
    shutil.copyfile(PRIOR/'web/context/scripts/release_internal_reports.py',fixture)
    release={'scope':'MS Content-Disposition filenames only','base_image':IMAGES['web'],
        'baseline_web_sha256':hashlib.sha256(captured).hexdigest(),'installed_web_sha256':b.sha(target),
        'changed_functions':list(functions),'new_helper':'_monthly_download_filename',
        'report_bytes_unchanged':True,'calculations_unchanged':True,'storage_keys_unchanged':True,
        'authorization_unchanged':True,'other_subjects_unchanged':True,'database_writes':0}
    b.save(context,'MS_DOWNLOAD_NAME_RELEASE',release)
    before="import pathlib,hashlib,json; p=pathlib.Path('/app'); assert hashlib.sha256((p/'src/ibkr_etl/web.py').read_bytes()).hexdigest()=="+repr(release['baseline_web_sha256'])+"; m=json.loads((p/'MS_CRISIS_RELEASE.json').read_bytes()); assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in m['installed_files_sha256'].items())"
    after="import pathlib,hashlib; from ibkr_etl.web import _monthly_download_filename; assert hashlib.sha256(pathlib.Path('/app/src/ibkr_etl/web.py').read_bytes()).hexdigest()=="+repr(release['installed_web_sha256'])+"; assert _monthly_download_filename({'report_type':'factsheet'},subject_id='alpha-world-ms',period_end='2026-09-30')=='AlphaWorld_MS_Factsheet_2026-09-30.pdf'; print("+repr(MARKER)+")"
    docker=f"FROM {IMAGES['web']}\nENV PYTHONPATH=/app/src:/app/scripts:/app PYTHONDONTWRITEBYTECODE=1\n"
    docker+='RUN '+json.dumps(['python','-c',before])+'\n'
    docker+='COPY --chown=10001:10001 src/ibkr_etl/web.py /app/src/ibkr_etl/web.py\nCOPY --chown=10001:10001 tests/ /app/ms-download-name-tests/\nCOPY --chown=10001:10001 MS_DOWNLOAD_NAME_RELEASE.json /app/\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/ms-download-name-tests","-v"]\n'
    docker+='RUN '+json.dumps(['python','-c',after])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    b.save(FOLDER/'web','source-intent',{'tag':'ibkr-platform:alpha-ms-download-names-v1-web-20261008',
        'base_image':IMAGES['web'],'scope':release['scope'],'files_sha256':{
            p.relative_to(context).as_posix():b.sha(p) for p in context.rglob('*') if p.is_file()}})
    print(json.dumps({'prepared':True,'scope':release['scope'],'non_header_logic_unchanged':True}))

def qualify():
    folder=FOLDER/'web';context=folder/'context'
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(FOLDER/'local-runtime/src',FOLDER/'local-runtime/scripts',context/'tests',ROOT/'scripts'))),
         'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1','TEMP':str(ROOT/'.work'),'TMP':str(ROOT/'.work')}
    result=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],
                          env=env,capture_output=True,text=True,encoding='utf-8')
    logs=list(FOLDER.glob('local-tests-*.log'))
    log=FOLDER/f'local-tests-{len(logs)+1}.log'
    with log.open('x',encoding='utf-8') as stream:stream.write(result.stdout+result.stderr)
    if result.returncode:raise ValueError('Isolated tests failed; inspect log')
    count=int(re.findall(r'Ran (\d+) tests',result.stderr)[-1]);assert count>=100
    prior_downloads=read(PRIOR,'browser-downloads-v1')
    rows=prior_downloads if isinstance(prior_downloads,list) else prior_downloads['rows'];assert len(rows)==21
    periods=[r['period_end'] for r in rows];assert len(set(periods))==21
    code="from ibkr_etl.web import _monthly_download_filename as f; periods="+repr(periods)+"; assert all(f({'report_type':k},subject_id='alpha-world-ms',period_end=d)==f'AlphaWorld_MS_{l}_{d}{e}' for d in periods for k,l,e in [('factsheet','Factsheet','.pdf'),('presentation','Presentation','.pptx')]); print('21 historical month pairs verified')"
    subprocess.run([sys.executable,'-c',code],env=env,check=True)
    b.save(folder,'local-qualification',{'status':'isolated_local_candidate_passed',
        'source_intent_sha256':b.sha(folder/'source-intent.json'),'replay':{'months_verified':21},
        'tests_passed':count,'tests_log_sha256':b.sha(log),
        'qualification':'Installed-source header-only diff, native endpoint safeguards, all historical names; reports not regenerated'})
    print(json.dumps({'isolated_tests':count,'month_pairs':21,'report_bytes_unchanged':True}))

def complete_local_runtime():
    """Recover the complete immutable web parent's imports, not dirty checkout imports."""
    scan=PRIOR/'web/scan-v1';digest=IMAGES['web'].split(':')[-1]
    config_digest=json.loads((scan/digest).read_bytes())['config']['digest'].split(':')[-1]
    config=json.loads((scan/config_digest).read_bytes());files={}
    with tarfile.open(scan/'image.docker.tar') as archive:
        manifest=json.load(archive.extractfile('manifest.json'))[0]
        for name,wanted in zip(manifest['Layers'],config['rootfs']['diff_ids']):
            h=hashlib.sha256()
            with archive.extractfile(name) as stream:
                while chunk:=stream.read(1024*1024):h.update(chunk)
            assert 'sha256:'+h.hexdigest()==wanted
            with tarfile.open(fileobj=archive.extractfile(name),mode='r|') as layer:
                for entry in layer:
                    key=entry.name.removeprefix('./').removeprefix('/')
                    if entry.isfile() and (key.startswith('app/src/ibkr_etl/') or
                            key.startswith('app/scripts/') and key.endswith(('.py','.mjs','.json'))):
                        assert '..' not in Path(key).parts and entry.size<20*1024*1024
                        files[key.removeprefix('app/')]=layer.extractfile(entry).read()
    assert files[NAME]==(FOLDER/'baseline/web.py').read_bytes()
    for name,data in files.items():
        if name==NAME:continue
        path=FOLDER/'local-runtime'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    b.save(FOLDER,'complete-runtime-capture',{'image':IMAGES['web'],'all_layer_hashes_verified':True,
        'source_files_sha256':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}})
    print(json.dumps({'complete_immutable_parent_imports_recovered':True,'files':len(files)}))

def gates():
    import scan_ms_presentation_worker_candidate as scanner
    scanner.verify(FOLDER/'web');scanner.jobs_unchanged(FOLDER/'web')
    scan=read(FOLDER/'web','scan-completed')
    protected_scan,_=deploy.restore_protected(read(FOLDER/'web','scan-protected-receipt'))
    assert protected_scan==scan and scan['assessment']['status']=='pass' and scan['assessment']['blocking_findings']==0
    built=read(FOLDER/'web','build-completed')['output_images'][0]
    assert scan['image']==built['registry']+'/'+built['repository']+'@'+built['digest']
    assert read(FOLDER/'web','native-build-qualified')['linux_tests']==read(FOLDER/'web','local-qualification')['tests_passed']
    baseline=read(PRIOR,'end-to-end-completion');deploy.verify_protected(baseline)
    assert baseline['status']=='ms_report_approval_blocker_resolved'
    assert baseline['targets']['ibkrv2stg-web']==IMAGES['web'] and baseline['rollback_transaction_verified']
    return scan['image']

def cutover(stage):
    image=gates();folder=FOLDER/'deployment-v1'
    current=request('GET',deploy.WEB+deploy.API)
    auth=request('GET',deploy.WEB+'/authConfigs/current'+deploy.API)
    actual=current['properties']['template']['containers'][0]['image']
    if stage=='plan':
        if folder.exists():raise ValueError('Protected plan already exists')
        jobs.window();assert actual==IMAGES['web']
        assert current['properties']['configuration']['activeRevisionsMode']=='Single'
        template=deploy.clean(copy.deepcopy(current['properties']['template']))
        for container in template['containers']:
            for env in container.get('env',[]):
                if env.get('secretRef') and env.get('value')=='':env.pop('value')
        rollback=copy.deepcopy(template);template['containers'][0]['image']=image;template['revisionSuffix']=REVISION
        folder.mkdir()
        protected(folder,'plan',{'before':current,'auth_before':auth,'invariant':deploy.web_hash(current),
            'image':image,'apply_body':{'properties':{'template':template}},
            'rollback_body':{'properties':{'template':rollback}},'rollback_image_qualified':IMAGES['web'],
            'rollback_qualification_sha256':b.sha(BASELINE_COMPLETION_PATH),
            'authorization':AUTHORIZATION,
            'database_writes':0,'worker_changes':False,'report_approvals_changed':False})
        print(json.dumps({'plan_ready':True,'website_only':True,'qualified_rollback':True}));return
    plan=read(folder,'plan');deploy.verify_protected(plan)
    assert deploy.web_hash(current)==plan['invariant'] and auth['properties']==plan['auth_before']['properties']
    assert plan['image']==image and b.sha(BASELINE_COMPLETION_PATH)==plan['rollback_qualification_sha256']
    if stage=='apply':
        jobs.window()
        for job in b.JOBS:jobs.idle(job)
        assert actual==IMAGES['web']
        protected(folder,'apply-intent',{'from_image':actual,'to_image':image,'plan_sha256':b.sha(folder/'plan.json')})
        result=request('PATCH',deploy.WEB+deploy.API,data=plan['apply_body'])
        protected(folder,'apply-ack',{'id':result.get('id'),'state':result.get('properties',{}).get('provisioningState')})
        print(json.dumps({'applied':True,'website_only':True}));return
    assert stage=='verify' and actual==image
    props=current['properties'];revision='ibkrv2stg-web--'+REVISION
    if props['provisioningState']!='Succeeded' or props['latestReadyRevisionName']!=revision:
        print(json.dumps({'waiting':True,'state':props['provisioningState']}));return
    running=request('GET',deploy.WEB+'/revisions/'+revision+deploy.API)['properties']
    assert running['healthState']=='Healthy' and running['active'] and running['runningState'] in ('Running','RunningAtMaxScale')
    traffic=props['configuration']['ingress']['traffic']
    assert len(traffic)==1 and traffic[0]['weight']==100 and traffic[0]['revisionName']==revision
    protected(folder,'qualified',{'status':'passed','image':image,'revision':revision,
        'settings_preserved':True,'authentication_preserved':True,'workers_preserved':True,
        'database_writes':0,'report_bytes_changed':False,'approvals_changed':False})
    print(json.dumps({'verified':True,'website_healthy':True,'settings_preserved':True}))

def seal():
    image=gates();deployment=read(FOLDER/'deployment-v1','qualified');deploy.verify_protected(deployment)
    assert deployment['status']=='passed' and deployment['image']==image
    current=request('GET',deploy.WEB+deploy.API);plan=read(FOLDER/'deployment-v1','plan');deploy.verify_protected(plan)
    assert current['properties']['template']['containers'][0]['image']==image
    assert current['properties']['provisioningState']=='Succeeded' and deploy.web_hash(current)==plan['invariant']
    assert request('GET',deploy.WEB+'/authConfigs/current'+deploy.API)['properties']==plan['auth_before']['properties']
    old=read(PRIOR,'browser-downloads-qualified');deploy.verify_protected(old)
    assert old['months']==21 and old['website_downloads_verified']==42
    expected={r['period_end']:r for r in old['rows']}
    manifest=read(FOLDER,'browser-downloads-v1');rows=manifest['rows']
    assert len(rows)==21 and {r['period_end'] for r in rows}==set(expected)
    verified=[]
    downloads=Path('C:/Users/antho/Downloads').resolve()
    for row in rows:
        period=row['period_end'];files={}
        assert row['review_status']=='No changes selected.' and not row['approval_checkboxes_checked']
        for kind,label,extension in (('factsheet','Factsheet','.pdf'),('presentation','Presentation','.pptx')):
            path=Path(row[kind+'_path']).resolve()
            assert path.parent==downloads and path.is_file()
            stem=f'AlphaWorld_MS_{label}_{period}'
            assert re.fullmatch(re.escape(stem)+r'(?: \(\d+\))?'+re.escape(extension),path.name)
            prior=expected[period]['files'][kind]
            assert b.sha(path)==prior['sha256'] and path.stat().st_size==prior['bytes']
            assert row[kind+'_run_id']==prior['generation_run_id']
            files[kind]={'filename':path.name,'sha256':b.sha(path),'bytes':path.stat().st_size,
                         'generation_run_id':row[kind+'_run_id']}
        verified.append({'period_end':period,'files':files})
    screenshot=FOLDER/'azure-download-names-verified.jpg';assert screenshot.is_file()
    protected(FOLDER,'completion',{'status':'azure_ms_download_names_deployed_and_verified','image':image,
        'months':21,'downloads_verified':42,'historical_and_future_download_names':True,
        'reports_byte_identical_to_previous_qualified_files':True,'storage_keys_and_version_history_preserved':True,
        'financial_calculations_and_layout_unchanged':True,'authentication_and_access_checks_preserved':True,
        'reporting_workers_unchanged':True,'database_writes':0,'approvals_submitted':0,'reports_released':False,
        'other_subjects_unchanged':True,'previously_downloaded_local_files_not_renamed':True,
        'rows':verified,'browser_manifest_sha256':b.sha(FOLDER/'browser-downloads-v1.json'),
        'screenshot_sha256':b.sha(screenshot),'native_tests':108})
    print(json.dumps({'status':'complete','months':21,'downloads':42,'report_bytes_unchanged':True,'approvals_changed':False}))

if __name__=='__main__':
    stage=sys.argv[1]
    if stage=='prepare':prepare()
    elif stage=='complete-runtime':complete_local_runtime()
    elif stage=='qualify':qualify()
    elif stage=='reconcile':
        sys.argv=[sys.argv[0],'reconcile','--folder',str(FOLDER/'web'),'--run-id',sys.argv[2]];b.main()
    elif stage in ('build','inspect','log'):
        native.FOLDER=FOLDER;native.IMAGES=IMAGES;native.MARKER=MARKER;native.native(stage,'web')
    elif stage in ('plan','apply','verify'):cutover(stage)
    elif stage=='seal':seal()
    else:raise ValueError('Unknown stage')
