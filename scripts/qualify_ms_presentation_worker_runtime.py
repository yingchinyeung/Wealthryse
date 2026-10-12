"""One-shot transient candidate/rollback executions; no persistent cutover."""
import argparse
import base64
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import build_ms_presentation_worker_candidate as build
from scan_ms_presentation_worker_candidate import verify, jobs_unchanged
from deploy_metadata_monitor import request, BASE
from run_specialist_gateway_read_verification import retain
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl.util import canonical_json

JOB = build.JOBS[1]
JOB_PATH = BASE + 'Microsoft.App/jobs/' + JOB
API = '?api-version=2024-03-01'
PREFIX = 'review/alpha-world-ms/presentation-worker-r2-20261007/'
WORKBOOK_CONTRACT = 'alpha-world-ms-v24-hkd-hibor-evidence-v2'


def runtime_prefix(folder):
    match = re.fullmatch(r'alpha-ms-(presentation|format)-worker-candidate-(\d{8})-(r[1-9]\d*)', folder.name)
    if not match:
        raise ValueError('Exact scoped candidate folder required for a new private prefix')
    datetime.strptime(match[2], '%Y%m%d')
    return 'review/alpha-world-ms/' + match[1] + '-worker-' + match[3] + '-' + match[2] + '/'


def read(folder, name):
    return json.loads((folder / (name + '.json')).read_bytes())


def installed_baseline(folder, image):
    """Bind the current format candidate to its already-qualified installed image."""
    if folder.name.startswith('alpha-ms-format-worker-candidate-'):
        path = build.ROOT / '.work/alpha-ms-presentation-worker-candidate-20261007-r5/deployment-v1' / (JOB + '-check-qualified.json')
        record = json.loads(path.read_bytes())
        protected = record.get('protected_receipt', {})
        payload = {k: v for k, v in record.items() if k != 'protected_receipt'}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if (record.get('status') != 'passed' or record.get('job') != JOB
                or record.get('image') != image or not record.get('execution')
                or protected.get('protected_readback_verified') is not True
                or not protected.get('version_id') or protected.get('sha256') != digest):
            raise ValueError('Exact protected installed worker baseline required')
        return {'image': image, 'execution': record['execution'],
                'receipt_path': str(path), 'receipt_sha256': build.sha(path)}
    path = build.ROOT / '.work/alpha-ms-distribution-v14r13-20261007/completion.json'
    record = json.loads(path.read_bytes())['record']
    if record['image'] != image:
        raise ValueError('Legacy installed baseline image differs')
    return {'image': image, 'execution': record['installed_probe'],
            'receipt_path': str(path), 'receipt_sha256': build.sha(path)}


def candidate_gate(folder):
    source, native, output = verify(folder)
    scan = read(folder, 'scan-completed')
    protected = read(folder, 'scan-protected-receipt')
    image = output['registry'] + '/' + output['repository'] + '@' + output['digest']
    if (scan['image'] != image or scan['assessment']['status'] != 'pass'
            or scan['assessment']['blocking_findings'] != 0
            or protected['protected_readback_verified'] is not True
            or not protected['version_id']):
        raise ValueError('Exact protected passing security gate required')
    scan_digest = hashlib.sha256(json.dumps(scan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if (protected['sha256'] != scan_digest
            or build.sha(folder / 'scan-v1/vulnerabilities.json') != scan['assessment']['report_sha256']
            or build.sha(folder / 'scan-v1/sbom.cdx.json') != scan['assessment']['sbom_sha256']):
        raise ValueError('Protected scan/source bytes differ from the passing gate')
    if build.az('account', 'show', '--query', 'id') != build.SUB:
        raise ValueError('Unexpected Azure subscription')
    jobs_unchanged(folder)
    return source, image


def prepare(folder, runtime, source, image):
    if runtime.exists():
        raise ValueError('Runtime attempt exists; inspect rather than replace')
    release = json.loads((folder / 'context/MS_PRESENTATION_WORKER_RELEASE.json').read_bytes())
    if release['content_contract_version'] != WORKBOOK_CONTRACT or release.get('chart_workbooks_required') is not True:
        raise ValueError('New runtime qualification requires the mandatory workbook contract')
    inputs = build.ROOT / '.work/alpha-ms-presentation-hkd-20261007/inputs'
    periods = {path.stem: {'canonical_payload_sha256': hashlib.sha256(
        canonical_json(json.loads(path.read_bytes())).encode()).hexdigest()}
        for path in sorted(inputs.glob('*.json'))}
    if len(periods) != 21 or min(periods) != '2025-01-31' or max(periods) != '2026-09-30':
        raise ValueError('Exactly 21 accepted input periods required')
    runner = build.ROOT / 'scripts/verify_ms_presentation_worker_runtime.py'
    content = runner.read_bytes()
    compile(content, str(runner), 'exec')
    baseline = installed_baseline(folder, source['base_image'])
    runtime.mkdir()
    (runtime / 'frozen-probe.py').write_bytes(content)
    intent = {'image': image, 'base_image': source['base_image'], 'periods': periods,
        'output_prefix': runtime_prefix(folder), 'content_contract_version': release['content_contract_version'],
        'installed_files_sha256': release['installed_files_sha256'], 'probe_sha256': build.sha(runner),
        'source_intent_sha256': build.sha(folder / 'source-intent.json'),
        'scan_completed_sha256': build.sha(folder / 'scan-completed.json'),
        'installed_baseline': baseline,
        'persistent_settings_changed': False, 'database_writes': 0, 'reports_released': False}
    protected = retain(intent, folder.name + '-runtime-source-intent')
    build.save(runtime, 'source-intent', {**intent, 'protected_receipt': protected})
    print(json.dumps({'prepared': True, 'months': 21, 'persistent_settings_changed': False}))


def dispatch(folder, runtime, mode):
    intent = read(runtime, 'source-intent')
    runner = (runtime / 'frozen-probe.py').read_bytes()
    if (build.sha(runtime / 'frozen-probe.py') != intent['probe_sha256']
            or intent['source_intent_sha256'] != build.sha(folder / 'source-intent.json')
            or intent['scan_completed_sha256'] != build.sha(folder / 'scan-completed.json')):
        raise ValueError('Frozen runtime intent changed')
    if intent.get('output_prefix') != runtime_prefix(folder) or intent.get('content_contract_version') != WORKBOOK_CONTRACT:
        raise ValueError('Private output prefix or workbook contract differs from this candidate')
    if (runtime / (mode + '-dispatch-intent.json')).exists():
        raise ValueError('Dispatch already attempted; reconcile execution, never repeat')
    if mode == 'rollback' and read(runtime, 'candidate-qualified')['status'] != 'passed':
        raise ValueError('Candidate runtime must pass before rollback qualification')
    for name in build.JOBS:
        runs = request('GET', BASE + 'Microsoft.App/jobs/' + name + '/executions' + API).get('value', [])
        if any(r['properties']['status'] in ('Running', 'Processing', 'Pending') for r in runs):
            raise ValueError('Reporting worker is busy: ' + name)
    job = request('GET', JOB_PATH + API)
    template = {k: v for k, v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    if len(template['containers']) != 1:
        raise ValueError('Unexpected worker container inventory')
    container = template['containers'][0]
    env = {e['name']: e for e in container.get('env', [])}
    if not env.get('DATABASE_URL', {}).get('secretRef') or not env.get('AZURE_STORAGE_ACCOUNT_URL', {}).get('value'):
        raise ValueError('Existing secret-bound database and storage settings required')
    image = intent['image'] if mode == 'candidate' else intent['base_image']
    binding = {'mode': mode, 'image': image, 'prefix': intent['output_prefix'] + mode + '/',
               'content_contract_version': intent['content_contract_version'],
               'periods': intent['periods'], 'installed_files_sha256': intent['installed_files_sha256']}
    source = ('PROBE_BINDING=' + repr(binding) + '\n').encode() + runner
    encoded = base64.b64encode(gzip.compress(source, mtime=0)).decode()
    container['image'] = image
    container['command'] = ['python']
    container['args'] = ['-c', "import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('" + encoded + "')),'ms-worker-read-only-probe','exec'))"]
    dispatch_intent = {'mode': mode, 'image': image, 'template': template,
        'source_sha256': hashlib.sha256(source).hexdigest(), 'prefix': binding['prefix'],
        'storage_url': env['AZURE_STORAGE_ACCOUNT_URL']['value'],
        'persistent_settings_changed': False, 'database_writes': 0, 'reports_released': False}
    protected = retain(dispatch_intent, folder.name + '-' + mode + '-dispatch-intent')
    build.save(runtime, mode + '-dispatch-intent', {**dispatch_intent, 'protected_receipt': protected})
    result = request('POST', JOB_PATH + '/start' + API, data=template)
    if not result.get('name'):
        raise ValueError('Unknown dispatch outcome; reconcile, never repeat')
    build.save(runtime, mode + '-dispatch', {'execution': result['name'], 'image': image})
    print(json.dumps({'mode': mode, 'execution': result['name'], 'persistent_settings_changed': False}))


def verify_execution_projection(expected, actual, baseline, job):
    """Accept only observed service projection, never a different credential."""
    if any(actual.get(k) != expected.get(k) for k in ('image', 'command', 'args', 'name')):
        raise ValueError('Execution image, command or container identity differs')
    resources = copy.deepcopy(actual.get('resources', {}))
    expected_resources = expected.get('resources', {})
    if 'ephemeralStorage' not in expected_resources and resources.get('ephemeralStorage') == '':
        if baseline.get('resources', {}).get('ephemeralStorage') != '':
            raise ValueError('Unverified service resource default')
        resources.pop('ephemeralStorage')
    if resources != expected_resources:
        raise ValueError('Execution resource settings differ')
    wanted, observed = copy.deepcopy(expected.get('env', [])), copy.deepcopy(actual.get('env', []))
    if len(wanted) != len(observed):
        raise ValueError('Execution environment inventory differs')
    alias_used = False
    for old, new in zip(wanted, observed):
        if old.get('secretRef') != new.get('secretRef'):
            old_db = [e for e in baseline.get('env', []) if e.get('name') == 'DATABASE_URL']
            metadata = [s for s in job['properties']['configuration'].get('secrets', [])
                        if s['name'] == old.get('secretRef')]
            if (old.get('name') != 'DATABASE_URL' or new.get('name') != 'DATABASE_URL'
                    or old.get('secretRef') != 'database-url'
                    or new.get('secretRef') != 'cappjob-' + JOB
                    or len(old_db) != 1 or old_db[0].get('secretRef') != new.get('secretRef')
                    or len(metadata) != 1 or metadata[0].get('keyVaultUrl') !=
                       'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/database-app-url'
                    or str(metadata[0].get('identity') or '').casefold() not in {
                        identity.casefold() for identity in
                        job.get('identity', {}).get('userAssignedIdentities', {})}):
                raise ValueError('Unverified execution database secret-reference alias')
            new['secretRef'] = old['secretRef']
            alias_used = True
        if old.get('value') is None or (old.get('secretRef') and old.get('value') == ''):
            old.pop('value', None)
        if new.get('value') is None or (new.get('secretRef') and new.get('value') == ''):
            new.pop('value', None)
        if old != new:
            raise ValueError('Execution environment or secret differs')
    return {'database_alias_verified_against_installed_baseline': alias_used,
            'null_value_and_empty_storage_projection_only': True,
            'secret_values_read': False}


def inspect(folder, runtime, mode):
    ack = read(runtime, mode + '-dispatch')
    intent = read(runtime, mode + '-dispatch-intent')
    run = request('GET', JOB_PATH + '/executions/' + ack['execution'] + API)
    state = run['properties']['status']
    print(json.dumps({'mode': mode, 'status': state, 'execution': ack['execution']}), flush=True)
    if state != 'Succeeded':
        return
    actual = run['properties']['template']['containers']
    expected = intent['template']['containers']
    if len(actual) != len(expected) or len(actual) != 1:
        raise ValueError('Actual isolated execution template differs')
    base_image = read(runtime, 'source-intent')['base_image']
    baseline_binding = installed_baseline(folder, base_image)
    if read(runtime, 'source-intent').get('installed_baseline', baseline_binding) != baseline_binding:
        raise ValueError('Frozen installed baseline receipt changed')
    baseline = request('GET', JOB_PATH + '/executions/' + baseline_binding['execution'] + API)
    if (baseline['properties']['status'] != 'Succeeded'
            or len(baseline['properties']['template']['containers']) != 1
            or baseline['properties']['template']['containers'][0]['image'] != base_image):
        raise ValueError('Previously qualified installed-image execution required')
    job = request('GET', JOB_PATH + API)
    projection = verify_execution_projection(expected[0], actual[0],
        baseline['properties']['template']['containers'][0], job)
    service = BlobServiceClient(intent['storage_url'], credential=AzureCliCredential())
    data = service.get_blob_client('internal-reports', intent['prefix'] + 'verification.json').download_blob().readall()
    if len(data) > 16 * 1024 * 1024:
        raise ValueError('Runtime proof exceeds bound')
    proof = json.loads(data)
    periods = read(runtime, 'source-intent')['periods']
    if (proof['status'] != 'passed' or proof['mode'] != mode or proof['image'] != intent['image']
            or proof['count'] != 21 or len(proof['artifacts']) != 21
            or {r['period_end'] for r in proof['artifacts']} != set(periods)
            or proof['database_writes'] != 0 or not proof['read_only_transaction_enforced']
            or not proof['owner_identity_verified'] or proof['source_data_changed']
            or proof['reports_released'] or proof['onboarding_activated']):
        raise ValueError('Runtime proof coverage or scope differs')
    output_dir = runtime / (mode + '-outputs')
    output_dir.mkdir(exist_ok=True)
    for row in proof['artifacts']:
        types = ['factsheet'] if mode == 'rollback' else ['factsheet', 'presentation']
        if mode == 'candidate' and (row['financial_charts_verified'] != 6 or row['risk_metrics_verified'] != 7):
            raise ValueError('Candidate exported financial audit incomplete')
        if mode == 'candidate' and read(runtime, 'source-intent').get('content_contract_version') == WORKBOOK_CONTRACT:
            require_workbook_proof(row)
        for kind in types:
            name = row[kind + '_blob']
            if not name.startswith(intent['prefix'] + 'outputs/') or Path(name).name != name.rsplit('/', 1)[1]:
                raise ValueError('Artifact is outside the private probe prefix')
            content = service.get_blob_client('internal-reports', name).download_blob().readall()
            if hashlib.sha256(content).hexdigest() != row[kind + '_sha256']:
                raise ValueError('Downloaded cloud artifact integrity differs')
            target = output_dir / Path(name).name
            if target.exists():
                if target.read_bytes() != content:
                    raise ValueError('Conflicting local runtime artifact')
            else:
                with target.open('xb') as stream:
                    stream.write(content)
    if mode == 'rollback':
        candidate = read(runtime, 'candidate-qualified')['proof']
        if ({r['period_end']: r['factsheet_sha256'] for r in candidate['artifacts']}
                != {r['period_end']: r['factsheet_sha256'] for r in proof['artifacts']}):
            raise ValueError('Rollback factsheet bytes differ from the candidate')
    jobs_unchanged(folder)
    record = {'status': 'passed', 'mode': mode, 'image': intent['image'], 'execution': ack['execution'],
        'proof': proof, 'proof_sha256': hashlib.sha256(data).hexdigest(),
        'persistent_jobs_unchanged': True, 'artifact_readback_verified': True,
        'visual_acceptance': False, 'production_promotion': False,
        'execution_projection_validation': projection,
        'rollback_scope': 'Installed-image MS factsheet rendering; no persistent image change' if mode == 'rollback' else None}
    protected = retain(record, folder.name + '-' + mode + '-runtime-qualified')
    build.save(runtime, mode + '-qualified', {**record, 'protected_receipt': protected})
    print(json.dumps({'mode': mode, 'status': 'passed', 'months': 21, 'visual_acceptance': False,
                      'persistent_jobs_unchanged': True}), flush=True)


def require_workbook_proof(row):
    validation = row.get('presentation_validation', {})
    workbooks = validation.get('ms_chart_workbook_audit', {})
    if (validation.get('content_contract_version') != WORKBOOK_CONTRACT
            or validation.get('output_sha256') != row.get('presentation_sha256')
            or row.get('embedded_workbooks_verified') != 6
            or workbooks.get('status') != 'passed' or workbooks.get('embedded_workbooks_verified') != 6
            or workbooks.get('owner_slides') != [6,7,8,9,10,11]
            or workbooks.get('output_sha256') != row.get('presentation_sha256')
            or len(workbooks.get('items', [])) != 6):
        raise ValueError('Complete output-bound mandatory chart workbook evidence required')


def logs(runtime, mode):
    ack = read(runtime, mode + '-dispatch')
    intent = read(runtime, mode + '-dispatch-intent')
    container = intent['template']['containers'][0]['name']
    result = subprocess.run([build.AZ, '-m', 'azure.cli', 'containerapp', 'job', 'logs', 'show',
        '--subscription', build.SUB, '-g', build.GROUP, '-n', JOB, '--execution', ack['execution'],
        '--container', container, '--tail', '100', '--follow', 'false', '--format', 'text',
        '--only-show-errors'], capture_output=True, text=True, encoding='utf-8', timeout=90)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    with (runtime / (mode + '-logs-' + timestamp + '.txt')).open('x', encoding='utf-8') as stream:
        stream.write(result.stdout + result.stderr)
    if result.returncode:
        print(json.dumps({'mode': mode, 'log_read_available': False, 'exit_code': result.returncode}))
        return
    periods = sorted(set(re.findall(r'"period":\s*"(\d{4}-\d{2}-\d{2})"', result.stdout)))
    markers = []
    for line in result.stdout.splitlines():
        if 'MS_PRESENTATION_RUNTIME_RESULT ' in line:
            try:
                markers.append(json.loads(line.split('MS_PRESENTATION_RUNTIME_RESULT ', 1)[1]))
            except ValueError:
                pass
    print(json.dumps({'mode': mode, 'completed_periods_in_log_tail': len(periods),
        'latest_period': periods[-1] if periods else None, 'result_markers': markers}), flush=True)


def seal(runtime, image):
    candidate = read(runtime, 'candidate-qualified')
    rollback = read(runtime, 'rollback-qualified')
    independent_path = runtime / 'cloud-local-audit-v1/receipt.json'
    independent = json.loads(independent_path.read_bytes())
    workbooks = read(runtime, 'chart-workbook-acceptance')
    if (candidate['status'] != 'passed' or rollback['status'] != 'passed'
            or candidate['image'] != image or independent['image'] != image
            or independent['status'] != 'passed' or independent['months_verified'] != 21
            or independent['runtime_qualified_sha256'] != build.sha(runtime / 'candidate-qualified.json')
            or workbooks['image'] != image or workbooks['months_checked'] != 21
            or workbooks['status'] not in ('passed', 'blocked')
            or (workbooks['status'] == 'passed') != (workbooks['missing_cloud_chart_workbooks'] == 0)):
        raise ValueError('Complete bound runtime/fallback/independent audit evidence required')
    blocked = workbooks['status'] != 'passed'
    record = {'status': 'blocked_chart_workbook_acceptance' if blocked else 'visual_acceptance_pending',
        'candidate_image': image, 'candidate_execution': candidate['execution'],
        'installed_image_fallback_execution': rollback['execution'],
        'candidate_render_runtime_passed': True, 'factsheet_fallback_passed': True,
        'months_rendered': 21, 'candidate_factsheets': 21, 'candidate_presentations': 21,
        'financial_charts_verified': 126, 'risk_metrics_verified': 147,
        'missing_embedded_chart_workbooks': workbooks['missing_cloud_chart_workbooks'],
        'candidate_qualified_sha256': build.sha(runtime / 'candidate-qualified.json'),
        'rollback_qualified_sha256': build.sha(runtime / 'rollback-qualified.json'),
        'independent_audit_sha256': build.sha(independent_path),
        'workbook_acceptance_sha256': build.sha(runtime / 'chart-workbook-acceptance.json'),
        'visual_acceptance': False, 'full_live_image_switch_rollback_exercised': False,
        'rollback_scope': 'Installed image renders byte-identical MS factsheets; no live settings switch',
        'production_promotion_authorized': False, 'persistent_jobs_unchanged': True,
        'database_writes': 0, 'source_data_changed': False, 'reports_released': False,
        'onboarding_activated': False,
        'next_gate': 'Materialize and verify all six native chart-data workbooks in Azure; then rebuild/requalify and review actual cloud visuals' if blocked else 'Review actual cloud visuals'}
    workbook_receipt = retain(workbooks, runtime.parent.name + '-chart-workbook-acceptance')
    independent_receipt = retain(independent, runtime.parent.name + '-independent-cloud-audit')
    protected = retain({**record, 'workbook_protected_receipt': workbook_receipt,
        'independent_protected_receipt': independent_receipt}, runtime.parent.name + '-runtime-programme-acceptance')
    build.save(runtime, 'programme-acceptance', {**record, 'protected_receipt': protected,
        'workbook_protected_receipt': workbook_receipt, 'independent_protected_receipt': independent_receipt})
    print(json.dumps({'status': record['status'], 'months': 21,
        'missing_embedded_chart_workbooks': record['missing_embedded_chart_workbooks'],
        'production_promotion_authorized': False}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'candidate', 'rollback', 'inspect-candidate', 'inspect-rollback', 'logs-candidate', 'logs-rollback', 'seal'))
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    source, image = candidate_gate(folder)
    runtime = folder / 'runtime-v1'
    if args.stage == 'prepare':
        prepare(folder, runtime, source, image)
    elif args.stage.startswith('inspect-'):
        inspect(folder, runtime, args.stage.removeprefix('inspect-'))
    elif args.stage.startswith('logs-'):
        logs(runtime, args.stage.removeprefix('logs-'))
    elif args.stage == 'seal':
        seal(runtime, image)
    else:
        dispatch(folder, runtime, args.stage)


if __name__ == '__main__':
    main()
