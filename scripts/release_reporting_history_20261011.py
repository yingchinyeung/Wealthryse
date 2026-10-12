"""Source-only v5: full portfolio history in generic factsheets, MS/T unchanged."""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import release_reporting_full_nav_20261011 as previous

ROOT = previous.ROOT
PRIOR = previous.OUT
c = previous.c
r = previous.r
OUT = PRIOR.parent / 'reporting-image-v5'
c.OUT = OUT
NAME = 'src/ibkr_etl/monthly_artifacts.py'


def prepare():
    if OUT.exists():
        raise ValueError('Preserve previous v5 candidate')
    old = r.read(PRIOR / 'source-intent.json')
    if r.manifest(PRIOR / 'context') != old['files_sha256']:
        raise ValueError('Qualified v4 context drift')
    qualified = r.read(PRIOR / 'native-build-qualified.json')
    if not qualified['qualified'] or qualified['linux_tests'] != 99 or qualified['native_log_sha256'] != r.sha(PRIOR / 'native-build.log'):
        raise ValueError('Qualified v4 native evidence required')
    before = (PRIOR / 'context/payload' / NAME).read_text()
    after = (ROOT / NAME).read_text()
    def unchanged_nodes(source):
        allowed_constants = {'FACTSHEET_LAYOUT_VERSION', 'SUBJECT_FACTSHEET_LAYOUT_VERSION'}
        return sorted(ast.dump(n, include_attributes=False) for n in ast.parse(source).body
            if not (isinstance(n, ast.FunctionDef) and n.name == 'factsheet_inputs')
            and not (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in allowed_constants for t in n.targets)))
    if unchanged_nodes(before) != unchanged_nodes(after):
        raise ValueError('Unrelated factsheet functions changed')
    context = OUT / 'context'
    shutil.copytree(PRIOR / 'context', context)
    shutil.copyfile(ROOT / NAME, context / 'payload' / NAME)
    for name in ('test_monthly_artifacts.py', 'test_factsheet_portfolio_history.py'):
        shutil.copyfile(ROOT / 'tests' / name, context / 'tests' / name)
    qualifier = (context / 'qualify.py').read_text()
    for a, b in (('result.testsRun!=81', 'result.testsRun!=86'), ("'python_tests':81", "'python_tests':86")):
        if qualifier.count(a) != 1:
            raise ValueError('Exact native count update required')
        qualifier = qualifier.replace(a, b)
    (context / 'qualify.py').write_text(qualifier, encoding='utf-8', newline='\n')
    manifest = r.read(context / 'patch-manifest.json')
    manifest['candidate'] = r.manifest(context / 'payload')
    (context / 'patch-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    r.save(OUT / 'source-intent.json', {'tag': 'ibkr-platform:reporting-material-corrections-20261011-v5',
        'base_image': c.BASE_IMAGE, 'files_sha256': r.manifest(context), 'candidate_hashes': manifest['candidate'],
        'scope': 'v4 corrections plus generic factsheet history derived from full frozen portfolio NAV. Comparison series, MS/T paths, approved wording and original data unchanged.',
        'source_only': True, 'private_records_uploaded': False, 'prior_source_intent_sha256': r.sha(PRIOR / 'source-intent.json')})
    print('Prepared v5 source-only candidate; not built or deployed')


def qualify():
    source = r.read(OUT / 'source-intent.json')
    if r.manifest(OUT / 'context') != source['files_sha256']:
        raise ValueError('Frozen v5 source drift')
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(('src', 'scripts')),
           'TEMP': str(ROOT / '.work/platform-upgrade-20261011-stage33/test-temp'),
           'TMP': str(ROOT / '.work/platform-upgrade-20261011-stage33/test-temp')}
    names = [*c.TESTS, 'monthly_reporting_analytics', 'factsheet_portfolio_history']
    py = subprocess.run([sys.executable, '-m', 'unittest', *['tests.test_' + n for n in names]], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    node = subprocess.run(['C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe', '--test', '--test-reporter=tap', *['scripts/' + n for n in c.NODE]], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    log = py.stdout + py.stderr + node.stdout + node.stderr
    with (OUT / 'local-tests.log').open('x', encoding='utf-8') as stream:
        stream.write(log)
    if py.returncode or node.returncode or 'Ran 86 tests' not in log or '# pass 18\n' not in log or '# fail 0\n' not in log:
        raise ValueError('Exact v5 suite required; failure retained')
    r.save(OUT / 'local-qualification.json', {'status': 'isolated_local_candidate_passed', 'tests_run': 104,
        'source_intent_sha256': r.sha(OUT / 'source-intent.json'), 'log_sha256': r.sha(OUT / 'local-tests.log')})
    print('104 local tests passed; native build and output review required')


def logs():
    c.verify()
    ack = r.read(OUT / 'build-ack.json')
    if not (OUT / 'build-completed.json').exists():
        raise ValueError('Native completion required')
    run = subprocess.run([r.build.AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '-r', r.build.REGISTRY, '--run-id', ack['run']], capture_output=True, text=True, encoding='utf-8', timeout=120)
    log = run.stdout + run.stderr
    with (OUT / 'native-build.log').open('x', encoding='utf-8') as stream:
        stream.write(log)
    proofs = [json.loads(x) for x in re.findall(r'^REPORTING_IMAGE_NATIVE (\{.*\})$', log, re.M)]
    if run.returncode or len(proofs) != 1 or not proofs[0]['passed'] or proofs[0]['python_tests'] != 86 or proofs[0]['node_tests'] != 18 or proofs[0]['candidate_hashes'] != r.read(OUT / 'source-intent.json')['candidate_hashes']:
        raise ValueError('Native v5 evidence incomplete')
    r.save(OUT / 'native-build-qualified.json', {'qualified': True, 'run': ack['run'], 'linux_tests': 104,
        'proof': proofs[0], 'native_log_sha256': r.sha(OUT / 'native-build.log')})
    print('104 native v5 tests passed; not deployed')


if __name__ == '__main__':
    {'prepare': prepare, 'qualify': qualify, 'build': c.dispatch, 'inspect': c.inspect, 'reconcile': c.reconcile, 'logs': logs}[sys.argv[1]]()
