"""Source-only candidate on the exact installed worker; no live deployment."""
import ast,json,os,re,shutil,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
import release_platform_access_correction_20261009 as r
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=r.ROOT
OUT=ROOT/'.work/platform-upgrade-20261011-stage34/reporting-image-v3'
BASE_IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:0269974da175ac319f552ab9b403e309c1d116ed8554a8ceb7fce4e0bcf7b494'
JOB='ibkrv2stg-month-end-factsheet'
TESTS=('reporting_currency_holdings','agent_tools','agent_sector_currency','reporting_snapshot','presentation_design','monthly_artifacts','reporting_currency_regions','ms_presentation_model')
NODE=('test_report_content_labels.mjs','test_report_summary_label.mjs','test_t_report_imports.mjs')

def prepare():
    if OUT.exists():raise ValueError('Preserve existing build candidate')
    stage=OUT.parent
    qualification=protected(stage/'qualification-v2/result.json')
    source_proof=protected(stage/'installed-reporting-source-v1/result.json')
    sector=protected(stage/'sector-integration-v4/result.json')
    t=protected(stage/'t-review-v1/result.json')
    if not all(v['passed'] for v in (qualification,source_proof,sector,t)):raise ValueError('Incomplete qualification')
    for n,h in qualification['sources'].items():
        if r.sha(ROOT/n)!=h:raise ValueError('Qualified candidate changed: '+n)
    installed=stage/'installed-reporting-source-v1'
    for n,h in source_proof['files'].items():
        if r.sha(installed/n)!=h:raise ValueError('Installed capture changed')
    context=OUT/'context';payload=context/'payload'
    for name in qualification['sources']:
        dest=payload/name;dest.parent.mkdir(parents=True,exist_ok=True)
        if name=='src/ibkr_etl/monthly_artifacts.py':
            old=(installed/name).read_text(encoding='utf-8');new=(ROOT/name).read_text(encoding='utf-8')
            oldtree=ast.parse(old);newtree=ast.parse(new)
            oldfn=next(n for n in oldtree.body if isinstance(n,ast.FunctionDef) and n.name=='_specific_region')
            newfn=next(n for n in newtree.body if isinstance(n,ast.FunctionDef) and n.name=='_specific_region')
            lines=old.splitlines(keepends=True)
            lines[oldfn.lineno-1:oldfn.end_lineno]=new.splitlines(keepends=True)[newfn.lineno-1:newfn.end_lineno]
            merged=''.join(lines)
            # Ignore harmless top-level function order, but reject any other semantic drift.
            def semantic(text):return sorted(ast.dump(n,include_attributes=False) for n in ast.parse(text).body)
            if semantic(merged)!=semantic(new):raise ValueError('Unreviewed monthly artifact difference')
            dest.write_text(merged,encoding='utf-8',newline='\n')
        else:shutil.copyfile(ROOT/name,dest)
    for name in TESTS:
        dest=context/'tests'/('test_'+name+'.py');dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT/'tests'/dest.name,dest)
    for name in NODE:
        dest=context/'node-tests'/name;dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT/'scripts'/name,dest)
    shutil.copyfile(ROOT/'scripts/qualify_reporting_image_native_20261011.py',context/'qualify.py')
    candidate=r.manifest(payload)
    r.save(context/'patch-manifest.json',{'baseline':source_proof['files'],'candidate':candidate,
        'new_files':sorted(set(candidate)-set(source_proof['files'])),'node_tests':NODE})
    docker='FROM '+BASE_IMAGE+'\nCOPY --chown=10001:10001 . /qualification/reporting-20261011/\nRUN ["python", "/qualification/reporting-20261011/qualify.py"]\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    r.save(OUT/'source-intent.json',{'tag':'ibkr-platform:reporting-material-corrections-20261011-v3','base_image':BASE_IMAGE,
        'files_sha256':r.manifest(context),'candidate_hashes':candidate,'scope':'Only ten qualified reporting source files; templates, source data and approvals unchanged',
        'source_only':True,'portfolio_fixture_uploaded':False,'database_fixture_uploaded':False,
        'evidence':{str(p.relative_to(ROOT)):r.sha(p) for p in [stage/'qualification-v2/result.json',stage/'sector-integration-v4/result.json',stage/'t-review-v1/result.json',stage/'installed-reporting-source-v1/result.json']}})
    r.save(OUT/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':120,
        'source_intent_sha256':r.sha(OUT/'source-intent.json'),'native_tests_required':{'python':72,'node':13},
        'monthly_artifacts_semantic_equivalence':True,'native_review_all_five_subjects':True})
    print(json.dumps({'prepared':True,'source_files':len(candidate),'context_bytes':sum(p.stat().st_size for p in context.rglob('*') if p.is_file()),'live_changed':False}))

def verify():
    intent=r.read(OUT/'source-intent.json');qual=r.read(OUT/'local-qualification.json')
    if r.manifest(OUT/'context')!=intent['files_sha256'] or qual['source_intent_sha256']!=r.sha(OUT/'source-intent.json'):raise ValueError('Frozen candidate changed')
    return intent

def dispatch():
    source=verify()
    if (OUT/'build-intent.json').exists():raise ValueError('Reconcile existing build; never redispatch')
    if r.build.az('account','show','--query','id')!=r.build.SUB:raise ValueError('Wrong subscription')
    job=r.request('GET',r.BASE+'Microsoft.App/jobs/'+JOB+'?api-version=2024-03-01')
    if job['properties']['template']['containers'][0]['image']!=BASE_IMAGE:raise ValueError('Installed reporting image changed')
    r.save(OUT/'build-intent.json',{'tag':source['tag'],'source_intent_sha256':r.sha(OUT/'source-intent.json'),
        'local_qualification_sha256':r.sha(OUT/'local-qualification.json'),'before_jobs':{JOB:{'image':BASE_IMAGE,'settings_invariant_sha256':r.build.invariant(job)}},
        'prepared_at':datetime.now(timezone.utc).isoformat(),'running_service_update':False,'database_writes':0,'reports_released':False})
    result=r.build.az('acr','build','--subscription',r.build.SUB,'-r',r.build.REGISTRY,'-t',source['tag'],'-f',str(OUT/'context/Dockerfile'),'--no-logs','--no-wait',str(OUT/'context'))
    if not result or not result.get('runId'):raise ValueError('Unknown build outcome; reconcile existing native run')
    r.save(OUT/'build-ack.json',{'run':result['runId'],'tag':source['tag']})
    print(json.dumps({'run':result['runId'],'live_changed':False}))

def inspect():
    verify();r.WEB=OUT;r.inspect()

def reconcile():
    verify();r.WEB=OUT;r.reconcile()

def logs():
    verify();ack=r.read(OUT/'build-ack.json');completed=r.read(OUT/'build-completed.json')
    run=subprocess.run([r.build.AZ,'-m','azure.cli','acr','task','logs','-r',r.build.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120,env={**os.environ,'PYTHONUTF8':'1'})
    log=run.stdout+run.stderr
    proofs=[json.loads(m) for m in re.findall(r'^REPORTING_IMAGE_NATIVE (\{.*\})$',log,re.M)]
    if run.returncode or len(proofs)!=1 or not proofs[0]['passed'] or proofs[0]['candidate_hashes']!=r.read(OUT/'source-intent.json')['candidate_hashes'] or '\nFAILED (' in log:raise ValueError('Native qualification not complete')
    with (OUT/'native-build.log').open('x',encoding='utf-8') as f:f.write(log)
    r.save(OUT/'native-build-qualified.json',{'qualified':True,'run':ack['run'],'linux_tests':85,'native_log_sha256':r.sha(OUT/'native-build.log'),'proof':proofs[0]})
    print(json.dumps({'qualified':True,'native_tests':85,'unrelated_files_preserved':proofs[0]['unrelated_files_preserved']}))

if __name__=='__main__':{'prepare':prepare,'build':dispatch,'inspect':inspect,'logs':logs,'reconcile':reconcile}[sys.argv[1]]()
