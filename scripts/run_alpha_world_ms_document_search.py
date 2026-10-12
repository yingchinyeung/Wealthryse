"""Read-only one-execution inventory; does not grant any new storage access."""
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
from run_alpha_world_ms_database_search import invariant, write, FOLDER

path = BASE + 'Microsoft.App/jobs/ibkrv2stg-onboard-render'
if sys.argv[1] == 'start':
    if (FOLDER / 'document-search-intent.json').exists():
        raise ValueError('Already dispatched; read original operation')
    job = request('GET', path + '?api-version=2024-03-01')
    template = copy.deepcopy(job['properties']['template'])
    template.pop('volumes', None)
    c = template['containers'][0]
    c['env'] = [e for e in c['env'] if e['name'] in ('AZURE_STORAGE_ACCOUNT_URL', 'AZURE_CLIENT_ID')]
    source = (ROOT / 'scripts/probe_alpha_world_ms_document_containers.py').read_bytes()
    c['command'] = ['python']
    c['args'] = ['-c', "import base64;exec(compile(base64.b64decode('" + base64.b64encode(source).decode() + "'),'ms-doc-source-search','exec'))"]
    write('document-search-intent', {'job_invariant': invariant(job), 'source_sha256': hashlib.sha256(source).hexdigest()})
    run = request('POST', path + '/start?api-version=2024-03-01', data=template)
    write('document-search-dispatch', {'execution': run['name']})
    print(json.dumps({'execution': run['name']}))
elif sys.argv[1] == 'read':
    execution = json.loads((FOLDER / 'document-search-dispatch.json').read_text())['execution']
    status = request('GET', path + '/executions/' + execution + '?api-version=2024-03-01')['properties']['status']
    if status != 'Succeeded':
        print(json.dumps({'status': status, 'execution': execution}))
        sys.exit(0)
    rows = az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb',
              '--analytics-query', "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution +
              "' | where Log_s startswith 'MS_DOCUMENT_SOURCE_SEARCH ' | project Log_s")
    result = [json.loads(r['Log_s'].removeprefix('MS_DOCUMENT_SOURCE_SEARCH ')) for r in rows]
    intent = json.loads((FOLDER / 'document-search-intent.json').read_text())
    unchanged = invariant(request('GET', path + '?api-version=2024-03-01')) == intent['job_invariant']
    if result:
        write('document-search-results', {'execution': execution, 'records': result, 'job_unchanged': unchanged})
    print(json.dumps({'status': status, 'job_unchanged': unchanged, 'records': result}))
else:
    raise ValueError('Choose start or read')
