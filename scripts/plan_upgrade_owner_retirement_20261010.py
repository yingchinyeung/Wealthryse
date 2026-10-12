"""Dependency and recovery evidence for the owner's explicit 22-job retirement."""
import hashlib, json, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_metadata_monitor import BASE
from deploy_ms_presentation_worker_release import settings_invariant
from control_upgrade_readers_20261010 import save, API, SOURCE
from control_upgrade_legacy_recovery_probe_20261009 import protected
from freeze_upgrade_owner_jobs_20261010 import OUT as FROZEN
from inspect_upgrade_owner_payloads_20261010 import decode
from audit_azure_runtime import az

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage17/retirement-v1'
TERMINAL = {'Succeeded', 'Failed', 'Stopped', 'Canceled', 'Cancelled'}


def load_plan():
    # This one 22-definition/27-execution archive exceeds the generic 2 MiB
    # recovery-record limit. Bind a 4 MiB read to its exact local bytes/version;
    # do not widen any shared recovery helper or accept arbitrary blob paths.
    from azure.identity import AzureCliCredential
    from azure.storage.blob import BlobServiceClient
    from ibkr_etl.specialist_archive_reader import ACCOUNT, CONTAINER
    value = json.loads((OUT / 'plan.json').read_bytes()); receipt = value['protected_receipt']
    expected = json.dumps({k: v for k, v in value.items() if k != 'protected_receipt'}, sort_keys=True, separators=(',', ':')).encode()
    if not 0 < len(expected) <= 4 * 1024**2 or receipt.get('protected_readback_verified') is not True or not receipt.get('version_id') or hashlib.sha256(expected).hexdigest() != receipt['sha256']: raise ValueError('Exact bounded archive required')
    if receipt['blob'] != 'acceptance/gateway-read-upgrade-readers-retirement-v1-plan-' + receipt['sha256'] + '.json': raise ValueError('Retirement archive route differs')
    blob = BlobServiceClient('https://' + ACCOUNT + '.blob.core.windows.net', credential=AzureCliCredential()).get_blob_client(CONTAINER, receipt['blob'], version_id=receipt['version_id'])
    props = blob.get_blob_properties()
    if props.size != len(expected) or props.version_id != receipt['version_id']: raise ValueError('Archived plan metadata differs')
    raw = blob.download_blob(offset=0, length=len(expected) + 1).readall()
    if raw != expected: raise ValueError('Archived plan bytes differ')
    return value


def listing(path, api):
    url = path + '?api-version=' + api; result = []; seen = set()
    for _ in range(30):
        value = request('GET', url); result.extend(value.get('value', []))
        link = value.get('nextLink')
        if not link: return result
        parsed = urlsplit(link)
        if parsed.netloc != 'management.azure.com' or parsed.scheme != 'https' or parsed.path.lower() != path.lower() or link in seen: raise ValueError('Pagination outside scope')
        seen.add(link); url = parsed.path + '?' + parsed.query
    raise ValueError('Pagination limit reached')


def strings(value):
    if isinstance(value, str): yield value
    elif isinstance(value, list):
        for v in value: yield from strings(v)
    elif isinstance(value, dict):
        for v in value.values(): yield from strings(v)


def refs(value, targets):
    text = '\n'.join(strings(value)).lower()
    return sorted(n for n in targets if n.lower() in text)


def bindings(items):
    return {x['name']: {'type': x['type'], 'invariant': settings_invariant(x),
        'images': [c['image'] for c in x['properties']['template']['containers']]} for x in items}


def assert_manual_idle(job, executions):
    p = job['properties']
    if p['configuration']['triggerType'] != 'Manual' or p.get('provisioningState') != 'Succeeded': raise ValueError('Not an idle manual definition')
    if any(e['properties']['status'] not in TERMINAL for e in executions): raise ValueError('Active/unknown execution; do not retire')


def capture_job(name):
    baseline = protected(FROZEN / name / 'baseline.json')
    path = rid('Microsoft.App/jobs', name)
    job = request('GET', path + API)
    if settings_invariant(job) != baseline['invariant'] or job['properties']['template']['containers'][0]['image'] != SOURCE: raise ValueError('Frozen job drift: ' + name)
    runs = listing(path + '/executions', '2026-07-01')
    assert_manual_idle(job, runs)
    # Preserve all execution templates, not only the summary list response.
    details = []
    for run in runs:
        full = request('GET', path + '/executions/' + run['name'] + API)
        for c in full['properties'].get('template', {}).get('containers', []):
            if any(e.get('value') for e in (c.get('env') or []) if any(k in e['name'] for k in ('PASSWORD', 'TOKEN', 'SECRET', '_URL'))): raise ValueError('Execution contains inline credential; do not archive')
        details.append(full)
    roles = listing(path + '/providers/Microsoft.Authorization/roleAssignments', '2022-04-01')
    return {'name': name, 'id': path, 'definition': job, 'invariant': baseline['invariant'],
        'baseline_receipt': baseline['protected_receipt'], 'executions': details, 'role_assignments': roles,
        'latest_execution': max((r['properties'].get('startTime', '') for r in details), default=None)}


def main():
    if (OUT / 'plan.json').exists(): raise ValueError('Preserve existing plan')
    inventory = protected(FROZEN / 'inventory.json')
    names = sorted(x['job'] for x in inventory['jobs'] if x['job'] != 'ibkrv2stg-bootstrap')
    if len(names) != 22 or any(not n.startswith('ibkrv2stg-own') for n in names): raise ValueError('Exact approved scope required')
    apps = listing(BASE + 'Microsoft.App/containerApps', '2026-07-01')
    jobs = listing(BASE + 'Microsoft.App/jobs', '2026-07-01')
    if not set(names) <= {j['name'] for j in jobs}: raise ValueError('Missing target; reconcile before planning')
    dependencies = []
    for item in apps + jobs:
        if item['name'] in names: continue
        found = refs(item, names)
        for c in item['properties']['template']['containers']:
            argv = (c.get('command') or []) + (c.get('args') or [])
            if '-c' in argv and argv[-1]:
                for source in decode(argv[-1]): found += refs(source, names)
        if found: dependencies.append({'caller': item['name'], 'targets': sorted(set(found)), 'kind': 'app_or_job_definition'})
    types = {}
    resources = az('resource', 'list', '-g', 'rg-ibkr-database-v11')
    for r in resources: types[r['type'].lower()] = types.get(r['type'].lower(), 0) + 1
    unsupported = [t for t in types if any(k in t for k in ('microsoft.logic/', 'microsoft.automation/', 'microsoft.web/sites', 'microsoft.datafactory/', 'microsoft.eventgrid/'))]
    if unsupported: dependencies.append({'kind': 'unreviewed_scheduler_types', 'types': unsupported})
    auxiliary = []
    for kind, version in (('Microsoft.Insights/actionGroups', '2023-01-01'), ('Microsoft.Insights/metricAlerts', '2018-03-01'), ('Microsoft.Insights/scheduledQueryRules', '2023-12-01')):
        for r in listing(BASE + kind, version):
            hits = refs(r, names)
            auxiliary.append({'id': r['id'], 'sha256': hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest(), 'target_references': hits})
            if hits: dependencies.append({'caller': r['name'], 'targets': hits, 'kind': kind})
    # Live ACR task definitions may run jobs through scripts; examine each task.
    for registry in ('ibkrv2stgauhecwc4itaoo', 'ibkrv115rcc62xxqblay'):
        for task in listing(rid('Microsoft.ContainerRegistry/registries', registry) + '/tasks', '2019-06-01-preview'):
            hits = refs(task, names)
            auxiliary.append({'id': task['id'], 'sha256': hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest(), 'target_references': hits})
            if hits: dependencies.append({'caller': task['name'], 'targets': hits, 'kind': 'registry_task'})
    # Local references are preserved and reported, never edited away to clear a gate.
    local = []
    for folder in ('src', 'scripts', 'infra'):
        for path in (ROOT / folder).rglob('*'):
            if path.suffix not in ('.py', '.json', '.bicep', '.yml', '.yaml') or path.name.startswith(('plan_upgrade_owner_retirement_', 'retire_upgrade_owner_')): continue
            text = path.read_text(encoding='utf-8', errors='replace'); hits = refs(text, names)
            if hits: local.append({'path': str(path.relative_to(ROOT)), 'targets': hits, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    # Collect in bounded read-only parallelism. No deletion is performed here.
    with ThreadPoolExecutor(max_workers=4) as pool: evidence = list(pool.map(capture_job, names))
    record = {'prepared_at': datetime.now(timezone.utc).isoformat(), 'targets': names, 'jobs': evidence,
        'non_target_bindings': bindings([x for x in apps + jobs if x['name'] not in names]),
        'resource_type_counts': types, 'auxiliary_callers': auxiliary, 'dependencies': dependencies, 'local_references': local,
        'owner_authorization': 'Approve retirement after dependency checks', 'deletion_authorized_only_after_review': True,
        'execution_logs': 'Existing Log Analytics records remain untouched; complete ARM execution metadata is archived here. Azure execution history cannot be recreated from a job-definition restore.',
        'restore_scope': 'Exact job definitions and scoped role-assignment metadata retained. No identity, database, source, report, schedule or other job is authorized for deletion.',
        'plan_passed': not dependencies, 'deleted': False, 'full_upgrade_complete': False}
    save(OUT, 'plan', record)
    print(json.dumps({'jobs': len(evidence), 'executions': sum(len(j['executions']) for j in evidence), 'dependencies': dependencies, 'local_references': len(local), 'plan_passed': not dependencies}))


if __name__ == '__main__': main()
