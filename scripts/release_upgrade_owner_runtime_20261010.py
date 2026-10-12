"""Build a separate no-TLS-override runtime; never alter live owner job contracts."""
import json, os, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import release_platform_access_correction_20261009 as r
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant

ROOT = Path(__file__).resolve().parents[1]
REVISION = 2 if '--v2' in sys.argv else 1
if '--v2' in sys.argv: sys.argv.remove('--v2')
OUT = ROOT / ('.work/platform-upgrade-20261010-stage17/owner-runtime-v' + str(REVISION))
SOURCE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c'
JOB = 'ibkrv2stg-ownerruntime-20261004'
API = '?api-version=2026-07-01'
r.WEB = OUT


def snapshot():
    j = request('GET', rid('Microsoft.App/jobs', JOB) + API)
    if j['properties']['template']['containers'][0]['image'] != SOURCE: raise ValueError('Existing runtime drift')
    return {JOB: {'image': SOURCE, 'settings_invariant_sha256': settings_invariant(j)}}


def prepare():
    if OUT.exists(): raise ValueError('Preserve candidate evidence')
    prior = ROOT / '.work/platform-upgrade-20261010-stage13/metadata-security-v2/context'
    intent = r.read(prior.parent / 'source-intent.json')
    if r.manifest(prior) != intent['files_sha256']: raise ValueError('Qualified recipe drift')
    if r.sha(ROOT / 'scripts/owner_task_provider_runtime.py') != '161db0ff2d9145f3fe72a2d96b0c6e8a2a71eec3fb2a3b0cff70e06aa85ec63d': raise ValueError('Frozen checker changed')
    jobs = snapshot(); c = OUT / 'context'; (c / 'tests').mkdir(parents=True); (c / 'scripts').mkdir()
    shutil.copytree(prior / 'support', c / 'support')
    shutil.copyfile(ROOT / 'tests/test_owner_task_provider_runtime.py', c / 'tests/test_owner_task_provider_runtime.py')
    shutil.copyfile(ROOT / 'scripts/owner_task_provider_runtime.py', c / 'scripts/owner_task_provider_runtime.py')
    shutil.copyfile(ROOT / 'scripts/qualify_upgrade_owner_runtime_20261010.py', c / 'qualify.py')
    docker = (prior / 'Dockerfile').read_text()
    docker = docker.replace('COPY --chown=10001:10001 tests/ /qualification/tests/', 'COPY --chown=10001:10001 tests/ /qualification/tests/\nCOPY --chown=10001:10001 scripts/ /qualification/scripts/')
    old = 'RUN ["python","/build-support/assemble.py"]'
    if docker.count(old) != 1 or docker.count(' SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt') != 1: raise ValueError('Recipe boundary differs')
    # Read the actual OpenSSL compiled defaults in the builder; distributions
    # differ. Never assume /usr/local/ssl or add a TLS environment override.
    add = "from pathlib import Path;import ssl;v=ssl.get_default_verify_paths();f=Path(v.openssl_cafile);d=Path(v.openssl_capath);assert str(f) in ('/usr/lib/ssl/cert.pem','/usr/local/ssl/cert.pem') and d==f.parent/'certs';p=Path('/runtime')/f.parent.relative_to('/');p.mkdir(parents=True,exist_ok=True);assert not (p/'cert.pem').exists();(p/'cert.pem').symlink_to('/etc/ssl/certs/ca-certificates.crt');assert not (p/'certs').exists();(p/'certs').symlink_to('/etc/ssl/certs')"
    docker = docker.replace(old, old + '\nRUN ' + json.dumps(['python', '-c', add]))
    docker = docker.replace(' SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt', '')
    docker = docker.replace('CMD ["python","/qualification/tests/monitor_payload.py"]', 'CMD ["python","-c","raise SystemExit(0)"]')
    with (c / 'Dockerfile').open('x', encoding='utf-8') as f: f.write(docker)
    for p in c.rglob('*.py'): compile(p.read_bytes(), str(p), 'exec')
    r.save(OUT / 'source-intent.json', {'tag': 'ibkr-platform:owner-runtime-minimal-20261010-v' + str(REVISION), 'base_image': SOURCE,
        'files_sha256': r.manifest(c), 'before_jobs': jobs, 'scope': 'Source-preserving OS/Python patch runtime with compiled-default CA paths and no TLS environment override. No owner job deployment or activation.'})
    r.save(OUT / 'local-qualification.json', {'tests_run': 0, 'status': 'syntax_only_native_pending', 'source_intent_sha256': r.sha(OUT / 'source-intent.json')})
    print('Prepared separate owner-runtime candidate; no live definitions changed')


def build():
    source = r.read(OUT / 'source-intent.json')
    if r.manifest(OUT / 'context') != source['files_sha256'] or (OUT / 'build-intent.json').exists(): raise ValueError('Source drift or reconcile existing dispatch')
    if snapshot() != source['before_jobs']: raise ValueError('Live boundary changed')
    r.save(OUT / 'build-intent.json', {'tag': source['tag'], 'source_intent_sha256': r.sha(OUT / 'source-intent.json'),
        'before_jobs': source['before_jobs'], 'prepared_at': datetime.now(timezone.utc).isoformat(), 'running_service_update': False})
    ack = r.build.az('acr', 'build', '-r', r.build.REGISTRY, '-t', source['tag'], '-f', str(OUT / 'context/Dockerfile'), '--no-logs', '--no-wait', str(OUT / 'context'))
    if not ack or not ack.get('runId'): raise ValueError('Unknown acknowledgement; reconcile, never redispatch')
    r.save(OUT / 'build-ack.json', {'run': ack['runId'], 'tag': source['tag']}); print(json.dumps({'run': ack['runId']}))


def logs():
    ack = r.read(OUT / 'build-ack.json')
    result = subprocess.run([r.build.AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '-r', r.build.REGISTRY, '--run-id', ack['run']], capture_output=True, text=True, encoding='utf-8', timeout=120, env={**os.environ, 'PYTHONUTF8': '1'})
    log = result.stdout + result.stderr
    if not (OUT / 'native-build.log').exists():
        with (OUT / 'native-build.log').open('x', encoding='utf-8') as f: f.write(log)
    proofs = [json.loads(x.split('OWNER_NATIVE_PROOF ', 1)[1]) for x in log.splitlines() if x.startswith('OWNER_NATIVE_PROOF ')]
    if result.returncode or len(proofs) != 2 or {p['stage'] for p in proofs} != {'baseline', 'candidate'} or not all(p['passed'] for p in proofs) or any(proofs[0][k] != proofs[1][k] for k in ('tests', 'source_files', 'source_manifest_sha256')):
        print(log[-4500:]); raise ValueError('Owner native qualification incomplete')
    r.save(OUT / 'native-build-qualified.json', {'qualified': True, 'run': ack['run'], 'proofs': proofs, 'linux_tests_per_image': proofs[0]['tests'], 'native_log_sha256': r.sha(OUT / 'native-build.log')})
    print(json.dumps(proofs))


if __name__ == '__main__': {'prepare': prepare, 'build': build, 'inspect': r.inspect, 'reconcile': r.reconcile, 'logs': logs}[sys.argv[1]]()
