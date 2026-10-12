"""Build-only exact-worker overlay. Never deploys a running workload."""
import json,os,re,shutil,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
import release_platform_access_correction_20261009 as release
from deploy_metadata_monitor import request,BASE
from build_ms_presentation_worker_candidate import invariant

ROOT=Path(__file__).resolve().parents[1]
BUNDLE=ROOT/'.work/platform-upgrade-20261009-stage3'
release.WEB=BUNDLE/'worker-release-v3'
release.IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:746db20f417f7586301ddd2a4ed48ccfe481b6190568d0835c0244db82716716'
release.MARKER='PASS: scoped monthly worker safety; financial renderers unchanged'
BASE_IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:af15770889dc589e493e496cc92334c2296f16ae1bc9f7869d2a2eb50852f1a9'
JOB='ibkrv2stg-month-end-factsheet'

def prepare():
    folder=release.WEB
    if folder.exists():raise ValueError('Preserve existing build evidence')
    context=folder/'context'
    shutil.copytree(BUNDLE/'worker',context/'worker')
    shutil.copytree(BUNDLE/'tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(BUNDLE/'Dockerfile',context/'Dockerfile')
    source={'tag':'ibkr-platform:platform-worker-safety-20261009-v3','base_image':BASE_IMAGE,
            'files_sha256':release.manifest(context),'financial_renderers_and_templates_unchanged':True,
            'scope':'Normal monthly worker only; exact snapshots, session concurrency lock and hash-verified immutable artifacts',
            'governed_design_regeneration_qualified':False,'report_approvals_changed':False}
    release.save(folder/'source-intent.json',source)
    run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],
        env={**os.environ,'WORKER_CANDIDATE':str(context/'worker/generate_month_end_package.py'),'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'},
        capture_output=True,text=True,encoding='utf-8',timeout=60)
    with (folder/'local-tests.log').open('x',encoding='utf-8') as f:f.write(run.stdout+run.stderr)
    if run.returncode:raise ValueError('Local tests failed')
    all_files={p.relative_to(context).as_posix():release.sha(p) for p in context.rglob('*') if p.is_file()}
    if all_files!=source['files_sha256']:raise ValueError('Unmanifested files appeared during local qualification')
    count=int(re.findall(r'Ran (\d+) tests',run.stderr)[-1])
    release.save(folder/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':count,
        'source_intent_sha256':release.sha(folder/'source-intent.json'),'log_sha256':release.sha(folder/'local-tests.log')})
    print(json.dumps({'prepared':True,'tests_passed':count,'live_changes':False}))

def dispatch():
    folder=release.WEB;source=release.read(folder/'source-intent.json');qualified=release.read(folder/'local-qualification.json')
    if release.manifest(folder/'context')!=source['files_sha256'] or qualified['source_intent_sha256']!=release.sha(folder/'source-intent.json'):
        raise ValueError('Frozen build drift')
    job=request('GET',BASE+'Microsoft.App/jobs/'+JOB+'?api-version=2024-03-01')
    if job['properties']['template']['containers'][0]['image']!=BASE_IMAGE:raise ValueError('Worker baseline drift')
    release.save(folder/'build-intent.json',{'tag':source['tag'],'source_intent_sha256':release.sha(folder/'source-intent.json'),
        'local_qualification_sha256':release.sha(folder/'local-qualification.json'),
        'before_jobs':{JOB:{'image':BASE_IMAGE,'settings_invariant_sha256':invariant(job)}},
        'before_website_image':release.IMAGE,'prepared_at':datetime.now(timezone.utc).isoformat(),
        'running_service_update':False,'database_writes':0})
    result=release.build.az('acr','build','--subscription',release.build.SUB,'-r',release.build.REGISTRY,
        '-t',source['tag'],'-f',str(folder/'context/Dockerfile'),'--no-logs','--no-wait',str(folder/'context'))
    if not result or not result.get('runId'):raise ValueError('Unknown build acknowledgement; reconcile, never redispatch')
    release.save(folder/'build-ack.json',{'run':result['runId'],'tag':source['tag']})
    print(json.dumps({'run':result['runId'],'live_changes':False}))

if __name__=='__main__':
    {'prepare':prepare,'build':dispatch,'inspect':release.inspect,'logs':release.logs,'reconcile':release.reconcile}[sys.argv[1]]()
