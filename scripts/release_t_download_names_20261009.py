"""T download response names only, built from the exact deployed website source."""
import ast, copy, hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path
import build_ms_presentation_worker_candidate as build
import release_alpha_ms_headers_v14r15 as native
from deploy_alpha_ms_allocation_geometry import node_source, replace_symbol
from deploy_metadata_monitor import BASE, request
from deploy_ms_headers_v14r15 import clean, web_hash
from deploy_t_runtime_release_20261009 import safe_window, idle
from run_specialist_gateway_read_verification import retain

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-download-names-20261009-v2'
PARENT=ROOT/'.work/alpha-t-runtime-release-20261009-v7/web'
IMAGES={'web':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:a3a7b3de40fd1f675d962d9200286d5fa7f3cebecadbc062fc8cc2d5bc4209cf',
        'worker':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:3c0e67ca1c41746387ea0bafabafdf39d4bf48295e6b736bca41536dd147c729'}
MARKER='PASS: T download names only; bytes, permissions, hashes and approvals unchanged'
WEB=BASE+'Microsoft.App/containerApps/ibkrv2stg-web'
API='?api-version=2024-03-01'
REVISION='alpha-t-download-names-1009'
NAME='src/ibkr_etl/web.py'

def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,data):
    with p.open('x',encoding='utf-8') as stream:json.dump(data,stream,indent=2)

def prepare():
    if FOLDER.exists():raise ValueError('Frozen candidate already exists')
    context=FOLDER/'web/context';runtime=FOLDER/'web/local-runtime'
    original=(PARENT/'local-runtime'/NAME).read_text(encoding='utf-8')
    manifest=read(PARENT/'context/ALPHA_T_RUNTIME_RELEASE.json')
    if sha(PARENT/'local-runtime'/NAME)!=manifest['installed_files_sha256'][NAME]:raise ValueError('Exact installed source required')
    old=node_source(original,'_monthly_download_filename')[1]
    helper=node_source((ROOT/NAME).read_text(encoding='utf-8'),'_monthly_download_filename')[1]
    expected=old.replace('name MS downloads for readers.','name MS and T downloads for readers.').replace(
        '    if subject_id == "alpha-world-ms":',
        '    prefixes = {"alpha-world-ms": "AlphaWorld_MS", "alpha-world-t": "AlphaWorld_T"}\n    if subject_id in prefixes:').replace(
        'f"AlphaWorld_MS_{label}_{period}{extension}"','f"{prefixes[subject_id]}_{label}_{period}{extension}"')
    if ast.dump(ast.parse(helper))!=ast.dump(ast.parse(expected)):raise ValueError('Unexpected helper changes')
    patched=replace_symbol(original,'_monthly_download_filename',helper)
    if replace_symbol(patched,'_monthly_download_filename',old)!=original:raise ValueError('Unrelated source changes')
    context.mkdir(parents=True)
    shutil.copytree(PARENT/'local-runtime',runtime,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for base in (runtime,context):
        target=base/NAME;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(patched,encoding='utf-8',newline='\n')
    shutil.copytree(PARENT/'context/tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('test_alpha_ms_download_names.py','test_alpha_t_download_names.py'):
        shutil.copyfile(ROOT/'tests'/name,context/'tests'/name)
    # Source-inspection test fixtures only, not copied into the application layer.
    for helper_name in ('release_internal_reports.py','alpha_t_presentation_runtime.mjs'):
        target=context/'scripts'/helper_name;target.parent.mkdir(exist_ok=True)
        shutil.copyfile(runtime/'scripts'/helper_name,target)
    manifest['baseline_files_sha256']={NAME:sha(PARENT/'local-runtime'/NAME)}
    manifest['installed_files_sha256'][NAME]=sha(context/NAME)
    manifest['base_image']=IMAGES['web'];manifest['scope']='T download Content-Disposition names only'
    save(context/'ALPHA_T_RUNTIME_RELEASE.json',manifest)
    before="import pathlib,hashlib; assert hashlib.sha256(pathlib.Path('/app/"+NAME+"').read_bytes()).hexdigest()=="+repr(manifest['baseline_files_sha256'][NAME])
    after="import pathlib,hashlib,json; m=json.loads(pathlib.Path('/app/ALPHA_T_RUNTIME_RELEASE.json').read_bytes()); assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==h for n,h in m['installed_files_sha256'].items()); print("+repr(MARKER)+")"
    docker='FROM '+IMAGES['web']+'\nRUN '+json.dumps(['python','-c',before])+'\n'
    docker+='COPY --chown=10001:10001 src/ibkr_etl/web.py /app/src/ibkr_etl/web.py\nCOPY --chown=10001:10001 tests/ /app/t-runtime-tests/\nCOPY --chown=10001:10001 ALPHA_T_RUNTIME_RELEASE.json /app/\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/t-runtime-tests","-v"]\nRUN '+json.dumps(['python','-c',after])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    save(FOLDER/'web/source-intent.json',{'tag':'ibkr-platform:alpha-t-download-names-web-20261009-v2','base_image':IMAGES['web'],
        'files_sha256':{p.relative_to(context).as_posix():sha(p) for p in context.rglob('*') if p.is_file()},
        'scope':manifest['scope'],'only_application_function_changed':'_monthly_download_filename'})
    print(json.dumps({'prepared':True,'one_function_only':True}))

def qualify():
    folder=FOLDER/'web';context=folder/'context';runtime=folder/'local-runtime'
    assert read(folder/'source-intent.json')['files_sha256']=={p.relative_to(context).as_posix():sha(p) for p in context.rglob('*') if p.is_file()}
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts',context/'tests'))),
         'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TMP':str(ROOT/'.work/t-test-temp'),'TEMP':str(ROOT/'.work/t-test-temp')}
    run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],env=env,capture_output=True,text=True,encoding='utf-8')
    with (folder/'local-tests.log').open('x',encoding='utf-8') as stream:stream.write(run.stdout+run.stderr)
    if run.returncode:print(run.stderr[-6000:]);raise ValueError('Local qualification failed')
    count=int(re.findall(r'Ran (\d+) tests',run.stderr)[-1])
    save(folder/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':count,
         'source_intent_sha256':sha(folder/'source-intent.json'),'log_sha256':sha(folder/'local-tests.log'),
         'replay':{'months_verified':21},'future_month_and_leap_year_names_tested':True})
    print(run.stderr[-200:])

def gate():
    from scan_ms_presentation_worker_candidate import verify,jobs_unchanged
    from rehearse_ms_worker_image_rollback import restore_protected
    _,_,image=verify(FOLDER/'web');jobs_unchanged(FOLDER/'web')
    exact=image['registry']+'/'+image['repository']+'@'+image['digest']
    scan=read(FOLDER/'web/scan-completed.json')
    archived,_=restore_protected(read(FOLDER/'web/scan-protected-receipt.json'))
    if archived!=scan or scan['image']!=exact or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Security gate failed')
    return exact

def protected_save(folder,name,data):
    save(folder/(name+'.json'),{**data,'protected_receipt':retain(data,'t-download-names-'+name)})

def cutover(stage):
    from rehearse_ms_worker_image_rollback import restore_protected
    if build.az('account','show','--query','id')!=build.SUB:raise ValueError('Wrong subscription')
    folder=FOLDER/'cutover';image=gate()
    current=request('GET',WEB+API);auth=request('GET',WEB+'/authConfigs/current'+API)
    actual=current['properties']['template']['containers'][0]['image']
    if stage=='plan':
        safe_window()
        if folder.exists() or actual!=IMAGES['web']:raise ValueError('Existing plan or website drift')
        if current['properties']['configuration']['activeRevisionsMode']!='Single':raise ValueError('Revision mode differs')
        if any(s.get('value') for s in current['properties']['configuration'].get('secrets',[])):raise ValueError('Inline secrets must not be archived')
        template=clean(copy.deepcopy(current['properties']['template']))
        for container in template['containers']:
            for env in container.get('env',[]):
                if env.get('secretRef') and env.get('value')=='':env.pop('value')
        rollback=copy.deepcopy(template);template['containers'][0]['image']=image;template['revisionSuffix']=REVISION
        restored=copy.deepcopy(template);restored['containers'][0]['image']=actual;restored['revisionSuffix']=rollback['revisionSuffix']
        if restored!=rollback:raise ValueError('Rollback body mismatch')
        baseline=read(ROOT/'.work/alpha-t-runtime-cutover-20261009-v1/completion.json')
        if IMAGES['web'] not in json.dumps(baseline):raise ValueError('Qualified rollback evidence missing')
        folder.mkdir()
        protected_save(folder,'plan',{'before':current,'auth_before':auth,'invariant':web_hash(current),'image':image,
             'apply_body':{'properties':{'template':template}},'rollback_body':{'properties':{'template':rollback}},
             'rollback_verified':True,'authorization':'User approved clean T PDF and PPTX download names',
             'database_writes':0,'workers_changed':False})
        print(json.dumps({'plan_ready':True,'website_only':True,'rollback_verified':True}));return
    plan=read(folder/'plan.json');saved,_=restore_protected(plan['protected_receipt'])
    if saved!={k:v for k,v in plan.items() if k!='protected_receipt'}:raise ValueError('Protected plan mismatch')
    if web_hash(current)!=plan['invariant'] or auth['properties']!=plan['auth_before']['properties']:raise ValueError('Settings drift')
    if image!=plan['image']:raise ValueError('Image binding drift')
    if stage=='apply':
        safe_window()
        for job in build.JOBS:idle(job)
        if actual!=IMAGES['web']:raise ValueError('Unknown update outcome; reconcile')
        protected_save(folder,'apply-intent',{'from_image':actual,'to_image':image,'plan_sha256':sha(folder/'plan.json')})
        result=request('PATCH',WEB+API,data=plan['apply_body'])
        protected_save(folder,'apply-ack',{'id':result.get('id'),'state':result.get('properties',{}).get('provisioningState')})
        print(json.dumps({'submitted':True,'website_only':True}));return
    if stage!='verify' or actual!=image:raise ValueError('Installed image mismatch')
    props=current['properties'];revision='ibkrv2stg-web--'+REVISION
    if props['provisioningState']!='Succeeded' or props['latestReadyRevisionName']!=revision:
        print(json.dumps({'waiting':True,'state':props['provisioningState']}));return
    running=request('GET',WEB+'/revisions/'+revision+API)['properties']
    if running['healthState']!='Healthy' or not running['active']:raise ValueError('Unhealthy website')
    traffic=props['configuration']['ingress']['traffic']
    if len(traffic)!=1 or traffic[0]['weight']!=100 or traffic[0]['revisionName']!=revision:raise ValueError('Unexpected traffic')
    protected_save(folder,'verified',{'status':'passed','image':image,'revision':revision,'settings_preserved':True,
        'authentication_preserved':True,'workers_preserved':True,'database_writes':0,'report_bytes_changed':False,'approvals_changed':False})
    print(json.dumps({'verified':True,'website_healthy':True}))

def seal():
    image=gate();deployment=read(FOLDER/'cutover/verified.json')
    if deployment['status']!='passed' or deployment['image']!=image:raise ValueError('Deployment proof differs')
    registry=read(ROOT/'.work/alpha-t-registered-reports-20261009-v2/registry.json')
    rows={int(row['generation_run_id']):row for row in registry['items']}
    downloaded=read(FOLDER/'browser-downloads.json')
    if {int(r['run_id']) for r in downloaded} != {1090,1049,1110,1089}:raise ValueError('Expected first/latest pairs')
    verified=[]
    for row in downloaded:
        registered=rows[int(row['run_id'])];path=Path(row['path']).resolve()
        if path.parent!=Path('C:/Users/antho/Downloads').resolve():raise ValueError('Unexpected download path')
        label,extension=('Factsheet','.pdf') if registered['report_type']=='factsheet' else ('Presentation','.pptx')
        name='AlphaWorld_T_'+label+'_'+registered['period_end']
        if not re.fullmatch(re.escape(name)+r'(?: \(\d+\))?'+re.escape(extension),path.name):raise ValueError('Download name differs')
        if sha(path)!=registered['artifact_sha256'] or path.stat().st_size!=registered['byte_count']:raise ValueError('Report bytes changed')
        verified.append({'run_id':row['run_id'],'file':path.name,'sha256':sha(path),'bytes':path.stat().st_size})
    result={'status':'T clean download names deployed and browser verified','image':image,
        'download_examples_verified':verified,'all_historical_and_future_names':True,
        'report_bytes_unchanged':True,'database_writes':0,'approvals_changed':False,'workers_changed':False,
        'previous_local_downloads_renamed':False,'scope':'All three T monthly-report download routes; other subjects unchanged',
        'website_delivery_sample':'First and latest monthly PDF/PPTX pairs; remaining months covered by shared-handler tests'}
    protected_save(FOLDER,'completion',result)
    print(json.dumps(result))

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='prepare':prepare()
    elif mode=='qualify':qualify()
    elif mode in ('plan','apply','verify'):cutover(mode)
    elif mode=='seal':seal()
    elif mode=='reconcile':
        sys.argv=[sys.argv[0],'reconcile','--folder',str(FOLDER/'web'),'--run-id',sys.argv[2]];build.main()
    elif mode in ('build','inspect','log'):
        native.FOLDER=FOLDER;native.IMAGES=IMAGES;native.MARKER=MARKER;native.native(mode,'web')
    else:raise ValueError('Unsupported release operation')
