"""Own-base source-only snapshot upgrade; never deploy or upload database fixtures."""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import release_platform_access_correction_20261009 as h
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT/'.work/platform-upgrade-20261011-stage38'
OUT = STAGE/'snapshot-image-v2'
BASE_CAPTURE = STAGE/'snapshot-source-v1'
QUALIFIED = STAGE/'qualified-source-v1'
JOB = 'ibkrv2stg-report-snapshot'
TESTS = ('reporting_snapshot','agent_tools','agent_sector_currency','reporting_currency_holdings',
    'monthly_reporting_analytics','fx_selection','alpha_world_ms_hkd','alpha_world_ms_crisis',
    'alpha_world_t_crisis','alpha_world_t_crisis_policy','snapshot_entrypoint_preservation')
ASSET = 'src/ibkr_etl/assets/alpha_world_ms_crisis_market_v1.json'
ASSET_SHA = '422df9135669fe1478f0d18544d1a55926e612859659ea72b151c73a496d81e2'

def unchanged():
    if b.bindings(b.current_resources()) != protected(STAGE/'resume-v1/result.json')['boundary']['workloads']:
        raise ValueError('Azure workload drift')

def prepare():
    if OUT.exists(): raise ValueError('Preserve existing candidate')
    installed = h.read(BASE_CAPTURE/'result.json'); accepted = h.read(QUALIFIED/'result.json')
    closure = h.read(QUALIFIED/'dependency-closure.json')
    names = ['src/ibkr_etl/'+n+'.py' for n in closure['changed']]
    payload = OUT/'context/payload'
    for name in names:
        if h.sha(QUALIFIED/name) != accepted['files_sha256'][name]: raise ValueError('Qualified dependency drift')
        (payload/name).parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(QUALIFIED/name,payload/name)
    entry = 'scripts/freeze_month_end_snapshot.py'
    # Existing entrypoint stays identical apart from the reviewed no-replacement
    # preflight and quality gate. All model modules are exact qualified copies.
    old = (BASE_CAPTURE/entry).read_text(encoding='utf-8')
    new = (ROOT/entry).read_text(encoding='utf-8')
    if h.sha(BASE_CAPTURE/entry) != installed['files_sha256'][entry]: raise ValueError('Entrypoint baseline drift')
    baseline_main = next(n for n in ast.parse(old).body if isinstance(n,ast.FunctionDef) and n.name=='main')
    new_main = next(n for n in ast.parse(new).body if isinstance(n,ast.FunctionDef) and n.name=='main')
    if len(new_main.body) != len(baseline_main.body): raise ValueError('Entrypoint outer scope changed')
    (payload/entry).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/entry,payload/entry)
    names.append(entry)
    baseline = {n:installed['files_sha256'][n] for n in names if n in installed['files_sha256']}
    for n,digest in baseline.items():
        if h.sha(BASE_CAPTURE/n) != digest: raise ValueError('Captured source drift')
    runtime = OUT/'local-runtime'
    shutil.copytree(BASE_CAPTURE/'src',runtime/'src')
    shutil.copytree(BASE_CAPTURE/'scripts',runtime/'scripts')
    shutil.copytree(payload,runtime,dirs_exist_ok=True)
    if h.sha(ROOT/ASSET) != ASSET_SHA: raise ValueError('Archived public-market asset differs')
    (runtime/ASSET).parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/ASSET,runtime/ASSET)
    tests = runtime/'tests';tests.mkdir()
    for name in TESTS: shutil.copyfile(ROOT/'tests'/('test_'+name+'.py'),tests/('test_'+name+'.py'))
    env = {**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts',tests))),
        'TEMP':str(ROOT/'.work/platform-upgrade-20261011-stage33/test-temp'),'TMP':str(ROOT/'.work/platform-upgrade-20261011-stage33/test-temp')}
    run = subprocess.run([sys.executable,'-m','unittest','discover','-s',str(tests),'-v'],cwd=runtime,env=env,capture_output=True,text=True,timeout=180)
    (OUT/'local-tests.log').write_text(run.stdout+run.stderr,encoding='utf-8')
    if run.returncode or re.search(r'\bskipped=',run.stderr):
        print(run.stderr[-5000:]);raise ValueError('Isolated snapshot qualification failed')
    count = int(re.findall(r'Ran (\d+) tests',run.stderr)[-1])
    context = OUT/'context'
    shutil.copytree(tests,context/'tests')
    shutil.copyfile(ROOT/'scripts/qualify_snapshot_native_20261011.py',context/'qualify.py')
    names.append(ASSET)
    prepared = {'consumer':JOB,'base_image':installed['image'],'baseline':baseline,'candidate':{**h.manifest(payload),ASSET:ASSET_SHA},
        'new_files':[n for n in names if n not in baseline],'dependency_closure':closure['closure'],'test_count':count,
        'inherited_assets':{ASSET:ASSET_SHA},
        'scope':'Snapshot collection dependencies copied exactly from qualified reporter; own snapshot base and unrelated source preserved. Standalone retries preserve existing active snapshots; new snapshots require data-quality acceptance.',
        'qualified_source_capture_sha256':h.sha(QUALIFIED/'result.json'),'installed_source_capture_sha256':h.sha(BASE_CAPTURE/'result.json')}
    h.save(context/'prepared.json',prepared)
    docker = 'FROM '+accepted['image']+' AS qualified_reference\nFROM '+installed['image']+' AS qualification\nCOPY --chown=10001:10001 . /qualification/snapshot-correction/\nCOPY --from=qualified_reference --chown=10001:10001 /app/'+ASSET+' /qualification/snapshot-correction/inherited/'+ASSET+'\nRUN ["python","/qualification/snapshot-correction/qualify.py"]\nFROM '+installed['image']+'\n'
    for name in names: docker += 'COPY --from=qualification --chown=10001:10001 /app/'+name+' /app/'+name+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    h.save(OUT/'source-intent.json',{'tag':'ibkr-platform:snapshot-correction-20261011-v2','base_image':installed['image'],
        'files_sha256':h.manifest(context),'candidate_hashes':prepared['candidate'],'scope':prepared['scope'],
        'source_only':True,'private_records_uploaded':False,'database_fixture_uploaded':False})
    h.save(OUT/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':count,
        'source_intent_sha256':h.sha(OUT/'source-intent.json'),'log_sha256':h.sha(OUT/'local-tests.log')})
    print(json.dumps({'prepared':True,'local_tests':count,'changed_source_files':len(names),'deployed':False}))

def main(stage):
    h.WEB = OUT
    if stage == 'prepare': return prepare()
    source = h.read(OUT/'source-intent.json')
    if h.manifest(OUT/'context') != source['files_sha256']: raise ValueError('Frozen source drift')
    unchanged()
    if stage == 'build':
        if (OUT/'build-intent.json').exists(): raise ValueError('Reconcile existing run')
        h.save(OUT/'build-intent.json',{'tag':source['tag'],'before_jobs':{},'source_intent_sha256':h.sha(OUT/'source-intent.json'),'private_records_uploaded':False})
        run = subprocess.run([h.build.AZ,'-m','azure.cli','acr','build','-r',h.build.REGISTRY,'-t',source['tag'],'-f',str(OUT/'context/Dockerfile'),'--no-logs','--no-wait',str(OUT/'context')],capture_output=True,text=True,encoding='utf-8',timeout=180)
        ids = re.findall(r'Queued a build with ID: ([a-z0-9]+)',run.stdout+run.stderr)
        if run.returncode or len(ids)!=1: raise ValueError('Unknown build outcome; reconcile before retry')
        h.save(OUT/'build-ack.json',{'run':ids[0],'tag':source['tag']});print(json.dumps({'run':ids[0]}));return
    if stage=='inspect': return h.inspect()
    if stage=='reconcile': return h.reconcile()
    if stage=='scan':
        import scan_ms_presentation_worker_candidate as scanner
        scanner.jobs_unchanged=lambda _:unchanged()
        sys.argv=[sys.argv[0],'--folder',str(OUT)];return scanner.main()
    if stage!='logs':raise ValueError('Unknown operation')
    ack=h.read(OUT/'build-ack.json')
    run=subprocess.run([h.build.AZ,'-m','azure.cli','acr','task','logs','-r',h.build.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120)
    log=run.stdout+run.stderr
    with (OUT/'native-build.log').open('x',encoding='utf-8') as f:f.write(log)
    proofs=[json.loads(x) for x in re.findall(r'^SNAPSHOT_CORRECTION_NATIVE (\{.*\})$',log,re.M)]
    if run.returncode or len(proofs)!=1 or not proofs[0]['passed'] or proofs[0]['candidate_hashes']!=source['candidate_hashes'] or proofs[0]['tests']!=h.read(OUT/'local-qualification.json')['tests_run']:
        raise ValueError('Native qualification incomplete; diagnostics preserved')
    h.save(OUT/'native-build-qualified.json',{'qualified':True,'run':ack['run'],'linux_tests':proofs[0]['tests'],'proof':proofs[0],'native_log_sha256':h.sha(OUT/'native-build.log')})
    print(json.dumps({'native_tests':proofs[0]['tests'],'passed':True}))

if __name__=='__main__':main(sys.argv[1])
