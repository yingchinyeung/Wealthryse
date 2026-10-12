"""Build-only OS remediation, freezing all three installed worker definitions."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import release_platform_access_correction_20261009 as r
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage14/institutional-security-v1'
SOURCE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-etl@sha256:44472829f7be06003fd34d0b188794c05003c075c7c0e500e84b523c0808c405'
JOBS = {'ibkrv2stg-institutional-daily': 'scripts/run_institutional_daily_pipeline.py',
        'ibkrv2stg-flex-discovery': 'scripts/discover_flex_sources.py',
        'ibkrv2stg-sector-classification': 'scripts/refresh_staging_sector_classifications.py'}
API = '?api-version=2026-07-01'
r.WEB = OUT


def snapshot():
    result = {}
    for name, script in JOBS.items():
        j = request('GET', rid('Microsoft.App/jobs', name) + API)
        c = j['properties']['template']['containers'][0]
        if c['image'] != SOURCE or c.get('command') != ['python'] or c.get('args') != [script]:
            raise ValueError('Installed worker boundary changed: ' + name)
        result[name] = {'image': SOURCE, 'settings_invariant_sha256': settings_invariant(j),
                        'identity_settings': j['properties']['configuration'].get('identitySettings', [])}
    return result


def prepare():
    if OUT.exists(): raise ValueError('Preserve existing evidence')
    prior = ROOT / '.work/platform-upgrade-20261010-stage12/sync-security-v4/context'
    intent = r.read(prior.parent / 'source-intent.json')
    if r.manifest(prior) != intent['files_sha256']: raise ValueError('Qualified recipe drift')
    jobs = snapshot()
    c = OUT / 'context'; (c / 'tests').mkdir(parents=True)
    shutil.copytree(prior / 'support', c / 'support')
    for name in ('test_institutional_control_plane.py', 'test_sector_classification.py', 'test_flex_sources.py', 'test_flex_safety.py'):
        shutil.copyfile(ROOT / 'tests' / name, c / 'tests' / name)
    shutil.copyfile(ROOT / 'scripts/qualify_upgrade_institutional_runtime_20261010.py', c / 'qualify.py')
    docker = (prior / 'Dockerfile').read_text(encoding='utf-8')
    grok = 'RUN ["python","-c","from pathlib import Path;import shutil;shutil.copyfile(\'/app/tests/test_pilot_import_reconcile.py\',\'/qualification/tests/test_pilot_import_reconcile.py\')"]\n'
    if docker.count(intent['base_image']) != 1 or docker.count(grok) != 1: raise ValueError('Recipe drift')
    docker = docker.replace(intent['base_image'], SOURCE).replace(grok, '')
    marker = "Path('/qualification/migrations').symlink_to('/app/migrations',target_is_directory=True)"
    if docker.count(marker) != 1: raise ValueError('Qualification link boundary changed')
    docker = docker.replace(marker, marker + ";Path('/qualification/scripts').symlink_to('/app/scripts',target_is_directory=True)")
    docker = docker.replace('CMD ["python","scripts/run_cloud_job.py"]', 'CMD ["python","scripts/run_institutional_daily_pipeline.py"]')
    with (c / 'Dockerfile').open('x', encoding='utf-8') as f: f.write(docker)
    for p in c.rglob('*.py'): compile(p.read_bytes(), str(p), 'exec')
    r.save(OUT / 'source-intent.json', {'tag': 'ibkr-platform:institutional-security-minimal-20261010-v1',
           'base_image': SOURCE, 'python_base': intent['python_base'], 'files_sha256': r.manifest(c),
           'before_jobs': jobs, 'scope': 'OS/Python patch only; exact installed application and Python packages retained. No live promotion authorized by this script.'})
    r.save(OUT / 'local-qualification.json', {'status': 'syntax_only_native_and_database_qualification_pending',
           'tests_run': 0, 'source_intent_sha256': r.sha(OUT / 'source-intent.json')})
    print('Prepared isolated institutional-family candidate; three live definitions unchanged')


def build():
    source = r.read(OUT / 'source-intent.json')
    if r.manifest(OUT / 'context') != source['files_sha256'] or (OUT / 'build-intent.json').exists():
        raise ValueError('Drift or existing dispatch: reconcile')
    if snapshot() != source['before_jobs']: raise ValueError('Live worker drift')
    r.save(OUT / 'build-intent.json', {'tag': source['tag'], 'source_intent_sha256': r.sha(OUT / 'source-intent.json'),
           'before_jobs': source['before_jobs'], 'prepared_at': datetime.now(timezone.utc).isoformat(),
           'running_service_update': False})
    ack = r.build.az('acr', 'build', '-r', r.build.REGISTRY, '-t', source['tag'], '-f', str(OUT / 'context/Dockerfile'),
                     '--no-logs', '--no-wait', str(OUT / 'context'))
    if not ack or not ack.get('runId'): raise ValueError('Unknown acknowledgement; reconcile, never repeat')
    r.save(OUT / 'build-ack.json', {'run': ack['runId'], 'tag': source['tag']})
    print(json.dumps({'run': ack['runId']}))


def logs():
    ack = r.read(OUT / 'build-ack.json')
    result = subprocess.run([r.build.AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '-r', r.build.REGISTRY, '--run-id', ack['run']],
                            capture_output=True, text=True, encoding='utf-8', timeout=120, env={**os.environ, 'PYTHONUTF8': '1'})
    log = result.stdout + result.stderr
    if not (OUT / 'native-build.log').exists():
        with (OUT / 'native-build.log').open('x', encoding='utf-8') as f: f.write(log)
    proofs = [json.loads(x.split('INSTITUTIONAL_NATIVE_PROOF ', 1)[1]) for x in log.splitlines() if x.startswith('INSTITUTIONAL_NATIVE_PROOF ')]
    if result.returncode or len(proofs) != 2 or {p['stage'] for p in proofs} != {'baseline', 'candidate'} or not all(p['passed'] for p in proofs) or any(proofs[0][k] != proofs[1][k] for k in ('tests', 'source_files', 'source_manifest_sha256')):
        print(log[-4500:]); raise ValueError('Native qualification incomplete; no deployment')
    r.save(OUT / 'native-build-qualified.json', {'qualified': True, 'run': ack['run'], 'proofs': proofs,
           'linux_tests_per_image': proofs[0]['tests'], 'native_log_sha256': r.sha(OUT / 'native-build.log')})
    print(json.dumps(proofs))


if __name__ == '__main__':
    {'prepare': prepare, 'build': build, 'inspect': r.inspect, 'reconcile': r.reconcile, 'logs': logs}[sys.argv[1]]()
