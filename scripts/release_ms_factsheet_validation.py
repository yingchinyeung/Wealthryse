"""Subject-specific MS approval validation, no report regeneration or approvals."""
import base64, gzip, hashlib, json, os, re, shutil, subprocess, sys, tarfile
from pathlib import Path
import release_alpha_ms_download_names as d
import release_alpha_ms_headers_v14r15 as native
import build_ms_presentation_worker_candidate as b
import deploy_ms_headers_v14r15 as deploy

ROOT=b.ROOT
BASE=ROOT/'.work/ms-download-names-20261008-r2'
FOLDER=ROOT/'.work/ms-release-validation-20261008-r2'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:0ab0bb3e02d4c884f689d80bfe59174e528129431d0a3dfb7d0a2e68a8abcc77'
MARKER='PASS: MS approval uses its own approved layout and full frozen-input numerical validation'
SOURCES=('src/ibkr_etl/web.py','src/ibkr_etl/monthly_artifacts.py')

def configure():
    d.FOLDER=FOLDER;d.IMAGES['web']=IMAGE;d.REVISION='ms-release-validation-v1'
    d.AUTHORIZATION='Owner reports blocked approval of MS factsheet and slides; repair subject-specific validation without bypass or report changes'
    d.BASELINE_COMPLETION_PATH=BASE/'completion.json';d.gates=gates
    native.FOLDER=FOLDER;native.IMAGES=d.IMAGES;native.MARKER=MARKER

def gates():
    import scan_ms_presentation_worker_candidate as scan
    _,_,output=scan.verify(FOLDER/'web');scan.jobs_unchanged(FOLDER/'web')
    assessment=d.read(FOLDER/'web','scan-completed')
    protected,_=deploy.restore_protected(d.read(FOLDER/'web','scan-protected-receipt'))
    assert protected==assessment and assessment['assessment']['status']=='pass' and assessment['assessment']['blocking_findings']==0
    image=output['registry']+'/'+output['repository']+'@'+output['digest'];assert image==assessment['image']
    assert d.read(FOLDER/'web','native-build-qualified')['linux_tests']==d.read(FOLDER/'web','local-qualification')['tests_passed']
    parent=d.read(BASE,'completion');deploy.verify_protected(parent)
    assert parent['image']==IMAGE and parent['downloads_verified']==42
    return image

def prepare():
    if FOLDER.exists():raise ValueError('Preserve frozen attempt')
    scan=BASE/'web/scan-v1';digest=IMAGE.split(':')[-1]
    raw=(scan/digest).read_bytes();assert hashlib.sha256(raw).hexdigest()==digest
    config_sha=json.loads(raw)['config']['digest'].split(':')[-1];raw_config=(scan/config_sha).read_bytes()
    assert hashlib.sha256(raw_config).hexdigest()==config_sha
    config=json.loads(raw_config);found={}
    with tarfile.open(scan/'image.docker.tar') as outer:
        manifest=json.load(outer.extractfile('manifest.json'))[0]
        assert hashlib.sha256(outer.extractfile(manifest['Config']).read()).hexdigest()==config_sha
        assert len(manifest['Layers'])==len(config['rootfs']['diff_ids'])
        for name,wanted in zip(manifest['Layers'],config['rootfs']['diff_ids']):
            h=hashlib.sha256()
            with outer.extractfile(name) as stream:
                while chunk:=stream.read(1024*1024):h.update(chunk)
            assert 'sha256:'+h.hexdigest()==wanted
            with tarfile.open(fileobj=outer.extractfile(name),mode='r|') as layer:
                for entry in layer:
                    key=entry.name.removeprefix('./').removeprefix('/')
                    if key in {'app/'+n for n in SOURCES}:
                        assert entry.isfile() and entry.size<2*1024*1024
                        found[key.removeprefix('app/')]=layer.extractfile(entry).read()
    assert set(found)==set(SOURCES)
    web=found[SOURCES[0]].decode();monthly=found[SOURCES[1]].decode()
    helper=native.node_source((ROOT/SOURCES[0]).read_text(encoding='utf-8'),'_validate_release_factsheet')[1]
    assert web.count('class MonthlyReleaseRequest(BaseModel):')==1
    patched_web=web.replace('class MonthlyReleaseRequest(BaseModel):',helper+'\n\n\nclass MonthlyReleaseRequest(BaseModel):')
    old_source=native.node_source(web,'release_subject_monthly_reports')[1]
    assert old_source.count('validate_factsheet_return_typography(content)')==1
    new_source=old_source.replace('validate_factsheet_return_typography(content)',
        '_validate_release_factsheet(connection, content, subject_id=subject_id, period_end=period_end, snapshot_id=preview["snapshot_id"])')
    patched_web=native.replace_symbol(patched_web,'release_subject_monthly_reports',new_source)
    assert native.unchanged(web,{'release_subject_monthly_reports','_validate_release_factsheet'})==native.unchanged(patched_web,{'release_subject_monthly_reports','_validate_release_factsheet'})
    function=native.node_source(monthly,'validate_alpha_world_factsheet_layout')[1]
    assert function.count('document = PdfReader(str(path))')==1
    patched_monthly=native.replace_symbol(monthly,'validate_alpha_world_factsheet_layout',function.replace('document = PdfReader(str(path))','document = PdfReader(path)'))
    assert native.unchanged(monthly,{'validate_alpha_world_factsheet_layout'})==native.unchanged(patched_monthly,{'validate_alpha_world_factsheet_layout'})
    FOLDER.mkdir();runtime=FOLDER/'local-runtime'
    for part in ('src','scripts'):
        shutil.copytree(BASE/'local-runtime'/part,runtime/part,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    assert b.sha(runtime/SOURCES[0])==hashlib.sha256(found[SOURCES[0]]).hexdigest()
    assert b.sha(runtime/SOURCES[1])==hashlib.sha256(found[SOURCES[1]]).hexdigest()
    context=FOLDER/'web/context';context.mkdir(parents=True)
    patch={SOURCES[0]:patched_web.encode(),SOURCES[1]:patched_monthly.encode(),
           'src/ibkr_etl/ms_factsheet_release.py':(ROOT/'src/ibkr_etl/ms_factsheet_release.py').read_bytes()}
    for name,data in patch.items():
        for parent in (context,runtime):
            target=parent/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    shutil.copytree(BASE/'web/context/tests',context/'tests')
    shutil.copytree(BASE/'web/context/scripts',context/'scripts')
    shutil.copyfile(ROOT/'tests/test_ms_factsheet_release.py',context/'tests/test_ms_factsheet_release.py')
    fixtures=context/'tests/ms-release-fixtures';(fixtures/'pdf').mkdir(parents=True);(fixtures/'inputs').mkdir()
    prior=d.read(BASE,'browser-downloads-v1')['rows'];proof=d.read(d.PRIOR,'browser-downloads-qualified')
    expected={r['period_end']:r for r in proof['rows']}
    for row in prior:
        period=row['period_end'];pdf=Path(row['factsheet_path'])
        assert b.sha(pdf)==expected[period]['files']['factsheet']['sha256']
        shutil.copyfile(pdf,fixtures/'pdf'/('AlphaWorld_MS_Factsheet_'+period+'.pdf'))
        source=ROOT/'.work/alpha-ms-crisis-release-20261008/qualified-inputs-v1'/(period+'.json')
        shutil.copyfile(source,fixtures/'inputs'/source.name)
    release={'scope':'MS approval-validator dispatch only; full MS typography and numeric checks, generic checks retained',
        'base_image':IMAGE,'baseline_files_sha256':{n:hashlib.sha256(data).hexdigest() for n,data in found.items()},
        'installed_files_sha256':{n:hashlib.sha256(data).hexdigest() for n,data in patch.items()},
        'report_bytes_unchanged':True,'source_data_changed':False,'approval_writes':0}
    b.save(context,'MS_RELEASE_VALIDATION',release)
    before="import pathlib,hashlib; p=pathlib.Path('/app'); e="+repr(release['baseline_files_sha256'])+"; assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in e.items())"
    after="import pathlib,hashlib; p=pathlib.Path('/app'); e="+repr(release['installed_files_sha256'])+"; assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in e.items()); print("+repr(MARKER)+")"
    docker=f'FROM {IMAGE}\nENV PYTHONPATH=/app/src:/app/scripts:/app PYTHONDONTWRITEBYTECODE=1\n'
    docker+='RUN '+json.dumps(['python','-c',before])+'\nCOPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/\nCOPY --chown=10001:10001 tests/ /app/ms-release-validation-tests/\nCOPY --chown=10001:10001 MS_RELEASE_VALIDATION.json /app/\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/ms-release-validation-tests","-v"]\nRUN '+json.dumps(['python','-c',after])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    b.save(FOLDER/'web','source-intent',{'tag':'ibkr-platform:ms-release-validation-v1-web-20261008','base_image':IMAGE,
        'scope':release['scope'],'files_sha256':{p.relative_to(context).as_posix():b.sha(p) for p in context.rglob('*') if p.is_file()}})
    print(json.dumps({'prepared':True,'existing_reports':21,'report_changes':0,'approval_writes':0}))

def qualify():
    context=FOLDER/'web/context';env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1',
        'PYTHONPATH':os.pathsep.join(map(str,(FOLDER/'local-runtime/src',FOLDER/'local-runtime/scripts',context/'tests',ROOT/'scripts'))),
        'TEMP':str(ROOT/'.work'),'TMP':str(ROOT/'.work')}
    result=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],env=env,capture_output=True,text=True,encoding='utf-8')
    log=FOLDER/f'local-tests-{len(list(FOLDER.glob("local-tests-*.log")))+1}.log'
    with log.open('x',encoding='utf-8') as stream:stream.write(result.stdout+result.stderr)
    if result.returncode:raise ValueError('Isolated qualification failed; inspect log')
    count=int(re.findall(r'Ran (\d+) tests',result.stderr)[-1])
    b.save(FOLDER/'web','local-qualification',{'status':'isolated_local_candidate_passed','source_intent_sha256':b.sha(FOLDER/'web/source-intent.json'),
        'replay':{'months_verified':21},'tests_passed':count,'tests_log_sha256':b.sha(log),'report_bytes_unchanged':True,'approval_writes':0})
    print(json.dumps({'passed':True,'tests':count,'actual_months':21,'approval_validator_bypassed':False}))

def cloud_verify():
    """Execute the installed validator against real files with a read-only transaction."""
    image=gates();deployment=d.read(FOLDER/'deployment-v1','qualified');deploy.verify_protected(deployment)
    assert deployment['image']==image and deployment['status']=='passed'
    prior_qualified=d.read(d.PRIOR/'worker/candidate-runtime-v1','qualified');deploy.verify_protected(prior_qualified)
    assert b.sha(d.PRIOR/'worker/candidate-runtime-v1/verification.json')==prior_qualified['verification_sha256']
    prior=d.read(d.PRIOR/'worker/candidate-runtime-v1','verification')['artifacts']
    expected={r['period_end']:{k:r[k] for k in ('snapshot_id','data_sha256','factsheet_sha256','presentation_sha256')} for r in prior}
    assert len(expected)==21
    parent=d.read(BASE,'completion');deploy.verify_protected(parent)
    expected_bindings=[]
    for row in sorted(parent['rows'],key=lambda r:r['period_end']):
        period=row['period_end'];files={kind:{k:row['files'][kind][k] for k in ('sha256','bytes','generation_run_id')} for kind in ('factsheet','presentation')}
        assert all(files[kind]['sha256']==expected[period][kind+'_sha256'] for kind in files)
        expected_bindings.append({'period_end':period,'snapshot_id':expected[period]['snapshot_id'],
            'data_sha256':expected[period]['data_sha256'],'files':files})
    binding_sha=hashlib.sha256(json.dumps(expected_bindings,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    installed=d.read(FOLDER/'web/context','MS_RELEASE_VALIDATION')['installed_files_sha256']
    source='''import os,json,hashlib,pathlib,psycopg
from psycopg.rows import dict_row
from ibkr_etl import web
PERIODS=__PERIODS__
BINDING_SHA=__BINDING_SHA__
manifest=json.loads(pathlib.Path('/app/MS_RELEASE_VALIDATION.json').read_bytes())
assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==h for n,h in manifest['installed_files_sha256'].items())
rows=[]
with psycopg.connect(os.environ['APPROVAL_DATABASE_URL'],connect_timeout=10,row_factory=dict_row) as c:
 c.execute('SET TRANSACTION READ ONLY')
 c.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
 for period in PERIODS:
  p=web._monthly_release_candidate(c,'alpha-world-ms',period,lock=False)
  assert p['release_eligible']
  s=c.execute('SELECT data_sha256 FROM reporting_snapshots WHERE tenant_id=%s AND snapshot_id=%s',(web.TENANT_ID,p['snapshot_id'])).fetchone()
  files={}
  for a in p['artifacts']:
   kind=a['report_type']
   content=web.download_blob_bytes(web.AZURE_STORAGE_ACCOUNT_URL,a['container_name'],a['blob_name'])
   digest=hashlib.sha256(content).hexdigest()
   assert digest==a['sha256']
   if kind=='factsheet':web._validate_release_factsheet(c,content,subject_id='alpha-world-ms',period_end=period,snapshot_id=p['snapshot_id'])
   files[kind]={'sha256':digest,'bytes':len(content),'generation_run_id':a['generation_run_id']}
  row={'period_end':period,'snapshot_id':str(p['snapshot_id']),'data_sha256':s['data_sha256'],'files':files,'approved_types':p['approved_types']}
  rows.append(row)
  print('MS_RELEASE_MONTH_PROOF:'+json.dumps(row),flush=True)
 c.rollback()
bindings=[{k:r[k] for k in ('period_end','snapshot_id','data_sha256','files')} for r in rows]
assert hashlib.sha256(json.dumps(bindings,sort_keys=True,separators=(',',':')).encode()).hexdigest()==BINDING_SHA
print('MS_RELEASE_CLOUD_PROOF:'+json.dumps({'passed':True,'months':len(rows),'files_verified':2*len(rows),'binding_sha256':BINDING_SHA,'database_writes':0,'approval_writes':0,'report_bytes_changed':False}))
'''.replace('__PERIODS__',repr(sorted(expected))).replace('__BINDING_SHA__',repr(binding_sha))
    encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    command='python -c "import base64,gzip;exec(gzip.decompress(base64.b64decode(\''+encoded+'\')))"'
    assert len(command)<1900,'Keep the remote diagnostic beneath the console URL limit'
    result=subprocess.run([b.AZ,'-m','azure.cli','containerapp','exec','--subscription',b.SUB,
        '-g',b.GROUP,'-n','ibkrv2stg-web','--container','web','--revision',deployment['revision'],
        '--command',command,'--only-show-errors'],capture_output=True,text=True,encoding='utf-8',timeout=240,
        env={**os.environ,'PYTHONUTF8':'1'})
    marker='MS_RELEASE_CLOUD_PROOF:'
    lines=[line[len(marker):] for line in result.stdout.splitlines() if line.startswith(marker)]
    if result.returncode or len(lines)!=1:
        attempt=len(list(FOLDER.glob('cloud-diagnostic-unconfirmed-*.json')))+1
        b.save(FOLDER,f'cloud-diagnostic-unconfirmed-{attempt}',{'exit_code':result.returncode,
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'command_length':len(command),
            'stdout_sha256':hashlib.sha256(result.stdout.encode()).hexdigest(),
            'stderr_sha256':hashlib.sha256(result.stderr.encode()).hexdigest(),
            'output_tail':result.stdout[-1500:],'error_tail':result.stderr[-1000:],
            'approval_writes':0,'read_only':True})
        raise ValueError('Read-only cloud diagnostic did not return one proof; inspect retained diagnostic receipt; no approval was submitted')
    proof=json.loads(lines[0])
    assert proof['passed'] and proof['months']==21 and proof['files_verified']==42,proof
    month_marker='MS_RELEASE_MONTH_PROOF:'
    proof['rows']=[json.loads(line[len(month_marker):]) for line in result.stdout.splitlines() if line.startswith(month_marker)]
    assert len(proof['rows'])==21 and proof['binding_sha256']==binding_sha
    d.protected(FOLDER,'cloud-read-only-qualification',{'image':image,**proof})
    print(json.dumps({'installed_validator_passed':True,'months':21,'real_files_verified':42,'approval_writes':0}))

def seal():
    image=gates();cloud=d.read(FOLDER,'cloud-read-only-qualification');deploy.verify_protected(cloud)
    deployment=d.read(FOLDER/'deployment-v1','qualified');deploy.verify_protected(deployment)
    assert cloud['passed'] and cloud['image']==image==deployment['image'] and cloud['months']==21
    current=d.request('GET',deploy.WEB+deploy.API);plan=d.read(FOLDER/'deployment-v1','plan');deploy.verify_protected(plan)
    assert current['properties']['template']['containers'][0]['image']==image
    assert current['properties']['provisioningState']=='Succeeded'
    assert current['properties']['latestReadyRevisionName']==deployment['revision']
    assert deploy.web_hash(current)==plan['invariant']
    assert d.request('GET',deploy.WEB+'/authConfigs/current'+deploy.API)['properties']==plan['auth_before']['properties']
    browser=d.read(FOLDER,'browser-september-downloads')
    september=next(r for r in cloud['rows'] if r['period_end']==browser['period_end'])
    for kind,file in browser['files'].items():
        path=Path(file['path']);assert path.is_file()
        assert b.sha(path)==file['sha256']==september['files'][kind]['sha256']
        assert path.stat().st_size==file['bytes']==september['files'][kind]['bytes']
        assert file['generation_run_id']==september['files'][kind]['generation_run_id']
    screenshot=FOLDER/'azure-ms-approval-repair-verified.jpg';assert screenshot.is_file()
    d.protected(FOLDER,'completion',{'status':'azure_ms_subject_specific_release_validation_deployed_and_verified',
        'image':image,'revision':deployment['revision'],'months_verified':21,'files_verified':42,
        'native_tests':119,'full_ms_layout_and_numerical_validation_enforced':True,
        'generic_validation_retained_for_other_subjects':True,'historical_and_future_ms_validation':True,
        'reports_byte_identical':True,'calculations_and_source_data_unchanged':True,
        'authentication_settings_workers_preserved':True,'approval_writes':0,
        'approval_counts_at_cloud_check':{r['period_end']:len(r['approved_types']) for r in cloud['rows']},
        'cloud_proof_sha256':b.sha(FOLDER/'cloud-read-only-qualification.json'),
        'authenticated_browser_downloads_verified':2,
        'browser_download_proof_sha256':b.sha(FOLDER/'browser-september-downloads.json'),
        'screenshot_sha256':b.sha(screenshot),'rollback_image':IMAGE})
    print(json.dumps({'repair_complete':True,'historical_months':21,'reports_changed':False,'approvals_submitted':0}))

if __name__=='__main__':
    configure();stage=sys.argv[1]
    if stage=='prepare':prepare()
    elif stage=='qualify':qualify()
    elif stage in ('build','inspect','log'):native.native(stage,'web')
    elif stage=='reconcile':sys.argv=[sys.argv[0],'reconcile','--folder',str(FOLDER/'web'),'--run-id',sys.argv[2]];b.main()
    elif stage in ('plan','apply','verify'):d.cutover(stage)
    elif stage=='cloud-verify':cloud_verify()
    elif stage=='seal':seal()
    else:raise ValueError('Unknown stage')
