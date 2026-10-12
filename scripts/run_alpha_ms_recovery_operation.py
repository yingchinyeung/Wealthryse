"""Audited one-shot dispatches; persistent Azure job definitions never change."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.work/alpha-ms-azure-controller-runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
from deploy_metadata_monitor import request, BASE
from audit_azure_runtime import az

FOLDER = ROOT / '.work/alpha-ms-recovery-20261007'
FOLDER.mkdir(exist_ok=True)


def invariant(job):
    return hashlib.sha256(json.dumps({'identity': job.get('identity'),
        'configuration': job['properties']['configuration'],
        'template': job['properties']['template']}, sort_keys=True).encode()).hexdigest()


def save(name, data):
    with (FOLDER / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2, default=str)


def start(label, job_name, script_name, env):
    if (FOLDER / (label + '-intent.json')).exists():
        raise ValueError('Operation already dispatched or outcome unknown; reconcile before retry')
    path = BASE + 'Microsoft.App/jobs/' + job_name
    job = request('GET', path + '?api-version=2024-03-01')
    runs = request('GET', path + '/executions?api-version=2024-03-01')['value']
    if any(r['properties']['status'] in ('Running', 'Processing') for r in runs):
        raise ValueError('Existing job busy; overlapping work prohibited')
    source = (ROOT / 'scripts' / script_name).read_bytes()
    if script_name == 'import_alpha_ms_recovered_positions.py':
        sources = []
        for day in ('2026-08-27', '2026-08-28'):
            item = json.loads((FOLDER / ('flex-' + day + '-source.json')).read_text())
            item['date'] = day
            sources.append(item)
        controls = json.loads((FOLDER / 'csv-controls.json').read_text())
        module = (ROOT / 'src/ibkr_etl/position_recovery.py').read_text()
        prelude = "import sys,types\nsys.path.insert(0,'/app/src')\nimport ibkr_etl\n"
        prelude += "m=types.ModuleType('ibkr_etl.position_recovery');sys.modules[m.__name__]=m\n"
        prelude += 'exec(compile(' + repr(module) + ",'position_recovery.py','exec'),m.__dict__)\n"
        prelude += 'CONTROLS=' + repr(controls) + '\nSOURCES=' + repr(sources) + '\n'
        # Transfer verified originals through the controlled execution, not by
        # widening the current reporting identity's legacy storage permissions.
        original_bytes = {}
        for s in sources:
            content = (FOLDER / (s['sha256'] + '.xml')).read_bytes()
            if hashlib.sha256(content).hexdigest() != s['sha256']:
                raise ValueError('Local original source integrity failed')
            original_bytes[s['sha256']] = base64.b64encode(zlib.compress(content)).decode()
        prelude += 'SOURCE_BYTES=' + repr(original_bytes) + '\n'
        source = prelude.encode() + source
    if script_name == 'rebuild_alpha_ms_recovered_history.py':
        reference = (FOLDER / 'reference_data.json').read_bytes()
        receipt = FOLDER / 'derived-apply-evidence.json'
        report_hashes = ({r['period_end']:r['data_sha256'] for r in json.loads(receipt.read_bytes())['reports']}
            if receipt.exists() else {})
        source = ('REFERENCE_BYTES=' + repr(base64.b64encode(zlib.compress(reference)).decode()) + '\n'
            + 'REFERENCE_SHA256=' + repr(hashlib.sha256(reference).hexdigest()) + '\n'
            + 'EXPECTED_REPORT_HASHES=' + repr(report_hashes) + '\n').encode() + source
    if script_name == 'archive_alpha_ms_recovery_closeout.py':
        content = (FOLDER / 'closeout.json').read_bytes()
        source = ('CLOSEOUT_BYTES=' + repr(base64.b64encode(zlib.compress(content)).decode()) + '\n'
            + 'CLOSEOUT_SHA256=' + repr(hashlib.sha256(content).hexdigest()) + '\n').encode() + source
    template = copy.deepcopy(job['properties']['template'])
    # ARM GET emits null optional members which the start endpoint rejects.
    template = {k: v for k, v in template.items() if v is not None}
    container = template['containers'][0]
    container['command'] = ['python']
    container['args'] = ['-c', "import base64,zlib;exec(compile(zlib.decompress(base64.b64decode('" + base64.b64encode(zlib.compress(source)).decode() + "')),'alpha-ms-recovery','exec'))"]
    for key, value in env.items():
        container['env'] = [e for e in container.get('env', []) if e['name'] != key]
        container['env'].append({'name': key, 'value': value})
    save(label + '-intent', {'job': job_name, 'script': script_name,
        'source_sha256': hashlib.sha256(source).hexdigest(),
        'job_invariant': invariant(job), 'environment_overrides': sorted(env)})
    ack = request('POST', path + '/start?api-version=2024-03-01', data=template)
    save(label + '-dispatch', {'job': job_name, 'execution': ack['name']})
    print(json.dumps({'label': label, 'job': job_name, 'execution': ack['name']}))


def read(label):
    ack = json.loads((FOLDER / (label + '-dispatch.json')).read_text())
    path = BASE + 'Microsoft.App/jobs/' + ack['job']
    run = request('GET', path + '/executions/' + ack['execution'] + '?api-version=2024-03-01')
    status = run['properties']['status']
    workspace_name = 'ibkrv11-logs' if ack['job'].startswith('ibkr-v11') else 'ibkrv2stg-logs'
    workspace = request('GET', BASE + 'Microsoft.OperationalInsights/workspaces/' + workspace_name + '?api-version=2023-09-01')
    logs = az('monitor', 'log-analytics', 'query', '-w', workspace['properties']['customerId'],
        '--analytics-query', "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" +
        ack['execution'] + "' | where Log_s startswith 'ALPHA_MS_RECOVERY_RESULT ' | project Log_s")
    records = [json.loads(r['Log_s'].removeprefix('ALPHA_MS_RECOVERY_RESULT ')) for r in logs]
    old = json.loads((FOLDER / (label + '-intent.json')).read_text())
    unchanged = invariant(request('GET', path + '?api-version=2024-03-01')) == old['job_invariant']
    result = {'status': status, 'job_settings_unchanged': unchanged, 'records': records, **ack}
    if status in ('Succeeded', 'Failed') and records and not (FOLDER / (label + '-result.json')).exists():
        save(label + '-result', result)
    print(json.dumps(result))


if __name__ == '__main__':
    mode, label = sys.argv[1:3]
    if mode == 'fetch':
        start('fetch-' + label, 'ibkr-v11-daily', 'recover_alpha_ms_flex_sources.py',
            {'ALPHA_MS_RECOVERY_DATE': label, 'IBKR_RUN_MIGRATIONS': 'false'})
    elif mode == 'read':
        read(label)
    elif mode == 'start':
        start(label, sys.argv[3], sys.argv[4], json.loads(sys.argv[5]))
    else:
        raise ValueError('Unknown recovery operation')
