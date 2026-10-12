"""Prepare/qualify/build the one-file T validation-contract repair only."""
import ast,hashlib,json,os,shutil,subprocess,sys
from datetime import datetime
from pathlib import Path
import release_alpha_ms_headers_v14r15 as native
from deploy_alpha_ms_allocation_geometry import node_source,replace_symbol

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-axis-contract-20261009-v3'
PARENT=ROOT/'.work/alpha-t-runtime-release-20261009-v7/worker'
IMAGES={'worker':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:67a01e058a6b697e3ba0b1891ad4ec877b77fee416b09664f2e33994373d81dd',
 'web':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:a3a7b3de40fd1f675d962d9200286d5fa7f3cebecadbc062fc8cc2d5bc4209cf'}
MARKER='PASS: qualified T presentation axis contract repair'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):
    with p.open('x',encoding='utf-8') as f: json.dump(value,f,indent=2)

def prepare():
    if FOLDER.exists(): raise ValueError('Frozen release folder already exists')
    folder=FOLDER/'worker'; context=folder/'context'; runtime=folder/'local-runtime'
    context.mkdir(parents=True)
    shutil.copytree(PARENT/'local-runtime',runtime,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    name='scripts/validate_subject_presentation.py'
    old=(runtime/name).read_text(encoding='utf-8')
    updated=replace_symbol(old,'validate_subject_presentation',node_source((ROOT/name).read_text(encoding='utf-8'),'validate_subject_presentation')[1])
    expected_old="        result={'status':'passed','slides':20,'t_financial_audit':financial,'t_chart_workbook_audit':workbooks}"
    replacement="        import zipfile\n        from validate_monthly_presentation import _validate_rebased_performance_axis\n        with zipfile.ZipFile(output) as package:\n            performance_axis = _validate_rebased_performance_axis(package, set(package.namelist()))\n        result={'status':'passed','slides':20,'t_financial_audit':financial,'t_chart_workbook_audit':workbooks,\n                'performance_axis':performance_axis}"
    if old.count(expected_old)!=1 or ast.dump(ast.parse(updated))!=ast.dump(ast.parse(old.replace(expected_old,replacement))): raise ValueError('More than the intended T axis repair changed')
    for parent in (runtime,context):
        p=parent/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(updated,encoding='utf-8')
    shutil.copytree(PARENT/'context/tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for helper in ('release_internal_reports.py','alpha_t_presentation_runtime.mjs'):
        shutil.copyfile(PARENT/'local-runtime/scripts'/helper,context/'scripts'/helper)
    shutil.copyfile(ROOT/'tests/test_t_presentation_axis_contract.py',context/'tests/test_t_presentation_axis_contract.py')
    fixtures=context/'tests/t-axis-fixtures'; fixtures.mkdir()
    shutil.copytree(PARENT/'context/tests/t-release-fixtures/inputs',fixtures/'inputs')
    for p in (ROOT/'.work/alpha-t-candidate-reports-20261009-v4/download').glob('AlphaWorld_T_Presentation_*.pptx'): shutil.copyfile(p,fixtures/p.name)
    manifest=json.loads((PARENT/'context/ALPHA_T_RUNTIME_RELEASE.json').read_bytes())
    manifest['base_image']=IMAGES['worker']
    manifest['baseline_files_sha256']={name:hashlib.sha256(old.encode()).hexdigest()}
    manifest['installed_files_sha256'][name]=sha(context/name)
    manifest['scope']='T validation performance_axis handoff only; calculation, design, website, permissions unchanged'
    save(context/'ALPHA_T_RUNTIME_RELEASE.json',manifest)
    before="import hashlib,pathlib; assert hashlib.sha256(pathlib.Path('/app/"+name+"').read_bytes()).hexdigest()=="+repr(manifest['baseline_files_sha256'][name])
    after="import hashlib,pathlib,json; m=json.loads(pathlib.Path('/app/ALPHA_T_RUNTIME_RELEASE.json').read_bytes()); assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==v for n,v in m['installed_files_sha256'].items()); print("+repr(MARKER)+")"
    docker='FROM '+IMAGES['worker']+'\nRUN '+json.dumps(['python','-c',before])+'\n'
    docker+='COPY --chown=10001:10001 scripts/validate_subject_presentation.py /app/scripts/validate_subject_presentation.py\nCOPY --chown=10001:10001 tests/ /app/t-runtime-tests/\nCOPY --chown=10001:10001 ALPHA_T_RUNTIME_RELEASE.json /app/ALPHA_T_RUNTIME_RELEASE.json\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/t-runtime-tests","-v"]\nRUN '+json.dumps(['python','-c',after])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    save(folder/'source-intent.json',{'tag':'ibkr-platform:alpha-t-axis-contract-worker-20261009-v3','base_image':IMAGES['worker'],'files_sha256':{p.relative_to(context).as_posix():sha(p) for p in context.rglob('*') if p.is_file()},'scope':manifest['scope']})
    print(json.dumps({'prepared':True,'runtime_files_changed':[name],'deployed':False}))

def qualify():
    folder=FOLDER/'worker';context=folder/'context';runtime=folder/'local-runtime'
    source=json.loads((folder/'source-intent.json').read_bytes())
    if {p.relative_to(context).as_posix():sha(p) for p in context.rglob('*') if p.is_file()}!=source['files_sha256']: raise ValueError('Frozen context drift')
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts',context/'tests'))),'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TMP':str(ROOT/'.work/t-test-temp'),'TEMP':str(ROOT/'.work/t-test-temp')}
    run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],env=env,capture_output=True,text=True,encoding='utf-8')
    (folder/'local-tests.log').write_text(run.stdout+run.stderr,encoding='utf-8')
    if run.returncode: print(run.stderr[-5000:]);raise ValueError('Qualification failed')
    save(folder/'local-qualification.json',{'status':'isolated_local_candidate_passed','source_intent_sha256':sha(folder/'source-intent.json'),'tests_log_sha256':sha(folder/'local-tests.log'),'all_21_presentation_axes_checked':True})
    print(run.stderr[-200:])

def reconcile(run_id):
    folder=FOLDER/'worker'; source=json.loads((folder/'source-intent.json').read_bytes())
    intent=json.loads((folder/'build-intent.json').read_bytes())
    qualified=json.loads((folder/'local-qualification.json').read_bytes())
    if qualified['status']!='isolated_local_candidate_passed' or not qualified['all_21_presentation_axes_checked'] or qualified['source_intent_sha256']!=sha(folder/'source-intent.json'): raise ValueError('Qualification binding differs')
    actual={p.relative_to(folder/'context').as_posix():sha(p) for p in (folder/'context').rglob('*') if p.is_file()}
    if actual!=source['files_sha256']:raise ValueError('Frozen source differs')
    run=native.build.az('acr','task','show-run','--subscription',native.build.SUB,'-r',native.build.REGISTRY,'--run-id',run_id)
    outputs=run.get('outputImages') or []
    elapsed=(datetime.fromisoformat(run['createTime'].replace('Z','+00:00'))-datetime.fromisoformat(intent['prepared_at'])).total_seconds()
    if run['runId']!=run_id or run['status']!='Succeeded' or not 0<=elapsed<=120 or len(outputs)!=1 or outputs[0]['registry']!=native.build.REGISTRY+'.azurecr.io' or outputs[0]['repository']!='ibkr-platform' or outputs[0]['tag']!=source['tag'].split(':',1)[1]:raise ValueError('Native build does not match intent')
    save(folder/'build-ack.json',{'run':run_id,'tag':source['tag'],'reconciled_after_missing_cli_ack':True,'output_images':outputs})
    save(folder/'build-completed.json',{'run':run_id,'native_status':'Succeeded','output_images':outputs,'source_intent_sha256':sha(folder/'source-intent.json'),'running_services_changed':False})
    print(json.dumps({'existing_run_reconciled':run_id,'image':outputs[0]['digest'],'redispatched':False}))

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='prepare':prepare()
    elif mode=='qualify':qualify()
    elif mode=='reconcile':reconcile(sys.argv[2])
    elif mode in ('build','inspect','log'):
        native.FOLDER=FOLDER;native.IMAGES=IMAGES;native.MARKER=MARKER;native.native(mode,'worker')
    else: raise ValueError('Unsupported release phase')
