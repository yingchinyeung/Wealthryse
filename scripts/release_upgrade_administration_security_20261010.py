"""Build-only, exact-source-preserving administration runtime candidates."""
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
TARGETS = {
    'admin': ('ibkrv2stg-autonomous-admin', 'run_autonomous_administrator.py',
        'a77ecaac7528f4c1837bbeda130156d9253ae7cf9b2dcc9e2f2ba71b32da92e0'),
    'review': ('ibkrv2stg-autonomy-review', 'run_monthly_authority_review.py',
        '73ea7fc70a90bfd576e575b45d5f2dadbab467393616797dea1d2464c0ff217e'),
}
API = '?api-version=2026-07-01'
REVISION = 2 if '--v2' in sys.argv else 1
if '--v2' in sys.argv: sys.argv.remove('--v2')


def snapshot(job, entry, source):
    live = request('GET', rid('Microsoft.App/jobs', job) + API)
    container = live['properties']['template']['containers'][0]
    if container['image'] != source or container.get('command') != ['python'] or container.get('args') != ['scripts/' + entry]:
        raise ValueError('Installed job boundary changed')
    return {job: {'image': source, 'settings_invariant_sha256': settings_invariant(live)}}


def main(target, stage):
    job, entry, digest = TARGETS[target]
    source = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-portfolio@sha256:' + digest
    out = ROOT / ('.work/platform-upgrade-20261010-stage18/' + target + '-security-v' + str(REVISION))
    r.WEB = out
    if stage == 'prepare':
        if out.exists(): raise ValueError('Preserve prepared evidence')
        prior = ROOT / '.work/platform-upgrade-20261010-stage17/owner-runtime-v2/context'
        intent = r.read(prior.parent / 'source-intent.json')
        if r.manifest(prior) != intent['files_sha256']: raise ValueError('Prior recipe drift')
        jobs = snapshot(job, entry, source)
        context = out / 'context'; (context / 'tests').mkdir(parents=True)
        shutil.copytree(prior / 'support', context / 'support')
        tests = ('test_autonomous_admin.py', 'test_autonomy_review.py', 'test_admin_actions.py')
        # The historical review image has no admin_chat module. Its failed v1
        # baseline is retained; do not import a later feature into old source.
        if not (target == 'review' and REVISION == 2): tests += ('test_admin_chat.py',)
        for name in tests:
            shutil.copyfile(ROOT / 'tests' / name, context / 'tests' / name)
        shutil.copyfile(ROOT / 'scripts/qualify_upgrade_administration_runtime_20261010.py', context / 'qualify.py')
        if target == 'review' and REVISION == 2:
            path = context / 'qualify.py'; qualification = path.read_text()
            if qualification.count('result.testsRun < 20') != 1: raise ValueError('Qualification threshold differs')
            qualification = qualification.replace('result.testsRun < 20', 'result.testsRun != 16')
            path.write_text(qualification, encoding='utf-8')
        docker = (prior / 'Dockerfile').read_text()
        if docker.count(intent['base_image']) != 1: raise ValueError('Recipe source boundary differs')
        docker = docker.replace(intent['base_image'], source)
        docker = docker.replace('COPY --chown=10001:10001 scripts/ /qualification/scripts/\n', '')
        docker = docker.replace('CMD ["python","-c","raise SystemExit(0)"]', 'CMD ' + json.dumps(['python', 'scripts/' + entry]))
        with (context / 'Dockerfile').open('x', encoding='utf-8') as f: f.write(docker)
        for path in context.rglob('*.py'): compile(path.read_bytes(), str(path), 'exec')
        r.save(out / 'source-intent.json', {'tag': 'ibkr-platform:administration-' + target + '-security-20261010-v' + str(REVISION),
            'base_image': source, 'files_sha256': r.manifest(context), 'before_jobs': jobs,
            'test_files': list(tests), 'review_v1_missing_later_admin_chat_feature_preserved': target == 'review' and REVISION == 2,
            'scope': 'OS/Python patch; preserve installed application and Python packages. No authority, credentials, schedule or policy changes. Build only.'})
        r.save(out / 'local-qualification.json', {'status': 'syntax_only_native_pending', 'tests_run': 0,
            'source_intent_sha256': r.sha(out / 'source-intent.json')})
        print('Prepared isolated ' + target + ' runtime; live job unchanged'); return
    intent = r.read(out / 'source-intent.json')
    if r.manifest(out / 'context') != intent['files_sha256'] or snapshot(job, entry, source) != intent['before_jobs']:
        raise ValueError('Source or runtime drift')
    if stage == 'build':
        if (out / 'build-intent.json').exists(): raise ValueError('Reconcile existing build')
        r.save(out / 'build-intent.json', {'tag': intent['tag'], 'source_intent_sha256': r.sha(out / 'source-intent.json'),
            'before_jobs': intent['before_jobs'], 'prepared_at': datetime.now(timezone.utc).isoformat(), 'running_service_update': False})
        ack = r.build.az('acr', 'build', '-r', r.build.REGISTRY, '-t', intent['tag'], '-f', str(out / 'context/Dockerfile'),
                         '--no-logs', '--no-wait', str(out / 'context'))
        if not ack or not ack.get('runId'): raise ValueError('Unknown outcome; reconcile rather than repeat')
        r.save(out / 'build-ack.json', {'run': ack['runId'], 'tag': intent['tag']})
        print(json.dumps({'run': ack['runId']})); return
    if stage == 'inspect': r.inspect(); return
    if stage == 'reconcile':
        # Reuse the established timestamp/tag/digest reconciliation, supplying
        # its explicit run argument without ever redispatching the build.
        if len(sys.argv) != 4: raise ValueError('Exact run ID required')
        saved = sys.argv[:]
        try:
            sys.argv = [saved[0], 'reconcile', saved[3]]
            r.reconcile()
        finally: sys.argv = saved
        return
    if stage != 'logs': raise ValueError('Unknown stage')
    ack = r.read(out / 'build-ack.json')
    result = subprocess.run([r.build.AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '-r', r.build.REGISTRY, '--run-id', ack['run']],
        capture_output=True, text=True, encoding='utf-8', timeout=120, env={**os.environ, 'PYTHONUTF8': '1'})
    log = result.stdout + result.stderr
    if not (out / 'native-build.log').exists():
        with (out / 'native-build.log').open('x', encoding='utf-8') as f: f.write(log)
    proofs = [json.loads(line.split('ADMINISTRATION_NATIVE_PROOF ', 1)[1]) for line in log.splitlines() if line.startswith('ADMINISTRATION_NATIVE_PROOF ')]
    if result.returncode or len(proofs) != 2 or {p['stage'] for p in proofs} != {'baseline', 'candidate'} or not all(p['passed'] for p in proofs) or any(proofs[0][k] != proofs[1][k] for k in ('tests', 'source_files', 'source_manifest_sha256')):
        print(log[-4500:]); raise ValueError('Native qualification failed; no deployment')
    r.save(out / 'native-build-qualified.json', {'qualified': True, 'run': ack['run'], 'proofs': proofs,
        'linux_tests_per_image': proofs[0]['tests'], 'native_log_sha256': r.sha(out / 'native-build.log')})
    print(json.dumps(proofs))


if __name__ == '__main__': main(sys.argv[1], sys.argv[2])
