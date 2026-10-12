"""Build the scoped MS candidate without promoting any running service."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
AZ = 'C:/Program Files/Microsoft SDKs/Azure/CLI2/python.exe'
SUB = '3956e821-cee4-403f-8708-e84e2ed490eb'
REGISTRY = 'ibkrv2stgauhecwc4itaoo'
GROUP = 'rg-ibkr-database-v11'
JOBS = ('ibkrv2stg-onboard-render', 'ibkrv2stg-month-end-factsheet')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def az(*args):
    result = subprocess.run([AZ, '-m', 'azure.cli', *args, '--only-show-errors', '-o', 'json'],
        env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'},
        capture_output=True, text=True, encoding='utf-8', timeout=120)
    if result.returncode:
        raise RuntimeError('Azure command failed: ' + ' '.join(args[:3]) + ' ' + result.stderr[-1500:])
    return json.loads(result.stdout or 'null')


def save(folder, name, record):
    path = folder / (name + '.json')
    if path.exists():
        raise ValueError('Evidence exists; reconcile, never redispatch or overwrite')
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(record, indent=2) + '\n')


def invariant(job):
    properties = job['properties']
    template = json.loads(json.dumps(properties['template']))
    for container in template['containers']:
        container.pop('image', None)
    return hashlib.sha256(json.dumps({'template': template, 'identity': job.get('identity'),
        'configuration': properties['configuration'], 'environmentId': properties['environmentId']},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('build', 'reconcile', 'inspect', 'log'))
    parser.add_argument('--run-id')
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    source_path = folder / 'source-intent.json'
    intent = json.loads(source_path.read_bytes())
    qualified = json.loads((folder / 'local-qualification.json').read_bytes())
    if (qualified['status'] != 'isolated_local_candidate_passed'
            or qualified['source_intent_sha256'] != sha(source_path)
            or qualified['replay']['months_verified'] != 21):
        raise ValueError('The frozen candidate must pass local isolated qualification')
    if intent.get('release', {}).get('chart_workbooks_required'):
        structure = json.loads((folder / 'local-structure-qualification.json').read_bytes())
        if (structure['status'] != 'passed' or structure['documents_checked'] != 22
                or structure['errors'] or structure['local_qualification_sha256'] != sha(folder / 'local-qualification.json')
                or structure['source_intent_sha256'] != sha(source_path)):
            raise ValueError('Workbook candidate must pass independent all-month structure and fresh-render checks')
    context = folder / 'context'
    actual = {p.relative_to(context).as_posix(): sha(p) for p in context.rglob('*') if p.is_file()}
    if actual != intent['files_sha256']:
        raise ValueError('Frozen candidate context changed')
    if az('account', 'show', '--query', 'id') != SUB:
        raise ValueError('Unexpected Azure subscription')
    if args.stage == 'build':
        if (folder / 'build-intent.json').exists() or (folder / 'build-ack.json').exists():
            raise ValueError('Build was already attempted; reconcile its native run')
        jobs = {}
        for name in JOBS:
            job = az('containerapp', 'job', 'show', '--subscription', SUB, '-g', GROUP, '-n', name)
            if (len(job['properties']['template']['containers']) != 1
                    or job['properties']['template']['containers'][0]['image'] != intent['base_image']):
                raise ValueError('Installed reporting baseline drifted: ' + name)
            jobs[name] = {'image': intent['base_image'], 'settings_invariant_sha256': invariant(job)}
        save(folder, 'build-intent', {'tag': intent['tag'], 'source_intent_sha256': sha(source_path),
             'local_qualification_sha256': sha(folder / 'local-qualification.json'),
             'before_jobs': jobs, 'prepared_at': datetime.now(timezone.utc).isoformat(),
             'running_service_update': False, 'database_writes': 0, 'reports_released': False})
        result = az('acr', 'build', '--subscription', SUB, '-r', REGISTRY, '-t', intent['tag'],
                    '-f', str(context / 'Dockerfile'), '--no-logs', '--no-wait', str(context))
        if not result or not result.get('runId'):
            raise ValueError('Unknown dispatch outcome; reconcile native runs, never redispatch')
        save(folder, 'build-ack', {'run': result['runId'], 'tag': intent['tag']})
        print(json.dumps({'candidate_build_dispatched': True, 'run': result['runId'],
                          'running_services_changed': False}))
        return
    if args.stage == 'reconcile':
        if not args.run_id:
            raise ValueError('An explicit existing native run is required')
        prepared = json.loads((folder / 'build-intent.json').read_bytes())
        run = az('acr', 'task', 'show-run', '--subscription', SUB, '-r', REGISTRY,
                 '--run-id', args.run_id)
        outputs = run.get('outputImages') or []
        prepared_at = datetime.fromisoformat(prepared['prepared_at'])
        created_at = datetime.fromisoformat(run['createTime'].replace('Z', '+00:00'))
        if (prepared['source_intent_sha256'] != sha(source_path)
                or prepared['tag'] != intent['tag']
                or not 0 <= (created_at - prepared_at).total_seconds() <= 120
                or run.get('runId') != args.run_id
                or run['status'] != 'Succeeded' or len(outputs) != 1
                or outputs[0]['registry'] != REGISTRY + '.azurecr.io'
                or outputs[0]['repository'] != 'ibkr-platform'
                or outputs[0]['tag'] != intent['tag'].split(':', 1)[1]
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', outputs[0].get('digest', ''))):
            raise ValueError('Native run does not uniquely match the frozen build intent')
        save(folder, 'build-ack', {'run': args.run_id, 'tag': intent['tag'],
             'reconciled_after_missing_cli_ack': True, 'native_created_at': run['createTime'],
             'source_intent_sha256': sha(source_path), 'output_images': outputs})
        print(json.dumps({'reconciled_existing_run': args.run_id, 'redispatched': False}))
        return
    ack = json.loads((folder / 'build-ack.json').read_bytes())
    run = az('acr', 'task', 'show-run', '--subscription', SUB, '-r', REGISTRY, '--run-id', ack['run'])
    if args.stage == 'inspect':
        print(json.dumps({'run': ack['run'], 'status': run['status']}))
        if run['status'] not in ('Succeeded', 'Failed', 'Canceled', 'Error', 'Timeout'):
            return
        outputs = run.get('outputImages') or []
        if run['status'] == 'Succeeded' and (len(outputs) != 1
                or outputs[0]['registry'] != REGISTRY + '.azurecr.io'
                or outputs[0]['repository'] != 'ibkr-platform'
                or outputs[0]['tag'] != intent['tag'].split(':', 1)[1]):
            raise ValueError('Native build output identity differs')
        save(folder, 'build-completed' if run['status'] == 'Succeeded' else 'build-rejected',
             {'run': ack['run'], 'native_status': run['status'], 'output_images': outputs,
              'source_intent_sha256': sha(source_path), 'running_services_changed': False})
        return
    result = subprocess.run([AZ, '-m', 'azure.cli', 'acr', 'task', 'logs', '--subscription', SUB,
        '-r', REGISTRY, '--run-id', ack['run']], capture_output=True, text=True,
        encoding='utf-8', timeout=120, env={**os.environ, 'PYTHONUTF8': '1'})
    if result.returncode:
        raise ValueError('Native build log retrieval failed')
    log = result.stdout + result.stderr
    with (folder / 'native-build.log').open('x', encoding='utf-8') as stream:
        stream.write(log)
    expected_tests = intent.get('expected_test_count', 62)
    if type(expected_tests) is not int or expected_tests < 62 or qualified['tests_passed'] != expected_tests:
        raise ValueError('Native build test count must match the frozen local qualification')
    passed = (run['status'] == 'Succeeded'
              and re.search(r'Ran ' + str(expected_tests) + r' tests\b', log) and '\nOK' in log
              and 'PASS: scoped MS presentation worker contract' in log
              and 'PASS: report templates, PowerPoint runtime and native chart canvas' in log)
    save(folder, 'native-build-qualified', {'run': ack['run'], 'qualified': bool(passed),
         'expected_test_count': expected_tests,
         'native_status': run['status'], 'native_log_sha256': sha(folder / 'native-build.log'),
         'running_services_changed': False, 'financial_or_visual_cloud_acceptance': False})
    print(json.dumps({'run': ack['run'], 'native_runtime_and_tests_passed': passed}))


if __name__ == '__main__':
    main()
