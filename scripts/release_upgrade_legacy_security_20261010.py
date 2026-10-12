"""Build only in the legacy registry; preserve exact legacy application bytes."""
from datetime import datetime,timezone
import json,os,shutil,subprocess,sys
from pathlib import Path
import release_platform_access_correction_20261009 as r
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
ROOT=Path(__file__).resolve().parents[1]
REVISION=2 if '--v2' in sys.argv else 1
if '--v2' in sys.argv:sys.argv.remove('--v2')
OUT=ROOT/('.work/platform-upgrade-20261010-stage15/legacy-security-v'+str(REVISION))
REGISTRY='ibkrv115rcc62xxqblay'
SOURCE=REGISTRY+'.azurecr.io/ibkr-etl@sha256:0d0eb9105de73b9339bcdbc58a5c05069177f010b21dd13c9bd2f77ff0a7ca91'
JOBS=('ibkr-v11-daily','ibkr-v11-bootstrap','ibkr-v11-history-backfill')
API='?api-version=2026-07-01'
r.WEB=OUT;r.build.REGISTRY=REGISTRY


def snapshot():
    result={}
    for name in JOBS:
        j=request('GET',rid('Microsoft.App/jobs',name)+API)
        if j['properties']['template']['containers'][0]['image']!=SOURCE:raise ValueError('Legacy image drift')
        result[name]={'image':SOURCE,'settings_invariant_sha256':settings_invariant(j),'identity_settings':j['properties']['configuration'].get('identitySettings',[])}
    return result


def prepare():
    if OUT.exists():raise ValueError('Preserve existing evidence')
    prior=ROOT/'.work/platform-upgrade-20261010-stage12/sync-security-v4/context'
    intent=r.read(prior.parent/'source-intent.json')
    if r.manifest(prior)!=intent['files_sha256']:raise ValueError('Qualified recipe drift')
    jobs=snapshot();c=OUT/'context';c.mkdir(parents=True)
    legacy=ROOT.parent/'IBKR Database v1.1'
    shutil.copytree(prior/'support',c/'support')
    shutil.copytree(legacy/'tests',c/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if REVISION==2:
        shutil.copyfile(ROOT/'.work/platform-upgrade-20261010-stage15/reviewed-fixture-v2/test_derived_portfolio.py',c/'tests/test_derived_portfolio.py')
    shutil.copytree(legacy/'infra',c/'infra')
    shutil.copyfile(ROOT/'scripts/qualify_upgrade_legacy_runtime_20261010.py',c/'qualify.py')
    docker=(prior/'Dockerfile').read_text(encoding='utf-8')
    grok='RUN ["python","-c","from pathlib import Path;import shutil;shutil.copyfile(\'/app/tests/test_pilot_import_reconcile.py\',\'/qualification/tests/test_pilot_import_reconcile.py\')"]\n'
    if docker.count(intent['base_image'])!=1 or docker.count(grok)!=1:raise ValueError('Recipe boundary differs')
    docker=docker.replace(intent['base_image'],SOURCE).replace(grok,'')
    marker="Path('/qualification/migrations').symlink_to('/app/migrations',target_is_directory=True)"
    if docker.count(marker)!=1:raise ValueError('Source link boundary differs')
    docker=docker.replace(marker,marker+";Path('/qualification/scripts').symlink_to('/app/scripts',target_is_directory=True)")
    docker=docker.replace('COPY --chown=10001:10001 tests/ /qualification/tests/','COPY --chown=10001:10001 tests/ /qualification/tests/\nCOPY --chown=10001:10001 infra/ /qualification/infra/')
    with (c/'Dockerfile').open('x',encoding='utf-8') as f:f.write(docker)
    for p in c.rglob('*.py'):compile(p.read_bytes(),str(p),'exec')
    r.save(OUT/'source-intent.json',{'tag':'ibkr-platform:legacy-security-minimal-20261010-v'+str(REVISION),'base_image':SOURCE,'python_base':intent['python_base'],
           'files_sha256':r.manifest(c),'before_jobs':jobs,'scope':'OS/Python-only isolated build preserving installed legacy app and packages. No live promotion.'})
    r.save(OUT/'local-qualification.json',{'tests_run':0,'status':'syntax_only_native_and_database_qualification_pending','source_intent_sha256':r.sha(OUT/'source-intent.json')})
    print('Prepared source-preserving legacy runtime candidate')


def build():
    source=r.read(OUT/'source-intent.json')
    if r.manifest(OUT/'context')!=source['files_sha256'] or (OUT/'build-intent.json').exists():raise ValueError('Source drift/existing dispatch; reconcile')
    if snapshot()!=source['before_jobs']:raise ValueError('Live boundary changed')
    r.save(OUT/'build-intent.json',{'tag':source['tag'],'source_intent_sha256':r.sha(OUT/'source-intent.json'),'before_jobs':source['before_jobs'],
           'prepared_at':datetime.now(timezone.utc).isoformat(),'running_service_update':False})
    ack=r.build.az('acr','build','-r',REGISTRY,'-t',source['tag'],'-f',str(OUT/'context/Dockerfile'),'--no-logs','--no-wait',str(OUT/'context'))
    if not ack or not ack.get('runId'):raise ValueError('Unknown acknowledgement; reconcile, never redispatch')
    r.save(OUT/'build-ack.json',{'run':ack['runId'],'tag':source['tag']});print(json.dumps({'run':ack['runId']}))


def logs():
    ack=r.read(OUT/'build-ack.json')
    result=subprocess.run([r.build.AZ,'-m','azure.cli','acr','task','logs','-r',REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120,env={**os.environ,'PYTHONUTF8':'1'})
    log=result.stdout+result.stderr
    if not (OUT/'native-build.log').exists():
        with (OUT/'native-build.log').open('x',encoding='utf-8') as f:f.write(log)
    proofs=[json.loads(x.split('LEGACY_NATIVE_PROOF ',1)[1]) for x in log.splitlines() if x.startswith('LEGACY_NATIVE_PROOF ')]
    if result.returncode or len(proofs)!=2 or {p['stage'] for p in proofs}!={'baseline','candidate'} or not all(p['passed'] for p in proofs) or any(proofs[0][k]!=proofs[1][k] for k in ('tests','source_files','source_manifest_sha256')):
        print(log[-5000:]);raise ValueError('Legacy native qualification incomplete')
    r.save(OUT/'native-build-qualified.json',{'qualified':True,'run':ack['run'],'proofs':proofs,'linux_tests_per_image':proofs[0]['tests'],'native_log_sha256':r.sha(OUT/'native-build.log')})
    print(json.dumps(proofs))


def failure():
    run_id=sys.argv[2];run=r.build.az('acr','task','show-run','-r',REGISTRY,'--run-id',run_id)
    intent=r.read(OUT/'build-intent.json')
    elapsed=(datetime.fromisoformat(run['createTime'].replace('Z','+00:00'))-datetime.fromisoformat(intent['prepared_at'])).total_seconds()
    if run['status']!='Failed' or run.get('outputImages') or not 0<=elapsed<=120:raise ValueError('Exact failed build required')
    result=subprocess.run([r.build.AZ,'-m','azure.cli','acr','task','logs','-r',REGISTRY,'--run-id',run_id],capture_output=True,text=True,encoding='utf-8',timeout=120)
    log=result.stdout+result.stderr
    if result.returncode or SOURCE not in log:raise ValueError('Unexpected failed-build identity')
    with (OUT/'failed-build.log').open('x',encoding='utf-8') as f:f.write(log)
    from run_specialist_gateway_read_verification import retain
    record={'run':run_id,'status':'Failed','no_output_image':True,'native_log_sha256':r.sha(OUT/'failed-build.log'),
            'source_intent_sha256':r.sha(OUT/'source-intent.json'),'live_images_changed':False}
    r.save(OUT/'failed-build.json',{**record,'protected_receipt':retain(record,'upgrade-legacy-build-failed-'+run_id)})
    print(json.dumps(record))


if __name__=='__main__':{'prepare':prepare,'build':build,'inspect':r.inspect,'reconcile':r.reconcile,'logs':logs,'failure':failure}[sys.argv[1]]()
