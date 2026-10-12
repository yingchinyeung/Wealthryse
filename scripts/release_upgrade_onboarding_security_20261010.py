"""Build-only patch of the website-called onboarding analytics runtime.

Preserve every installed application file and Python package. Native baseline
and candidate must pass the same synthetic T tests; deployment is separate.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import release_platform_access_correction_20261009 as r
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant

ROOT = Path(__file__).resolve().parents[1]
REVISION = 2 if '--v2' in sys.argv else 1
if '--v2' in sys.argv: sys.argv.remove('--v2')
OUT = ROOT/('.work/platform-upgrade-20261010-stage19/onboarding-security-v'+str(REVISION))
JOB = 'ibkrv2stg-onboard-analytics'
SOURCE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:46288cdcfaa511e9a84a1403d2d65f9dd5484317e5aaa3ac15d755087ed7e62c'
ENTRY = 'run_self_service_onboarding_analytics.py'
API = '?api-version=2026-07-01'


def snapshot():
    live = request('GET',rid('Microsoft.App/jobs',JOB)+API)
    c = live['properties']['template']['containers'][0]
    if c['image'] != SOURCE or c.get('command') != ['python'] or c.get('args') != ['scripts/'+ENTRY]:
        raise ValueError('Installed onboarding entrypoint changed')
    return {JOB:{'image':SOURCE,'settings_invariant_sha256':settings_invariant(live)}}


def main(stage):
    r.WEB = OUT
    if stage == 'prepare':
        if OUT.exists(): raise ValueError('Preserve existing candidate')
        prior = ROOT/'.work/platform-upgrade-20261010-stage17/owner-runtime-v2/context'
        original = r.read(prior.parent/'source-intent.json')
        if r.manifest(prior) != original['files_sha256']: raise ValueError('Qualified recipe drifted')
        jobs = snapshot(); context = OUT/'context'; (context/'tests').mkdir(parents=True)
        shutil.copytree(prior/'support',context/'support')
        tests = (('test_upgrade_physical_onboarding_contract_20261010.py',) if REVISION==2 else
                 ('test_alpha_world_t_onboarding.py','test_alpha_world_t_analytics.py','test_alpha_world_t_cache.py'))
        for name in tests: shutil.copyfile(ROOT/('scripts' if REVISION==2 else 'tests')/name,context/'tests'/name)
        shutil.copyfile(ROOT/'scripts/qualify_upgrade_onboarding_runtime_20261010.py',context/'qualify.py')
        if REVISION==2:
            q=context/'qualify.py'; text=q.read_text()
            if text.count('result.testsRun < 20')!=1: raise ValueError('Qualification threshold drift')
            q.write_text(text.replace('result.testsRun < 20','result.testsRun != 11'),encoding='utf-8')
        docker = (prior/'Dockerfile').read_text()
        if docker.count(original['base_image']) != 1: raise ValueError('Recipe source boundary mismatch')
        docker = docker.replace(original['base_image'], SOURCE)
        docker = docker.replace('COPY --chown=10001:10001 scripts/ /qualification/scripts/\n','')
        docker = docker.replace('CMD ["python","-c","raise SystemExit(0)"]','CMD '+json.dumps(['python','scripts/'+ENTRY]))
        with (context/'Dockerfile').open('x',encoding='utf-8') as f: f.write(docker)
        for path in context.rglob('*.py'): compile(path.read_bytes(),str(path),'exec')
        r.save(OUT/'source-intent.json',{'tag':'ibkr-platform:onboarding-security-20261010-v'+str(REVISION),'base_image':SOURCE,
            'files_sha256':r.manifest(context),'before_jobs':jobs,'test_files':list(tests),
            'scope':'OS/Python security patch, preserve all installed application files/packages; no real onboarding, formulas, reports or activation changes.'})
        r.save(OUT/'local-qualification.json',{'status':'syntax_only_native_pending','tests_run':0,
            'source_intent_sha256':r.sha(OUT/'source-intent.json')})
        print('Prepared source-preserving onboarding candidate; live settings unchanged'); return
    source = r.read(OUT/'source-intent.json')
    if r.manifest(OUT/'context') != source['files_sha256'] or snapshot() != source['before_jobs']:
        raise ValueError('Source or installed image drift')
    if stage == 'build':
        if (OUT/'build-intent.json').exists(): raise ValueError('Reconcile prior build; never redispatch')
        r.save(OUT/'build-intent.json',{'tag':source['tag'],'source_intent_sha256':r.sha(OUT/'source-intent.json'),
            'before_jobs':source['before_jobs'],'prepared_at':datetime.now(timezone.utc).isoformat(),'running_service_update':False})
        done = subprocess.run([r.build.AZ,'-m','azure.cli','acr','build','-r',r.build.REGISTRY,'-t',source['tag'],
            '-f',str(OUT/'context/Dockerfile'),'--no-logs','--no-wait',str(OUT/'context')],capture_output=True,text=True,
            encoding='utf-8',timeout=180,env={**os.environ,'PYTHONUTF8':'1'})
        runs = re.findall(r'Queued a build with ID: ([a-z0-9]+)',done.stdout+done.stderr)
        if done.returncode or len(runs) != 1: raise ValueError('Unknown build outcome; reconcile run list, do not repeat')
        r.save(OUT/'build-ack.json',{'run':runs[0],'tag':source['tag']}); print(json.dumps({'run':runs[0]})); return
    if stage == 'inspect': r.inspect(); return
    if stage != 'logs': raise ValueError('Unknown stage')
    ack = r.read(OUT/'build-ack.json')
    done = subprocess.run([r.build.AZ,'-m','azure.cli','acr','task','logs','-r',r.build.REGISTRY,'--run-id',ack['run']],
        capture_output=True,text=True,encoding='utf-8',timeout=120,env={**os.environ,'PYTHONUTF8':'1'})
    log = done.stdout+done.stderr
    if not (OUT/'native-build.log').exists():
        with (OUT/'native-build.log').open('x',encoding='utf-8') as f: f.write(log)
    proofs = [json.loads(line.split('ONBOARDING_NATIVE_PROOF ',1)[1]) for line in log.splitlines() if line.startswith('ONBOARDING_NATIVE_PROOF ')]
    if done.returncode or len(proofs) != 2 or {p['stage'] for p in proofs} != {'baseline','candidate'} or not all(p['passed'] for p in proofs) or any(proofs[0][k] != proofs[1][k] for k in ('tests','source_files','source_manifest_sha256')):
        raise ValueError('Native onboarding qualification failed; no deployment permitted')
    r.save(OUT/'native-build-qualified.json',{'qualified':True,'run':ack['run'],'proofs':proofs,
        'linux_tests_per_image':proofs[0]['tests'],'native_log_sha256':r.sha(OUT/'native-build.log')})
    print(json.dumps(proofs))


if __name__ == '__main__': main(sys.argv[1])
