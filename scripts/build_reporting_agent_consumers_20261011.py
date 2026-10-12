"""Qualified source-only builds on exact web/assurance images; no deployment."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import release_platform_access_correction_20261009 as h
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / '.work/platform-upgrade-20261011-stage34'
TESTS = ('agent_sector_currency', 'reporting_currency_holdings', 'agent_tools')


def unchanged():
    baseline = protected(STAGE / 'resume-v1/result.json')
    if b.bindings(b.current_resources()) != baseline['boundary']['workloads']:
        raise ValueError('Installed workload drift; rebase before build')


def main(stage, kind):
    if kind not in ('web', 'assurance') or stage not in ('prepare', 'build', 'inspect', 'reconcile', 'logs', 'scan'):
        raise ValueError('Exact source-only consumer build operation required')
    out = STAGE / ('consumer-agent-' + kind + '-v1')
    prepared = h.read(out / 'prepared.json')
    h.WEB = out
    context = out / 'context'
    if stage == 'prepare':
        if context.exists():
            raise ValueError('Preserve previous build context')
        if h.manifest(out / 'payload') != prepared['candidate']:
            raise ValueError('Prepared source drift')
        shutil.copytree(out / 'payload', context / 'payload', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copyfile(out / 'prepared.json', context / 'prepared.json')
        shutil.copyfile(ROOT / 'scripts/qualify_reporting_agent_native_20261011.py', context / 'qualify.py')
        (context / 'tests').mkdir()
        for name in TESTS:
            shutil.copyfile(ROOT / 'tests' / ('test_' + name + '.py'), context / 'tests' / ('test_' + name + '.py'))
        docker = ('FROM ' + prepared['base_image'] + ' AS qualification\n'
                  'COPY --chown=10001:10001 . /qualification/reporting-agent/\n'
                  'RUN ["python", "/qualification/reporting-agent/qualify.py"]\n'
                  'FROM ' + prepared['base_image'] + '\n')
        for name in (*prepared['candidate'], 'ALPHA_T_RUNTIME_RELEASE.json'):
            docker += 'COPY --from=qualification --chown=10001:10001 /app/' + name + ' /app/' + name + '\n'
        (context / 'Dockerfile').write_text(docker, encoding='utf-8')
        # Isolate the patched module while using local dependencies for this
        # preliminary suite. Native build repeats it on installed dependencies.
        code = '''import importlib.util,sys,pathlib,unittest
import ibkr_etl
root=pathlib.Path(sys.argv[1])/'src/ibkr_etl'
for short in ('reporting_currency_holdings','agent_tools'):
 name='ibkr_etl.'+short
 spec=importlib.util.spec_from_file_location(name,root/(short+'.py'))
 module=importlib.util.module_from_spec(spec);sys.modules[name]=module
 spec.loader.exec_module(module);setattr(ibkr_etl,short,module)
suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[2:])
result=unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful() or result.testsRun!=33 or bool(result.skipped))
'''
        run = subprocess.run([sys.executable, '-c', code, str(out / 'payload'),
                              *['tests.test_' + n for n in TESTS]], cwd=ROOT,
                             env={**os.environ, 'PYTHONPATH': 'src' + os.pathsep + 'scripts', 'PYTHONDONTWRITEBYTECODE': '1'},
                             capture_output=True, text=True, timeout=120)
        (out / 'local-tests.log').write_text(run.stdout + run.stderr, encoding='utf-8')
        if run.returncode:
            raise ValueError('Local candidate tests failed; preserve diagnostics')
        h.save(out / 'source-intent.json', {'tag': 'ibkr-platform:reporting-agent-' + kind + '-20261011-v1',
               'base_image': prepared['base_image'], 'files_sha256': h.manifest(context),
               'scope': prepared['scope'], 'private_records_uploaded': False,
               'candidate_hashes': prepared['candidate']})
        h.save(out / 'local-qualification.json', {'passed': True, 'tests_run': 33,
               'source_intent_sha256': h.sha(out / 'source-intent.json'),
               'log_sha256': h.sha(out / 'local-tests.log'), 'native_dependency_tests_required': True})
        print(json.dumps({'prepared': kind, 'local_tests': 33, 'source_only': True})); return
    source = h.read(out / 'source-intent.json')
    if h.manifest(context) != source['files_sha256']:
        raise ValueError('Frozen source context drift')
    unchanged()
    if stage == 'build':
        if (out / 'build-intent.json').exists():
            raise ValueError('Reconcile existing native run; never duplicate')
        h.save(out / 'build-intent.json', {'tag': source['tag'], 'before_jobs': {},
               'source_intent_sha256': h.sha(out / 'source-intent.json'),
               'live_changes': False, 'private_records_uploaded': False})
        run = subprocess.run([h.build.AZ, '-m', 'azure.cli', 'acr', 'build', '-r', h.build.REGISTRY,
                              '-t', source['tag'], '-f', str(context / 'Dockerfile'), '--no-logs',
                              '--no-wait', str(context)], capture_output=True, text=True,
                             encoding='utf-8', timeout=180)
        ids = re.findall(r'Queued a build with ID: ([a-z0-9]+)', run.stdout + run.stderr)
        if run.returncode or len(ids) != 1:
            raise ValueError('Unknown native build outcome; reconcile existing run')
        h.save(out / 'build-ack.json', {'run': ids[0], 'tag': source['tag']})
        print(json.dumps({'kind': kind, 'run': ids[0], 'deployed': False})); return
    if stage == 'inspect': return h.inspect()
    if stage == 'reconcile': return h.reconcile()
    if stage == 'scan':
        import scan_ms_presentation_worker_candidate as scanner
        scanner.jobs_unchanged = lambda _: unchanged()
        sys.argv = [sys.argv[0], '--folder', str(out)]
        return scanner.main()
    ack = h.read(out / 'build-ack.json')
    if not (out / 'build-completed.json').exists():
        raise ValueError('Completed native image required')
    run = subprocess.run([h.build.AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '-r', h.build.REGISTRY,
                          '--run-id', ack['run']], capture_output=True, text=True, encoding='utf-8', timeout=120)
    log = run.stdout + run.stderr
    proofs = [json.loads(m) for m in re.findall(r'^REPORTING_AGENT_NATIVE (\{.*\})$', log, re.M)]
    with (out / 'native-build.log').open('x', encoding='utf-8') as stream:
        stream.write(log)
    if run.returncode or len(proofs) != 1 or proofs[0].get('passed') is not True or proofs[0]['tests'] != 33 or proofs[0]['candidate_hashes'] != prepared['candidate']:
        raise ValueError('Native qualification incomplete; preserve failure')
    h.save(out / 'native-build-qualified.json', {'qualified': True, 'run': ack['run'], 'linux_tests': 33,
           'proof': proofs[0], 'native_log_sha256': h.sha(out / 'native-build.log')})
    print(json.dumps({'kind': kind, 'native_tests': 33, 'passed': True, 'deployed': False}))


if __name__ == '__main__':
    main(*sys.argv[1:])
