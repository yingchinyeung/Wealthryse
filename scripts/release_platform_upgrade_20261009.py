"""Build and qualify the frozen upgrade. No running-service deployment capability."""
import json
import os
import re
import shutil
import subprocess
import sys

import release_platform_access_correction_20261009 as release
from check_platform_upgrade_20261009 import check

ROOT = release.ROOT
BUNDLE = ROOT / '.work/platform-upgrade-20261009-v1'
release.WEB = BUNDLE / 'application-release-v1'
release.IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:417c684a8185c23e0f5337c019caad28a44192e51578aa48768170a35fea80d1'


def prepare():
    result = check()
    if not result['integrity_passed']:
        raise ValueError('Frozen bundle failed integrity checks')
    folder = release.WEB
    if folder.exists():
        raise ValueError('Preserve existing release evidence')
    shutil.copytree(BUNDLE / 'build/application', folder / 'context')
    source = {
        'tag': 'ibkr-platform:platform-upgrade-20261009-v1',
        'base_image': release.IMAGE,
        'files_sha256': release.manifest(folder / 'context'),
        'bundle_manifest_sha256': release.sha(BUNDLE / 'bundle-manifest.json'),
        'scope': 'Qualified benchmark and T risk reads, readiness route, derived trade and display corrections',
        'worker_fixture_only': True,
        'financial_renderers_and_templates_unchanged': True,
        'authorization': 'User requested deployment; qualification precedes any live-service change',
    }
    release.save(folder / 'source-intent.json', source)
    shutil.copyfile(BUNDLE / 'evidence/local-tests.log', folder / 'local-tests.log')
    release.save(folder / 'local-qualification.json', {
        'status': 'isolated_local_candidate_passed', 'tests_run': result['tests_run'],
        'source_intent_sha256': release.sha(folder / 'source-intent.json'),
        'log_sha256': release.sha(folder / 'local-tests.log'),
        'bundle_qualification_sha256': release.sha(BUNDLE / 'evidence/local-qualification.json'),
    })
    print(json.dumps({'prepared': True, 'tests_run': result['tests_run'], 'live_changes': False}))


def logs():
    folder = release.WEB
    ack = release.read(folder / 'build-ack.json')
    completed = release.read(folder / 'build-completed.json')
    if completed['native_status'] != 'Succeeded' or completed['run'] != ack['run']:
        raise ValueError('Successful exact native build required')
    response = subprocess.run([
        release.build.AZ, '-m', 'azure.cli', 'acr', 'task', 'logs',
        '-r', release.build.REGISTRY, '--run-id', ack['run']],
        capture_output=True, text=True, encoding='utf-8', timeout=120,
        env={**os.environ, 'PYTHONUTF8': '1'})
    log = response.stdout + response.stderr
    count = release.read(folder / 'local-qualification.json')['tests_run']
    if (response.returncode or not re.search(r'Ran ' + str(count) + r' tests\b', log)
            or '\nFAILED (' in log or not re.search(r'\bOK \(skipped=1\)', log)):
        raise ValueError('Native test qualification failed')
    with (folder / 'native-build.log').open('x', encoding='utf-8') as stream:
        stream.write(log)
    release.save(folder / 'native-build-qualified.json', {
        'qualified': True, 'run': ack['run'], 'linux_tests': count,
        'native_log_sha256': release.sha(folder / 'native-build.log'),
    })
    print(json.dumps({'native_tests': count, 'qualified': True, 'live_changes': False}))


if __name__ == '__main__':
    {'prepare': prepare, 'build': release.dispatch, 'inspect': release.inspect,
     'logs': logs, 'reconcile': release.reconcile}[sys.argv[1]]()
