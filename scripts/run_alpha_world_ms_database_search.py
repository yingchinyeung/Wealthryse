"""Dispatch/read isolated read-only searches using already configured jobs."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.work/alpha-ms-azure-controller-runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
from deploy_metadata_monitor import request, BASE
from audit_azure_runtime import az

FOLDER = ROOT / '.work/alpha-ms-source-search-20261007'
TARGETS = {'legacy': ('ibkr-v11-daily', 'database-url'),
           'legacy-r2': ('ibkr-v11-daily', 'database-url'),
           'current': ('ibkrv2stg-bootstrap', 'database-admin-url'),
           'recovery': ('ibkrv2stg-bootstrap', 'database-admin-url')}


def write(name, data):
    with (FOLDER / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2, default=str, sort_keys=True)


def invariant(job):
    return hashlib.sha256(json.dumps({'identity': job.get('identity'),
        'configuration': job['properties']['configuration'], 'template': job['properties']['template']},
        sort_keys=True).encode()).hexdigest()


def start(kind):
    job_name, secret = TARGETS[kind]
    intent_path = FOLDER / ('db-' + kind + '-intent.json')
    if intent_path.exists():
        raise ValueError('Search already dispatched; reconcile the existing execution')
    path = BASE + 'Microsoft.App/jobs/' + job_name
    job = request('GET', path + '?api-version=2024-03-01')
    runs = request('GET', path + '/executions?api-version=2024-03-01')['value']
    if any(r['properties']['status'] in ('Running', 'Processing') for r in runs):
        raise ValueError('Existing job busy; do not overlap')
    source = (ROOT / 'scripts/probe_alpha_world_ms_source_databases.py').read_bytes()
    template = copy.deepcopy(job['properties']['template'])
    template.pop('volumes', None)
    container = template['containers'][0]
    container['command'] = ['python']
    container['args'] = ['-c', "import base64;exec(compile(base64.b64decode('" + base64.b64encode(source).decode() + "'),'ms-source-db-readonly','exec'))"]
    container['env'] = [{'name': 'DATABASE_URL', 'secretRef': secret},
                        {'name': 'MS_SOURCE_DATABASE_TARGET', 'value': kind.split('-r')[0]}]
    write('db-' + kind + '-intent', {'job': job_name, 'source_sha256': hashlib.sha256(source).hexdigest(),
                                  'persistent_invariant': invariant(job), 'read_only': True})
    run = request('POST', path + '/start?api-version=2024-03-01', data=template)
    write('db-' + kind + '-dispatch', {'execution': run['name'], 'job': job_name})
    print(json.dumps({'target': kind, 'execution': run['name']}))


def read(kind):
    ack = json.loads((FOLDER / ('db-' + kind + '-dispatch.json')).read_text())
    path = BASE + 'Microsoft.App/jobs/' + ack['job']
    run = request('GET', path + '/executions/' + ack['execution'] + '?api-version=2024-03-01')
    status = run['properties']['status']
    if status not in ('Succeeded', 'Failed'):
        print(json.dumps({'target': kind, 'status': status}))
        return
    workspace_name = 'ibkrv11-logs' if kind.startswith('legacy') else 'ibkrv2stg-logs'
    workspace = request('GET', BASE + 'Microsoft.OperationalInsights/workspaces/' + workspace_name + '?api-version=2023-09-01')
    logs = az('monitor', 'log-analytics', 'query', '-w', workspace['properties']['customerId'],
              '--analytics-query', "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" +
              ack['execution'] + "' | where Log_s startswith 'MS_SOURCE_DB_RESULT ' | project Log_s")
    records, incomplete = [], 0
    for row in logs:
        try:
            records.append(json.loads(row['Log_s'].removeprefix('MS_SOURCE_DB_RESULT ')))
        except json.JSONDecodeError:
            incomplete += 1
    original = json.loads((FOLDER / ('db-' + kind + '-intent.json')).read_text())
    unchanged = invariant(request('GET', path + '?api-version=2024-03-01')) == original['persistent_invariant']
    if records:
        write('db-' + kind + '-results', {'execution': ack['execution'], 'native_status': status,
                                        'job_settings_unchanged': unchanged, 'records': records,
                                        'truncated_log_records': incomplete})
    print(json.dumps({'target': kind, 'status': status, 'records': len(records),
                      'summary': [r for r in records if 'summary' in r], 'job_settings_unchanged': unchanged,
                      'truncated_log_records': incomplete}))


if __name__ == '__main__':
    {'start': start, 'read': read}[sys.argv[1]](sys.argv[2])
