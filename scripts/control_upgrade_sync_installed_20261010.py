"""Installed image acceptance, existing storage reads only; no imports."""
import hashlib
import json
from pathlib import Path
import sys
from control_upgrade_sync_postgres_20261010 import OUT, JOB, PATH, API
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request
FOLDER = OUT / 'installed-read-v1'


def save(name, value):
    with (FOLDER / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump({**value, 'protected_receipt': retain(value, 'upgrade-sync-installed-' + name)}, f, indent=2)


def main(stage):
    deployed = protected(OUT / 'deployment-v1/verified.json')
    plan = protected(OUT / 'deployment-v1/plan.json')
    job = request('GET', PATH + API); image = job['properties']['template']['containers'][0]['image']
    if not deployed['passed'] or image != deployed['image'] or image != plan['image'] or settings_invariant(job) != plan['invariant']:
        raise ValueError('Installed deployment boundary required')
    if stage == 'start':
        if FOLDER.exists(): raise ValueError('Reconcile existing installed read test')
        runtime.idle(JOB)
        code = Path(__file__).with_name('probe_upgrade_sync_installed_20261010.py').read_text(encoding='utf-8')
        template = corrected_template(job, code); c = template['containers'][0]
        allowed = {'PRODUCTION_STORAGE_ACCOUNT_URL', 'PRODUCTION_EXPORT_CONTAINER', 'PRODUCTION_EXPORT_PREFIX', 'PORTFOLIO_TENANT_ID', 'AZURE_CLIENT_ID'}
        c['env'] = [e for e in c.get('env', []) if e['name'] in allowed]
        if not {'PRODUCTION_STORAGE_ACCOUNT_URL', 'PORTFOLIO_TENANT_ID'} <= {e['name'] for e in c['env']}:
            raise ValueError('Existing source configuration required')
        FOLDER.mkdir(); save('intent', {'image': image, 'template': template, 'source_sha256': hashlib.sha256(code.encode()).hexdigest(), 'invariant': settings_invariant(job), 'scope': 'Current export storage read and exact SHA256 validation only; no database connection or broker call; normal freshness rule unchanged'})
        ack = request('POST', PATH + '/start' + API, data=template); save('dispatch', {'execution': ack['name']})
        print(json.dumps({'execution': ack['name']})); return
    if stage != 'read': raise ValueError('Unknown step')
    intent = protected(FOLDER / 'intent.json'); execution = protected(FOLDER / 'dispatch.json')['execution']
    if settings_invariant(job) != intent['invariant']: raise ValueError('Saved configuration drift')
    run = request('GET', PATH + '/executions/' + execution + API)
    actual = run['properties']['template']['containers'][0]; expected = intent['template']['containers'][0]
    if any(actual.get(k) != expected.get(k) for k in ('image', 'command', 'args')): raise ValueError('Executed check differs')
    state = run['properties']['status']; print(json.dumps({'execution': execution, 'status': state}))
    if state not in ('Succeeded', 'Failed'): return
    prefix = 'SYNC_INSTALLED_READ_PROOF '
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution + "-' | where Log_s startswith '" + prefix + "' | project Result=parse_json(substring(Log_s," + str(len(prefix)) + '))'
    rows = az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', query)
    if len(rows) != 1: print('Final proof not available; inspect errors if terminal failure'); return
    proof = json.loads(rows[0]['Result'])
    passed = state == 'Succeeded' and proof['passed'] and proof['source_hash_verified'] and proof['application_files_unchanged'] == 452 and all(proof[k] == 0 for k in ('database_connections', 'database_writes', 'blob_writes', 'broker_calls'))
    if not (FOLDER / 'result.json').exists(): save('result', {'passed': passed, 'image': image, 'execution': execution, 'proof': proof, 'all_other_settings_preserved': True, 'normal_cycle_verified': False})
    print(json.dumps({'passed': passed, 'proof': proof}))


if __name__ == '__main__': main(sys.argv[1])
