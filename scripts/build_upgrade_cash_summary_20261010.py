"""Frozen code-only candidate build; no private records and no live deployment."""
import json,re,shutil,subprocess,sys
from pathlib import Path
import build_upgrade_model_cash_20261010 as original
from prepare_upgrade_cash_summary_20261010 import ROOT,OUT,IMAGE

h=original.h;TAG='ibkr-platform:cash-summary-display-20261010-v1'

def unchanged():
    live=original.request('GET',original.rid('Microsoft.App/containerApps','ibkrv2stg-web')+'?api-version=2026-07-01')
    if live['properties']['template']['containers'][0]['image']!=IMAGE or original.settings_invariant(live)!=h.read(OUT/'prepared.json')['settings_invariant_sha256']:raise ValueError('Website drift; rebase required')

def main(stage):
    h.WEB=OUT;unchanged()
    context=OUT/'context'
    if stage=='prepare':
        before=h.read(OUT/'prepared.json')
        if h.manifest(context)!=before['files_sha256'] or (OUT/'source-intent.json').exists():raise ValueError('Candidate changed or frozen')
        browser=h.read(OUT/'browser-v3/result.json')
        if browser.get('passed') is not True or browser.get('synthetic_only') is not True:raise ValueError('Synthetic browser qualification required')
        shutil.copyfile(ROOT/'scripts/qualify_upgrade_cash_summary_20261010.py',context/'qualify.py')
        docker='''FROM {base} AS qualification
COPY --chown=10001:10001 qualify.py /qualification/qualify.py
RUN ["python","/qualification/qualify.py","before"]
COPY --chown=10001:10001 src/ibkr_etl/web.py /app/src/ibkr_etl/web.py
COPY --chown=10001:10001 ALPHA_T_RUNTIME_RELEASE.json /app/
COPY --chown=10001:10001 tests/ /qualification/tests/
RUN ["python","scripts/check_report_runtime.py"]
RUN ["python","/qualification/qualify.py","after"]
FROM {base}
COPY --from=qualification --chown=10001:10001 /app/src/ibkr_etl/web.py /app/src/ibkr_etl/web.py
COPY --from=qualification --chown=10001:10001 /app/ALPHA_T_RUNTIME_RELEASE.json /app/
RUN ["python","scripts/check_report_runtime.py"]
'''.replace('{base}',IMAGE)
        (context/'Dockerfile').write_text(docker,encoding='utf-8')
        h.save(OUT/'source-intent.json',{'tag':TAG,'base_image':IMAGE,'before_jobs':{},'before_web_invariant':before['settings_invariant_sha256'],'files_sha256':h.manifest(context),
            'scope':before['scope'],'expected_changed_files':['src/ibkr_etl/web.py','ALPHA_T_RUNTIME_RELEASE.json'],'private_records_uploaded':False});return
    source=h.read(OUT/'source-intent.json')
    if h.manifest(context)!=source['files_sha256']:raise ValueError('Frozen build source changed')
    if stage=='build':
        if (OUT/'build-intent.json').exists():raise ValueError('Reconcile existing build')
        local=h.read(OUT/'local-qualification.json')
        if local.get('passed') is not True or any(source['files_sha256'].get(k)!=v for k,v in local['source_files_sha256'].items()):raise ValueError('Local tests required on unchanged candidate files')
        h.save(OUT/'build-intent.json',{'tag':TAG,'before_jobs':{},'source_intent_sha256':h.sha(OUT/'source-intent.json'),'website_changed':False})
        done=subprocess.run([h.build.AZ,'-m','azure.cli','acr','build','-r',h.build.REGISTRY,'-t',TAG,'-f',str(context/'Dockerfile'),'--no-logs','--no-wait',str(context)],capture_output=True,text=True,encoding='utf-8',timeout=180)
        runs=re.findall(r'Queued a build with ID: ([a-z0-9]+)',done.stdout+done.stderr)
        if done.returncode or len(runs)!=1:raise ValueError('Unknown build outcome; reconcile')
        h.save(OUT/'build-ack.json',{'run':runs[0],'tag':TAG});print(json.dumps({'run':runs[0]}));return
    if stage=='inspect':return h.inspect()
    if stage=='scan':
        import scan_ms_presentation_worker_candidate as scanner
        scanner.jobs_unchanged=lambda _:unchanged();sys.argv=[sys.argv[0],'--folder',str(OUT)];return scanner.main()
    if stage!='logs' or not (OUT/'build-completed.json').exists():raise ValueError('Completed build required')
    ack=h.read(OUT/'build-ack.json')
    done=subprocess.run([h.build.AZ,'-m','azure.cli','acr','task','logs','-r',h.build.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120)
    log=done.stdout+done.stderr
    with (OUT/'native-build.log').open('x',encoding='utf-8') as f:f.write(log)
    proofs=[json.loads(line.split('CASH_SUMMARY_NATIVE ',1)[1]) for line in log.splitlines() if line.startswith('CASH_SUMMARY_NATIVE ')]
    if done.returncode or len(proofs)!=1 or proofs[0].get('passed') is not True or proofs[0]['tests']!=301 or proofs[0]['passed_tests']!=300:raise ValueError('Native qualification incomplete')
    h.save(OUT/'native-build-qualified.json',{'qualified':True,'run':ack['run'],'linux_tests':300,'private_fixture_test_qualified_locally':True,'proof':proofs[0],'native_log_sha256':h.sha(OUT/'native-build.log')})
    print('300 offline native regression tests passed; private fixture kept local')

if __name__=='__main__':main(*sys.argv[1:])
