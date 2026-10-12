"""Build-only broker runtime replacement; preserve installed application bytes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import release_platform_access_correction_20261009 as r
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_platform_access_correction_20261009 import invariant

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage12/broker-security-v1'
SOURCE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:8575b64cb212e4ba32de57f00bc21b045320974b91fceacdce9aace8008416d7'
APP = rid('Microsoft.App/containerApps', 'ibkrv2stg-credential-broker')
API = '?api-version=2024-03-01'
r.WEB = OUT


def prepare():
    if OUT.exists(): raise ValueError('Preserve existing build evidence')
    prior = ROOT / '.work/platform-upgrade-20261010-stage12/sync-security-v4/context'
    source = json.loads((prior.parent / 'source-intent.json').read_bytes())
    if r.manifest(prior) != source['files_sha256']: raise ValueError('Qualified build recipe drift')
    context = OUT / 'context'; (context / 'tests').mkdir(parents=True)
    shutil.copytree(prior / 'support', context / 'support')
    shutil.copyfile(ROOT / 'scripts/test_upgrade_broker_contract_20261010.py', context / 'tests/test_broker_contract.py')
    shutil.copyfile(ROOT / 'scripts/qualify_upgrade_broker_runtime_20261010.py', context / 'qualify.py')
    docker = (prior / 'Dockerfile').read_text(encoding='utf-8')
    old = source['base_image']
    if docker.count(old) != 1: raise ValueError('Exact source replacement required')
    docker = docker.replace(old, SOURCE)
    grok = 'RUN ["python","-c","from pathlib import Path;import shutil;shutil.copyfile(\'/app/tests/test_pilot_import_reconcile.py\',\'/qualification/tests/test_pilot_import_reconcile.py\')"]\n'
    if docker.count(grok) != 1: raise ValueError('Original sync-only test insertion differs')
    docker = docker.replace(grok, '')
    docker = docker.replace('CMD ["python","scripts/run_cloud_job.py"]', 'CMD ["python","-m","uvicorn","ibkr_etl.credential_broker:app","--host","0.0.0.0","--port","8000","--no-access-log"]')
    with (context / 'Dockerfile').open('x', encoding='utf-8') as handle: handle.write(docker)
    for path in context.rglob('*.py'): compile(path.read_bytes(), str(path), 'exec')
    r.save(OUT / 'source-intent.json', {'tag': 'ibkr-platform:broker-security-minimal-20261010-v1',
           'base_image': SOURCE, 'python_base': source['python_base'], 'files_sha256': r.manifest(context),
           'scope': 'OS and Python patch runtime only, preserving every installed app file and Python package; broker authorization and set-only contract unchanged',
           'production_promotion': False})
    r.save(OUT / 'local-qualification.json', {'status': 'syntax_passed_native_equality_and_security_pending',
           'tests_run': 0, 'source_intent_sha256': r.sha(OUT / 'source-intent.json')})
    print('Prepared isolated source-preserving broker runtime')


def build():
    source = r.read(OUT / 'source-intent.json')
    if r.manifest(OUT / 'context') != source['files_sha256'] or (OUT / 'build-intent.json').exists():
        raise ValueError('Source drift or existing dispatch; reconcile')
    live = request('GET', APP + API)
    if live['properties']['template']['containers'][0]['image'] != SOURCE or live['properties']['configuration']['ingress']['external']:
        raise ValueError('Exact internal broker baseline required')
    r.save(OUT / 'build-intent.json', {'tag': source['tag'], 'source_intent_sha256': r.sha(OUT / 'source-intent.json'),
           'before_jobs': {}, 'before_application': {'id': APP, 'image': SOURCE, 'invariant': invariant(live)},
           'prepared_at': datetime.now(timezone.utc).isoformat(), 'running_service_update': False})
    ack = r.build.az('acr', 'build', '-r', r.build.REGISTRY, '-t', source['tag'], '-f', str(OUT / 'context/Dockerfile'),
                     '--no-logs', '--no-wait', str(OUT / 'context'))
    if not ack or not ack.get('runId'): raise ValueError('Unknown build acknowledgement; never redispatch')
    r.save(OUT / 'build-ack.json', {'run': ack['runId'], 'tag': source['tag']})
    print(json.dumps({'run': ack['runId']}))


def logs():
    ack = r.read(OUT / 'build-ack.json')
    run = subprocess.run([r.build.AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '-r', r.build.REGISTRY, '--run-id', ack['run']],
                         capture_output=True, text=True, encoding='utf-8', timeout=120, env={**os.environ, 'PYTHONUTF8': '1'})
    log = run.stdout + run.stderr
    if not (OUT / 'native-build.log').exists():
        with (OUT / 'native-build.log').open('x', encoding='utf-8') as handle: handle.write(log)
    proofs = [json.loads(line.split('BROKER_NATIVE_PROOF ', 1)[1]) for line in log.splitlines() if line.startswith('BROKER_NATIVE_PROOF ')]
    if run.returncode or len(proofs) != 2 or {p['stage'] for p in proofs} != {'baseline', 'candidate'} or not all(p['passed'] for p in proofs) or any(proofs[0][k] != proofs[1][k] for k in ('tests', 'source_files', 'source_manifest_sha256', 'broker_source_sha256')):
        print(log[-3500:]); raise ValueError('Native broker preservation/contract checks failed')
    r.save(OUT / 'native-build-qualified.json', {'qualified': True, 'run': ack['run'], 'proofs': proofs,
           'linux_tests_per_image': proofs[0]['tests'], 'native_log_sha256': r.sha(OUT / 'native-build.log')})
    print(json.dumps({'qualified': True, 'proofs': proofs}))


if __name__ == '__main__':
    {'prepare': prepare, 'build': build, 'inspect': r.inspect, 'logs': logs, 'reconcile': r.reconcile}[sys.argv[1]]()
