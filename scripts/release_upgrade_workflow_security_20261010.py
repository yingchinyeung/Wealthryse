"""Separate, source-preserving security candidates; no normal workflow execution."""
from datetime import datetime,timezone
import json,os,re,shutil,subprocess,sys
from pathlib import Path
import release_platform_access_correction_20261009 as r
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage21'
TARGETS={
 'intake':{'job':'ibkrv2stg-template-intake','digest':'22cb599b70c38a98a0fc2dddac42f2fafa5cac4955d5fa37e428107497b7b08c',
   'entry':'register_account_template_intake.py','tests':14,'test_files':['scripts/test_upgrade_intake_contract_20261010.py'],
   'missing_environment_error':'Template intake may run only in controlled environments'},
 'shadow':{'job':'ibkrv2stg-institutional-shadow','digest':'2b0bd37cc80b93b96d40d2d46a6cc13686c6a3afc4a1707269ec772e33f86c39',
   'entry':'reconcile_institutional_shadow.py','tests':11,'test_files':['scripts/test_upgrade_shadow_contract_20261010.py','tests/test_shadow_reconciliation.py'],
   'missing_environment_error':'Shadow reconciliation is restricted to internal production'}}
API='?api-version=2026-07-01'


def source_image(target):return 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:'+TARGETS[target]['digest']


def snapshot(target):
    spec=TARGETS[target];live=request('GET',rid('Microsoft.App/jobs',spec['job'])+API)
    c=live['properties']['template']['containers'][0]
    if c['image']!=source_image(target) or c.get('command')!=['python'] or c.get('args')!=['scripts/'+spec['entry']] or live['properties']['configuration']['triggerType']!='Manual':raise ValueError('Installed workflow boundary changed')
    return {spec['job']:{'image':c['image'],'settings_invariant_sha256':settings_invariant(live)}}


def main(target,stage):
    spec=TARGETS[target];folder=OUT/(target+'-security-v1');r.WEB=folder
    if stage=='prepare':
        if folder.exists():raise ValueError('Preserve existing candidate')
        prior=ROOT/'.work/platform-upgrade-20261010-stage19/onboarding-security-v2/context'
        previous=r.read(prior.parent/'source-intent.json')
        if r.manifest(prior)!=previous['files_sha256']:raise ValueError('Qualified recipe changed')
        before=snapshot(target);context=folder/'context';(context/'tests').mkdir(parents=True)
        shutil.copytree(prior/'support',context/'support')
        for name in spec['test_files']:shutil.copyfile(ROOT/name,context/'tests'/Path(name).name)
        shutil.copyfile(ROOT/'scripts/qualify_upgrade_workflow_runtime_20261010.py',context/'qualify.py')
        r.save(context/'config.json',{k:spec[k] for k in ('entry','tests','missing_environment_error')})
        docker=(prior/'Dockerfile').read_text(encoding='utf-8')
        old=previous['base_image']
        if docker.count(old)!=1:raise ValueError('Base-image boundary changed')
        docker=docker.replace(old,source_image(target)).replace('COPY --chown=10001:10001 qualify.py /qualification/qualify.py','COPY --chown=10001:10001 qualify.py config.json /qualification/')
        marker="Path('/qualification/migrations').symlink_to('/app/migrations',target_is_directory=True)"
        if docker.count(marker)!=1:raise ValueError('Source linkage drift')
        docker=docker.replace(marker,marker+";Path('/qualification/scripts').symlink_to('/app/scripts',target_is_directory=True)")
        docker=docker.replace('scripts/run_self_service_onboarding_analytics.py','scripts/'+spec['entry'])
        with (context/'Dockerfile').open('x',encoding='utf-8') as f:f.write(docker)
        for p in context.rglob('*.py'):compile(p.read_bytes(),str(p),'exec')
        r.save(folder/'source-intent.json',{'tag':'ibkr-platform:'+target+'-security-20261010-v1','base_image':source_image(target),
            'files_sha256':r.manifest(context),'before_jobs':before,'scope':'OS/Python security only; installed application, templates and Python packages retained; no normal workflow, report, approval or data mutation.'})
        r.save(folder/'local-qualification.json',{'status':'syntax_only_native_pending','tests_run':0,'source_intent_sha256':r.sha(folder/'source-intent.json')})
        print('Prepared isolated '+target+' candidate');return
    source=r.read(folder/'source-intent.json')
    if r.manifest(folder/'context')!=source['files_sha256'] or snapshot(target)!=source['before_jobs']:raise ValueError('Source or installed runtime changed')
    if stage=='build':
        if (folder/'build-intent.json').exists():raise ValueError('Reconcile previous build; do not redispatch')
        r.save(folder/'build-intent.json',{'tag':source['tag'],'source_intent_sha256':r.sha(folder/'source-intent.json'),
            'before_jobs':source['before_jobs'],'prepared_at':datetime.now(timezone.utc).isoformat(),'running_service_update':False})
        done=subprocess.run([r.build.AZ,'-m','azure.cli','acr','build','-r',r.build.REGISTRY,'-t',source['tag'],'-f',str(folder/'context/Dockerfile'),
            '--no-logs','--no-wait',str(folder/'context')],capture_output=True,text=True,encoding='utf-8',timeout=180,env={**os.environ,'PYTHONUTF8':'1'})
        runs=re.findall(r'Queued a build with ID: ([a-z0-9]+)',done.stdout+done.stderr)
        if done.returncode or len(runs)!=1:raise ValueError('Unknown build outcome; reconcile, never repeat')
        r.save(folder/'build-ack.json',{'run':runs[0],'tag':source['tag']});print(json.dumps({'target':target,'run':runs[0]}));return
    if stage=='inspect':r.inspect();return
    if stage!='logs':raise ValueError('Unknown stage')
    ack=r.read(folder/'build-ack.json')
    done=subprocess.run([r.build.AZ,'-m','azure.cli','acr','task','logs','-r',r.build.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120,env={**os.environ,'PYTHONUTF8':'1'})
    log=done.stdout+done.stderr
    if not (folder/'native-build.log').exists():
        with (folder/'native-build.log').open('x',encoding='utf-8') as f:f.write(log)
    proofs=[json.loads(line.split('WORKFLOW_NATIVE_PROOF ',1)[1]) for line in log.splitlines() if line.startswith('WORKFLOW_NATIVE_PROOF ')]
    if done.returncode or len(proofs)!=2 or {p['stage'] for p in proofs}!={'baseline','candidate'} or not all(p['passed'] for p in proofs) or any(proofs[0][k]!=proofs[1][k] for k in ('tests','source_files','source_manifest_sha256')):
        print(log[-4500:]);raise ValueError('Native workflow qualification failed; no deployment')
    r.save(folder/'native-build-qualified.json',{'qualified':True,'run':ack['run'],'proofs':proofs,'linux_tests_per_image':proofs[0]['tests'],'native_log_sha256':r.sha(folder/'native-build.log')})
    print(json.dumps(proofs))


if __name__=='__main__':main(*sys.argv[1:])
