"""Isolated ACR render comparison; never deploys its fixture-bearing image."""
import json,os,re,shutil,subprocess,sys
from pathlib import Path
import prepare_upgrade_full_legacy_20261010 as p
import release_platform_access_correction_20261009 as h
from control_upgrade_readers_20261010 import save
from scan_ms_presentation_worker_candidate import verify

REVISION=3 if '--v3' in sys.argv else 2 if '--v2' in sys.argv else 1
for option in ('--v2','--v3'):
    if option in sys.argv:sys.argv.remove(option)
OUT=p.OUT/('render-acceptance-v'+str(REVISION));TAG='ibkr-platform:legacy-render-acceptance-20261010-v'+str(REVISION)


def main(stage):
    source,_,image=verify(p.OUT/'legacypresentation-security-v1');candidate=image['registry']+'/'+image['repository']+'@'+image['digest'];h.WEB=OUT
    if stage=='prepare':
        if OUT.exists():raise ValueError('Preserve comparison evidence')
        context=OUT/'context';context.mkdir(parents=True)
        shutil.copyfile(p.ROOT/'.work/v23-render-test/snapshot.json',context/'snapshot.json')
        shutil.copyfile(p.ROOT/'scripts/qualify_upgrade_legacy_render_20261010.py',context/'qualify.py')
        h.save(context/'config.json',{'snapshot_sha256':h.sha(context/'snapshot.json'),'template_sha256':'31a8a54d83453b14ed58adaadc61ca8b9e64cbfb5516d31f59e9570e38769179'})
        docker='''FROM {original} AS baseline
COPY --chown=10001:10001 qualify.py config.json snapshot.json /qualification/
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src:/app/scripts
RUN ["python","/qualification/qualify.py","baseline"]
FROM {candidate}
COPY --from=baseline --chown=10001:10001 /qualification/ /qualification/
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src:/app/scripts
RUN ["python","/qualification/qualify.py","candidate"]
'''.replace('{original}',source['base_image']).replace('{candidate}',candidate)
        (context/'Dockerfile').write_text(docker,encoding='utf-8')
        h.save(OUT/'source-intent.json',{'tag':TAG,'base_image':source['base_image'],'candidate_image':candidate,'files_sha256':h.manifest(context),
            'scope':'Private offline regression render only; fixture-bearing comparison image must never be deployed. No database, released reports, template wording or approvals changed.'})
        return
    intent=h.read(OUT/'source-intent.json')
    if h.manifest(OUT/'context')!=intent['files_sha256'] or candidate!=intent['candidate_image']:raise ValueError('Comparison inputs changed')
    if stage=='build':
        if (OUT/'build-intent.json').exists():raise ValueError('Reconcile existing build')
        h.save(OUT/'build-intent.json',{'source_intent_sha256':h.sha(OUT/'source-intent.json'),'tag':TAG,'candidate_image':candidate})
        done=subprocess.run([h.build.AZ,'-m','azure.cli','acr','build','-r',h.build.REGISTRY,'-t',TAG,'-f',str(OUT/'context/Dockerfile'),'--no-logs','--no-wait',str(OUT/'context')],capture_output=True,text=True,encoding='utf-8',timeout=180)
        runs=re.findall(r'Queued a build with ID: ([a-z0-9]+)',done.stdout+done.stderr)
        if done.returncode or len(runs)!=1:raise ValueError('Unknown build outcome; reconcile')
        h.save(OUT/'build-ack.json',{'run':runs[0],'tag':TAG});print(json.dumps({'run':runs[0]}));return
    if stage=='inspect':return h.inspect()
    if stage!='logs' or not (OUT/'build-completed.json').exists():raise ValueError('Completed render build required')
    ack=h.read(OUT/'build-ack.json');done=subprocess.run([h.build.AZ,'-m','azure.cli','acr','task','logs','-r',h.build.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120)
    log=done.stdout+done.stderr
    with (OUT/'native-build.log').open('x',encoding='utf-8') as stream:stream.write(log)
    proofs=[json.loads(line.split('LEGACY_RENDER_ACCEPTANCE ',1)[1]) for line in log.splitlines() if line.startswith('LEGACY_RENDER_ACCEPTANCE ')]
    if done.returncode or len(proofs)!=2 or {v['stage'] for v in proofs}!={'baseline','candidate'} or any(v.get('passed') is not True or v['slides']!=20 for v in proofs) or proofs[0]['normalized_package_sha256']!=proofs[1]['normalized_package_sha256']:raise ValueError('Deck equality not established')
    save(OUT,'result',{'passed':True,'candidate_image':candidate,'original_image':source['base_image'],'run':ack['run'],'proofs':proofs,
        'native_log_sha256':h.sha(OUT/'native-build.log'),'live_reports_changed':False,'comparison_image_deployable':False,'powerpoint_application_opened':False})
    print('Exact 20-slide serialized content/render equality verified')


if __name__=='__main__':main(*sys.argv[1:])
