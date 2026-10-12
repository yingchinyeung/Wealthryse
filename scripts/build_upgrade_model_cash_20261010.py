"""Build and scan only. Live website deployment has separate acceptance gates."""
import json,os,re,shutil,subprocess,sys
from pathlib import Path
import release_platform_access_correction_20261009 as h
from prepare_upgrade_model_cash_20261010 import ROOT,IMAGE,WEB_SHA
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant

OUT=ROOT/'.work/platform-upgrade-20261010-stage26/model-cash-v3';TAG='ibkr-platform:model-cash-display-20261010-v2'


def unchanged():
    live=request('GET',rid('Microsoft.App/containerApps','ibkrv2stg-web')+'?api-version=2026-07-01');prior=h.read(OUT/'prepared.json')
    if live['properties']['template']['containers'][0]['image']!=IMAGE or settings_invariant(live)!=prior['settings_invariant_sha256']:raise ValueError('Website drift; rebase required')


def main(stage):
    h.WEB=OUT;unchanged()
    if stage=='prepare':
        before=h.read(OUT/'prepared.json');context=OUT/'context'
        if h.manifest(context)!=before['files_sha256'] or (OUT/'source-intent.json').exists():raise ValueError('Candidate changed or already frozen')
        if h.read(OUT/'browser-v1/result.json').get('passed') is not True:raise ValueError('Synthetic responsive tests required')
        shutil.copyfile(ROOT/'scripts/qualify_upgrade_model_cash_20261010.py',context/'qualify.py')
        docker='''FROM {base} AS qualification
COPY --chown=10001:10001 qualify.py /qualification/qualify.py
RUN ["python","/qualification/qualify.py","before"]
COPY --chown=10001:10001 src/ibkr_etl/web.py src/ibkr_etl/model_cash_display.py /app/src/ibkr_etl/
COPY --chown=10001:10001 ALPHA_T_RUNTIME_RELEASE.json /app/
COPY --chown=10001:10001 tests/ /qualification/tests/
RUN ["python","scripts/check_report_runtime.py"]
RUN ["python","/qualification/qualify.py","after"]
FROM {base}
COPY --from=qualification --chown=10001:10001 /app/src/ibkr_etl/web.py /app/src/ibkr_etl/model_cash_display.py /app/src/ibkr_etl/
COPY --from=qualification --chown=10001:10001 /app/ALPHA_T_RUNTIME_RELEASE.json /app/
RUN ["python","scripts/check_report_runtime.py"]
'''.replace('{base}',IMAGE)
        (context/'Dockerfile').write_text(docker,encoding='utf-8')
        h.save(OUT/'source-intent.json',{'tag':TAG,'base_image':IMAGE,'before_jobs':{},'before_web_invariant':before['settings_invariant_sha256'],'files_sha256':h.manifest(context),
            'scope':before['scope'],'expected_changed_files':['src/ibkr_etl/web.py','src/ibkr_etl/model_cash_display.py','ALPHA_T_RUNTIME_RELEASE.json']});return
    source=h.read(OUT/'source-intent.json')
    if h.manifest(OUT/'context')!=source['files_sha256']:raise ValueError('Frozen build source changed')
    if stage=='build':
        if (OUT/'build-intent.json').exists():raise ValueError('Reconcile existing build')
        h.save(OUT/'build-intent.json',{'tag':TAG,'before_jobs':{},'source_intent_sha256':h.sha(OUT/'source-intent.json'),'website_changed':False})
        done=subprocess.run([h.build.AZ,'-m','azure.cli','acr','build','-r',h.build.REGISTRY,'-t',TAG,'-f',str(OUT/'context/Dockerfile'),'--no-logs','--no-wait',str(OUT/'context')],capture_output=True,text=True,encoding='utf-8',timeout=180)
        runs=re.findall(r'Queued a build with ID: ([a-z0-9]+)',done.stdout+done.stderr)
        if done.returncode or len(runs)!=1:raise ValueError('Unknown build outcome; reconcile')
        h.save(OUT/'build-ack.json',{'run':runs[0],'tag':TAG});print(json.dumps({'run':runs[0]}));return
    if stage=='inspect':return h.inspect()
    if stage=='scan':
        import scan_ms_presentation_worker_candidate as scanner
        scanner.jobs_unchanged=lambda _:unchanged();sys.argv=[sys.argv[0],'--folder',str(OUT)];return scanner.main()
    if stage!='logs' or not (OUT/'build-completed.json').exists():raise ValueError('Completed native build required')
    ack=h.read(OUT/'build-ack.json');done=subprocess.run([h.build.AZ,'-m','azure.cli','acr','task','logs','-r',h.build.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120);log=done.stdout+done.stderr
    with (OUT/'native-build.log').open('x',encoding='utf-8') as f:f.write(log)
    proofs=[json.loads(line.split('MODEL_CASH_NATIVE ',1)[1]) for line in log.splitlines() if line.startswith('MODEL_CASH_NATIVE ')]
    if done.returncode or len(proofs)!=1 or proofs[0].get('passed') is not True or proofs[0]['tests']!=301 or proofs[0]['passed_tests']!=300:raise ValueError('Website native qualification incomplete')
    h.save(OUT/'native-build-qualified.json',{'qualified':True,'run':ack['run'],'linux_tests':300,'private_fixture_test_pending_local':True,'proof':proofs[0],'native_log_sha256':h.sha(OUT/'native-build.log')});print('300 offline website regression tests passed; private fixture test requires separate local evidence')


if __name__=='__main__':main(*sys.argv[1:])
