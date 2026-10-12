"""Build only: preserve exact installed sync application on a minimal runtime."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import release_platform_access_correction_20261009 as r
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant

ROOT = Path(__file__).resolve().parents[1]
REVISION = 4 if '--v4' in sys.argv else (3 if '--v3' in sys.argv else (2 if '--v2' in sys.argv else 1))
for flag in ('--v2', '--v3', '--v4'):
    if flag in sys.argv: sys.argv.remove(flag)
OUT = ROOT / ('.work/platform-upgrade-20261010-stage12/sync-security-v' + str(REVISION))
SOURCE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-etl@sha256:39f73f592d98432b1c1912f57008c2171a591b3c851468ebd20fd97e4a8bdb81'
PYTHON = 'python:3.12.15-slim-bookworm@sha256:2ed6491b93cd49272ee6de2b5a38440c3448360322c089fc23e370722d74179d'
if REVISION == 4:
    PYTHON = 'python:3.12.15-slim-trixie@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe'
JOB = 'ibkrv2stg-account-sync-daily'
r.WEB = OUT


def prepare():
    if OUT.exists(): raise ValueError('Preserve existing candidate')
    c = OUT / 'context'; (c / 'tests').mkdir(parents=True)
    for name in ('test_daily_account_sync.py', 'test_flex_safety.py', 'test_flex_sources.py'):
        shutil.copyfile(ROOT / 'tests' / name, c / 'tests' / name)
    shutil.copyfile(ROOT / 'scripts/qualify_upgrade_sync_runtime_20261010.py', c / 'qualify.py')
    (c / 'support/ibkr_etl').mkdir(parents=True)
    (c / 'support/ibkr_etl/__init__.py').touch()
    shutil.copyfile(ROOT / 'src/ibkr_etl/minimal_runtime.py', c / 'support/ibkr_etl/minimal_runtime.py')
    shutil.copyfile(ROOT / 'scripts/assemble_minimal_risk_runtime.py', c / 'support/assemble.py')
    docker = '''FROM {source} AS installed
COPY --chown=10001:10001 tests/ /qualification/tests/
COPY --chown=10001:10001 qualify.py /qualification/qualify.py
USER 0:0
RUN ["python","-c","from pathlib import Path;Path('/qualification/src').symlink_to('/app/src',target_is_directory=True);Path('/qualification/migrations').symlink_to('/app/migrations',target_is_directory=True)"]
USER 10001:10001
ENV PYTHONPATH=/app/src:/app/scripts PYTHONDONTWRITEBYTECODE=1
RUN ["python","-c","from pathlib import Path;import shutil;shutil.copyfile('/app/tests/test_pilot_import_reconcile.py','/qualification/tests/test_pilot_import_reconcile.py')"]
RUN ["python","/qualification/qualify.py","baseline"]

FROM {python} AS builder
RUN ["python","-c","from pathlib import Path;import shutil;p=Path('/usr/local/lib/python3.12/site-packages');assert p.resolve()==p and p.is_dir();shutil.rmtree(p)"]
COPY --from=installed /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=installed /usr/local/bin/ibkr-etl /usr/local/bin/ibkr-etl
COPY support/ /build-support/
ENV PYTHONPATH=/build-support PYTHONDONTWRITEBYTECODE=1
RUN ["python","-m","pip","check"]
RUN ["python","/build-support/assemble.py"]

FROM scratch
COPY --from=builder /runtime/ /
COPY --from=installed --chown=10001:10001 /app/ /app/
COPY --from=installed --chown=10001:10001 /qualification/ /qualification/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 IBKR_PROJECT_ROOT=/app PYTHONPATH=/app/src:/app/scripts LD_LIBRARY_PATH=/usr/local/lib PATH=/usr/local/bin SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
WORKDIR /app
USER 10001:10001
RUN ["python","/qualification/qualify.py","candidate"]
CMD ["python","scripts/run_cloud_job.py"]
'''.format(source=SOURCE, python=PYTHON)
    if REVISION == 4:
        marker = 'FROM ' + PYTHON + ' AS builder\n'
        upgrade = "RUN apt-get update && apt-get dist-upgrade -y && dpkg --compare-versions \"$(dpkg-query -W -f='${Version}' libsqlite3-0)\" ge 3.46.1-7+deb13u2 && dpkg --compare-versions \"$(dpkg-query -W -f='${Version}' libssl3t64)\" ge 3.5.7-1~deb13u3 && dpkg --compare-versions \"$(dpkg-query -W -f='${Version}' liblzma5)\" ge 5.8.1-1+deb13u2\n"
        if docker.count(marker) != 1: raise ValueError('Exact builder marker required')
        docker = docker.replace(marker, marker + upgrade)
    (c / 'Dockerfile').write_text(docker, encoding='utf-8')
    r.save(OUT / 'source-intent.json', {'tag': 'ibkr-platform:sync-security-minimal-20261010-v' + str(REVISION), 'base_image': SOURCE, 'python_base': PYTHON, 'files_sha256': r.manifest(c), 'scope': 'Isolated minimal OS/Python runtime candidate preserving every installed application file and Python package. Native source equality, original Grok regression test, non-root tests and exact-image scan required before any deployment.', 'production_promotion': False})
    for p in c.rglob('*.py'): compile(p.read_bytes(), str(p), 'exec')
    r.save(OUT / 'local-qualification.json', {'status': 'syntax_only_native_and_database_qualification_pending', 'tests_run': 0, 'source_intent_sha256': r.sha(OUT / 'source-intent.json')})
    print('Frozen isolated sync security candidate; no application or live image changes')


def build():
    intent = r.read(OUT / 'source-intent.json')
    if r.manifest(OUT / 'context') != intent['files_sha256'] or (OUT / 'build-intent.json').exists(): raise ValueError('Source drift or existing build; reconcile')
    job = request('GET', rid('Microsoft.App/jobs', JOB) + '?api-version=2024-03-01')
    if job['properties']['template']['containers'][0]['image'] != SOURCE: raise ValueError('Installed sync image drift')
    r.save(OUT / 'build-intent.json', {'tag': intent['tag'], 'source_intent_sha256': r.sha(OUT / 'source-intent.json'), 'before_jobs': {JOB: {'image': SOURCE, 'settings_invariant_sha256': settings_invariant(job)}}, 'prepared_at': datetime.now(timezone.utc).isoformat(), 'database_writes': 0, 'broker_calls': 0, 'running_service_update': False})
    ack = r.build.az('acr', 'build', '-r', r.build.REGISTRY, '-t', intent['tag'], '-f', str(OUT / 'context/Dockerfile'), '--no-logs', '--no-wait', str(OUT / 'context'))
    if not ack or not ack.get('runId'): raise ValueError('Unknown build acknowledgement: reconcile, never repeat')
    r.save(OUT / 'build-ack.json', {'run': ack['runId'], 'tag': intent['tag']})
    print(json.dumps({'run': ack['runId']}))


def logs():
    ack = r.read(OUT / 'build-ack.json')
    result = subprocess.run([r.build.AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '-r', r.build.REGISTRY, '--run-id', ack['run']], capture_output=True, text=True, encoding='utf-8', timeout=120, env={**os.environ, 'PYTHONUTF8': '1'})
    log = result.stdout + result.stderr
    path = OUT / 'native-build.log'
    if not path.exists(): path.write_text(log, encoding='utf-8')
    proofs = [json.loads(line.split('SYNC_NATIVE_PROOF ', 1)[1]) for line in log.splitlines() if line.startswith('SYNC_NATIVE_PROOF ')]
    if result.returncode or len(proofs) != 2 or {p['stage'] for p in proofs} != {'baseline', 'candidate'} or not all(p['passed'] for p in proofs) or proofs[0]['tests'] != proofs[1]['tests'] or proofs[0]['source_manifest_sha256'] != proofs[1]['source_manifest_sha256']:
        print(log[-3500:]); raise ValueError('Native baseline/candidate qualification incomplete; no deployment')
    r.save(OUT / 'native-build-qualified.json', {'qualified': True, 'run': ack['run'], 'linux_tests_per_image': proofs[0]['tests'], 'proofs': proofs, 'native_log_sha256': r.sha(path)})
    print(json.dumps({'qualified': True, 'proofs': proofs}))


def failure():
    run_id = sys.argv[2]
    run = r.build.az('acr', 'task', 'show-run', '-r', r.build.REGISTRY, '--run-id', run_id)
    intent = r.read(OUT / 'build-intent.json')
    age = (datetime.fromisoformat(run['createTime'].replace('Z', '+00:00')) - datetime.fromisoformat(intent['prepared_at'])).total_seconds()
    if run['status'] != 'Failed' or not 0 <= age <= 120 or run.get('outputImages'):
        raise ValueError('Exact failed private build required')
    result = subprocess.run([r.build.AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '-r', r.build.REGISTRY, '--run-id', run_id], capture_output=True, text=True, encoding='utf-8', timeout=120, env={**os.environ, 'PYTHONUTF8': '1'})
    log = result.stdout + result.stderr
    if SOURCE not in log or 'qualify.py' not in log or 'Traceback' not in log:
        raise ValueError('Unexpected build diagnostic')
    with (OUT / 'failed-build.log').open('x', encoding='utf-8') as f: f.write(log)
    record = {'run': run_id, 'status': 'Failed', 'createTime': run['createTime'], 'no_output_image': True, 'source_intent_sha256': r.sha(OUT / 'source-intent.json'), 'log_sha256': r.sha(OUT / 'failed-build.log'), 'production_changed': False}
    from run_specialist_gateway_read_verification import retain
    r.save(OUT / 'failed-build.json', {**record, 'protected_receipt': retain(record, 'upgrade-sync-build-failed-' + run_id)})
    print(json.dumps(record))


if __name__ == '__main__':
    {'prepare': prepare, 'build': build, 'inspect': r.inspect, 'logs': logs, 'reconcile': r.reconcile, 'failure': failure}[sys.argv[1]]()
