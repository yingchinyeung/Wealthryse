"""Build-only OS remediation; preserve metadata job's exact inline payload."""
import base64
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
OUT = ROOT / ('.work/platform-upgrade-20261010-stage13/metadata-security-v' + str(REVISION))
JOB = 'ibkrv2stg-metadata-monitor'
PATH = rid('Microsoft.App/jobs', JOB)
API = '?api-version=2024-03-01'
SOURCE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c'
r.WEB = OUT


def payload(job):
    c = job['properties']['template']['containers'][0]
    args = c.get('args', [])
    if c['image'] != SOURCE or c.get('command') != ['python'] or len(args) != 2 or args[0] != '-c':
        raise ValueError('Unexpected installed metadata command')
    match = re.fullmatch(r"import base64;exec\(compile\(base64.b64decode\('([A-Za-z0-9+/=]+)'\),'metadata-monitor','exec'\)\)", args[1])
    if not match:
        raise ValueError('Exact monitor wrapper required')
    source = base64.b64decode(match[1], validate=True)
    compile(source, 'installed-monitor', 'exec')
    return source


def prepare():
    if OUT.exists(): raise ValueError('Preserve existing evidence')
    prior = ROOT / '.work/platform-upgrade-20261010-stage12/sync-security-v4/context'
    intent = r.read(prior.parent / 'source-intent.json')
    if r.manifest(prior) != intent['files_sha256']: raise ValueError('Qualified recipe drift')
    job = request('GET', PATH + API)
    source = payload(job)
    c = OUT / 'context'; (c / 'tests').mkdir(parents=True)
    shutil.copytree(prior / 'support', c / 'support')
    for name in ('test_operational_monitor.py', 'test_metadata_monitor_access.py', 'test_market_calendar.py'):
        shutil.copyfile(ROOT / 'tests' / name, c / 'tests' / name)
    with (c / 'tests/monitor_payload.py').open('xb') as f: f.write(source)
    shutil.copyfile(ROOT / 'scripts/qualify_upgrade_metadata_runtime_20261010.py', c / 'qualify.py')
    docker = (prior / 'Dockerfile').read_text(encoding='utf-8')
    old = intent['base_image']
    grok = 'RUN ["python","-c","from pathlib import Path;import shutil;shutil.copyfile(\'/app/tests/test_pilot_import_reconcile.py\',\'/qualification/tests/test_pilot_import_reconcile.py\')"]\n'
    if docker.count(old) != 1 or docker.count(grok) != 1: raise ValueError('Recipe boundary differs')
    docker = docker.replace(old, SOURCE).replace(grok, '')
    docker = docker.replace('CMD ["python","scripts/run_cloud_job.py"]', 'CMD ["python","/qualification/tests/monitor_payload.py"]')
    with (c / 'Dockerfile').open('x', encoding='utf-8') as f: f.write(docker)
    r.save(OUT / 'source-intent.json', {'tag': 'ibkr-platform:metadata-security-minimal-20261010-v' + str(REVISION),
           'base_image': SOURCE, 'python_base': intent['python_base'], 'files_sha256': r.manifest(c),
           'payload_sha256': r.sha(c / 'tests/monitor_payload.py'), 'job_invariant': settings_invariant(job),
           'scope': 'OS/Python patch runtime only. Preserve installed application and exact inline monitor payload, permissions, settings and schedule.',
           'production_promotion': False})
    for p in c.rglob('*.py'): compile(p.read_bytes(), str(p), 'exec')
    print('Prepared source-preserving metadata candidate; live job unchanged')


def build():
    source = r.read(OUT / 'source-intent.json')
    if r.manifest(OUT / 'context') != source['files_sha256'] or (OUT / 'build-intent.json').exists():
        raise ValueError('Source drift or existing dispatch; reconcile')
    live = request('GET', PATH + API)
    if settings_invariant(live) != source['job_invariant'] or r.sha(OUT / 'context/tests/monitor_payload.py') != source['payload_sha256'] or payload(live) != (OUT / 'context/tests/monitor_payload.py').read_bytes():
        raise ValueError('Installed monitor drift')
    r.save(OUT / 'build-intent.json', {'tag': source['tag'], 'source_intent_sha256': r.sha(OUT / 'source-intent.json'),
           'before_jobs': {JOB: {'image': SOURCE, 'settings_invariant_sha256': settings_invariant(live)}},
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
        with (OUT / 'native-build.log').open('x', encoding='utf-8') as f: f.write(log)
    proofs = [json.loads(line.split('METADATA_NATIVE_PROOF ', 1)[1]) for line in log.splitlines() if line.startswith('METADATA_NATIVE_PROOF ')]
    if run.returncode or len(proofs) != 2 or {p['stage'] for p in proofs} != {'baseline', 'candidate'} or not all(p['passed'] for p in proofs) or any(proofs[0][k] != proofs[1][k] for k in ('tests', 'source_files', 'source_manifest_sha256', 'payload_sha256', 'excluded_cross_module_tests')):
        print(log[-3500:]); raise ValueError('Exact metadata native qualification incomplete')
    r.save(OUT / 'native-build-qualified.json', {'qualified': True, 'run': ack['run'], 'proofs': proofs,
           'linux_tests_per_image': proofs[0]['tests'], 'native_log_sha256': r.sha(OUT / 'native-build.log')})
    print(json.dumps({'qualified': True, 'proofs': proofs}))


if __name__ == '__main__':
    {'prepare': prepare, 'build': build, 'inspect': r.inspect, 'logs': logs, 'reconcile': r.reconcile}[sys.argv[1]]()
